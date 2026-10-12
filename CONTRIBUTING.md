# Contributing

Bug reports, fixes, and new scan-stitching edge cases are all welcome via
issues/PRs.

## Adding a translation

See the "Adding a language" section in [README.md](README.md) — in short:
copy `flatstitch/locales/en.json` to a new `<code>.json`, translate the
values (not the keys, and not the `{placeholders}`), and register the
language in `flatstitch/i18n.py`.

Translations are shared by all three interfaces. A quick way to sanity-check a translation file has no missing/extra keys:

```bash
python3 -c "
import json
en = json.load(open('flatstitch/locales/en.json'))
x = json.load(open('flatstitch/locales/<code>.json'))
print('missing:', set(en) - set(x))
print('extra:', set(x) - set(en))
"
```

Both sets should print empty.

## Code layout

- `flatstitch/` — the core library (feature matching, registration,
  compositing, TIFF I/O) plus the CLI (`cli.py`).
- `flatstitch_launcher.py` — picks the GUI for the running desktop
  (`--detect` shows the choice, `--gui gtk|qt|tk` overrides it).
- `flatstitch_gtk.py` / `flatstitch_qt.py` — the native GTK3 and Qt5
  interfaces. They run under the system Python and drive the stitching
  through `flatstitch_native_common.py` (subprocess bridge to the venv).
- `flatstitch_gui.py` — the Tkinter GUI (fallback for other desktops).
- `build-deb.sh` — syncs the sources into `deb/` and builds the `.deb`.
- `flatstitch/locales/` — translation catalogs (see above).
- `deb/` — packaging scaffold for the `.deb` (see the README's
  "Building the .deb from source" section).
- `icon/` — source artwork for the application icon (PNG + SVG).
