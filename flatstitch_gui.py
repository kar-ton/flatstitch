#!/usr/bin/env python3
"""Desktop GUI for flatstitch, built on Tkinter (ships with most Python
installs; no heavy toolkit like Qt required).

Drag-and-drop works if the optional `tkinterdnd2` package is installed;
otherwise the app falls back cleanly to the "Add files..." button.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import traceback
from pathlib import Path

import cv2
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

from flatstitch import i18n, io_utils, pipeline
from flatstitch.i18n import _

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
except ImportError:
    _HAS_DND = False

IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
THUMB_SIZE = (128, 96)
RESULT_THUMB_SIZE = (460, 320)
CONFIG_PATH = Path.home() / ".config" / "flatstitch" / "config.json"

logger = logging.getLogger("flatstitch")


# ---------------------------------------------------------------------------
# Persisted preferences (currently just the chosen UI language)
# ---------------------------------------------------------------------------

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class QueueHandler(logging.Handler):
    """Sends formatted log records into a thread-safe queue instead of
    touching any widget directly - the pipeline runs on a background
    thread, and Tkinter widgets may only be touched from the main thread.
    """
    def __init__(self, q: "queue.Queue"):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        self.q.put(("log", self.format(record), record.levelno))


def _to_display_rgb8(img: np.ndarray) -> np.ndarray:
    """Normalize any array flatstitch can load (8/16-bit, gray/RGB/RGBA)
    down to an 8-bit RGB array suitable for on-screen preview."""
    if img.dtype == np.uint16:
        img8 = (img / 256).astype(np.uint8)
    elif img.dtype != np.uint8:
        img8 = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    else:
        img8 = img
    if img8.ndim == 2:
        img8 = cv2.cvtColor(img8, cv2.COLOR_GRAY2RGB)
    elif img8.shape[2] == 4:
        img8 = img8[..., :3]
    return img8


def make_thumbnail(path: str, max_size=THUMB_SIZE) -> ImageTk.PhotoImage:
    """Build a Tk-displayable thumbnail straight from flatstitch's own
    loader, so the preview always matches what the tool actually reads
    (same bit-depth/channel handling, no surprises)."""
    img = io_utils.load_image(path)
    pil_img = Image.fromarray(_to_display_rgb8(img))
    pil_img.thumbnail(max_size, Image.LANCZOS)
    return ImageTk.PhotoImage(pil_img)


def make_thumbnail_from_array(img: np.ndarray, max_size=RESULT_THUMB_SIZE) -> ImageTk.PhotoImage:
    pil_img = Image.fromarray(_to_display_rgb8(img))
    pil_img.thumbnail(max_size, Image.LANCZOS)
    return ImageTk.PhotoImage(pil_img)


class ScrollableFrame(ttk.Frame):
    """Tkinter has no built-in scrollable container; this is the standard
    canvas+frame+scrollbar pattern for one."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

    def _on_inner_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self._win, width=event.width)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class FlatstitchApp:
    def __init__(self, root):
        self.root = root
        self._last_written = []
        self._build_everything(initial=True)

        if _HAS_DND:
            self.drop_zone.drop_target_register(DND_FILES)
            self.drop_zone.dnd_bind("<<Drop>>", self._on_drop)

        self.root.after(100, self._poll_queue)

    # -- Full (re)build, used at startup and whenever the language changes --

    def _build_everything(self, initial: bool):
        self.root.title(_("gui.window_title"))
        self.root.geometry("1060x820")
        self.root.minsize(820, 600)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Accent.TButton", font=("", 10, "bold"))
        style.configure("Item.TFrame", background="#ffffff")

        self.items: dict[str, dict] = {}   # abs_path -> {"frame":...}
        self._thumb_refs = []              # keep PhotoImage refs alive
        self.result_photo = None
        self.msg_queue: "queue.Queue" = queue.Queue()
        self.worker_thread: threading.Thread | None = None

        self._build_toolbar()
        self._build_body()
        self._build_settings()
        self._build_output_row()
        self._build_action_row()
        self._build_notebook()

        logging.getLogger("flatstitch").setLevel(logging.INFO)
        logging.getLogger("flatstitch").addHandler(QueueHandler(self.msg_queue))

    def _rebuild_ui(self, new_lang: str):
        """Live language switch: remember what's on screen, tear the whole
        UI down, rebuild it with the new language's strings, then restore
        the file list / output path / settings values."""
        saved_paths = list(self.items.keys())
        saved_output = self.output_var.get()
        saved = dict(
            detector=self.detector_var.get(),
            background=self.background_var.get(),
            bundle=self.bundle_var.get(),
            adv_visible=self.adv_visible.get(),
            downscale=self.downscale_var.get(),
            min_inliers=self.min_inliers_var.get(),
            sharpen=self.sharpen_var.get(),
            compression=self.compression_var.get(),
            interpolation=self.interpolation_var.get(),
            jobs=self.jobs_var.get(),
        )

        i18n.set_language(new_lang)
        save_config({**load_config(), "lang": new_lang})

        for w in self.root.winfo_children():
            w.destroy()

        self._build_everything(initial=False)

        self.detector_var.set(saved["detector"])
        self.background_var.set(saved["background"])
        self.bundle_var.set(saved["bundle"])
        self.downscale_var.set(saved["downscale"])
        self.min_inliers_var.set(saved["min_inliers"])
        self.sharpen_var.set(saved["sharpen"])
        self.compression_var.set(saved["compression"])
        self.interpolation_var.set(saved["interpolation"])
        self.jobs_var.set(saved["jobs"])
        if saved["adv_visible"]:
            self.adv_visible.set(True)
            self._toggle_advanced()

        if saved_paths:
            self.add_paths(saved_paths)
        self.output_var.set(saved_output)

        if _HAS_DND:
            self.drop_zone.drop_target_register(DND_FILES)
            self.drop_zone.dnd_bind("<<Drop>>", self._on_drop)

    # -- UI construction ----------------------------------------------------

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text=_("gui.toolbar.add_files"), command=self.add_files).pack(side="left")
        ttk.Button(bar, text=_("gui.toolbar.add_folder"), command=self.add_folder).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text=_("gui.toolbar.clear_list"), command=self.clear_list).pack(side="left", padx=(6, 0))

        lang_codes = sorted(i18n.SUPPORTED_LANGUAGES, key=lambda c: i18n.SUPPORTED_LANGUAGES[c][1])
        lang_display = [i18n.SUPPORTED_LANGUAGES[c][1] for c in lang_codes]
        self._lang_code_by_display = dict(zip(lang_display, lang_codes))
        lang_box = ttk.Combobox(bar, values=lang_display, width=14, state="readonly")
        lang_box.set(i18n.SUPPORTED_LANGUAGES[i18n.current_language()][1])
        lang_box.pack(side="right")
        lang_box.bind("<<ComboboxSelected>>",
                       lambda e: self._rebuild_ui(self._lang_code_by_display[lang_box.get()]))
        ttk.Label(bar, text=_("gui.settings.language_label")).pack(side="right", padx=(0, 6))

        self.count_label = ttk.Label(bar, text=_("gui.count.none"))
        self.count_label.pack(side="right", padx=(0, 18))

    def _build_body(self):
        dnd_hint = "" if _HAS_DND else _("gui.body.dnd_disabled_hint")
        outer = ttk.LabelFrame(self.root, text=f"{_('gui.body.scans_label')}{dnd_hint}", padding=6)
        outer.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.scroll_area = ScrollableFrame(outer)
        self.scroll_area.pack(fill="both", expand=True)
        self.list_inner = self.scroll_area.inner

        # The scrollable area itself doubles as the drop zone.
        self.drop_zone = self.scroll_area.canvas
        hint_text = _("gui.body.drop_hint_with_dnd") if _HAS_DND else _("gui.body.drop_hint_no_dnd")
        self.empty_label = ttk.Label(
            self.list_inner, text=hint_text, justify="center", foreground="#888888",
        )
        self.empty_label.pack(pady=40)

    def _build_settings(self):
        frame = ttk.Frame(self.root, padding=(10, 4))
        frame.pack(fill="x")

        self.detector_var = tk.StringVar(value="sift")
        self.background_var = tk.StringVar(value="white")
        self.bundle_var = tk.BooleanVar(value=True)
        self.language_var = tk.StringVar(value=i18n.current_language())

        ttk.Label(frame, text=_("gui.settings.detector_label")).grid(row=0, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=self.detector_var, values=["sift", "orb"],
                     width=6, state="readonly").grid(row=0, column=1, padx=(4, 16))

        ttk.Label(frame, text=_("gui.settings.background_label")).grid(row=0, column=2, sticky="w")
        ttk.Combobox(frame, textvariable=self.background_var, values=["white", "black"],
                     width=6, state="readonly").grid(row=0, column=3, padx=(4, 16))

        ttk.Checkbutton(frame, text=_("gui.settings.bundle_checkbox"),
                         variable=self.bundle_var).grid(row=0, column=4, padx=(0, 16))

        self.adv_visible = tk.BooleanVar(value=False)
        self.adv_toggle = ttk.Checkbutton(
            frame, text=_("gui.settings.advanced_toggle"), variable=self.adv_visible,
            command=self._toggle_advanced, style="Toolbutton",
        )
        self.adv_toggle.grid(row=0, column=5, padx=(0, 16))

        self.adv_frame = ttk.Frame(self.root, padding=(10, 0, 10, 6))

        self.downscale_var = tk.DoubleVar(value=1.0)
        self.min_inliers_var = tk.IntVar(value=12)
        self.sharpen_var = tk.DoubleVar(value=6.0)
        self.compression_var = tk.StringVar(value="lzw")
        self.interpolation_var = tk.StringVar(value="cubic")
        self.jobs_var = tk.IntVar(value=os.cpu_count() or 1)

        ttk.Label(self.adv_frame, text=_("gui.advanced.downscale_label")).grid(row=0, column=0, sticky="w")
        ttk.Spinbox(self.adv_frame, textvariable=self.downscale_var, from_=1.0, to=16.0,
                    increment=0.5, width=6).grid(row=0, column=1, padx=(4, 16))

        ttk.Label(self.adv_frame, text=_("gui.advanced.min_inliers_label")).grid(row=0, column=2, sticky="w")
        ttk.Spinbox(self.adv_frame, textvariable=self.min_inliers_var, from_=4, to=200,
                    increment=1, width=6).grid(row=0, column=3, padx=(4, 16))

        ttk.Label(self.adv_frame, text=_("gui.advanced.sharpen_label")).grid(row=0, column=4, sticky="w")
        ttk.Spinbox(self.adv_frame, textvariable=self.sharpen_var, from_=1.0, to=20.0,
                    increment=1.0, width=6).grid(row=0, column=5, padx=(4, 16))

        ttk.Label(self.adv_frame, text=_("gui.advanced.compression_label")).grid(row=0, column=6, sticky="w")
        ttk.Combobox(self.adv_frame, textvariable=self.compression_var,
                     values=["lzw", "zlib", "none"], width=6, state="readonly").grid(row=0, column=7, padx=(4, 0))

        ttk.Label(self.adv_frame, text=_("gui.advanced.interpolation_label")).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Combobox(self.adv_frame, textvariable=self.interpolation_var,
                     values=["cubic", "linear", "lanczos4", "nearest"], width=8,
                     state="readonly").grid(row=1, column=1, padx=(4, 16), pady=(4, 0))

        ttk.Label(self.adv_frame, text=_("gui.advanced.jobs_label")).grid(row=1, column=2, sticky="w", pady=(4, 0))
        ttk.Spinbox(self.adv_frame, textvariable=self.jobs_var, from_=1, to=64,
                    increment=1, width=6).grid(row=1, column=3, padx=(4, 16), pady=(4, 0))

    def _toggle_advanced(self):
        if self.adv_visible.get():
            self.adv_frame.pack(fill="x", before=self.output_row)
        else:
            self.adv_frame.pack_forget()

    def _build_output_row(self):
        self.output_row = ttk.Frame(self.root, padding=(10, 4))
        self.output_row.pack(fill="x")
        ttk.Label(self.output_row, text=_("gui.output.label")).pack(side="left")
        self.output_var = tk.StringVar(value="")
        ttk.Entry(self.output_row, textvariable=self.output_var).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(self.output_row, text=_("gui.output.browse_button"), command=self.browse_output).pack(side="left")

    def _build_action_row(self):
        row = ttk.Frame(self.root, padding=(10, 4))
        row.pack(fill="x")
        self.stitch_btn = ttk.Button(row, text=_("gui.action.stitch_button"),
                                      style="Accent.TButton", command=self.start_stitch)
        self.stitch_btn.pack(side="left")
        self.progress = ttk.Progressbar(row, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=10)
        self.status_label = ttk.Label(row, text=_("gui.action.status_ready"))
        self.status_label.pack(side="left")

    def _build_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        log_tab = ttk.Frame(self.notebook)
        self.log_text = tk.Text(log_tab, height=10, state="disabled", wrap="word",
                                 background="#111318", foreground="#d6d6d6")
        log_scroll = ttk.Scrollbar(log_tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        self.notebook.add(log_tab, text=_("gui.notebook.log_tab"))

        result_tab = ttk.Frame(self.notebook)
        result_scroll = ScrollableFrame(result_tab)
        result_scroll.pack(fill="both", expand=True)
        result_inner = result_scroll.inner

        self.result_paths_label = ttk.Label(result_inner, text="", justify="left")
        self.result_paths_label.pack(pady=(10, 4), anchor="w", padx=10)
        self.open_folder_btn = ttk.Button(result_inner, text=_("gui.result.open_folder_button"),
                                           command=self._open_output_folder, state="disabled")
        self.open_folder_btn.pack(anchor="w", padx=10, pady=(0, 10))
        self.result_label = ttk.Label(result_inner, text=_("gui.result.placeholder"),
                                       foreground="#888888")
        self.result_label.pack(pady=10)
        self.notebook.add(result_tab, text=_("gui.notebook.result_tab"))

    # -- File list management ------------------------------------------------

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title=_("gui.dialog.add_files_title"),
            filetypes=[(_("gui.dialog.filetype_images"), "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"),
                       (_("gui.dialog.filetype_all"), "*.*")],
        )
        self.add_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title=_("gui.dialog.add_folder_title"))
        if not folder:
            return
        paths = sorted(
            str(p) for p in Path(folder).iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        )
        self.add_paths(paths)

    def _on_drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        expanded = []
        for p in paths:
            pth = Path(p)
            if pth.is_dir():
                expanded.extend(
                    str(f) for f in sorted(pth.iterdir())
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTS
                )
            else:
                expanded.append(str(pth))
        self.add_paths(expanded)

    def add_paths(self, paths):
        added = 0
        for p in paths:
            path = str(Path(p).resolve())
            if path in self.items:
                continue
            if Path(path).suffix.lower() not in IMAGE_EXTS:
                continue
            try:
                photo = make_thumbnail(path)
            except Exception as exc:
                messagebox.showwarning(
                    _("gui.dialog.read_error_title"),
                    f"{Path(path).name}: {exc}",
                )
                continue
            self._add_row(path, photo)
            added += 1
        if added:
            self.empty_label.pack_forget()
            self._refresh_count()
            if not self.output_var.get():
                self.output_var.set(str(pipeline.default_output_path(list(self.items.keys()))))

    def _add_row(self, path, photo):
        self._thumb_refs.append(photo)
        row = ttk.Frame(self.list_inner, style="Item.TFrame", padding=6)
        row.pack(fill="x", pady=2, padx=2)
        thumb_lbl = ttk.Label(row, image=photo)
        thumb_lbl.pack(side="left")
        name_lbl = ttk.Label(row, text=Path(path).name)
        name_lbl.pack(side="left", padx=10)
        remove_btn = ttk.Button(row, text="✕", width=3,
                                 command=lambda: self.remove_item(path))
        remove_btn.pack(side="right")
        self.items[path] = {"frame": row}

    def remove_item(self, path):
        entry = self.items.pop(path, None)
        if entry:
            entry["frame"].destroy()
        self._refresh_count()
        if not self.items:
            self.empty_label.pack(pady=40)

    def clear_list(self):
        for entry in self.items.values():
            entry["frame"].destroy()
        self.items.clear()
        self._thumb_refs.clear()
        self._refresh_count()
        self.empty_label.pack(pady=40)

    def _refresh_count(self):
        n = len(self.items)
        self.count_label.config(text=_("gui.count.none") if n == 0 else _("gui.count.some", n=n))

    # -- Output path ----------------------------------------------------------

    def browse_output(self):
        path = filedialog.asksaveasfilename(
            title=_("gui.dialog.save_as_title"),
            defaultextension=".tiff",
            filetypes=[("TIFF", "*.tiff *.tif")],
            initialfile=Path(self.output_var.get() or "stitched.tiff").name,
        )
        if path:
            self.output_var.set(path)

    def _open_output_folder(self):
        if self._last_written:
            folder = str(Path(self._last_written[0]).parent)
            try:
                import subprocess
                subprocess.Popen(["xdg-open", folder])
            except Exception:
                messagebox.showinfo(_("gui.dialog.result_folder_title"), folder)

    # -- Stitch workflow --------------------------------------------------------

    def start_stitch(self):
        paths = list(self.items.keys())
        if len(paths) < 2:
            messagebox.showwarning(_("gui.dialog.too_few_images_title"), _("gui.dialog.too_few_images_body"))
            return
        output = self.output_var.get().strip()
        if not output:
            messagebox.showwarning(_("gui.dialog.no_output_title"), _("gui.dialog.no_output_body"))
            return

        settings = dict(
            input_paths=paths,
            output_path=output,
            detector=self.detector_var.get(),
            downscale=self.downscale_var.get(),
            min_inliers=self.min_inliers_var.get(),
            bundle_adjustment=self.bundle_var.get(),
            sharpen_power=self.sharpen_var.get(),
            interpolation=self.interpolation_var.get(),
            compression=self.compression_var.get(),
            background=self.background_var.get(),
            n_jobs=self.jobs_var.get(),
        )

        self.stitch_btn.config(state="disabled")
        self.progress.start(12)
        self.status_label.config(text=_("gui.action.status_processing"))
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")
        self.notebook.select(0)
        self._last_written = []

        self.worker_thread = threading.Thread(target=self._worker, args=(settings,), daemon=True)
        self.worker_thread.start()

    def _worker(self, settings):
        try:
            written = pipeline.stitch(**settings)
            self.msg_queue.put(("done", written, None))
        except pipeline.StitchError as exc:
            self.msg_queue.put(("error", str(exc), None))
        except Exception:
            self.msg_queue.put(("error", traceback.format_exc(), None))

    def _poll_queue(self):
        try:
            while True:
                kind, payload, _levelno = self.msg_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "done":
                    self._on_stitch_done(payload)
                elif kind == "error":
                    self._on_stitch_error(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _append_log(self, line):
        self.log_text.config(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _on_stitch_done(self, written):
        self.progress.stop()
        self.stitch_btn.config(state="normal")
        self.status_label.config(text=_("gui.action.status_done"))
        self._last_written = written
        self.open_folder_btn.config(state="normal")

        names = "\n".join(written)
        self.result_paths_label.config(text=_("gui.result.saved_prefix", names=names))
        try:
            arr = io_utils.load_image(written[0])
            photo = make_thumbnail_from_array(arr)
            self.result_photo = photo  # keep a reference
            self.result_label.config(image=photo, text="")
        except Exception:
            self.result_label.config(text=_("gui.result.preview_failed"))
        self.notebook.select(1)

    def _on_stitch_error(self, message):
        self.progress.stop()
        self.stitch_btn.config(state="normal")
        self.status_label.config(text=_("gui.action.status_error"))
        self._append_log(message)
        messagebox.showerror(_("gui.dialog.stitch_failed_title"), message.strip().splitlines()[-1])


def main():
    cfg = load_config()
    i18n.set_language(cfg.get("lang"))
    root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
    FlatstitchApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
