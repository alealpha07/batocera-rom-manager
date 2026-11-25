# batocera-rom-manager

A web ROM manager for [Batocera](https://batocera.org/), reachable from any device on the home network.

It runs on the Batocera machine as a service and reads the game list from EmulationStation, so it always shows what the TV shows.

## Features

- Games table with name, system, file and size, sortable by any column.
- Search by title or file name; option to show games hidden in EmulationStation.
- Export the games list as shown (system, search and filters) to CSV or TXT.
- Game page with box art, screenshots, marquee and video, description, metadata, play stats and every file the game uses (tracks of `.cue`/`.gdi`, discs of `.m3u`).
- Sidebar with every system; systems without games can be shown or hidden.
- Add ROMs by uploading them from the browser (several files at once, with a progress bar) or by giving download links (one per line, downloaded one at a time in the background).
- Edit a game's info (name, description, genre, developer, publisher, release date, players, rating, region, favorite, hidden), replace or remove its scraped media, or delete it with its files and media.
- BIOS page: what Batocera's own BIOS check reports as missing or wrong, with a way to upload or download each file to the exact path it needs, plus a browser of the BIOS folder.
- Start a game on the TV from its page, and see or quit the running game from the top bar.
- Boot check: starts a game, a system or the whole library on the TV one game at a time and marks each game green or red, depending on whether it keeps running without emulator errors.

## Requirements

- Batocera with user services support (`batocera-services`). Tested on Batocera 44.

## Install

Copy the repository to the Batocera machine and run the installer:

```sh
bash install.sh
```

The installer copies:

- `rom_manager/` to `/userdata/system/rom-manager/`
- `services/rom_manager` to `/userdata/system/services/`

It then enables and starts the service. It is safe to run it again to update.

## Usage

Open `http://<batocera-ip>:18765` in a browser. On the first visit, choose a password: every device will ask for it, and a login lasts 30 days.

The game list is loaded once and kept in memory. Press **Refresh** after adding or scraping games in EmulationStation.

Forgot the password? Delete `/userdata/system/configs/rom-manager/config.json` and restart the service, then choose a new one:

```sh
batocera-services restart rom_manager
```

It is made for the home network and uses plain HTTP. To reach it from outside, use a VPN instead of opening the port on your router.

## Enable / disable

From EmulationStation: **Main menu → System settings → Services → `rom_manager`**.

The log is in `/userdata/system/logs/rom-manager.log`.

## Uninstall

```sh
bash uninstall.sh
```

This stops and disables the service and removes its files, password and log.

## License

[MIT](LICENSE). Bundles [Jinja2](https://jinja.palletsprojects.com/) and [MarkupSafe](https://markupsafe.palletsprojects.com/) (BSD-3-Clause) and [Bootstrap](https://getbootstrap.com/) (MIT); their licenses are next to their files.
