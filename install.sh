#!/bin/bash
set -e

SERVICE_NAME="rom_manager"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="/userdata/system/rom-manager"
SERVICES_DIR="/userdata/system/services"

batocera-services stop "$SERVICE_NAME" 2>/dev/null || true

mkdir -p "$INSTALL_DIR" "$SERVICES_DIR"
rm -rf "$INSTALL_DIR/rom_manager"
cp -r "$SOURCE_DIR/rom_manager" "$INSTALL_DIR/rom_manager"
cp "$SOURCE_DIR/services/$SERVICE_NAME" "$SERVICES_DIR/$SERVICE_NAME"
chmod 755 "$SERVICES_DIR/$SERVICE_NAME"

batocera-services enable "$SERVICE_NAME"
batocera-services start "$SERVICE_NAME"
batocera-services status "$SERVICE_NAME"
