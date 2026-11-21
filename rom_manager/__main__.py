import logging

from aiohttp import web

from .app import create_app
from .config import HOST, PORT


def main():
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s | %(levelname)s | %(message)s")
    web.run_app(create_app(), host=HOST, port=PORT, access_log=None, print=None)


if __name__ == "__main__":
    main()
