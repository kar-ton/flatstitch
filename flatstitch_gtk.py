#!/usr/bin/env python3
"""Native GTK3 frontend for flatstitch - runs under the system Python
(so it can see the system's GTK bindings) and shells out to the
isolated venv for the actual stitching (see flatstitch_native_common).
Picked automatically on GNOME/Cinnamon/XFCE/MATE/LXDE by
flatstitch_launcher.py; can also be run directly.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GdkPixbuf, GLib, Gdk, Gio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flatstitch_native_common as nc
from flatstitch import i18n
from flatstitch.i18n import _

IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
THUMB_SIZE = (128, 96)
RESULT_MAX = (520, 380)
CONFIG_PATH = Path.home() / ".config" / "flatstitch" / "config.json"


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


class FlatstitchWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.items: dict[str, Gtk.ListBoxRow] = {}
        self.runner: nc.SubprocessRunner | None = None
        self.last_written: list[str] = []
        self._poll_source = None

        self.set_default_size(1080, 820)
        self._build_ui()
        self._setup_dnd()

    # -- UI construction -----------------------------------------------

    def _build_ui(self):
        self.set_title(_("gui.window_title"))
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                        margin=10)
        self.add(root)

        root.add(self._build_toolbar())
        root.add(self._build_language_row())
        root.pack_start(self._build_file_list(), True, True, 0)
        root.add(self._build_settings())
        self.adv_expander, _adv_box = self._build_advanced()
        root.add(self.adv_expander)
        root.add(self._build_output_row())
        root.add(self._build_action_row())
        root.pack_start(self._build_notebook(), True, True, 0)

        self.show_all()

    def _build_toolbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        add_files_btn = Gtk.Button(label=_("gui.toolbar.add_files"))
        add_files_btn.connect("clicked", self.on_add_files)
        bar.pack_start(add_files_btn, False, False, 0)

        add_folder_btn = Gtk.Button(label=_("gui.toolbar.add_folder"))
        add_folder_btn.connect("clicked", self.on_add_folder)
        bar.pack_start(add_folder_btn, False, False, 0)

        clear_btn = Gtk.Button(label=_("gui.toolbar.clear_list"))
        clear_btn.connect("clicked", self.on_clear_list)
        bar.pack_start(clear_btn, False, False, 0)

        self.count_label = Gtk.Label(label=_("gui.count.none"))
        bar.pack_end(self.count_label, False, False, 0)
        return bar

    def _build_language_row(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lang_label = Gtk.Label(label=_("gui.settings.language_label"))
        self.lang_codes = sorted(i18n.SUPPORTED_LANGUAGES,
                                  key=lambda c: i18n.SUPPORTED_LANGUAGES[c][1])
        self.lang_combo = Gtk.ComboBoxText()
        for code in self.lang_codes:
            self.lang_combo.append(code, i18n.SUPPORTED_LANGUAGES[code][1])
        self.lang_combo.set_active_id(i18n.current_language())
        self.lang_combo.connect("changed", self.on_language_changed)
        row.pack_end(self.lang_combo, False, False, 0)
        row.pack_end(lang_label, False, False, 0)
        return row

    def _build_file_list(self):
        frame = Gtk.Frame(label=_("gui.body.scans_label"))
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(150)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        self.empty_row = Gtk.ListBoxRow(selectable=False, activatable=False)
        self.empty_label = Gtk.Label(label=_("gui.body.drop_hint_with_dnd"),
                                      justify=Gtk.Justification.CENTER)
        self.empty_label.set_margin_top(40)
        self.empty_label.set_margin_bottom(40)
        self.empty_row.add(self.empty_label)
        self.listbox.add(self.empty_row)

        scroller.add(self.listbox)
        frame.add(scroller)
        return frame

    def _build_settings(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row1.pack_start(Gtk.Label(label=_("gui.settings.detector_label")), False, False, 0)
        self.detector_combo = Gtk.ComboBoxText()
        for v in ("sift", "orb"):
            self.detector_combo.append(v, v)
        self.detector_combo.set_active_id("sift")
        row1.pack_start(self.detector_combo, False, False, 0)

        row1.pack_start(Gtk.Label(label=_("gui.settings.background_label")), False, False, 0)
        self.background_combo = Gtk.ComboBoxText()
        self.background_combo.append("transparent", _("gui.background.transparent"))
        self.background_combo.append("white", "white")
        self.background_combo.append("black", "black")
        self.background_combo.set_active_id("transparent")
        row1.pack_start(self.background_combo, False, False, 0)
        outer.add(row1)

        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.bundle_check = Gtk.CheckButton(label=_("gui.settings.bundle_checkbox"))
        self.bundle_check.set_active(True)
        row2.pack_start(self.bundle_check, False, False, 0)

        self.auto_orient_check = Gtk.CheckButton(label=_("gui.settings.auto_orient_checkbox"))
        self.auto_orient_check.set_active(True)
        row2.pack_start(self.auto_orient_check, False, False, 0)

        outer.add(row2)
        return outer

    def _build_advanced(self):
        expander = Gtk.Expander(label=_("gui.settings.advanced_toggle"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        def labeled_spin(parent_row, label_key, lo, hi, step, digits, default):
            parent_row.pack_start(Gtk.Label(label=_(label_key)), False, False, 0)
            adj = Gtk.Adjustment(value=default, lower=lo, upper=hi,
                                  step_increment=step)
            spin = Gtk.SpinButton(adjustment=adj, digits=digits)
            parent_row.pack_start(spin, False, False, 0)
            return spin

        self.downscale_spin = labeled_spin(row1, "gui.advanced.downscale_label", 1.0, 16.0, 0.5, 1, 1.0)
        self.min_inliers_spin = labeled_spin(row1, "gui.advanced.min_inliers_label", 4, 200, 1, 0, 12)
        self.sharpen_spin = labeled_spin(row1, "gui.advanced.sharpen_label", 1.0, 20.0, 1.0, 1, 6.0)

        row2.pack_start(Gtk.Label(label=_("gui.advanced.compression_label")), False, False, 0)
        self.compression_combo = Gtk.ComboBoxText()
        for v in ("lzw", "zlib", "none"):
            self.compression_combo.append(v, v)
        self.compression_combo.set_active_id("lzw")
        row2.pack_start(self.compression_combo, False, False, 0)

        row2.pack_start(Gtk.Label(label=_("gui.advanced.interpolation_label")), False, False, 0)
        self.interp_combo = Gtk.ComboBoxText()
        for v in ("cubic", "linear", "lanczos4", "nearest"):
            self.interp_combo.append(v, v)
        self.interp_combo.set_active_id("cubic")
        row2.pack_start(self.interp_combo, False, False, 0)

        self.jobs_spin = labeled_spin(row2, "gui.advanced.jobs_label", 1, 64, 1, 0, os.cpu_count() or 1)

        box.add(row1)
        box.add(row2)
        expander.add(box)
        return expander, box

    def _build_output_row(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.pack_start(Gtk.Label(label=_("gui.output.label")), False, False, 0)
        self.output_entry = Gtk.Entry()
        row.pack_start(self.output_entry, True, True, 0)
        browse_btn = Gtk.Button(label=_("gui.output.browse_button"))
        browse_btn.connect("clicked", self.on_browse_output)
        row.pack_start(browse_btn, False, False, 0)
        return row

    def _build_action_row(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.stitch_btn = Gtk.Button(label=_("gui.action.stitch_button"))
        self.stitch_btn.get_style_context().add_class("suggested-action")
        self.stitch_btn.connect("clicked", self.on_stitch_clicked)
        row.pack_start(self.stitch_btn, False, False, 0)

        self.progress = Gtk.ProgressBar()
        row.pack_start(self.progress, True, True, 0)

        self.status_label = Gtk.Label(label=_("gui.action.status_ready"))
        row.pack_start(self.status_label, False, False, 0)
        return row

    def _build_notebook(self):
        self.notebook = Gtk.Notebook()

        log_scroller = Gtk.ScrolledWindow()
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_buffer = self.log_view.get_buffer()
        log_scroller.add(self.log_view)
        self.notebook.append_page(log_scroller, Gtk.Label(label=_("gui.notebook.log_tab")))

        result_scroller = Gtk.ScrolledWindow()
        result_scroller.set_min_content_height(300)
        log_scroller.set_min_content_height(300)
        result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)
        self.result_paths_label = Gtk.Label(label="", xalign=0)
        self.result_paths_label.set_line_wrap(True)
        result_box.pack_start(self.result_paths_label, False, False, 0)
        self.open_folder_btn = Gtk.Button(label=_("gui.result.open_folder_button"))
        self.open_folder_btn.set_halign(Gtk.Align.START)
        self.open_folder_btn.set_sensitive(False)
        self.open_folder_btn.connect("clicked", self.on_open_folder)
        result_box.pack_start(self.open_folder_btn, False, False, 0)
        self.result_image = Gtk.Image()
        self.result_placeholder = Gtk.Label(label=_("gui.result.placeholder"))
        result_box.pack_start(self.result_placeholder, False, False, 0)
        result_box.pack_start(self.result_image, False, False, 0)
        result_scroller.add(result_box)
        self.notebook.append_page(result_scroller, Gtk.Label(label=_("gui.notebook.result_tab")))
        return self.notebook

    # -- Drag and drop (native GTK, no extra dependency needed) ---------

    def _setup_dnd(self):
        self.listbox.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.listbox.drag_dest_add_uri_targets()
        self.listbox.connect("drag-data-received", self.on_drag_data_received)

    def on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        uris = data.get_uris()
        paths = []
        for uri in uris:
            p = Gio.File.new_for_uri(uri).get_path()
            if p:
                paths.append(p)
        self.add_paths(paths)
        drag_context.finish(True, False, time)

    # -- File list management --------------------------------------------

    def on_add_files(self, _btn):
        dialog = Gtk.FileChooserDialog(
            title=_("gui.dialog.add_files_title"), parent=self,
            action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                            Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        dialog.set_select_multiple(True)
        flt = Gtk.FileFilter()
        flt.set_name(_("gui.dialog.filetype_images"))
        for ext in IMAGE_EXTS:
            flt.add_pattern(f"*{ext}")
        dialog.add_filter(flt)
        if dialog.run() == Gtk.ResponseType.OK:
            self.add_paths(dialog.get_filenames())
        dialog.destroy()

    def on_add_folder(self, _btn):
        dialog = Gtk.FileChooserDialog(
            title=_("gui.dialog.add_folder_title"), parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                            Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        if dialog.run() == Gtk.ResponseType.OK:
            folder = Path(dialog.get_filename())
            paths = sorted(str(p) for p in folder.iterdir()
                            if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
            self.add_paths(paths)
        dialog.destroy()

    def on_clear_list(self, _btn):
        for row in list(self.items.values()):
            self.listbox.remove(row)
        self.items.clear()
        self._refresh_count()
        self._show_empty_row(True)

    def add_paths(self, paths):
        added = 0
        for p in paths:
            path = str(Path(p).resolve())
            if path in self.items or Path(path).suffix.lower() not in IMAGE_EXTS:
                continue
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    path, THUMB_SIZE[0], THUMB_SIZE[1], True)
            except GLib.Error as exc:
                self._show_error(_("gui.dialog.read_error_title"), f"{Path(path).name}: {exc}")
                continue
            self._add_row(path, pixbuf)
            added += 1
        if added:
            self._show_empty_row(False)
            self._refresh_count()
            if not self.output_entry.get_text():
                default = nc_default_output_path(list(self.items.keys()))
                self.output_entry.set_text(default)

    def _show_empty_row(self, show):
        if show:
            if self.empty_row.get_parent() is None:
                self.listbox.add(self.empty_row)
                self.empty_row.show_all()
        else:
            if self.empty_row.get_parent() is not None:
                self.listbox.remove(self.empty_row)

    def _add_row(self, path, pixbuf):
        row = Gtk.ListBoxRow(selectable=False)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin=6)
        hbox.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), False, False, 0)
        hbox.pack_start(Gtk.Label(label=Path(path).name), False, False, 0)
        remove_btn = Gtk.Button(label="✕")
        remove_btn.connect("clicked", lambda _b, p=path: self.remove_item(p))
        hbox.pack_end(remove_btn, False, False, 0)
        row.add(hbox)
        self.listbox.add(row)
        row.show_all()
        self.items[path] = row

    def remove_item(self, path):
        row = self.items.pop(path, None)
        if row is not None:
            self.listbox.remove(row)
        self._refresh_count()
        if not self.items:
            self._show_empty_row(True)

    def _refresh_count(self):
        n = len(self.items)
        self.count_label.set_text(_("gui.count.none") if n == 0 else _("gui.count.some", n=n))

    # -- Output path --------------------------------------------------------

    def on_browse_output(self, _btn):
        dialog = Gtk.FileChooserDialog(
            title=_("gui.dialog.save_as_title"), parent=self,
            action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                            Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dialog.set_do_overwrite_confirmation(True)
        current = self.output_entry.get_text()
        dialog.set_current_name(Path(current).name if current else "stitched.tiff")
        if dialog.run() == Gtk.ResponseType.OK:
            self.output_entry.set_text(dialog.get_filename())
        dialog.destroy()

    def on_open_folder(self, _btn):
        if self.last_written:
            folder = str(Path(self.last_written[0]).parent)
            try:
                Gio.AppInfo.launch_default_for_uri(Path(folder).as_uri())
            except GLib.Error:
                self._show_info(_("gui.dialog.result_folder_title"), folder)

    # -- Language ------------------------------------------------------------

    def on_language_changed(self, combo):
        new_lang = combo.get_active_id()
        if new_lang and new_lang != i18n.current_language():
            self._rebuild_ui(new_lang)

    def _current_settings(self):
        return dict(
            detector=self.detector_combo.get_active_id(),
            background=self.background_combo.get_active_id(),
            bundle_adjustment=self.bundle_check.get_active(),
            auto_orient=self.auto_orient_check.get_active(),
            downscale=self.downscale_spin.get_value(),
            min_inliers=int(self.min_inliers_spin.get_value()),
            sharpen_power=self.sharpen_spin.get_value(),
            compression=self.compression_combo.get_active_id(),
            interpolation=self.interp_combo.get_active_id(),
            jobs=int(self.jobs_spin.get_value()),
        )

    def _rebuild_ui(self, new_lang):
        saved_paths = list(self.items.keys())
        saved_output = self.output_entry.get_text()
        saved_settings = self._current_settings()
        adv_open = self.adv_expander.get_expanded()

        i18n.set_language(new_lang)
        save_config({**load_config(), "lang": new_lang})

        for child in self.get_children():
            self.remove(child)
        self.items = {}
        self._build_ui()
        self._setup_dnd()
        self.lang_combo.set_active_id(new_lang)

        self.detector_combo.set_active_id(saved_settings["detector"])
        self.background_combo.set_active_id(saved_settings["background"])
        self.bundle_check.set_active(saved_settings["bundle_adjustment"])
        self.auto_orient_check.set_active(saved_settings["auto_orient"])
        self.downscale_spin.set_value(saved_settings["downscale"])
        self.min_inliers_spin.set_value(saved_settings["min_inliers"])
        self.sharpen_spin.set_value(saved_settings["sharpen_power"])
        self.compression_combo.set_active_id(saved_settings["compression"])
        self.interp_combo.set_active_id(saved_settings["interpolation"])
        self.jobs_spin.set_value(saved_settings["jobs"])
        if adv_open:
            self.adv_expander.set_expanded(True)

        if saved_paths:
            self.add_paths(saved_paths)
        self.output_entry.set_text(saved_output)

    # -- Stitch workflow -------------------------------------------------------

    def on_stitch_clicked(self, _btn):
        paths = list(self.items.keys())
        if len(paths) < 2:
            self._show_warning(_("gui.dialog.too_few_images_title"), _("gui.dialog.too_few_images_body"))
            return
        output = self.output_entry.get_text().strip()
        if not output:
            self._show_warning(_("gui.dialog.no_output_title"), _("gui.dialog.no_output_body"))
            return

        args = nc.build_stitch_args(paths, output, self._current_settings(), i18n.current_language())
        self.stitch_btn.set_sensitive(False)
        self.status_label.set_text(_("gui.action.status_processing"))
        self.log_buffer.set_text("")
        self.notebook.set_current_page(0)
        self.last_written = []

        self.runner = nc.SubprocessRunner(args)
        self.runner.start()
        self._poll_source = GLib.timeout_add(100, self._poll_runner)

    def _poll_runner(self):
        self.progress.pulse()
        for line in self.runner.drain():
            if line is None:
                self._on_stitch_finished()
                return False
            end_iter = self.log_buffer.get_end_iter()
            self.log_buffer.insert(end_iter, line + "\n")
            self.log_view.scroll_to_iter(self.log_buffer.get_end_iter(), 0, False, 0, 0)
        return True

    def _on_stitch_finished(self):
        self.stitch_btn.set_sensitive(True)
        self.progress.set_fraction(0)
        if self.runner.returncode == 0:
            self.status_label.set_text(_("gui.action.status_done"))
            log_text = self.log_buffer.get_text(
                self.log_buffer.get_start_iter(), self.log_buffer.get_end_iter(), False)
            written = nc.parse_written_paths(log_text.splitlines())
            output_path = self.output_entry.get_text().strip()
            self.last_written = [w for w in written if os.path.exists(w)] or (
                [output_path] if os.path.exists(output_path) else written)
            if not self.last_written:
                self.last_written = [output_path]
            self.result_paths_label.set_text(_("gui.result.saved_prefix", names="\n".join(self.last_written)))
            self.open_folder_btn.set_sensitive(True)
            self._render_result_preview(self.last_written[0])
            self.notebook.set_current_page(1)
        else:
            self.status_label.set_text(_("gui.action.status_error"))
            last_lines = self.log_buffer.get_text(
                self.log_buffer.get_start_iter(), self.log_buffer.get_end_iter(), False)
            last_line = last_lines.strip().splitlines()[-1] if last_lines.strip() else ""
            self._show_error(_("gui.dialog.stitch_failed_title"), last_line)

    def _render_result_preview(self, path):
        import tempfile
        fd, tmp_png = tempfile.mkstemp(prefix="flatstitch_preview_", suffix=".png")
        os.close(fd)
        try:
            ok, detail = nc.run_preview_sync(path, tmp_png, RESULT_MAX[0], RESULT_MAX[1], timeout=180.0)
            if ok:
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(tmp_png)
                    self.result_image.set_from_pixbuf(pixbuf)
                    self.result_placeholder.hide()
                    return
                except GLib.Error as exc:
                    detail = str(exc)
        finally:
            try:
                os.remove(tmp_png)
            except OSError:
                pass
        self.result_image.clear()
        self.result_placeholder.show()
        self.result_placeholder.set_text(_("gui.result.preview_failed"))
        end_iter = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end_iter, f"[preview] {detail}\n")

    # -- Dialog helpers -----------------------------------------------------

    def _show_warning(self, title, body):
        self._dialog(Gtk.MessageType.WARNING, title, body)

    def _show_error(self, title, body):
        self._dialog(Gtk.MessageType.ERROR, title, body)

    def _show_info(self, title, body):
        self._dialog(Gtk.MessageType.INFO, title, body)

    def _dialog(self, kind, title, body):
        dialog = Gtk.MessageDialog(parent=self, modal=True, message_type=kind,
                                    buttons=Gtk.ButtonsType.OK, text=title)
        dialog.format_secondary_text(body)
        dialog.run()
        dialog.destroy()


def nc_default_output_path(paths: list[str]) -> str:
    """Same rule as flatstitch.pipeline.default_output_path, reimplemented
    here since this process doesn't have access to the venv's package."""
    folder = Path(paths[0]).resolve().parent
    candidate = folder / "stitched.tiff"
    if not candidate.exists():
        return str(candidate)
    n = 2
    while True:
        candidate = folder / f"stitched_{n}.tiff"
        if not candidate.exists():
            return str(candidate)
        n += 1


class FlatstitchApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.flatstitch.gtk")

    def do_activate(self):
        win = FlatstitchWindow(self)
        win.present()


def main():
    cfg = load_config()
    i18n.set_language(cfg.get("lang"))
    app = FlatstitchApplication()
    app.run(sys.argv[:1])


if __name__ == "__main__":
    main()
