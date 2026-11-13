#!/bin/bash
#
# TelaEdit installer (Linux)
#
#   ./install.sh              install TelaEdit for the current user
#   ./install.sh --uninstall  remove it again
#
# Nothing here needs administrator rights. Everything goes into your own
# home folder:
#   ~/.local/share/telaedit/                  the program and its icon
#   ~/.local/share/applications/telaedit.desktop   the menu launcher

set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
APPDIR="$HOME/.local/share/telaedit"
MENUDIR="$HOME/.local/share/applications"
LAUNCHER="$MENUDIR/telaedit.desktop"

refresh_menu() {
    # Tell the desktop to notice the new (or removed) launcher, if it can
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$MENUDIR" >/dev/null 2>&1 || true
    fi
}

if [ "$1" = "--uninstall" ]; then
    rm -rf "$APPDIR"
    rm -f "$LAUNCHER"
    refresh_menu
    echo "TelaEdit has been removed from the menu and from $APPDIR."
    echo "Your settings in ~/.config/TelaEdit were left alone."
    exit 0
fi

if [ ! -f "$HERE/telaedit" ]; then
    echo "ERROR: the 'telaedit' program was not found next to install.sh."
    echo "Run install.sh from inside the folder you extracted."
    exit 1
fi

if [ ! -f "$HERE/telaedit-icon.png" ]; then
    echo "ERROR: 'telaedit-icon.png' was not found next to install.sh."
    exit 1
fi

mkdir -p "$APPDIR" "$MENUDIR"

cp "$HERE/telaedit" "$APPDIR/telaedit"
cp "$HERE/telaedit-icon.png" "$APPDIR/telaedit-icon.png"
chmod +x "$APPDIR/telaedit"
chmod 644 "$APPDIR/telaedit-icon.png"

# Quotes around the program path are only needed if it contains a space
EXEC_PATH="$APPDIR/telaedit"
case "$EXEC_PATH" in
    *" "*) EXEC_PATH="\"$EXEC_PATH\"" ;;
esac

# Write the launcher with the correct paths for this computer.
#
# %F on the Exec line: the file manager adds the file(s) you chose with
# "Open With", and TelaEdit opens them.
# MimeType: the kinds of file TelaEdit is offered for in "Open With"
# (HTML, CSS, JavaScript, XML and plain text). It does not make TelaEdit
# the default program for them; you choose that in the file manager.
cat > "$LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Name=TelaEdit
Comment=HTML editor
Exec=$EXEC_PATH %F
Icon=$APPDIR/telaedit-icon.png
Terminal=false
Categories=Development;WebDevelopment;TextEditor;
MimeType=text/html;application/xhtml+xml;text/css;text/javascript;application/javascript;application/x-javascript;text/xml;application/xml;text/plain;
EOF

chmod 644 "$LAUNCHER"

# TelaEdit was called SiteForge before version 0.5. If the old version was
# installed, remove its program and menu entry so there are not two of them.
# The old settings in ~/.config/SiteForge are never touched: TelaEdit copies
# them the first time it starts.
OLD_APPDIR="$HOME/.local/share/siteforge"
OLD_LAUNCHER="$MENUDIR/siteforge.desktop"

if [ -d "$OLD_APPDIR" ] || [ -f "$OLD_LAUNCHER" ]; then
    rm -rf "$OLD_APPDIR"
    rm -f "$OLD_LAUNCHER"
    echo "The old SiteForge program and menu entry were removed."
    echo "Your settings in ~/.config/SiteForge were left alone and will be"
    echo "copied into TelaEdit the first time you start it."
    echo
fi

refresh_menu

echo "TelaEdit is installed."
echo
echo "Look for TelaEdit in your applications menu (under Programming or"
echo "Development). If it isn't there yet, log out and back in."
echo
echo "To open a file with TelaEdit from the file manager, right-click it and"
echo "choose Open With > TelaEdit. To make TelaEdit the usual program for"
echo "that kind of file, use Open With > Other Application and tick the"
echo "option to remember it (the wording varies between file managers)."
echo
echo "To remove it later, run:  ./install.sh --uninstall"
