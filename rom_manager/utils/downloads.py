import asyncio
import itertools
import logging
import urllib.parse

import aiohttp

from .roms import CHUNK_SIZE, RomError, check_space, clean_name, destination, save_stream

MAX_KEPT_JOBS = 50
ACTIVE_STATES = ("queued", "downloading")
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(sock_connect=20, sock_read=120)


class Target:
    def __init__(self, area, folder, place, subfolder="", reload_es=False, warning=None):
        self.area, self.folder, self.place, self.subfolder = area, folder, place, subfolder
        self.reload_es, self.warning = reload_es, warning or (lambda name: None)


class Job:
    def __init__(self, job_id, url, target, overwrite, name):
        self.id, self.url, self.target, self.overwrite, self.name = job_id, url, target, overwrite, name
        self.status, self.received, self.total = "queued", 0, None
        self.error = self.warning = self.task = None

    def add_progress(self, size):
        self.received += size

    def as_dict(self):
        return {"id": self.id, "url": self.url, "name": self.name, "place": self.target.place,
                "subfolder": self.target.subfolder, "status": self.status, "received": self.received,
                "total": self.total, "error": self.error, "warning": self.warning}


def clean_urls(text):
    urls = [line.strip() for line in text.splitlines() if line.strip()]
    if not urls:
        raise RomError("Add at least one link")
    for url in urls:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise RomError("Not an http(s) link: %s" % url)
    return urls


def name_from_response(response, url):
    disposition = response.content_disposition
    if disposition and disposition.filename:
        return disposition.filename
    return response.url.name or urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])


def describe_error(error):
    if isinstance(error, aiohttp.ClientResponseError):
        return "HTTP %d %s" % (error.status, error.message)
    if isinstance(error, asyncio.TimeoutError):
        return "Timed out"
    return str(error) or type(error).__name__


class DownloadQueue:
    def __init__(self):
        self.jobs = {}
        self.pending = asyncio.Queue()
        self.next_id = itertools.count(1)
        self.saved_since_reload = False

    def add(self, urls_text, target, overwrite, name=""):
        urls = clean_urls(urls_text)
        if name.strip():
            if len(urls) > 1:
                raise RomError("A file name can only be set for a single link")
            name = clean_name(name)
        jobs = [Job(next(self.next_id), url, target, overwrite, name.strip() or None) for url in urls]
        for job in jobs:
            self.jobs[job.id] = job
            self.pending.put_nowait(job)
        self.prune()
        return [job.as_dict() for job in jobs]

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if not job or job.status not in ACTIVE_STATES:
            raise RomError("That download isn't running")
        if job.task:
            job.task.cancel()
        else:
            job.status = "cancelled"

    def snapshot(self, area):
        return [job.as_dict() for job in sorted(self.jobs.values(), key=lambda j: j.id, reverse=True)
                if job.target.area == area]

    def prune(self):
        finished = [job_id for job_id, job in sorted(self.jobs.items()) if job.status not in ACTIVE_STATES]
        for job_id in finished[:max(0, len(self.jobs) - MAX_KEPT_JOBS)]:
            del self.jobs[job_id]

    async def run(self, app):
        async with aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT) as session:
            while True:
                job = await self.pending.get()
                if job.status == "queued":
                    await self.run_job(job, session)
                if self.pending.empty() and self.saved_since_reload:
                    self.saved_since_reload = False
                    try:
                        await app["library"].rescan(app["es"])
                    except Exception:
                        logging.exception("ES reload after downloads failed")

    async def run_job(self, job, session):
        job.task = asyncio.create_task(self.download(job, session))
        try:
            # asyncio.wait keeps a cancelled job from cancelling the worker itself
            await asyncio.wait([job.task])
        finally:
            if not job.task.done():
                job.task.cancel()
                await asyncio.wait([job.task])
            job.task = None

    async def download(self, job, session):
        job.status = "downloading"
        try:
            folder = job.target.folder
            async with session.get(job.url) as response:
                response.raise_for_status()
                job.name = clean_name(job.name or name_from_response(response, job.url))
                job.total = response.content_length
                check_space(folder, job.total)
                path = destination(folder, job.name, job.overwrite)
                await save_stream(response.content.iter_chunked(CHUNK_SIZE), path, job.add_progress)
        except asyncio.CancelledError:
            job.status = "cancelled"
            return
        except RomError as error:
            job.status, job.error = "failed", str(error)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
            job.status, job.error = "failed", describe_error(error)
            return
        job.status = "done"
        job.warning = await asyncio.to_thread(job.target.warning, job.name)
        self.saved_since_reload = self.saved_since_reload or job.target.reload_es
