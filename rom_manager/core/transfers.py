import asyncio
import collections
import itertools
import logging
import os
import shutil
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

import aiohttp

from ..config import ROMS_DIR
from .game_files import human_size

CHUNK_SIZE = 1 << 20
PART_SUFFIX = ".part"
MAX_KEPT_JOBS = 50
PARALLEL_DOWNLOADS = 3
SPEED_WINDOW_SECONDS = 5
ACTIVE_STATES = ("queued", "downloading")
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(sock_connect=20, sock_read=120)


class TransferError(Exception):
    pass


def clean_name(name, label="file name"):
    name = (name or "").strip()
    if not name or name in (".", "..") or any(char in name for char in "/\\\0") or name.endswith(PART_SUFFIX):
        raise TransferError(f"Invalid {label}: {name!r}")
    return name


def check_space(folder, size):
    while not os.path.isdir(folder) and folder != os.path.dirname(folder):
        folder = os.path.dirname(folder)
    if size and size > shutil.disk_usage(folder or ROMS_DIR).free:
        raise TransferError(f"Not enough free space for {human_size(size)}")


async def save_stream(chunks, path, progress=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part_path = path + PART_SUFFIX
    try:
        with open(part_path, "wb") as part_file:
            async for chunk in chunks:
                part_file.write(chunk)
                if progress:
                    progress(len(chunk))
        os.replace(part_path, path)
    except BaseException:
        try:
            os.remove(part_path)
        except OSError:
            pass
        raise


def no_warning(name):
    return None


@dataclass
class Destination:
    area: str
    folder: str
    label: str
    subfolder: str = ""
    reload_es: bool = False
    warning: Callable = no_warning

    def path_for(self, name, overwrite):
        path = os.path.join(self.folder, name)
        if os.path.isdir(path):
            raise TransferError(f"{name} is a folder")
        if os.path.lexists(path) and not overwrite:
            raise TransferError(f"{name} already exists")
        return path

    def problems(self, names, overwrite):
        problems, seen = {}, set()
        for name in names:
            try:
                self.path_for(clean_name(name), overwrite)
                if name in seen:
                    raise TransferError(f"{name} is listed twice")
            except TransferError as error:
                problems[name] = str(error)
            seen.add(name)
        return problems


def clean_urls(text):
    urls = [line.strip() for line in text.splitlines() if line.strip()]
    if not urls:
        raise TransferError("Add at least one link")
    for url in urls:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise TransferError(f"Not an http(s) link: {url}")
    return urls


def name_from_response(response, url):
    disposition = response.content_disposition
    if disposition and disposition.filename:
        return disposition.filename
    return response.url.name or urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])


def describe_error(error):
    if isinstance(error, aiohttp.ClientResponseError):
        return f"HTTP {error.status} {error.message}"
    if isinstance(error, TimeoutError):
        return "Timed out"
    return str(error) or type(error).__name__


@dataclass
class Job:
    id: int
    url: str
    destination: Destination
    overwrite: bool
    name: str | None
    status: str = "queued"
    received: int = 0
    total: int | None = None
    error: str | None = None
    warning: str | None = None
    task: asyncio.Task | None = None
    samples: collections.deque = field(default_factory=collections.deque)

    def add_progress(self, size):
        self.received += size
        now = time.monotonic()
        self.samples.append((now, self.received))
        while now - self.samples[0][0] > SPEED_WINDOW_SECONDS:
            self.samples.popleft()

    def speed(self):
        if self.status != "downloading" or not self.samples:
            return None
        first_time, first_received = self.samples[0]
        elapsed = time.monotonic() - first_time
        return (self.received - first_received) / elapsed if elapsed >= 1 else None

    def as_dict(self):
        speed = self.speed()
        eta = (self.total - self.received) / speed if speed and self.total and self.total > self.received else None
        return {"id": self.id, "url": self.url, "name": self.name, "destination": self.destination.label,
                "subfolder": self.destination.subfolder, "status": self.status, "received": self.received,
                "total": self.total, "speed": speed, "eta": eta, "error": self.error, "warning": self.warning}


class DownloadQueue:
    def __init__(self):
        self.jobs = {}
        self.pending = asyncio.Queue()
        self.next_id = itertools.count(1)
        self.saved_since_reload = False
        self.writing = set()

    def add(self, urls_text, destination, overwrite, name=""):
        urls = clean_urls(urls_text)
        if name.strip():
            if len(urls) > 1:
                raise TransferError("A file name can only be set for a single link")
            name = clean_name(name)
        jobs = [Job(next(self.next_id), url, destination, overwrite, name.strip() or None) for url in urls]
        for job in jobs:
            self.jobs[job.id] = job
            self.pending.put_nowait(job)
        self.prune()
        return [job.as_dict() for job in jobs]

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if not job or job.status not in ACTIVE_STATES:
            raise TransferError("That download isn't running")
        if job.task:
            job.task.cancel()
        else:
            job.status = "cancelled"

    def listing(self, area):
        return [job.as_dict() for job in sorted(self.jobs.values(), key=lambda job: job.id, reverse=True)
                if job.destination.area == area]

    def prune(self):
        finished = [job_id for job_id, job in sorted(self.jobs.items()) if job.status not in ACTIVE_STATES]
        for job_id in finished[:max(0, len(self.jobs) - MAX_KEPT_JOBS)]:
            del self.jobs[job_id]

    def active(self):
        return any(job.status in ACTIVE_STATES for job in self.jobs.values())

    async def run(self, after_downloads):
        async with aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT) as session, asyncio.TaskGroup() as workers:
            for _ in range(PARALLEL_DOWNLOADS):
                workers.create_task(self.work(session, after_downloads))

    async def work(self, session, after_downloads):
        while True:
            job = await self.pending.get()
            if job.status == "queued":
                await self.run_job(job, session)
            if self.pending.empty() and not self.active() and self.saved_since_reload:
                self.saved_since_reload = False
                try:
                    await after_downloads()
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
            async with session.get(job.url) as response:
                response.raise_for_status()
                job.name = clean_name(job.name or name_from_response(response, job.url))
                job.total = response.content_length
                check_space(job.destination.folder, job.total)
                path = os.path.realpath(job.destination.path_for(job.name, job.overwrite))
                if path in self.writing:
                    raise TransferError(f"{job.name} is already being downloaded")
                self.writing.add(path)
                try:
                    await save_stream(response.content.iter_chunked(CHUNK_SIZE), path, job.add_progress)
                finally:
                    self.writing.discard(path)
        except asyncio.CancelledError:
            job.status = "cancelled"
            return
        except TransferError as error:
            job.status, job.error = "failed", str(error)
            return
        except (aiohttp.ClientError, TimeoutError, OSError) as error:
            job.status, job.error = "failed", describe_error(error)
            return
        job.status = "done"
        job.warning = await asyncio.to_thread(job.destination.warning, job.name)
        self.saved_since_reload = self.saved_since_reload or job.destination.reload_es
