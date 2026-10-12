#!/usr/bin/env python3
"""Native Qt5 frontend for flatstitch - runs under the system Python (so
it can see the system's PyQt5) and shells out to the isolated venv for
the actual stitching (see flatstitch_native_common). Picked
automatically on KDE Plasma / LXQt by flatstitch_launcher.py; can also be
run directly.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flatstitch_native_common as nc
from flatstitch import i18n
from flatstitch.i18n import _

THUMB_SIZE = (128, 96)
RESULT_MAX = (520, 380)


def _png_via_preview(path: str, max_w: int, max_h: int):
    """Render any image (16-bit / RGBA TIFF included, which Qt may lack a
    plugin for) to a QPixmap through the venv helper. Returns
    (pixmap | None, error_detail)."""
    fd, tmp = tempfile.mkstemp(prefix="flatstitch_preview_", suffix=".png")
    os.close(fd)
    try:
        ok, detail = nc.run_preview_sync(path, tmp, max_w, max_h, timeout=180.0)
        if not ok:
            return None, detail
        pm = QtGui.QPixmap(tmp)
        if pm.isNull():
            return None, "Qt could not load the preview PNG"
        return pm, ""
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _thumbnail(path: str):
    img = QtGui.QImage(path)
    if not img.isNull():
        return QtGui.QPixmap.fromImage(img).scaled(
            THUMB_SIZE[0], THUMB_SIZE[1], QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    pm, _detail = _png_via_preview(path, THUMB_SIZE[0], THUMB_SIZE[1])
    return pm


class FileRow(QtWidgets.QWidget):
    def __init__(self, path, pixmap, on_remove):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        thumb = QtWidgets.QLabel()
        thumb.setFixedSize(*THUMB_SIZE)
        thumb.setAlignment(QtCore.Qt.AlignCenter)
        thumb.setPixmap(pixmap)
        lay.addWidget(thumb)
        lay.addWidget(QtWidgets.QLabel(Path(path).name), 1)
        btn = QtWidgets.QToolButton()
        btn.setText("✕")
        btn.clicked.connect(lambda: on_remove(path))
        lay.addWidget(btn)


class FlatstitchWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.items: dict[str, QtWidgets.QListWidgetItem] = {}
        self.runner: nc.SubprocessRunner | None = None
        self.last_written: list[str] = []
        self.log_lines: list[str] = []
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll_runner)
        self.setAcceptDrops(True)
        self.resize(1080, 820)
        self._build_ui()

    # -- UI construction -----------------------------------------------

    def _build_ui(self):
        self.setWindowTitle(_("gui.window_title"))
        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)

        bar = QtWidgets.QHBoxLayout()
        for key, slot in (("gui.toolbar.add_files", self.on_add_files),
                          ("gui.toolbar.add_folder", self.on_add_folder),
                          ("gui.toolbar.clear_list", self.on_clear_list)):
            b = QtWidgets.QPushButton(_(key))
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        self.count_label = QtWidgets.QLabel(_("gui.count.none"))
        bar.addWidget(self.count_label)
        root.addLayout(bar)

        lang_row = QtWidgets.QHBoxLayout()
        lang_row.addStretch(1)
        lang_row.addWidget(QtWidgets.QLabel(_("gui.settings.language_label")))
        self.lang_combo = QtWidgets.QComboBox()
        self.lang_codes = sorted(i18n.SUPPORTED_LANGUAGES, key=lambda c: i18n.SUPPORTED_LANGUAGES[c][1])
        for code in self.lang_codes:
            self.lang_combo.addItem(i18n.SUPPORTED_LANGUAGES[code][1], code)
        self.lang_combo.setCurrentIndex(self.lang_codes.index(i18n.current_language()))
        self.lang_combo.currentIndexChanged.connect(self.on_language_changed)
        lang_row.addWidget(self.lang_combo)
        root.addLayout(lang_row)

        group = QtWidgets.QGroupBox(_("gui.body.scans_label"))
        gl = QtWidgets.QVBoxLayout(group)
        self.listw = QtWidgets.QListWidget()
        self.listw.setMinimumHeight(150)
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        gl.addWidget(self.listw)
        self.drop_hint = QtWidgets.QLabel(_("gui.body.drop_hint_with_dnd"))
        self.drop_hint.setAlignment(QtCore.Qt.AlignCenter)
        gl.addWidget(self.drop_hint)
        root.addWidget(group, 1)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(QtWidgets.QLabel(_("gui.settings.detector_label")))
        self.detector_combo = QtWidgets.QComboBox()
        for v in ("sift", "orb"):
            self.detector_combo.addItem(v, v)
        row1.addWidget(self.detector_combo)
        row1.addWidget(QtWidgets.QLabel(_("gui.settings.background_label")))
        self.background_combo = QtWidgets.QComboBox()
        self.background_combo.addItem(_("gui.background.transparent"), "transparent")
        self.background_combo.addItem("white", "white")
        self.background_combo.addItem("black", "black")
        row1.addWidget(self.background_combo)
        row1.addStretch(1)
        root.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        self.bundle_check = QtWidgets.QCheckBox(_("gui.settings.bundle_checkbox"))
        self.bundle_check.setChecked(True)
        self.auto_orient_check = QtWidgets.QCheckBox(_("gui.settings.auto_orient_checkbox"))
        self.auto_orient_check.setChecked(True)
        row2.addWidget(self.bundle_check)
        row2.addWidget(self.auto_orient_check)
        row2.addStretch(1)
        self.adv_toggle = QtWidgets.QToolButton()
        self.adv_toggle.setText(_("gui.settings.advanced_toggle"))
        self.adv_toggle.setCheckable(True)
        self.adv_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.adv_toggle.setArrowType(QtCore.Qt.RightArrow)
        self.adv_toggle.toggled.connect(self.on_toggle_advanced)
        row2.addWidget(self.adv_toggle)
        root.addLayout(row2)

        self.adv_panel = QtWidgets.QWidget()
        al = QtWidgets.QGridLayout(self.adv_panel)
        al.setContentsMargins(0, 0, 0, 0)

        def spin(cls, lo, hi, step, val, decimals=None):
            w = cls()
            if decimals is not None:
                w.setDecimals(decimals)
            w.setRange(lo, hi)
            w.setSingleStep(step)
            w.setValue(val)
            return w

        self.downscale_spin = spin(QtWidgets.QDoubleSpinBox, 1.0, 16.0, 0.5, 1.0, 1)
        self.min_inliers_spin = spin(QtWidgets.QSpinBox, 4, 200, 1, 12)
        self.sharpen_spin = spin(QtWidgets.QDoubleSpinBox, 1.0, 20.0, 1.0, 6.0, 1)
        self.compression_combo = QtWidgets.QComboBox()
        for v in ("lzw", "zlib", "none"):
            self.compression_combo.addItem(v, v)
        self.interp_combo = QtWidgets.QComboBox()
        for v in ("cubic", "linear", "lanczos4", "nearest"):
            self.interp_combo.addItem(v, v)
        self.jobs_spin = spin(QtWidgets.QSpinBox, 1, 64, 1, os.cpu_count() or 1)
        cells = [
            ("gui.advanced.downscale_label", self.downscale_spin),
            ("gui.advanced.min_inliers_label", self.min_inliers_spin),
            ("gui.advanced.sharpen_label", self.sharpen_spin),
            ("gui.advanced.compression_label", self.compression_combo),
            ("gui.advanced.interpolation_label", self.interp_combo),
            ("gui.advanced.jobs_label", self.jobs_spin),
        ]
        for i, (key, w) in enumerate(cells):
            r, c = divmod(i, 3)
            al.addWidget(QtWidgets.QLabel(_(key)), r, c * 2)
            al.addWidget(w, r, c * 2 + 1)
        al.setColumnStretch(6, 1)
        self.adv_panel.setVisible(False)
        root.addWidget(self.adv_panel)

        out = QtWidgets.QHBoxLayout()
        out.addWidget(QtWidgets.QLabel(_("gui.output.label")))
        self.output_edit = QtWidgets.QLineEdit()
        out.addWidget(self.output_edit, 1)
        browse = QtWidgets.QPushButton(_("gui.output.browse_button"))
        browse.clicked.connect(self.on_browse_output)
        out.addWidget(browse)
        root.addLayout(out)

        act = QtWidgets.QHBoxLayout()
        self.stitch_btn = QtWidgets.QPushButton(_("gui.action.stitch_button"))
        self.stitch_btn.setDefault(True)
        self.stitch_btn.clicked.connect(self.on_stitch_clicked)
        act.addWidget(self.stitch_btn)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        act.addWidget(self.progress, 1)
        self.status_label = QtWidgets.QLabel(_("gui.action.status_ready"))
        act.addWidget(self.status_label)
        root.addLayout(act)

        self.tabs = QtWidgets.QTabWidget()
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(220)
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.log_view.setFont(mono)
        self.tabs.addTab(self.log_view, _("gui.notebook.log_tab"))

        res = QtWidgets.QScrollArea()
        res.setWidgetResizable(True)
        res_inner = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(res_inner)
        self.result_paths_label = QtWidgets.QLabel("")
        self.result_paths_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        rl.addWidget(self.result_paths_label)
        self.open_folder_btn = QtWidgets.QPushButton(_("gui.result.open_folder_button"))
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self.on_open_folder)
        rl.addWidget(self.open_folder_btn, 0, QtCore.Qt.AlignLeft)
        self.result_placeholder = QtWidgets.QLabel(_("gui.result.placeholder"))
        rl.addWidget(self.result_placeholder)
        self.result_image = QtWidgets.QLabel()
        self.result_image.setAlignment(QtCore.Qt.AlignCenter)
        rl.addWidget(self.result_image)
        rl.addStretch(1)
        res.setWidget(res_inner)
        self.tabs.addTab(res, _("gui.notebook.result_tab"))
        root.addWidget(self.tabs, 1)

        self.setCentralWidget(central)

    # -- Drag and drop ---------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        expanded = []
        for p in paths:
            if os.path.isdir(p):
                expanded += sorted(str(f) for f in Path(p).iterdir() if f.is_file())
            else:
                expanded.append(p)
        self.add_paths(expanded)
        event.acceptProposedAction()

    # -- File list --------------------------------------------------------

    def on_add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(nc.IMAGE_EXTS))
        flt = f"{_('gui.dialog.filetype_images')} ({exts});;{_('gui.dialog.filetype_all')} (*)"
        files, _f = QtWidgets.QFileDialog.getOpenFileNames(self, _("gui.dialog.add_files_title"), "", flt)
        if files:
            self.add_paths(files)

    def on_add_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, _("gui.dialog.add_folder_title"))
        if folder:
            self.add_paths(sorted(str(p) for p in Path(folder).iterdir() if p.is_file()))

    def on_clear_list(self):
        self.listw.clear()
        self.items.clear()
        self._refresh_count()

    def add_paths(self, paths):
        added = 0
        for p in paths:
            path = str(Path(p).resolve())
            if path in self.items or Path(path).suffix.lower() not in nc.IMAGE_EXTS:
                continue
            pm = _thumbnail(path)
            if pm is None:
                self._error(_("gui.dialog.read_error_title"), Path(path).name)
                continue
            item = QtWidgets.QListWidgetItem()
            row = FileRow(path, pm, self.remove_item)
            item.setSizeHint(row.sizeHint())
            self.listw.addItem(item)
            self.listw.setItemWidget(item, row)
            self.items[path] = item
            added += 1
        if added:
            self._refresh_count()
            if not self.output_edit.text():
                self.output_edit.setText(nc.default_output_path(list(self.items)))

    def remove_item(self, path):
        item = self.items.pop(path, None)
        if item is not None:
            self.listw.takeItem(self.listw.row(item))
        self._refresh_count()

    def _refresh_count(self):
        n = len(self.items)
        self.count_label.setText(_("gui.count.none") if n == 0 else _("gui.count.some", n=n))
        self.drop_hint.setVisible(n == 0)

    # -- Settings / language ---------------------------------------------------

    def on_toggle_advanced(self, on):
        self.adv_panel.setVisible(on)
        self.adv_toggle.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow)

    def on_browse_output(self):
        cur = self.output_edit.text() or "stitched.tiff"
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, _("gui.dialog.save_as_title"), cur, "TIFF (*.tif *.tiff)")
        if path:
            self.output_edit.setText(path)

    def _current_settings(self):
        return dict(
            detector=self.detector_combo.currentData(),
            background=self.background_combo.currentData(),
            bundle_adjustment=self.bundle_check.isChecked(),
            auto_orient=self.auto_orient_check.isChecked(),
            downscale=self.downscale_spin.value(),
            min_inliers=self.min_inliers_spin.value(),
            sharpen_power=self.sharpen_spin.value(),
            compression=self.compression_combo.currentData(),
            interpolation=self.interp_combo.currentData(),
            jobs=self.jobs_spin.value(),
        )

    def on_language_changed(self, _idx):
        new_lang = self.lang_combo.currentData()
        if new_lang and new_lang != i18n.current_language():
            self._rebuild_ui(new_lang)

    def _rebuild_ui(self, new_lang):
        saved = self._current_settings()
        saved_paths = list(self.items)
        saved_out = self.output_edit.text()
        adv_open = self.adv_toggle.isChecked()
        i18n.set_language(new_lang)
        nc.save_config({**nc.load_config(), "lang": new_lang})
        old = self.takeCentralWidget()
        self.items = {}
        self._build_ui()
        if old is not None:
            old.deleteLater()

        def pick(combo, value):
            i = combo.findData(value)
            if i >= 0:
                combo.setCurrentIndex(i)
        pick(self.detector_combo, saved["detector"])
        pick(self.background_combo, saved["background"])
        pick(self.compression_combo, saved["compression"])
        pick(self.interp_combo, saved["interpolation"])
        self.bundle_check.setChecked(saved["bundle_adjustment"])
        self.auto_orient_check.setChecked(saved["auto_orient"])
        self.downscale_spin.setValue(saved["downscale"])
        self.min_inliers_spin.setValue(saved["min_inliers"])
        self.sharpen_spin.setValue(saved["sharpen_power"])
        self.jobs_spin.setValue(saved["jobs"])
        if adv_open:
            self.adv_toggle.setChecked(True)
        if saved_paths:
            self.add_paths(saved_paths)
        self.output_edit.setText(saved_out)

    # -- Stitch workflow ---------------------------------------------------------

    def on_stitch_clicked(self):
        paths = list(self.items)
        if len(paths) < 2:
            self._warn(_("gui.dialog.too_few_images_title"), _("gui.dialog.too_few_images_body"))
            return
        output = self.output_edit.text().strip()
        if not output:
            self._warn(_("gui.dialog.no_output_title"), _("gui.dialog.no_output_body"))
            return
        args = nc.build_stitch_args(paths, output, self._current_settings(), i18n.current_language())
        self.stitch_btn.setEnabled(False)
        self.status_label.setText(_("gui.action.status_processing"))
        self.log_view.clear()
        self.log_lines = []
        self.tabs.setCurrentIndex(0)
        self.progress.setRange(0, 0)  # busy indicator
        self.runner = nc.SubprocessRunner(args)
        self.runner.start()
        self.timer.start()

    def _poll_runner(self):
        for line in self.runner.drain():
            if line is None:
                self.timer.stop()
                self._on_stitch_finished()
                return
            self.log_lines.append(line)
            if not line.startswith("__FLATSTITCH_WRITTEN__:"):
                self.log_view.appendPlainText(line)

    def _on_stitch_finished(self):
        self.stitch_btn.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        if self.runner.returncode == 0:
            self.status_label.setText(_("gui.action.status_done"))
            out = self.output_edit.text().strip()
            written = nc.parse_written_paths(self.log_lines)
            self.last_written = [w for w in written if os.path.exists(w)] or (
                [out] if os.path.exists(out) else written) or [out]
            self.result_paths_label.setText(_("gui.result.saved_prefix", names="\n".join(self.last_written)))
            self.open_folder_btn.setEnabled(True)
            self._render_result_preview(self.last_written[0])
            self.tabs.setCurrentIndex(1)
        else:
            self.status_label.setText(_("gui.action.status_error"))
            tail = [l for l in self.log_lines if l.strip()]
            self._error(_("gui.dialog.stitch_failed_title"), tail[-1] if tail else "")

    def _render_result_preview(self, path):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            pm, detail = _png_via_preview(path, RESULT_MAX[0], RESULT_MAX[1])
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if pm is not None:
            self.result_image.setPixmap(pm)
            self.result_placeholder.hide()
            return
        self.result_image.clear()
        self.result_placeholder.show()
        self.result_placeholder.setText(_("gui.result.preview_failed"))
        self.log_view.appendPlainText(f"[preview] {detail}")

    def on_open_folder(self):
        if self.last_written:
            folder = str(Path(self.last_written[0]).parent)
            if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder)):
                self._info(_("gui.dialog.result_folder_title"), folder)

    # -- Dialogs -----------------------------------------------------------------

    def _warn(self, title, body):
        QtWidgets.QMessageBox.warning(self, title, body)

    def _error(self, title, body):
        QtWidgets.QMessageBox.critical(self, title, body)

    def _info(self, title, body):
        QtWidgets.QMessageBox.information(self, title, body)


def main():
    i18n.set_language(nc.load_config().get("lang"))
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("flatstitch")
    app.setDesktopFileName("flatstitch")
    win = FlatstitchWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
