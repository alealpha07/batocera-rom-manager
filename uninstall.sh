#!/bin/bash

SERVICE_NAME="rom_manager"
INSTALL_DIR="/userdata/system/rom-manager"

batocera-services stop "$SERVICE_NAME"
batocera-services disable "$SERVICE_NAME"
rm -f "/userdata/system/services/$SERVICE_NAME"
rm -rf "$INSTALL_DIR" "/userdata/system/configs/rom-manager"
rm -f "/userdata/system/logs/rom-manager.log"
echo "${SERVICE_NAME}: removed"
