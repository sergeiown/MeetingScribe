"""Main window: pick files, choose options, run transcription/diarization."""

import shutil
import time
from pathlib import Path

import core

from PySide6.QtCore import Qt, QSettings, QThread, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QFormLayout, QGroupBox, QTableWidget, QTableWidgetItem,
    QPushButton, QComboBox, QCheckBox, QLabel, QProgressBar, QTextEdit,
    QFileDialog, QMessageBox, QSpinBox,
)

from .device_prefs import get_device_preference
from .i18n import tr, get_language
from .settings_dialog import SettingsDialog
from .dialogs import SpeakerNameDialog
from .workers import TranscriptionWorker, DiarizationWorker, UpdateCheckWorker

_PREFERRED_WIDTH = 1080
_PREFERRED_HEIGHT = 680
_MIN_WIDTH = 760
_MIN_HEIGHT = 480
_SIDEBAR_WIDTH = 300

_ABOUT_TEXT = (
    "<b>MeetingScribe</b> v{version}<br><br>"
    "Local meeting transcription with speaker diarization and "
    "known-speaker recognition. Everything runs on this machine - "
    "no audio ever leaves the host.<br><br>"
    "MIT License © Serhii Myshko<br>"
    '<a href="https://github.com/sergeiown/MeetingScribe">github.com/sergeiown/MeetingScribe</a>'
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeetingScribe")
        self.setMinimumSize(_MIN_WIDTH, _MIN_HEIGHT)

        self._worker = None
        self._progress_phase = None
        self._progress_phase_start = None
        self._current_file_index = 0
        self._diarize_models_missing_notified = False

        self._build_ui()
        self._apply_initial_geometry()
        self._refresh_file_list()
        self._refresh_model_choices()
        self._refresh_diarize_availability()
        self._check_for_updates(manual=False)

    def _apply_initial_geometry(self):
        """Size the window to fit the actual screen (important on laptops
        with display scaling, where a fixed pixel size can exceed the
        available desktop area), and center it."""
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        width = max(_MIN_WIDTH, min(_PREFERRED_WIDTH, int(avail.width() * 0.9)))
        height = max(_MIN_HEIGHT, min(_PREFERRED_HEIGHT, int(avail.height() * 0.9)))
        self.resize(width, height)
        self.move(avail.center().x() - width // 2, avail.center().y() - height // 2)

    def _build_ui(self):
        # A QMenuBar instead of a QToolBar: same slim, standard OS-native
        # height, but its content (Settings, Help > About, Exit) is expected
        # chrome rather than a mostly-empty bar taking up its own row.
        menu_bar = self.menuBar()
        self._settings_action = QAction(tr("Settings"), self)
        self._settings_action.triggered.connect(self._open_settings)
        menu_bar.addAction(self._settings_action)

        self._help_menu = menu_bar.addMenu(tr("Help"))
        self._check_updates_action = QAction(tr("Check for updates"), self)
        self._check_updates_action.triggered.connect(lambda: self._check_for_updates(manual=True))
        self._help_menu.addAction(self._check_updates_action)
        self._about_action = QAction(tr("About"), self)
        self._about_action.triggered.connect(self._show_about)
        self._help_menu.addAction(self._about_action)

        self._exit_action = QAction(tr("Exit"), self)
        self._exit_action.triggered.connect(self.close)
        menu_bar.addAction(self._exit_action)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        root.addLayout(self._build_main_column(), stretch=1)
        root.addWidget(self._build_sidebar())

    def _build_main_column(self):
        col = QVBoxLayout()
        col.setSpacing(14)

        # --- Files ---------------------------------------------------------
        self._files_box = QGroupBox(tr("Files"))
        files_layout = QVBoxLayout(self._files_box)

        self._file_table = QTableWidget(0, 4)
        self._file_table.setHorizontalHeaderLabels(
            [tr("File"), tr("Size"), tr("Duration"), tr("Status")])
        self._file_table.verticalHeader().setVisible(False)
        self._file_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._file_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._file_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._file_table.setShowGrid(False)
        self._file_table.setFixedHeight(180)
        header = self._file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col_idx in (1, 2, 3):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeToContents)
        self._file_table.itemSelectionChanged.connect(self._refresh_diarize_button)
        files_layout.addWidget(self._file_table)

        self._empty_hint = QLabel(tr("No files yet - click \"Add files...\" or drop some into input\\"))
        self._empty_hint.setProperty("hint", True)
        self._empty_hint.setAlignment(Qt.AlignCenter)
        files_layout.addWidget(self._empty_hint)

        file_btn_row = QHBoxLayout()
        self._add_btn = QPushButton(tr("Add files..."))
        self._add_btn.clicked.connect(self._on_add_files)
        self._refresh_btn = QPushButton(tr("Refresh"))
        self._refresh_btn.clicked.connect(self._refresh_file_list)
        self._delete_btn = QPushButton(tr("Delete selected"))
        self._delete_btn.clicked.connect(self._on_delete_files)
        file_btn_row.addWidget(self._add_btn)
        file_btn_row.addWidget(self._refresh_btn)
        file_btn_row.addWidget(self._delete_btn)
        file_btn_row.addStretch()
        files_layout.addLayout(file_btn_row)
        col.addWidget(self._files_box)

        # --- Transcript ---------------------------------------------------------
        self._transcript_box = QGroupBox(tr("Transcript"))
        transcript_layout = QVBoxLayout(self._transcript_box)
        self._transcript_view = QTextEdit()
        self._transcript_view.setReadOnly(True)
        self._transcript_view.setPlaceholderText(
            tr("The transcript will appear here once processing starts..."))
        transcript_layout.addWidget(self._transcript_view)
        col.addWidget(self._transcript_box, stretch=1)

        return col

    def _build_sidebar(self):
        sidebar = QWidget()
        sidebar.setFixedWidth(_SIDEBAR_WIDTH)
        col = QVBoxLayout(sidebar)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(14)

        # --- Options ---------------------------------------------------------
        self._options_box = QGroupBox(tr("Options"))
        options_form = QFormLayout(self._options_box)
        options_form.setVerticalSpacing(10)
        # Labels above fields (not side-by-side) so a longer translated
        # label (e.g. Ukrainian "Модель:") never gets squeezed/clipped by
        # the fixed sidebar width - width no longer depends on label length.
        options_form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self._model_combo = QComboBox()
        self._lang_combo = QComboBox()
        self._lang_combo.addItem(tr("Auto-detect"), "auto")
        self._lang_combo.addItem(tr("Ukrainian"), "uk")
        default_lang = QSettings("MeetingScribe", "MeetingScribe").value("default_language", "auto")
        idx = self._lang_combo.findData(default_lang)
        self._lang_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._model_label = QLabel(tr("Model:"))
        self._language_label = QLabel(tr("Language:"))
        options_form.addRow(self._model_label, self._model_combo)
        options_form.addRow(self._language_label, self._lang_combo)

        col.addWidget(self._options_box)

        # --- Speaker diarization ---------------------------------------------
        self._diar_box = QGroupBox(tr("Speaker diarization"))
        diar_layout = QVBoxLayout(self._diar_box)
        self._diarize_checkbox = QCheckBox(tr("Automatically identify speakers\nafter recognition"))
        self._diarize_checkbox.setChecked(
            QSettings("MeetingScribe", "MeetingScribe").value("diarize_default", True, type=bool))
        diar_layout.addWidget(self._diarize_checkbox)
        speaker_count_row = QHBoxLayout()
        speaker_count_row.setContentsMargins(22, 0, 0, 0)
        self._count_label = QLabel(tr("Count (0 = auto):"))
        self._count_label.setProperty("hint", True)
        speaker_count_row.addWidget(self._count_label)
        self._num_speakers_spin = QSpinBox()
        self._num_speakers_spin.setRange(0, 20)
        self._num_speakers_spin.setFixedWidth(70)
        self._num_speakers_spin.setEnabled(False)
        speaker_count_row.addWidget(self._num_speakers_spin)
        speaker_count_row.addStretch()
        diar_layout.addLayout(speaker_count_row)

        # A hover-only tooltip on the checkbox is easy to miss - this is
        # the same explanation, always visible, so "why is this greyed out"
        # doesn't require hovering to discover.
        self._diarize_hint = QLabel()
        self._diarize_hint.setProperty("hint", True)
        self._diarize_hint.setWordWrap(True)
        self._diarize_hint.setVisible(False)
        diar_layout.addWidget(self._diarize_hint)

        col.addWidget(self._diar_box)

        # --- Run ---------------------------------------------------------
        self._transcribe_btn = QPushButton(tr("Recognize speech"))
        self._transcribe_btn.setObjectName("primaryButton")
        self._transcribe_btn.clicked.connect(self._on_transcribe)
        col.addWidget(self._transcribe_btn)

        run_row = QHBoxLayout()
        run_row.setSpacing(10)
        self._diarize_btn = QPushButton(tr("Identify speakers"))
        self._diarize_btn.clicked.connect(self._on_diarize)
        self._diarize_btn.setEnabled(False)
        self._cancel_btn = QPushButton(tr("Cancel"))
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.setEnabled(False)
        run_row.addWidget(self._diarize_btn, stretch=1)
        run_row.addWidget(self._cancel_btn, stretch=1)
        col.addLayout(run_row)

        status_row = QVBoxLayout()
        status_row.setSpacing(6)
        self._status_label = QLabel(tr("Ready."))
        self._status_label.setProperty("hint", True)
        self._status_label.setWordWrap(True)
        status_row.addWidget(self._status_label)

        self._overall_label = QLabel(tr("Overall"))
        self._overall_label.setProperty("hint", True)
        status_row.addWidget(self._overall_label)
        self._overall_progress_bar = QProgressBar()
        self._overall_progress_bar.setTextVisible(True)
        self._overall_progress_bar.setFormat(tr("File %v of %m"))
        status_row.addWidget(self._overall_progress_bar)

        self._current_file_label = QLabel(tr("Current file"))
        self._current_file_label.setProperty("hint", True)
        status_row.addWidget(self._current_file_label)
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(True)
        status_row.addWidget(self._progress_bar)
        col.addLayout(status_row)

        self._open_output_btn = QPushButton(tr("Open output folder"))
        self._open_output_btn.clicked.connect(self._on_open_output_folder)
        col.addWidget(self._open_output_btn)

        col.addStretch()
        return sidebar

    # --- i18n --------------------------------------------------------------

    def retranslate_ui(self):
        """Re-applies all static text after a language change in Settings.
        Dynamic log-style content already written into the transcript view
        is left as-is - only the app's own chrome is re-labeled."""
        self._settings_action.setText(tr("Settings"))
        self._help_menu.setTitle(tr("Help"))
        self._check_updates_action.setText(tr("Check for updates"))
        self._about_action.setText(tr("About"))
        self._exit_action.setText(tr("Exit"))

        self._files_box.setTitle(tr("Files"))
        self._file_table.setHorizontalHeaderLabels(
            [tr("File"), tr("Size"), tr("Duration"), tr("Status")])
        self._empty_hint.setText(tr("No files yet - click \"Add files...\" or drop some into input\\"))
        self._add_btn.setText(tr("Add files..."))
        self._refresh_btn.setText(tr("Refresh"))
        self._delete_btn.setText(tr("Delete selected"))
        self._transcript_box.setTitle(tr("Transcript"))
        self._transcript_view.setPlaceholderText(
            tr("The transcript will appear here once processing starts..."))

        self._options_box.setTitle(tr("Options"))
        self._model_label.setText(tr("Model:"))
        self._language_label.setText(tr("Language:"))
        prev_lang_data = self._lang_combo.currentData()
        self._lang_combo.setItemText(0, tr("Auto-detect"))
        self._lang_combo.setItemText(1, tr("Ukrainian"))
        idx = self._lang_combo.findData(prev_lang_data)
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)
        self._refresh_model_choices()

        self._diar_box.setTitle(tr("Speaker diarization"))
        self._diarize_checkbox.setText(tr("Automatically identify speakers\nafter recognition"))
        self._count_label.setText(tr("Count (0 = auto):"))
        self._refresh_diarize_availability()

        self._transcribe_btn.setText(tr("Recognize speech"))
        self._diarize_btn.setText(tr("Identify speakers"))
        self._cancel_btn.setText(tr("Cancel"))
        self._overall_label.setText(tr("Overall"))
        self._overall_progress_bar.setFormat(tr("File %v of %m"))
        self._current_file_label.setText(tr("Current file"))
        self._open_output_btn.setText(tr("Open output folder"))

    # --- refresh helpers -------------------------------------------------

    def _refresh_file_list(self):
        self._file_table.setRowCount(0)
        core.ensure_workdirs()
        files = sorted(
            f for f in core.INPUT_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in core.SUPPORTED_EXTENSIONS
        )
        self._empty_hint.setVisible(not files)
        self._file_table.setVisible(bool(files))
        self._file_table.setRowCount(len(files))
        for row, f in enumerate(files):
            dur = core.get_duration(f)
            dur_str = core.format_duration(dur) if dur else tr("unknown")
            size_mb = f.stat().st_size / 1024 / 1024
            transcribed, _diarizable = core.file_status(f)

            name_item = QTableWidgetItem(f.name)
            name_item.setData(Qt.UserRole, f)
            size_item = QTableWidgetItem(f"{size_mb:.1f} MB")
            dur_item = QTableWidgetItem(dur_str)
            status_item = QTableWidgetItem(tr("✓ Transcribed") if transcribed else "")

            if transcribed:
                for it in (name_item, size_item, dur_item, status_item):
                    it.setForeground(Qt.darkGreen)

            self._file_table.setItem(row, 0, name_item)
            self._file_table.setItem(row, 1, size_item)
            self._file_table.setItem(row, 2, dur_item)
            self._file_table.setItem(row, 3, status_item)
        # Select everything by default (convenient when there's just a
        # handful of files) - but selection is now the real, explicit
        # source of truth for what Start processes, not a fallback.
        self._file_table.selectAll()

    def _refresh_model_choices(self):
        self._model_combo.clear()
        installed = core.installed_whisper_sizes()
        # Torch-free: never import torch in the main GUI process (see
        # core.has_diarization_support's docstring for why) - just enough
        # info to pick heaviest-vs-lightest, the actual device is decided
        # for real inside the isolated ML pipeline process.
        use_gpu = core.nvidia_gpu_present() and get_device_preference() != "cpu"
        device = "cuda" if use_gpu else "cpu"
        recommended = core.recommend_whisper_model(installed, device)
        for spec in core.WHISPER_MODELS:  # heaviest-first catalog order
            if spec.key in installed:
                label = spec.label + (tr("  [recommended]") if spec.key == recommended else "")
                self._model_combo.addItem(label, spec.key)
        if recommended:
            idx = self._model_combo.findData(recommended)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
        if self._model_combo.count() == 0:
            self._model_combo.addItem(tr("No model installed - open Settings"), None)

    def _diarize_unavailable_message(self, has_package, has_token, models_missing):
        """Shared by the checkbox hint and the "Identify speakers" button
        tooltip, so the two controls never disagree about why diarization
        isn't available right now."""
        if not has_package:
            return tr("Speaker diarization isn't available in this install.")
        if not has_token and models_missing:
            return tr("Enter a Hugging Face token and download the diarization "
                      "models in Settings > Models.")
        if not has_token:
            return tr("Enter a Hugging Face token in Settings > Models to enable this.")
        if models_missing:
            return tr("Diarization models aren't downloaded yet - get them from Settings > Models.")
        return ""

    def _refresh_diarize_availability(self):
        has_token = bool(core.read_hf_token())
        has_package = core.has_diarization_support()
        # Independent of has_token now (it used to short-circuit to False
        # whenever the token was missing, which hid the fact that the
        # models were ALSO missing) - both gaps need to surface together,
        # since a fresh install commonly has neither yet.
        models_missing = has_package and not all(
            core.is_pyannote_installed(spec.key) for spec in core.PYANNOTE_MODELS)
        available = has_package and has_token and not models_missing

        self._diarize_checkbox.setEnabled(available)
        self._num_speakers_spin.setEnabled(available)
        if available:
            self._diarize_checkbox.setToolTip("")
            self._diarize_hint.setVisible(False)
        else:
            self._diarize_checkbox.setChecked(False)
            message = self._diarize_unavailable_message(has_package, has_token, models_missing)
            self._diarize_checkbox.setToolTip(message)
            # A visible hint, not just a hover-only tooltip - "why is this
            # greyed out" shouldn't require hovering to discover.
            self._diarize_hint.setText(message)
            self._diarize_hint.setVisible(True)
            if models_missing and models_missing != self._diarize_models_missing_notified:
                # A distinct, more visible nudge than the passive hover
                # tooltip above - specifically for "everything else is set
                # up, you just haven't downloaded the models yet", since
                # that's an easy one-click fix (unlike the token case, which
                # needs the user to go get one from HuggingFace first).
                from PySide6.QtWidgets import QToolTip
                pos = self._diarize_checkbox.mapToGlobal(self._diarize_checkbox.rect().topLeft())
                QToolTip.showText(pos, message, self._diarize_checkbox)
        self._diarize_models_missing_notified = models_missing
        self._refresh_diarize_button()

    def _refresh_diarize_button(self):
        """The "Identify speakers" button only makes sense once a file has
        been recognized but not yet had speakers identified - and only
        while diarization support (pyannote + token + models) is actually
        there. Shares its unavailability message with the checkbox hint
        above (_diarize_unavailable_message) so the two never disagree."""
        has_token = bool(core.read_hf_token())
        has_package = core.has_diarization_support()
        models_missing = has_package and not all(
            core.is_pyannote_installed(spec.key) for spec in core.PYANNOTE_MODELS)
        available = has_package and has_token and not models_missing
        selected = self._selected_files()
        diarizable = available and any(core.file_status(f)[1] for f in selected)
        self._diarize_btn.setEnabled(diarizable and self._worker is None)
        if not available:
            self._diarize_btn.setToolTip(
                self._diarize_unavailable_message(has_package, has_token, models_missing))
        elif not diarizable:
            self._diarize_btn.setToolTip(
                tr("Select a recognized file that hasn't had speakers identified yet."))
        else:
            self._diarize_btn.setToolTip("")

    # --- actions -----------------------------------------------------------

    def _on_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Add audio/video files"), str(core.INPUT_DIR),
            f"{tr('Media files')} (*.mp4 *.webm *.mkv *.mov *.avi *.m4a *.mp3 *.wav)")
        for p in paths:
            src = Path(p)
            if src.parent != core.INPUT_DIR:
                shutil.copy(str(src), str(core.INPUT_DIR / src.name))
        if paths:
            self._refresh_file_list()

    def _on_open_output_folder(self):
        core.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(core.OUTPUT_DIR)))

    def _open_settings(self):
        prev_lang = get_language()
        SettingsDialog(self).exec()
        if get_language() != prev_lang:
            self.retranslate_ui()
        else:
            self._refresh_model_choices()
            self._refresh_diarize_availability()

    def _show_about(self):
        QMessageBox.about(self, tr("About MeetingScribe"), tr(_ABOUT_TEXT, version=core.VERSION))

    def _check_for_updates(self, manual=False):
        thread = QThread(self)
        worker = UpdateCheckWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.checked.connect(lambda info: self._on_update_checked(info, manual))
        worker.checked.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._update_check_thread = thread  # kept alive on self until it finishes
        thread.start()

    def _on_update_checked(self, info, manual):
        if info:
            box = QMessageBox(self)
            box.setWindowTitle(tr("Update available"))
            box.setText(tr("MeetingScribe {version} is available (you have v{current}).",
                            version=info["version"], current=core.VERSION))
            open_btn = box.addButton(tr("Open releases page"), QMessageBox.AcceptRole)
            box.addButton(tr("Close"), QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() == open_btn:
                QDesktopServices.openUrl(QUrl(info["url"]))
        elif manual:
            QMessageBox.information(self, tr("Check for updates"), tr("You're using the latest version."))

    def _selected_rows(self):
        return sorted({idx.row() for idx in self._file_table.selectedIndexes()})

    def _selected_files(self):
        """Only what's actually selected in the table - no "nothing selected
        means everything" fallback."""
        rows = self._selected_rows()
        files = [self._file_table.item(r, 0).data(Qt.UserRole) for r in rows]
        return [f for f in files if f is not None]

    def _on_delete_files(self):
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, tr("Delete files"), tr("Select one or more files in the table first."))
            return
        files = [self._file_table.item(r, 0).data(Qt.UserRole) for r in rows]
        names = "\n".join(f.name for f in files[:10])
        ans = QMessageBox.question(
            self, tr("Delete files"),
            tr("Delete {n} file(s) from input\\? This cannot be undone.\n\n{names}", n=len(files), names=names))
        if ans != QMessageBox.Yes:
            return
        for f in files:
            f.unlink(missing_ok=True)
        self._refresh_file_list()

    def _begin_run(self, file_count):
        self._transcript_view.clear()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("%p%")
        self._overall_progress_bar.setRange(0, file_count)
        self._overall_progress_bar.setValue(0)
        self._progress_phase = None
        self._progress_phase_start = None
        self._current_file_index = 0
        self._transcribe_btn.setEnabled(False)
        self._diarize_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

    def _connect_worker_signals(self):
        self._worker.status.connect(self._on_status)
        self._worker.progress.connect(self._on_progress)
        self._worker.speaker_decision_needed.connect(self._on_speaker_decision_needed)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.file_failed.connect(self._on_file_failed)
        self._worker.finished.connect(self._on_finished)
        self._worker.cancelled.connect(self._on_cancelled)

    def _on_transcribe(self):
        files = self._selected_files()
        if not files:
            QMessageBox.information(self, tr("No files selected"), tr("Select one or more files in the table first."))
            return
        model_key = self._model_combo.currentData()
        if not model_key:
            QMessageBox.information(self, tr("No model"), tr("Install a recognition model first (Settings > Models)."))
            return

        existing = [f for f in files if (core.OUTPUT_DIR / (f.stem + ".txt")).exists()]
        if existing:
            names = "\n".join(f.name for f in existing[:10])
            ans = QMessageBox.question(
                self, tr("Already transcribed"),
                tr("{n} file(s) already have output. Re-transcribe and overwrite them?\n\n{names}",
                   n=len(existing), names=names),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ans == QMessageBox.Cancel:
                return
            if ans == QMessageBox.No:
                files = [f for f in files if f not in existing]
        if not files:
            return

        language = self._lang_combo.currentData()
        language = None if language == "auto" else language
        # Unchecked: stop after recognition, leaving the file eligible for a
        # later, separate "Identify speakers" run instead of chaining into
        # diarization right away.
        auto_diarize = self._diarize_checkbox.isChecked()
        hf_token = core.read_hf_token() if auto_diarize else None
        num_speakers = self._num_speakers_spin.value() or None

        self._begin_run(len(files))
        self._worker = TranscriptionWorker(files, model_key, language, auto_diarize, hf_token, num_speakers,
                                           get_device_preference())
        self._connect_worker_signals()
        self._worker.run()

    def _on_diarize(self):
        files = [f for f in self._selected_files() if core.file_status(f)[1]]
        if not files:
            QMessageBox.information(
                self, tr("Identify speakers"),
                tr("Select a recognized file that hasn't had speakers identified yet."))
            return
        hf_token = core.read_hf_token()
        num_speakers = self._num_speakers_spin.value() or None

        self._begin_run(len(files))
        self._worker = DiarizationWorker(files, hf_token, num_speakers, get_device_preference())
        self._connect_worker_signals()
        self._worker.run()

    def _on_cancel(self):
        if self._worker:
            self._worker.cancel()
            self._status_label.setText(tr("Cancelling..."))
            self._cancel_btn.setEnabled(False)

    # --- worker signal handlers --------------------------------------------

    def _on_status(self, msg):
        self._status_label.setText(tr(msg))

    def _on_progress(self, phase, current, total, label):
        if phase != self._progress_phase:
            # A new phase (transcribe -> diarize, or a new file) starts its
            # own ETA clock - elapsed time from the previous phase/file
            # wouldn't mean anything applied to this one's current/total scale.
            self._progress_phase = phase
            self._progress_phase_start = time.time()

        if total > 0:
            pct = min(current / total, 1.0)
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(int(pct * 100))
            eta_str = ""
            if pct > 0.02 and self._progress_phase_start is not None:
                elapsed = time.time() - self._progress_phase_start
                eta_sec = elapsed / pct * (1.0 - pct)
                eta_str = f"  ·  {tr('ETA {time}', time=core.format_time(eta_sec))}"
            self._progress_bar.setFormat(f"%p%{eta_str}")
        else:
            self._progress_bar.setRange(0, 0)
            self._progress_bar.setFormat("")

        if phase == "transcribe" and label:
            self._transcript_view.append(label)

    def _on_speaker_decision_needed(self, req):
        dlg = SpeakerNameDialog(req.label, req.samples, self)
        dlg.exec()
        req.fulfill(dlg.choice)

    def _on_file_started(self, name, index, total):
        self._current_file_index = index
        self._progress_phase = None  # force a fresh ETA clock for this file
        self._overall_progress_bar.setValue(index - 1)
        self._transcript_view.append(f"\n=== [{index}/{total}] {name} ===")

    def _on_file_done(self, name, out_path):
        self._overall_progress_bar.setValue(self._current_file_index)
        self._transcript_view.append(tr("Saved: {path}", path=out_path))

    def _on_file_failed(self, name, message):
        self._overall_progress_bar.setValue(self._current_file_index)
        self._transcript_view.append(f"{name}: {message}")

    def _on_finished(self):
        self._reset_run_state()
        self._status_label.setText(tr("Done."))
        self._refresh_file_list()

    def _on_cancelled(self):
        self._reset_run_state()
        self._status_label.setText(tr("Cancelled."))

    def _reset_run_state(self):
        self._transcribe_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._worker = None
        self._refresh_diarize_button()

    def closeEvent(self, event):
        if self._worker is not None:
            ans = QMessageBox.question(
                self, tr("Transcription running"), tr("A transcription is still running. Cancel it and quit?"))
            if ans != QMessageBox.Yes:
                event.ignore()
                return
            self._worker.terminate()
        event.accept()
