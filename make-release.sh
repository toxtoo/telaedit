#!/bin/bash
#
# Packs the built TelaEdit into one download for GitHub Releases.
#
# Run this from your project folder (the one with the 'dist' folder in it),
# after ./build.sh has finished. Put install.sh in the same folder as this
# script.
#
#   ./make-release.sh          makes telaedit-linux-v0.5.tar.gz
#   ./make-release.sh 0.6      makes telaedit-linux-v0.6.tar.gz
#
# The result is in a new folder called 'release', so nothing in 'dist' is
# touched. Attach the .tar.gz file to your GitHub release.

set -e

VERSION="${1:-0.5}"
NAME="telaedit-linux-v$VERSION"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

PROGRAM="dist/telaedit"
ICON="dist/telaedit-icon.png"

if [ ! -f "$PROGRAM" ]; then
    echo "ERROR: $PROGRAM not found. Run ./build.sh first."
    exit 1
fi

# Use the icon from dist, or fall back to the one in the icons folder
if [ ! -f "$ICON" ]; then
    ICON="icons/telaedit-icon.png"
fi

if [ ! -f "$ICON" ]; then
    echo "ERROR: telaedit-icon.png not found in dist or icons."
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/install.sh" ]; then
    echo "ERROR: install.sh must be in the same folder as make-release.sh."
    exit 1
fi

rm -rf "release/$NAME" "release/$NAME.tar.gz"
mkdir -p "release/$NAME"

cp "$PROGRAM" "release/$NAME/telaedit"
cp "$ICON" "release/$NAME/telaedit-icon.png"
cp "$SCRIPT_DIR/install.sh" "release/$NAME/install.sh"
chmod +x "release/$NAME/telaedit" "release/$NAME/install.sh"

cat > "release/$NAME/README.txt" <<EOF
TelaEdit $VERSION (Linux, 64-bit)

To install:
  1. Extract this archive.
  2. Open a terminal in the extracted folder.
  3. Run:  ./install.sh

TelaEdit then appears in your applications menu. Press F1 inside the
program for the help.

To remove it:  ./install.sh --uninstall
EOF

tar -C release -czf "release/$NAME.tar.gz" "$NAME"

echo
echo "Release file created:"
ls -lh "release/$NAME.tar.gz"
