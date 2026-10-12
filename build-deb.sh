#!/usr/bin/env bash
# Builds flatstitch_<version>_all.deb from this source tree.
# Copies the current sources into the package scaffold under deb/ first,
# so the package can never drift out of sync with the code.
#   ./build-deb.sh            -> ./flatstitch_<version>_all.deb
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PKG_DIR=$(ls -d deb/flatstitch_*_all | sort -V | tail -n1)
OPT="$PKG_DIR/opt/flatstitch"

rm -rf "$OPT"
mkdir -p "$OPT"
cp -r flatstitch "$OPT/flatstitch"
find "$OPT" -name '__pycache__' -type d -prune -exec rm -rf {} +
cp flatstitch_gui.py flatstitch_gtk.py flatstitch_qt.py \
   flatstitch_native_common.py flatstitch_launcher.py requirements.txt "$OPT/"
chmod 755 "$OPT/flatstitch_launcher.py"
find "$OPT" -type d -exec chmod 755 {} + ; find "$OPT" -type f ! -name flatstitch_launcher.py -exec chmod 644 {} +

DOC="$PKG_DIR/usr/share/doc/flatstitch"
cp README.md README.uk.md "$DOC/"

rm -f "$PKG_DIR/DEBIAN/md5sums"
( cd "$PKG_DIR" && find . -type f ! -path './DEBIAN/*' -print0 | sort -z | xargs -0 md5sum | sed 's# \./# #' > DEBIAN/md5sums )

OUT="$(basename "$PKG_DIR").deb"
fakeroot dpkg-deb --build --root-owner-group "$PKG_DIR" "$OUT"
echo "Built $OUT"
