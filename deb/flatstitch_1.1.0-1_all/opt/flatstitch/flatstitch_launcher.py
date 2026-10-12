#!/usr/bin/env python3
"""flatstitch GUI launcher: picks the interface that fits the desktop.

  GNOME, Cinnamon, XFCE, MATE, LXDE, Budgie, Pantheon, Unity -> GTK3
  KDE Plasma, LXQt, Deepin                                   -> Qt5
  anything else (sway, i3, COSMIC, unknown ...)               -> Tk
  A missing GTK/Qt binding falls back to the other one, then to Tk.

Run it with the SYSTEM python3 (the .deb launcher does), because the GTK
and Qt bindings come from the distribution, not from the private venv.
The Tk fallback re-executes itself under the venv's interpreter.

Override the choice (first match wins):
  flatstitch-gui --gui gtk|qt|tk|auto
  FLATSTITCH_GUI=gtk|qt|tk|auto      (environment)
  "gui": "gtk"                       (in ~/.config/flatstitch/config.json)
  flatstitch-gui --detect            prints what would be used and exits
"""
from __future__ import annotations

import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

GTK_DESKTOPS = {
    "gnome", "gnome-classic", "gnome-flashback", "ubuntu", "unity", "x-cinnamon",
    "cinnamon", "xfce", "mate", "lxde", "budgie", "pantheon", "endeavouros",
}
QT_DESKTOPS = {"kde", "plasma", "lxqt", "deepin", "dde"}
CHOICES = ("auto", "gtk", "qt", "tk")


def desktop_tokens(env=None) -> list[str]:
    """Lower-cased desktop identifiers, most specific first.
    XDG_CURRENT_DESKTOP is a colon-separated list ("ubuntu:GNOME",
    "X-Cinnamon"); session variables are used as a fallback."""
    env = os.environ if env is None else env
    tokens: list[str] = []
    for var in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION"):
        for part in env.get(var, "").replace(";", ":").split(":"):
            part = part.strip().lower()
            if part and part not in tokens:
                tokens.append(part)
    return tokens


def preferred_toolkit(env=None) -> str:
    """'gtk', 'qt' or 'tk' for the running desktop (no availability check)."""
    tokens = desktop_tokens(env)
    # LXQt / KDE must win over a stray generic token, so check Qt first
    # only for the exact Qt desktops, then GTK ones.
    for t in tokens:
        if t in QT_DESKTOPS:
            return "qt"
    for t in tokens:
        if t in GTK_DESKTOPS:
            return "gtk"
    return "tk"


def _gtk_available() -> bool:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import Gtk  # noqa: F401
        return True
    except Exception:
        return False


def _qt_available() -> bool:
    try:
        from PyQt5 import QtWidgets  # noqa: F401
        return True
    except Exception:
        return False


def _requested(argv: list[str]) -> tuple[str, list[str]]:
    """Explicit user choice (CLI > env > config) and the remaining args."""
    rest, choice = [], None
    it = iter(argv)
    for a in it:
        if a == "--gui":
            choice = next(it, "auto")
        elif a.startswith("--gui="):
            choice = a.split("=", 1)[1]
        else:
            rest.append(a)
    if choice is None:
        choice = os.environ.get("FLATSTITCH_GUI")
    if choice is None:
        try:
            import flatstitch_native_common as nc
            choice = nc.load_config().get("gui")
        except Exception:
            choice = None
    choice = (choice or "auto").lower()
    return (choice if choice in CHOICES else "auto"), rest


# If the preferred toolkit is unusable, try the others in this order
# (the other native one before the generic Tk fallback).
_FALLBACKS = {"gtk": ("qt", "tk"), "qt": ("gtk", "tk"), "tk": ()}


def choose(requested: str = "auto", env=None) -> tuple[str, str]:
    """Return (toolkit, reason) after checking the bindings really import."""
    wanted = preferred_toolkit(env) if requested == "auto" else requested
    why = (f"desktop {'/'.join(desktop_tokens(env)) or 'unknown'}"
           if requested == "auto" else "chosen explicitly")
    available = {"gtk": _gtk_available, "qt": _qt_available, "tk": lambda: True}
    if available[wanted]():
        return wanted, why
    for alt in _FALLBACKS[wanted]:
        if available[alt]():
            return alt, f"{wanted} bindings not usable ({why}); falling back to {alt}"
    return "tk", f"{wanted} bindings not usable ({why}); falling back to tk"


def _run_tk(args: list[str]) -> None:
    import flatstitch_native_common as nc
    py = nc.venv_python()
    env = os.environ.copy()
    env["PYTHONPATH"] = APP_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    os.execve(py, [py, os.path.join(APP_DIR, "flatstitch_gui.py"), *args], env)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    requested, rest = _requested(argv)
    if "--detect" in rest:
        toolkit, why = choose(requested)
        print(f"{toolkit}  ({why})")
        return
    toolkit, why = choose(requested)
    if os.environ.get("FLATSTITCH_DEBUG"):
        print(f"flatstitch: using {toolkit} GUI - {why}", file=sys.stderr)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    if toolkit == "gtk":
        import flatstitch_gtk
        flatstitch_gtk.main()
    elif toolkit == "qt":
        import flatstitch_qt
        flatstitch_qt.main()
    else:
        _run_tk(rest)


if __name__ == "__main__":
    main()
