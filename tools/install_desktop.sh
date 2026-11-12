#!/bin/sh
# Add sfere to the app drawer: installs its icon and a desktop entry for this user.
#   tools/install_desktop.sh [checkout]    (default: the checkout this script is in)
# The checkout needs its .venv (see README). Run again after moving the checkout.
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
root=$(cd "${1:-$here}" && pwd)
python="$root/.venv/bin/python"
[ -x "$python" ] || { echo "no venv at $root/.venv; create it first (see README)" >&2; exit 1; }

data=${XDG_DATA_HOME:-$HOME/.local/share}
mkdir -p "$data/applications" "$data/icons/hicolor/scalable/apps"
cp "$here/share/sfere.svg" "$data/icons/hicolor/scalable/apps/sfere.svg"

cat > "$data/applications/sfere.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=sfere
Comment=Music-reactive visuals around a tracked juggling ball
Exec="$python" -m sfere
Path=$root
Icon=sfere
Terminal=false
Categories=AudioVideo;Video;
Keywords=visuals;vj;juggling;webcam;music;
StartupWMClass=sfere
EOF

update-desktop-database "$data/applications" 2>/dev/null || true
# -t: a user hicolor folder has no index.theme, and a stale cache hides new icons
gtk-update-icon-cache -q -f -t "$data/icons/hicolor" || echo "warning: icon cache not updated; the icon may not show" >&2
echo "installed $data/applications/sfere.desktop -> $root"
