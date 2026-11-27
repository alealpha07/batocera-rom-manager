import datetime
import os
import shutil
import subprocess
import time

from ..config import APP_DIR, BATOCERA_VERSION_FILE, SERVICE_NAME, USERDATA_DIR

APP_STARTED = time.time()


def read_text(path):
    try:
        with open(path) as text_file:
            return text_file.read().strip()
    except OSError:
        return ""


def batocera_version():
    return read_text(BATOCERA_VERSION_FILE) or "unknown"


def uptime_seconds():
    try:
        return float(read_text("/proc/uptime").split()[0])
    except (ValueError, IndexError):
        return None


def duration(seconds):
    minutes = int(seconds // 60)
    days, hours = minutes // 1440, minutes // 60 % 24
    if days:
        return f"{days} d {hours} h"
    return f"{hours} h {minutes % 60} min" if hours else f"{minutes} min"


def local_time(timestamp):
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def system_info():
    uptime = uptime_seconds()
    return {"batocera_version": batocera_version(), "installed": local_time(os.path.getmtime(APP_DIR)),
            "uptime": duration(uptime) if uptime is not None else "unknown",
            "app_started": local_time(APP_STARTED), "app_uptime": duration(time.time() - APP_STARTED)}


def disk_usage():
    usage = shutil.disk_usage(USERDATA_DIR)
    return {"total": usage.total, "used": usage.used, "free": usage.free,
            "percent": round(usage.used * 100 / usage.total) if usage.total else 0}


def restart_service():
    subprocess.Popen(["/usr/bin/batocera-services", "restart", SERVICE_NAME], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
