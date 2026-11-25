import asyncio
import logging
import os

from aiohttp import web

from ...utils import bios
from ...utils.downloads import Target
from ...utils.roms import RomError, save_stream
from ..layout import flash, part_chunks, render, safe_next
from ..transfer import transfer


def folder_for(fields):
    return bios.bios_folder(fields.get("folder", ""), fields.get("subfolder", ""))


def no_warning(fields, name):
    return None


def place(folder):
    return os.path.join("bios", bios.relative_bios_path(folder)).rstrip("/")


def target_for(fields):
    folder = folder_for(fields)
    return Target("bios", folder, place(folder))


BIOS = transfer.Area(folder_for, no_warning, target_for)


def breadcrumbs(relative):
    crumbs, parts = [], [part for part in relative.split("/") if part]
    for index, part in enumerate(parts):
        crumbs.append((part, "/".join(parts[:index + 1])))
    return crumbs


async def bios_page(request):
    library = request.app["library"]
    await library.ensure_loaded(request.app["es"])
    show_all = request.query.get("all") == "1"
    relative, listing_error = request.query.get("path", "").strip("/"), None
    try:
        folders, files = await asyncio.to_thread(bios.folder_listing, bios.bios_folder(relative))
    except (RomError, OSError) as error:
        folders, files, listing_error = [], [], str(error)
    try:
        report, report_error = await asyncio.to_thread(bios.bios_report), None
    # Batocera's checker is an outside script that can change between releases
    except Exception as error:
        logging.exception("BIOS check failed")
        report, report_error = [], "Batocera's BIOS check failed: %s" % error
    counts = library.counts(hidden=True)
    shown = [system for system in report if show_all or counts[system["name"]]]
    return await render(request, "bios", relative=relative, crumbs=breadcrumbs(relative), folders=folders, files=files,
                        listing_error=listing_error, report_error=report_error, show_all=show_all,
                        problems=[s for s in shown if s["problems"]], complete=[s for s in shown if not s["problems"]])


async def check(request):
    return await transfer.check(request, BIOS)


async def upload(request):
    return await transfer.upload(request, BIOS)


async def links(request):
    return await transfer.links(request, BIOS)


async def jobs(request):
    return web.json_response(request.app["downloads"].snapshot("bios"))


def entry_warning(system, file):
    problem = bios.entry_problem(system, file)
    return "Saved, but %s" % problem if problem else None


async def entry_upload(request):
    fields, message = {}, None
    try:
        async for part in await request.multipart():
            if part.filename is None:
                fields[part.name] = await part.text()
                continue
            path = bios.entry_path(fields.get("system", ""), fields.get("file", ""))
            if not part.filename:
                raise RomError("Choose a file")
            await save_stream(part_chunks(part), path)
            problem = await asyncio.to_thread(bios.entry_problem, fields["system"], fields["file"])
            if problem:
                message = ("warning", "Saved %s, but %s" % (fields["file"], problem))
            else:
                message = ("success", "Saved %s, Batocera's check is happy" % fields["file"])
            break
        else:
            raise RomError("Choose a file")
    except ConnectionError:
        message = ("danger", "Not saved: the upload was interrupted")
    except (RomError, OSError) as error:
        message = ("danger", "Not saved: %s" % error)
    response = web.HTTPFound(safe_next(fields.get("next", "/bios")))
    flash(response, *message)
    raise response


async def entry_link(request):
    form = await request.post()
    response = web.HTTPFound(safe_next(form.get("next", "/bios")))
    system, file = form.get("system", ""), form.get("file", "")
    try:
        path = bios.entry_path(system, file)
        folder = os.path.dirname(path)
        target = Target("bios", folder, place(folder), warning=lambda name: entry_warning(system, file))
        request.app["downloads"].add(form.get("url", ""), target, True, os.path.basename(path))
        flash(response, "success", "Download of %s queued, it shows under Downloads" % file)
    except RomError as error:
        flash(response, "danger", "Not queued: %s" % error)
    raise response


async def delete(request):
    form = await request.post()
    response = web.HTTPFound(safe_next(form.get("next", "/bios")))
    try:
        await asyncio.to_thread(bios.delete_bios_file, form.get("path", ""))
        flash(response, "success", "Deleted bios/%s" % form.get("path", ""))
    except (RomError, OSError) as error:
        flash(response, "danger", "Not deleted: %s" % error)
    raise response
