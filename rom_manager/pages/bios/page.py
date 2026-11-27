import asyncio
import logging
import os

from aiohttp import web

from ...app_keys import DOWNLOADS
from ...core import bios
from ...core.transfers import Destination, TransferError, save_stream
from ..layout import loaded_library, local_url, redirect, render, upload_chunks
from ..transfer import transfer

routes = web.RouteTableDef()


def bios_destination(fields):
    folder = bios.bios_folder(fields.get("folder", ""), fields.get("subfolder", ""))
    return Destination("bios", folder, bios.bios_label(folder))


def breadcrumbs(relative):
    parts = [part for part in relative.split("/") if part]
    return [(part, "/".join(parts[:index + 1])) for index, part in enumerate(parts)]


def entry_warning(system, file):
    problem = bios.entry_problem(system, file)
    return f"Saved, but {problem}" if problem else None


@routes.get("/bios")
async def bios_page(request):
    library = await loaded_library(request)
    show_all = request.query.get("all") == "1"
    relative, listing_error = request.query.get("path", "").strip("/"), None
    try:
        folders, files = await asyncio.to_thread(bios.folder_listing, bios.bios_folder(relative))
    except (TransferError, OSError) as error:
        folders, files, listing_error = [], [], str(error)
    try:
        report, report_error = await asyncio.to_thread(bios.bios_report), None
    # Batocera's checker is an outside script that can change between releases
    except Exception as error:
        logging.exception("BIOS check failed")
        report, report_error = [], f"Batocera's BIOS check failed: {error}"
    counts = library.counts(show_hidden=True)
    shown = [system for system in report if show_all or counts[system["name"]]]
    return await render(request, "bios/page.html", relative=relative, crumbs=breadcrumbs(relative), folders=folders,
                        files=files, listing_error=listing_error, report_error=report_error, show_all=show_all,
                        problems=[system for system in shown if system["problems"]],
                        complete=[system for system in shown if not system["problems"]])


@routes.post("/bios/check")
async def check(request):
    return await transfer.check(request, bios_destination)


@routes.post("/bios/upload")
async def upload(request):
    return await transfer.upload(request, bios_destination)


@routes.post("/bios/links")
async def links(request):
    return await transfer.links(request, bios_destination)


@routes.get("/bios/jobs")
async def jobs(request):
    return web.json_response(request.app[DOWNLOADS].listing("bios"))


@routes.post("/bios/entry/upload")
async def entry_upload(request):
    fields = {}
    try:
        async for part in await request.multipart():
            if part.filename is None:
                fields[part.name] = await part.text()
                continue
            path = bios.entry_path(fields.get("system", ""), fields.get("file", ""))
            if not part.filename:
                raise TransferError("Choose a file")
            await save_stream(upload_chunks(part), path)
            problem = await asyncio.to_thread(bios.entry_problem, fields["system"], fields["file"])
            if problem:
                message = ("warning", f"Saved {fields['file']}, but {problem}")
            else:
                message = ("success", f"Saved {fields['file']}, Batocera's check is happy")
            break
        else:
            raise TransferError("Choose a file")
    except ConnectionError:
        message = ("danger", "Not saved: the upload was interrupted")
    except (TransferError, OSError) as error:
        message = ("danger", f"Not saved: {error}")
    raise redirect(local_url(fields.get("next", ""), "/bios"), *message)


@routes.post("/bios/entry/link")
async def entry_link(request):
    form = await request.post()
    back = local_url(form.get("next", ""), "/bios")
    system, file = form.get("system", ""), form.get("file", "")
    try:
        path = bios.entry_path(system, file)
        folder = os.path.dirname(path)
        destination = Destination("bios", folder, bios.bios_label(folder),
                                  warning=lambda name: entry_warning(system, file))
        request.app[DOWNLOADS].add(form.get("url", ""), destination, True, os.path.basename(path))
    except TransferError as error:
        raise redirect(back, "danger", f"Not queued: {error}")
    raise redirect(back, "success", f"Download of {file} queued, it shows under Downloads")


@routes.post("/bios/delete")
async def delete(request):
    form = await request.post()
    back = local_url(form.get("next", ""), "/bios")
    try:
        await asyncio.to_thread(bios.delete_bios_file, form.get("path", ""))
    except (TransferError, OSError) as error:
        raise redirect(back, "danger", f"Not deleted: {error}")
    raise redirect(back, "success", f"Deleted bios/{form.get('path', '')}")
