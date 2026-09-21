"""Settings dialog: Models (recognition/diarization), Speakers, General tabs."""

from pathlib import Path

import core

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QHeaderView, QTableWidget, QTableWidgetItem, QLineEdit, QPushButton, QComboBox,
    QLabel, QMessageBox, QInputDialog, QFileDialog, QScrollArea, QFrame,
)

from .device_prefs import get_device_preference, set_device_preference
from .hardware_icons import cpu_icon_path, gpu_icon_path
from .i18n import tr, available_languages, get_language, set_language
from .style import apply_theme, get_theme_preference, set_theme_preference
from .widgets import ModelRowWidget
from .workers import ModelDownloadWorker

_PYANNOTE_NOTE = (
    "Gated models - accept the license while logged in to Hugging Face:<br>"
    '<a href="https://hf.co/pyannote/speaker-diarization-3.1">speaker-diarization-3.1</a>, '
    '<a href="https://hf.co/pyannote/segmentation-3.0">segmentation-3.0</a>, '
    '<a href="https://hf.co/pyannote/embedding">embedding</a>'
)


class ModelsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # This tab scrolls internally instead of sizing the dialog to its
        # content, so the dialog stays a consistent size across tabs.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

        self._rows = {}  # key -> (ModelRowWidget, ModelSpec), Whisper models only
        self._active_downloads = []  # keeps (thread, worker) alive while running

        rec_box = QGroupBox(tr("Recognition (Whisper)"))
        rec_layout = QVBoxLayout(rec_box)
        rec_layout.setSpacing(10)
        for spec in core.WHISPER_MODELS:
            row = ModelRowWidget(spec.label, spec.size, spec.description, core.is_whisper_installed(spec.key))
            row.download_requested.connect(lambda s=spec: self._start_download(s))
            row.delete_requested.connect(lambda s=spec: self._delete_whisper(s))
            rec_layout.addWidget(row)
            self._rows[spec.key] = (row, spec)
        layout.addWidget(rec_box)

        diar_box = QGroupBox(tr("Diarization (pyannote)"))
        diar_layout = QVBoxLayout(diar_box)
        diar_layout.setSpacing(10)

        # Same card treatment (QFrame#modelRow, see style.py) as the model
        # row below it, so the token block and the download row read as two
        # equally-weighted cards instead of one floating loose above a card.
        token_card = QFrame()
        token_card.setObjectName("modelRow")
        token_card_layout = QVBoxLayout(token_card)
        token_card_layout.setContentsMargins(12, 10, 12, 10)
        token_card_layout.setSpacing(4)

        note = QLabel(tr(_PYANNOTE_NOTE))
        note.setOpenExternalLinks(True)
        note.setWordWrap(True)
        token_card_layout.addWidget(note)

        # The token lives here, not in General, since it's only needed for these gated models.
        token_label = QLabel(tr(
            'Hugging Face token: <a href="https://huggingface.co/settings/tokens">get one here</a>'))
        token_label.setOpenExternalLinks(True)
        token_card_layout.addWidget(token_label)
        token_row = QHBoxLayout()
        self._token_edit = QLineEdit(core.read_hf_token())
        self._token_edit.setEchoMode(QLineEdit.Password)
        # An icon embedded in the field itself (the usual place for a
        # password show/hide toggle) instead of a separate button next to it.
        assets_dir = Path(__file__).parent / "assets"
        self._show_action = self._token_edit.addAction(
            QIcon(str(assets_dir / "eye.svg")), QLineEdit.TrailingPosition)
        self._show_action.setToolTip(tr("Show"))
        self._show_action.triggered.connect(self._toggle_token_visibility)
        self._token_visible = False
        token_row.addWidget(self._token_edit, stretch=1)
        save_token_btn = QPushButton(tr("Save token"))
        save_token_btn.clicked.connect(self._save_token)
        token_row.addWidget(save_token_btn)
        token_card_layout.addLayout(token_row)
        diar_layout.addWidget(token_card)

        # The four pyannote models are only useful together, so they're
        # offered as one combined download instead of four separate ones.
        self._diarization_row = ModelRowWidget(
            core.DIARIZATION_BUNDLE_LABEL, core.DIARIZATION_BUNDLE_SIZE,
            core.DIARIZATION_BUNDLE_DESCRIPTION, core.is_diarization_complete())
        self._diarization_row.download_requested.connect(self._start_diarization_download)
        self._diarization_row.delete_requested.connect(self._delete_diarization)
        diar_layout.addWidget(self._diarization_row)
        layout.addWidget(diar_box)
        layout.addStretch()

        self.refresh_token_state()

    def refresh_token_state(self):
        has_token = bool(core.read_hf_token())
        self._diarization_row.set_needs_token(not has_token)

    def _toggle_token_visibility(self):
        self._token_visible = not self._token_visible
        self._token_edit.setEchoMode(QLineEdit.Normal if self._token_visible else QLineEdit.Password)
        assets_dir = Path(__file__).parent / "assets"
        icon_name = "eye_off.svg" if self._token_visible else "eye.svg"
        self._show_action.setIcon(QIcon(str(assets_dir / icon_name)))
        self._show_action.setToolTip(tr("Hide") if self._token_visible else tr("Show"))

    def _save_token(self):
        self._write_token()
        QMessageBox.information(self, tr("Saved"), tr("HF_TOKEN saved to config.env."))

    def _write_token(self):
        core.write_config_env({"HF_TOKEN": self._token_edit.text().strip()})
        self.refresh_token_state()

    def _start_download(self, spec):
        token = core.read_hf_token()

        def fn(ct, on_progress, spec=spec, token=token):
            core.download_whisper_model(spec.key, spec.hf_repo, token, cancel_token=ct, on_progress=on_progress)

        row, _ = self._rows[spec.key]
        self._run_download(row, fn, lambda spec=spec: core.is_whisper_installed(spec.key))

    def _start_diarization_download(self):
        token = core.read_hf_token()

        def fn(ct, on_progress, token=token):
            core.download_diarization_models(token, cancel_token=ct, on_progress=on_progress)

        self._run_download(self._diarization_row, fn, core.is_diarization_complete)

    def _delete_whisper(self, spec):
        if QMessageBox.question(
                self, tr("Delete model"),
                tr('Delete the "{name}" model ({size})? You can download it again anytime.',
                   name=spec.label, size=spec.size)) != QMessageBox.Yes:
            return
        core.delete_whisper_model(spec.key)
        row, _ = self._rows[spec.key]
        row.set_installed(False)

    def _delete_diarization(self):
        if QMessageBox.question(
                self, tr("Delete model"),
                tr("Delete the diarization models? Speaker identification will be "
                   "unavailable until you download them again.")) != QMessageBox.Yes:
            return
        core.delete_diarization_models()
        self._diarization_row.set_installed(False)

    def _run_download(self, row, fn, check_installed):
        # worker.finished also fires on a cooperative cancel (see
        # ModelDownloadWorker.run()'s except Cancelled branch) - checking the
        # real on-disk state here, rather than trusting the signal alone,
        # keeps a Stop-clicked row from being mislabeled "Installed".
        def on_finished():
            if check_installed():
                row.set_done(True)
            else:
                row.set_installed(False)

        thread = QThread()
        worker = ModelDownloadWorker(fn)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(row.set_downloading)
        worker.finished.connect(on_finished)
        worker.failed.connect(lambda err: row.set_done(False, err))
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # DirectConnection: while run() is mid-loop, worker's own thread isn't
        # spinning its event loop, so a normal (queued) cross-thread
        # connection here would just sit undelivered until run() returns on
        # its own - cancel_token.cancel() only touches a threading.Event, so
        # calling it synchronously from the GUI thread is safe.
        row.cancel_requested.connect(worker.cancel, Qt.DirectConnection)
        entry = (thread, worker)
        self._active_downloads.append(entry)
        thread.finished.connect(lambda: self._active_downloads.remove(entry) if entry in self._active_downloads else None)
        thread.start()

    def cleanup_active_downloads(self):
        """Called when Settings is about to close: quits and waits on each
        still-running download thread first. A running QThread losing its
        last Python reference (this tab's _active_downloads is the only
        thing keeping it alive) crashes the process with "QThread:
        Destroyed while thread is still running"."""
        for thread, worker in list(self._active_downloads):
            worker.cancel()
            thread.quit()
            thread.wait()


class SpeakersTab(QWidget):
    _ROLE = Qt.UserRole

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([tr("Speaker"), tr("Version"), tr("Date")])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setShowGrid(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        self._rename_btn = QPushButton(tr("Rename"))
        self._delete_version_btn = QPushButton(tr("Delete version"))
        self._delete_person_btn = QPushButton(tr("Delete all versions"))
        for b in (self._rename_btn, self._delete_version_btn, self._delete_person_btn):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        io_row = QHBoxLayout()
        self._export_selected_btn = QPushButton(tr("Export selected..."))
        self._export_all_btn = QPushButton(tr("Export all..."))
        self._import_btn = QPushButton(tr("Import..."))
        for b in (self._export_selected_btn, self._export_all_btn, self._import_btn):
            io_row.addWidget(b)
        layout.addLayout(io_row)

        self._rename_btn.clicked.connect(self._on_rename)
        self._delete_version_btn.clicked.connect(self._on_delete_version)
        self._delete_person_btn.clicked.connect(self._on_delete_person)
        self._export_selected_btn.clicked.connect(self._on_export_selected)
        self._export_all_btn.clicked.connect(self._on_export_all)
        self._import_btn.clicked.connect(self._on_import)

        self.refresh()

    def refresh(self):
        rows = [(person.base_name, v) for person in core.list_speaker_persons() for v in person.versions]
        self._table.setRowCount(len(rows))
        for row, (base_name, v) in enumerate(rows):
            name_item = QTableWidgetItem(base_name)
            name_item.setData(self._ROLE, (base_name, v.path))
            version_item = QTableWidgetItem(f"v{v.version}")
            date_item = QTableWidgetItem(v.modified.strftime("%Y-%m-%d %H:%M"))
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, version_item)
            self._table.setItem(row, 2, date_item)

    def _selected(self):
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not rows:
            return None
        return self._table.item(rows[0], 0).data(self._ROLE)

    def _selected_paths(self):
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        return [self._table.item(r, 0).data(self._ROLE)[1] for r in rows]

    def _on_rename(self):
        sel = self._selected()
        if sel is None:
            QMessageBox.information(self, tr("Rename"), tr("Select a speaker to rename."))
            return
        base_name, _path = sel
        new_name, ok = QInputDialog.getText(self, tr("Rename speaker"), tr("New name:"), text=base_name)
        new_name = core.base_speaker_name(new_name.strip()) if ok else ""
        if ok and new_name and new_name != base_name:
            core.rename_speaker_person(base_name, new_name)
            self.refresh()

    def _on_delete_version(self):
        sel = self._selected()
        if sel is None:
            QMessageBox.information(self, tr("Delete version"), tr("Select a version to delete."))
            return
        _base_name, path = sel
        if QMessageBox.question(self, tr("Delete version"), tr("Delete {name}?", name=path.name)) == QMessageBox.Yes:
            core.delete_speaker_version(path)
            self.refresh()

    def _on_delete_person(self):
        sel = self._selected()
        if sel is None:
            QMessageBox.information(self, tr("Delete all versions"), tr("Select a speaker to delete."))
            return
        base_name, _path = sel
        if QMessageBox.question(
                self, tr("Delete all versions"),
                tr('Delete ALL versions of "{name}"? This cannot be undone.', name=base_name)) == QMessageBox.Yes:
            core.delete_speaker_person(base_name)
            self.refresh()

    def _on_export_selected(self):
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, tr("Export selected"), tr("Select one or more rows to export."))
            return
        self._export(paths, "speakers_export.zip")

    def _on_export_all(self):
        paths = [v.path for p in core.list_speaker_persons() for v in p.versions]
        if not paths:
            QMessageBox.information(self, tr("Export all"), tr("There are no enrolled speakers to export."))
            return
        self._export(paths, "speakers_export_all.zip")

    def _export(self, paths, default_name):
        dest, _ = QFileDialog.getSaveFileName(self, tr("Export speakers"), default_name, "Zip files (*.zip)")
        if not dest:
            return
        if not dest.lower().endswith(".zip"):
            dest += ".zip"
        try:
            count = core.export_speakers(paths, Path(dest))
            QMessageBox.information(
                self, tr("Export speakers"), tr("Exported {n} voiceprint(s) to {path}.", n=count, path=dest))
        except Exception as e:
            QMessageBox.warning(self, tr("Export failed"), tr("Could not write the export file:\n\n{error}", error=str(e)))

    def _on_import(self):
        src, _ = QFileDialog.getOpenFileName(self, tr("Import speakers"), "", "Zip files (*.zip)")
        if not src:
            return
        try:
            count = core.import_speakers(Path(src))
            self.refresh()
            QMessageBox.information(self, tr("Import speakers"), tr("Imported {n} voiceprint(s).", n=count))
        except Exception as e:
            QMessageBox.warning(self, tr("Import failed"), tr("Could not read the import file:\n\n{error}", error=str(e)))


class GeneralTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("Theme:")))
        self._theme_combo = QComboBox()
        self._theme_combo.addItem(tr("System (auto)"), "auto")
        self._theme_combo.addItem(tr("Light"), "light")
        self._theme_combo.addItem(tr("Dark"), "dark")
        idx = self._theme_combo.findData(get_theme_preference())
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        layout.addWidget(self._theme_combo)

        layout.addWidget(QLabel(tr("Interface language:")))
        self._ui_lang_combo = QComboBox()
        for code, name in available_languages():
            self._ui_lang_combo.addItem(name, code)
        idx = self._ui_lang_combo.findData(get_language())
        if idx >= 0:
            self._ui_lang_combo.setCurrentIndex(idx)
        self._ui_lang_combo.currentIndexChanged.connect(self._on_ui_language_changed)
        layout.addWidget(self._ui_lang_combo)

        layout.addWidget(self._build_hardware_box())
        layout.addStretch()

    def _build_hardware_box(self):
        box = QGroupBox(tr("Hardware"))
        box_layout = QVBoxLayout(box)

        cpu_name, gpu_name, gpu_supported = core.detect_hardware_info()

        cpu_row = QHBoxLayout()
        cpu_icon = QLabel()
        cpu_icon.setPixmap(QPixmap(str(cpu_icon_path(cpu_name))).scaled(
            16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        cpu_row.addWidget(cpu_icon)
        cpu_row.addWidget(QLabel(tr("CPU: {name}", name=cpu_name)))
        cpu_row.addStretch()
        box_layout.addLayout(cpu_row)

        if gpu_name:
            gpu_row = QHBoxLayout()
            gpu_icon = QLabel()
            gpu_icon.setPixmap(QPixmap(str(gpu_icon_path(gpu_name))).scaled(
                16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            gpu_row.addWidget(gpu_icon)
            gpu_label_text = (tr("GPU: {name}", name=gpu_name) if gpu_supported
                              else tr("GPU: {name} (not supported for acceleration)", name=gpu_name))
            gpu_row.addWidget(QLabel(gpu_label_text))
            gpu_row.addStretch()
            box_layout.addLayout(gpu_row)

        if gpu_supported:
            # Only meaningful when both a CPU and a supported GPU are present.
            box_layout.addWidget(QLabel(tr("Use for processing:")))
            self._device_combo = QComboBox()
            self._device_combo.addItem(tr("Automatic (use GPU - recommended)"), "auto")
            self._device_combo.addItem(tr("CPU only"), "cpu")
            idx = self._device_combo.findData(get_device_preference())
            self._device_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self._device_combo.currentIndexChanged.connect(
                lambda _i: set_device_preference(self._device_combo.currentData()))
            box_layout.addWidget(self._device_combo)

        # Always shown, tailored to the actual configuration - CPU-only
        # setups still deserve to know why (and that it's normal, just slower).
        if gpu_supported:
            explanation_text = tr(
                "The GPU is much faster for speech recognition and speaker "
                "identification, especially with larger models. CPU works "
                "everywhere and leaves the GPU free for other tasks (e.g. "
                "gaming) while processing.")
        elif gpu_name:
            explanation_text = tr(
                "This GPU isn't supported for acceleration here (NVIDIA/CUDA "
                "only), so everything runs on the CPU instead. It still "
                "works fine, just slower - especially with larger models.")
        else:
            explanation_text = tr(
                "No GPU detected, so everything runs on the CPU. It works "
                "fine, just slower than a GPU would be, especially with "
                "larger models - an NVIDIA GPU would speed this up automatically.")
        explanation = QLabel(explanation_text)
        explanation.setWordWrap(True)
        explanation.setProperty("hint", True)
        box_layout.addWidget(explanation)

        return box

    def _on_theme_changed(self, _index):
        set_theme_preference(self._theme_combo.currentData())
        apply_theme()

    def _on_ui_language_changed(self, _index):
        set_language(self._ui_lang_combo.currentData())


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Settings"))
        self.resize(780, 700)
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        self.general_tab = GeneralTab()
        self.models_tab = ModelsTab()
        self.speakers_tab = SpeakersTab()
        tabs.addTab(self.general_tab, tr("General"))
        tabs.addTab(self.models_tab, tr("Models"))
        tabs.addTab(self.speakers_tab, tr("Speakers"))
        layout.addWidget(tabs)

        close_btn = QPushButton(tr("Save and Close"))
        close_btn.clicked.connect(self._on_save_and_close)
        layout.addWidget(close_btn)

    def _on_save_and_close(self):
        self.models_tab._write_token()
        self.accept()

    def done(self, result):
        # The single path accept()/reject()/closeEvent() all funnel through -
        # covers "Save and Close", Escape, and the window's X equally, so a
        # still-running download can never outlive the widgets it reports
        # progress to (see cleanup_active_downloads()).
        self.models_tab.cleanup_active_downloads()
        super().done(result)
