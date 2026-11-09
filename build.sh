#!/bin/bash
set -e

# Stop early if the help file is missing, so it is not left out of the build
if [ ! -f "help/help.html" ]; then
    echo "ERROR: help/help.html not found. Put the help folder next to this script."
    exit 1
fi

echo "--- Cleaning previous build artifacts ---"
rm -rf build dist telaedit.spec

echo "--- Activating virtual environment & building with PyInstaller ---"
./venv/bin/python3 -m PyInstaller \
    --onefile \
    --noconsole \
    --name telaedit \
    --icon="icons/telaedit-icon.png" \
    --add-data "icons:icons" \
    --add-data "help:help" \
    telaedit.py

echo "--- Build complete! ---"
ls -lh dist/telaedit
