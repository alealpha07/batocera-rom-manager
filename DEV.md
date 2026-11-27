# Development

## Stack

- Python 3 and aiohttp, both already part of Batocera. Nothing is installed with pip.
- Jinja2 and MarkupSafe are vendored in `rom_manager/vendor/`, Bootstrap in `rom_manager/static/vendor/`.
- No database and no build step: data comes from the EmulationStation API and the files on `/userdata`, and is kept in memory.

## Layout

```
install.sh  uninstall.sh
services/rom_manager          Batocera user service (start, stop, status), started by batocera-services
docs/screenshots/             images used by the README
rom_manager/
  __main__.py                 entry point (python3 -m rom_manager)
  app.py                      builds the app: shared objects, background tasks, routes of every page
  app_keys.py                 typed keys for the shared objects (config, es, library, downloads, ...)
  config.py                   paths on Batocera and the config.json with the password hash
  core/                       what the app can do; no HTTP request handling
    emulationstation.py       ES API client, known systems, running game, quit and launch
    library.py                Game, Library: the in-memory game list and the edits on a game
    game_files.py             files of a game, gamelist.xml edits, media
    transfers.py              upload safety, destinations, download queue
    bios.py                   Batocera's BIOS check and the BIOS folder
    boot_check.py             BootCheck: launches games and reads the launch logs
    collections.py            ES collection settings, genres, manual and dynamic collections
    auth.py                   password, session cookie, login middleware
    batocera.py               version, uptime, disk usage, service restart
  pages/                      what the user sees; only request handling and rendering
    layout.py                 render(), redirect() with a flash message, sidebar, quit button
    layout.html               base page; layout_with_sidebar.html for the game pages
    layout.js                 theme, confirm prompts, sidebar scroll, auto submit
    components.html           macros: alerts, modal, checkbox, breadcrumb
    <page>/page.py            handlers of one page, each with its route above it
    <page>/*.html, *.js       its templates and scripts (scripts are inlined in the page)
    transfer/                 upload and download component shared by Add ROMs and BIOS
  static/                     app.css and Bootstrap
```

## What goes where

- Logic, file and EmulationStation work goes in `core/`, as a method of the class it belongs to or a function of that module. It raises its module's error (`TransferError`, `CollectionError`, ...) with a message meant for the user.
- A handler in `pages/` reads the request, calls `core/`, then renders a template or returns `redirect(url, kind, message)`. Blocking file work runs in `asyncio.to_thread`.
- A new route is a decorator above its handler: `@routes.get("/path")`. A new page module is added to `PAGES` in `app.py`.
- Templates extend `layout.html` or `layout_with_sidebar.html` and use the macros in `components.html`.
- Forms that need a confirmation get `data-confirm="Question?"`; inputs that submit their form on change get `data-autosubmit`. Page scripts are wrapped in a `{ }` block.

## Paths on Batocera

| What | Where |
|---|---|
| App | `/userdata/system/rom-manager/` |
| Service | `/userdata/system/services/rom_manager` |
| Password and settings | `/userdata/system/configs/rom-manager/config.json` |
| Boot check results | `/userdata/system/configs/rom-manager/health.json` |
| Log | `/userdata/system/logs/rom-manager.log` |
| EmulationStation API | `http://127.0.0.1:1234` |

## Working on it

The app needs the Batocera paths and the EmulationStation API, so it is tested on a Batocera machine. From a clone, copy it over and run the installer:

```sh
tar -czf - install.sh uninstall.sh services rom_manager | ssh root@batocera.local \
  'rm -rf /tmp/rom-manager && mkdir /tmp/rom-manager && tar -xzf - -C /tmp/rom-manager && bash /tmp/rom-manager/install.sh'
```

Errors and tracebacks go to the log. Restart after a manual change with `batocera-services restart rom_manager`.
