import hashlib
import hmac
import os
import time

from aiohttp import web

COOKIE_NAME = "rom_manager_session"
SESSION_SECONDS = 30 * 24 * 3600
PBKDF2_ITERATIONS = 600_000
PUBLIC_PATHS = {"/setup", "/login"}


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (PBKDF2_ITERATIONS, salt.hex(), digest.hex())


def check_password(password, stored):
    _, iterations, salt, digest = stored.split("$")
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(candidate.hex(), digest)


def sign(value, secret):
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session_cookie(secret):
    expires = str(int(time.time()) + SESSION_SECONDS)
    return expires + "." + sign(expires, secret)


def valid_session_cookie(cookie, secret):
    expires, _, signature = (cookie or "").partition(".")
    return (expires.isdigit() and int(expires) > time.time()
            and hmac.compare_digest(signature, sign(expires, secret)))


def start_session(response, secret):
    response.set_cookie(COOKIE_NAME, make_session_cookie(secret), max_age=SESSION_SECONDS,
                        httponly=True, samesite="Strict")


def end_session(response):
    response.del_cookie(COOKIE_NAME)


def logged_in(request):
    secret = request.app["config"].get("secret")
    return bool(secret) and valid_session_cookie(request.cookies.get(COOKIE_NAME), secret)


@web.middleware
async def login_required(request, handler):
    if request.path in PUBLIC_PATHS or request.path.startswith("/static/"):
        return await handler(request)
    if not request.app["config"].get("password"):
        raise web.HTTPFound("/setup")
    if not logged_in(request):
        raise web.HTTPFound("/login")
    return await handler(request)
