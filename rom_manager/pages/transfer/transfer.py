import asyncio

from aiohttp import web

from ...utils.roms import RomError, check_space, clean_name, destination, save_stream
from ..layout import part_chunks


class Area:
    def __init__(self, folder, warning, make_target, after_upload=None):
        self.folder, self.warning, self.make_target, self.after_upload = folder, warning, make_target, after_upload


def check_names(folder, names, overwrite):
    problems, seen = {}, set()
    for name in names:
        try:
            destination(folder, clean_name(name), overwrite)
            if name in seen:
                raise RomError("%s is listed twice" % name)
        except RomError as error:
            problems[name] = str(error)
        seen.add(name)
    return problems


async def check(request, area):
    try:
        data = await request.json()
        names = [str(n) for n in data.get("names") or []]
        fields = {key: str(value) for key, value in data.items() if isinstance(value, str)}
        folder = area.folder(fields)
        problems = check_names(folder, names, bool(data.get("overwrite")))
        check_space(folder, int(data.get("size") or 0))
    except RomError as error:
        return web.json_response({"error": str(error)}, status=400)
    except (ValueError, TypeError, AttributeError):
        return web.json_response({"error": "Invalid request"}, status=400)
    warnings = {name: warning for name in names if name not in problems and (warning := area.warning(fields, name))}
    return web.json_response({"problems": problems, "warnings": warnings})


async def save_parts(request, area, fields, saved, failed):
    folder = None
    async for part in await request.multipart():
        if part.filename is None:
            fields[part.name] = await part.text()
            continue
        if folder is None:
            folder = area.folder(fields)
            check_space(folder, request.content_length)
        try:
            path = destination(folder, clean_name(part.filename), fields.get("overwrite") == "1")
        except RomError as error:
            failed[part.filename] = str(error)
            continue
        await save_stream(part_chunks(part), path)
        saved.append({"name": part.filename, "warning": await asyncio.to_thread(area.warning, fields, part.filename)})


async def upload(request, area):
    fields, saved, failed = {}, [], {}
    try:
        await save_parts(request, area, fields, saved, failed)
    except RomError as error:
        return web.json_response({"error": str(error), "saved": saved, "failed": failed}, status=400)
    except ConnectionError:
        return web.json_response({"error": "Upload interrupted", "saved": saved, "failed": failed}, status=400)
    finally:
        if saved and area.after_upload:
            await area.after_upload(request)
    return web.json_response({"saved": saved, "failed": failed})


async def links(request, area):
    try:
        data = await request.json()
        fields = {key: str(value) for key, value in data.items() if isinstance(value, str)}
        jobs = request.app["downloads"].add(fields.get("urls", ""), area.make_target(fields),
                                            bool(data.get("overwrite")), fields.get("name", ""))
    except RomError as error:
        return web.json_response({"error": str(error)}, status=400)
    except (ValueError, TypeError, AttributeError):
        return web.json_response({"error": "Invalid request"}, status=400)
    return web.json_response({"jobs": jobs})


async def cancel_job(request):
    job_id = request.match_info["job_id"]
    try:
        request.app["downloads"].cancel(int(job_id) if job_id.isdigit() else None)
    except RomError as error:
        return web.json_response({"error": str(error)}, status=400)
    return web.json_response({"ok": True})
