from aiohttp import web

from ...utils.es import all_systems
from ...utils.roms import (CHUNK_SIZE, RomError, check_space, clean_name, destination, extension_warning, save_stream,
                           target_dir)
from ..layout import render


async def add(request):
    systems = sorted(all_systems().values(), key=lambda s: s["fullname"].lower())
    return render(request, "add", systems=systems, selected=request.query.get("system", ""))


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


async def check(request):
    try:
        data = await request.json()
        system, names = str(data.get("system", "")), [str(n) for n in data.get("names") or []]
        folder = target_dir(system, str(data.get("subfolder", "")))
        problems = check_names(folder, names, bool(data.get("overwrite")))
        check_space(folder, int(data.get("size") or 0))
    except RomError as error:
        return web.json_response({"error": str(error)}, status=400)
    except (ValueError, TypeError, AttributeError):
        return web.json_response({"error": "Invalid request"}, status=400)
    warnings = {name: warning for name in names
                if name not in problems and (warning := extension_warning(system, name))}
    return web.json_response({"problems": problems, "warnings": warnings})


async def part_chunks(part):
    while chunk := await part.read_chunk(CHUNK_SIZE):
        yield chunk


async def save_parts(request, fields, saved, failed):
    folder = None
    async for part in await request.multipart():
        if part.filename is None:
            fields[part.name] = await part.text()
            continue
        if folder is None:
            folder = target_dir(fields.get("system", ""), fields.get("subfolder", ""))
            check_space(folder, request.content_length)
        try:
            path = destination(folder, clean_name(part.filename), fields.get("overwrite") == "1")
        except RomError as error:
            failed[part.filename] = str(error)
            continue
        await save_stream(part_chunks(part), path)
        saved.append({"name": part.filename, "warning": extension_warning(fields["system"], part.filename)})


async def upload(request):
    fields, saved, failed = {}, [], {}
    try:
        await save_parts(request, fields, saved, failed)
    except RomError as error:
        return web.json_response({"error": str(error), "saved": saved, "failed": failed}, status=400)
    except ConnectionError:
        return web.json_response({"error": "Upload interrupted", "saved": saved, "failed": failed}, status=400)
    finally:
        if saved:
            await request.app["library"].rescan(request.app["es"])
    return web.json_response({"saved": saved, "failed": failed})


async def links(request):
    try:
        data = await request.json()
        jobs = request.app["downloads"].add(
            str(data.get("urls", "")), str(data.get("system", "")), str(data.get("subfolder", "")),
            bool(data.get("overwrite")), str(data.get("name") or ""))
    except RomError as error:
        return web.json_response({"error": str(error)}, status=400)
    except (ValueError, TypeError, AttributeError):
        return web.json_response({"error": "Invalid request"}, status=400)
    return web.json_response({"jobs": jobs})


async def jobs(request):
    return web.json_response(request.app["downloads"].snapshot())


async def cancel_job(request):
    job_id = request.match_info["job_id"]
    try:
        request.app["downloads"].cancel(int(job_id) if job_id.isdigit() else None)
    except RomError as error:
        return web.json_response({"error": str(error)}, status=400)
    return web.json_response({"ok": True})
