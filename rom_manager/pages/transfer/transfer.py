import asyncio

from aiohttp import web

from ...app_keys import DOWNLOADS
from ...core.transfers import TransferError, check_space, clean_name, save_stream
from ..layout import upload_chunks

routes = web.RouteTableDef()


def refuse(message):
    return web.json_response({"error": message}, status=400)


def text_fields(data):
    return {key: value for key, value in data.items() if isinstance(value, str)}


async def check(request, destination_from):
    try:
        data = await request.json()
        names = [str(name) for name in data.get("names") or []]
        destination = destination_from(text_fields(data))
        problems = destination.problems(names, bool(data.get("overwrite")))
        check_space(destination.folder, int(data.get("size") or 0))
    except TransferError as error:
        return refuse(str(error))
    except (ValueError, TypeError, AttributeError):
        return refuse("Invalid request")
    warnings = {name: warning for name in names if name not in problems and (warning := destination.warning(name))}
    return web.json_response({"problems": problems, "warnings": warnings})


async def save_upload_parts(request, destination_from, fields, saved, failed):
    destination = None
    async for part in await request.multipart():
        if part.filename is None:
            fields[part.name] = await part.text()
            continue
        if destination is None:
            destination = destination_from(fields)
            check_space(destination.folder, request.content_length)
        try:
            path = destination.path_for(clean_name(part.filename), fields.get("overwrite") == "1")
        except TransferError as error:
            failed[part.filename] = str(error)
            continue
        await save_stream(upload_chunks(part), path)
        saved.append({"name": part.filename, "warning": await asyncio.to_thread(destination.warning, part.filename)})


async def upload(request, destination_from, after_upload=None):
    fields, saved, failed = {}, [], {}
    try:
        await save_upload_parts(request, destination_from, fields, saved, failed)
    except TransferError as error:
        return web.json_response({"error": str(error), "saved": saved, "failed": failed}, status=400)
    except ConnectionError:
        return web.json_response({"error": "Upload interrupted", "saved": saved, "failed": failed}, status=400)
    finally:
        if saved and after_upload:
            await after_upload()
    return web.json_response({"saved": saved, "failed": failed})


async def links(request, destination_from):
    try:
        data = await request.json()
        fields = text_fields(data)
        jobs = request.app[DOWNLOADS].add(fields.get("urls", ""), destination_from(fields),
                                          bool(data.get("overwrite")), fields.get("name", ""))
    except TransferError as error:
        return refuse(str(error))
    except (ValueError, TypeError, AttributeError):
        return refuse("Invalid request")
    return web.json_response({"jobs": jobs})


@routes.post("/jobs/{job_id}/cancel")
async def cancel_job(request):
    job_id = request.match_info["job_id"]
    try:
        request.app[DOWNLOADS].cancel(int(job_id) if job_id.isdigit() else None)
    except TransferError as error:
        return refuse(str(error))
    return web.json_response({"ok": True})
