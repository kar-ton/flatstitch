"""Shared helpers for the native (GTK/Qt) frontends.

This module runs under the SYSTEM Python (so the frontends can see the
system's GTK/Qt bindings), never the isolated venv - so it, and anything
it imports, must have zero third-party dependencies. All the actual
image processing (and the flatstitch.i18n translation catalogs, which
have no third-party deps of their own and work fine here) is reached by
invoking the venv's python3 as a subprocess; this module builds that
command line and streams its output back a line at a time.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading

IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".config", "flatstitch", "config.json")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
_VENV_PYTHON = os.path.join(APP_DIR, "venv", "bin", "python3")


def venv_python() -> str:
    """Path to the isolated venv's python3 (has numpy/opencv/etc), or
    the current interpreter as a fallback when running from source
    outside of a .deb install (e.g. a developer checkout with
    everything installed into one environment already)."""
    if os.path.exists(_VENV_PYTHON):
        return _VENV_PYTHON
    return sys.executable


def build_stitch_args(input_paths: list[str], output_path: str, settings: dict, lang: str) -> list[str]:
    args = [
        venv_python(), "-m", "flatstitch.cli", *input_paths, "-o", output_path,
        "--detector", settings["detector"],
        "--background", settings["background"],
        "--interpolation", settings["interpolation"],
        "--compression", settings["compression"],
        "--downscale", str(settings["downscale"]),
        "--min-inliers", str(settings["min_inliers"]),
        "--sharpen-power", str(settings["sharpen_power"]),
        "--jobs", str(settings["jobs"]),
        "--lang", lang,
    ]
    if not settings.get("bundle_adjustment", True):
        args.append("--no-bundle-adjust")
    if not settings.get("auto_orient", True):
        args.append("--no-auto-orient")
    return args


def build_preview_args(src_path: str, dst_path: str, max_w: int = 480, max_h: int = 360) -> list[str]:
    return [venv_python(), "-m", "flatstitch.preview", src_path, dst_path, str(max_w), str(max_h)]


def _env_with_pythonpath() -> dict:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = APP_DIR + (os.pathsep + existing if existing else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


class SubprocessRunner:
    """Runs a command, streaming stdout+stderr lines into a queue as
    they arrive, read from a background thread so the caller's own main
    (UI) thread never blocks. Typical use, from a GLib/Qt timer polling
    every ~100ms:

        runner = SubprocessRunner(args)
        runner.start()
        ...
        for line in runner.drain():
            if line is None:      # sentinel: process has exited
                handle_done(runner.returncode)
            else:
                append_to_log(line)
    """
    def __init__(self, args: list[str]):
        self.args = args
        self.q: "queue.Queue" = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.returncode: int | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.proc = subprocess.Popen(
            self.args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=_env_with_pythonpath(),
        )
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            self.q.put(line.rstrip("\n"))
        self.proc.wait()
        self.returncode = self.proc.returncode
        self.q.put(None)  # sentinel: done

    def drain(self) -> list:
        """Non-blocking: returns whatever lines have arrived since the
        last call (possibly none). A None entry marks process exit."""
        lines = []
        try:
            while True:
                lines.append(self.q.get_nowait())
        except queue.Empty:
            pass
        return lines


_WRITTEN_MARKER = "__FLATSTITCH_WRITTEN__:"


def parse_written_paths(log_lines) -> list[str]:
    """Output files reported by the stitch process (see pipeline.stitch)."""
    return [ln[len(_WRITTEN_MARKER):] for ln in log_lines
            if ln and ln.startswith(_WRITTEN_MARKER)]


def run_preview_sync(src_path: str, dst_path: str, max_w: int = 480, max_h: int = 360,
                     timeout: float = 30.0) -> tuple[bool, str]:
    """Blocking preview render - fine to call from a background thread
    (never from a UI thread) since it's a small, fast, one-off image
    resize, not the potentially-long stitch job itself.

    Returns (ok, detail); detail is the error text when ok is False."""
    try:
        if os.path.exists(dst_path):
            os.remove(dst_path)
        result = subprocess.run(
            build_preview_args(src_path, dst_path, max_w, max_h),
            env=_env_with_pythonpath(), capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0 and os.path.exists(dst_path):
            return True, ""
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        return False, " | ".join(tail) or f"exit code {result.returncode}"
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError:
        pass


def default_output_path(paths: list[str]) -> str:
    """Same rule as flatstitch.pipeline.default_output_path: next to the
    inputs, stitched.tiff / stitched_2.tiff / ..."""
    folder = os.path.dirname(os.path.realpath(paths[0]))
    candidate = os.path.join(folder, "stitched.tiff")
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"stitched_{n}.tiff")
        n += 1
    return candidate
