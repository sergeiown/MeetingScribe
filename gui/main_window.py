"""Main window: pick files, choose options, run transcription/diarization."""

import shutil
import time
from datetime import datetime
from pathlib import Path

import core

from PySide6.QtCore import Qt, QSettings, QThread, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor, QColor
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QFormLayout, QGroupBox, QTableWidget, QTableWidgetItem,
    QPushButton, QComboBox, QCheckBox, QLabel, QProgressBar, QTextEdit, QSlider,
    QFileDialog, QMessageBox, QSpinBox, QToolTip, QToolButton, QMenu,
)

from .device_prefs import get_device_preference
from .i18n import tr, get_language
from .recording_prefs import (
    get_recording_source, set_recording_source,
    get_last_mic_device_name, set_last_mic_device_name,
    get_last_loopback_device_name, set_last_loopback_device_name,
    get_exclusive_default,
)
from .recording_worker import RecordingController
from .settings_dialog import SettingsDialog
from .style import get_prevent_sleep_preference
from .dialogs import SpeakerNameDialog
from .update_dialog import UpdateDownloadDialog
from .widgets import LevelMeterWidget
from .workers import TranscriptionWorker, DiarizationWorker, UpdateCheckWorker

_PREFERRED_WIDTH = 1340
_PREFERRED_HEIGHT = 680
_MIN_WIDTH = 920
_MIN_HEIGHT = 480
_SIDEBAR_WIDTH = 300
_RECORDING_PANEL_WIDTH = 260
_STARTUP_UPDATE_CHECK_DELAY_MS = 10_000

_ABOUT_TEXT = (
    "<b>MeetingScribe</b> v{version}<br><br>"
    "Local meeting transcription with speaker diarization and "
    "known-speaker recognition. Everything runs on this machine - "
    "no audio ever leaves the host.<br><br>"
    "MIT License © Serhii Myshko<br>"
    '<a href="https://github.com/sergeiown/MeetingScribe">github.com/sergeiown/MeetingScribe</a>'
)

_HOW_TO_USE_TEXT = (
    "<ol>"
    "<li>Add audio/video files with <b>Add files...</b> (or drop them into the <code>input</code> folder).</li>"
    "<li>Select one or more files, pick a recognition model and language.</li>"
    "<li>Click <b>Recognize speech</b>. With automatic speaker identification off, it stops after "
    "a plain transcript - click <b>Identify speakers</b> whenever you're ready to add speaker labels.</li>"
    "<li>For unrecognized speakers, keep the suggested name or type your own, and optionally save "
    "their voiceprint for next time.</li>"
    "<li>Find the transcript in the <code>output</code> folder (one .txt per input file).</li>"
    "</ol>"
    "Installed models, saved speakers, your Hugging Face token, theme, and interface language "
    "are all managed from <b>Settings</b>."
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

        self._recording_controller = None
        self._recording_devices = []
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed_label)

        # Created once, reused across files - independent of the recording
        # subsystem above (sounddevice/soundcard); playback is 100% QtMultimedia.
        self._media_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.positionChanged.connect(self._on_playback_position_changed)
        self._media_player.durationChanged.connect(self._on_playback_duration_changed)
        self._media_player.playbackStateChanged.connect(self._on_playback_state_changed)

        self._build_ui()
        self._apply_initial_geometry()
        self._refresh_file_list(select_all=False)
        self._refresh_model_choices()
        self._refresh_diarize_availability()
        # Delayed so it never competes with startup itself for network/CPU,
        # and so a quick "open, do one thing, close" session never triggers it at all.
        QTimer.singleShot(_STARTUP_UPDATE_CHECK_DELAY_MS, lambda: self._check_for_updates(manual=False))

    def _apply_initial_geometry(self):
        """Fits the window to the available screen area (a fixed pixel size
        can exceed it on scaled displays) and centers it."""
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        width = max(_MIN_WIDTH, min(_PREFERRED_WIDTH, int(avail.width() * 0.9)))
        height = max(_MIN_HEIGHT, min(_PREFERRED_HEIGHT, int(avail.height() * 0.9)))
        self.resize(width, height)
        self.move(avail.center().x() - width // 2, avail.center().y() - height // 2)

    def _build_ui(self):
        # A plain sized-to-content button row instead of QMainWindow's own
        # menuBar(): a real QMenuBar always spans the full window width, so
        # with only three items it was mostly empty dark bar - especially
        # once the window widened for the recording panel. QToolButtons in
        # a normal row take only the width their labels need.
        central = QWidget()
        self.setCentralWidget(central)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addLayout(self._build_top_row())

        root = QHBoxLayout()
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(16)
        central_layout.addLayout(root)

        root.addWidget(self._build_recording_panel())
        root.addLayout(self._build_main_column(), stretch=1)
        root.addWidget(self._build_sidebar())

    def _build_top_row(self):
        row = QHBoxLayout()
        row.setContentsMargins(12, 6, 12, 4)
        row.setSpacing(4)

        self._settings_btn = QToolButton()
        self._settings_btn.setText(tr("Settings"))
        self._settings_btn.setAutoRaise(True)
        self._settings_btn.clicked.connect(self._open_settings)
        row.addWidget(self._settings_btn)

        self._help_btn = QToolButton()
        self._help_btn.setText(tr("Help"))
        self._help_btn.setAutoRaise(True)
        self._help_btn.setPopupMode(QToolButton.InstantPopup)
        self._help_menu = QMenu(self._help_btn)
        self._how_to_use_action = self._help_menu.addAction(tr("How to use"))
        self._how_to_use_action.triggered.connect(self._show_how_to_use)
        self._check_updates_action = self._help_menu.addAction(tr("Check for updates"))
        self._check_updates_action.triggered.connect(lambda: self._check_for_updates(manual=True))
        self._about_action = self._help_menu.addAction(tr("About"))
        self._about_action.triggered.connect(self._show_about)
        self._help_btn.setMenu(self._help_menu)
        row.addWidget(self._help_btn)

        self._exit_btn = QToolButton()
        self._exit_btn.setText(tr("Exit"))
        self._exit_btn.setAutoRaise(True)
        self._exit_btn.clicked.connect(self.close)
        row.addWidget(self._exit_btn)

        row.addStretch()
        return row

    def _build_recording_panel(self):
        panel = QWidget()
        panel.setFixedWidth(_RECORDING_PANEL_WIDTH)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)

        self._record_box = QGroupBox(tr("Record audio"))
        layout = QVBoxLayout(self._record_box)
        layout.setSpacing(10)

        self._record_source_label = QLabel(tr("Source:"))
        layout.addWidget(self._record_source_label)
        self._record_source_combo = QComboBox()
        self._record_source_combo.addItem(tr("Microphone"), "microphone")
        self._record_source_combo.addItem(tr("System audio"), "system")
        self._record_source_combo.addItem(tr("Microphone + System audio"), "both")
        self._record_source_combo.currentIndexChanged.connect(self._on_record_source_changed)
        layout.addWidget(self._record_source_combo)

        self._record_device_label = QLabel(tr("Device:"))
        layout.addWidget(self._record_device_label)
        self._record_device_combo = QComboBox()
        layout.addWidget(self._record_device_combo)

        self._level_meter = LevelMeterWidget()
        layout.addWidget(self._level_meter)

        self._record_elapsed_label = QLabel("00:00")
        self._record_elapsed_label.setProperty("hint", True)
        self._record_elapsed_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._record_elapsed_label)

        self._record_btn = QPushButton(tr("Record"))
        self._record_btn.setObjectName("primaryButton")
        self._record_btn.clicked.connect(self._on_record_clicked)
        layout.addWidget(self._record_btn)

        layout.addStretch()
        outer.addWidget(self._record_box)

        idx = self._record_source_combo.findData(get_recording_source())
        self._record_source_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._on_record_source_changed(self._record_source_combo.currentIndex())

        return panel

    def _current_record_source(self):
        return self._record_source_combo.currentData()

    def _on_record_source_changed(self, _index):
        source = self._current_record_source()
        set_recording_source(source)
        # "both" still shows a device combo, but it's for the microphone
        # half only - the system-audio half silently uses the current
        # default loopback device (most machines have exactly one output
        # device anyway, and a second device picker isn't worth the extra
        # UI for this).
        is_mic_combo = source in ("microphone", "both")

        self._record_device_combo.clear()
        self._recording_devices = core.list_microphones() if is_mic_combo else core.list_loopback_outputs()
        last_name = get_last_mic_device_name() if is_mic_combo else get_last_loopback_device_name()
        for d in self._recording_devices:
            self._record_device_combo.addItem(d.name, d.name)
        select_idx = self._record_device_combo.findData(last_name) if last_name else -1
        if select_idx < 0:
            select_idx = next((i for i, d in enumerate(self._recording_devices) if d.is_default), 0)
        if self._record_device_combo.count():
            self._record_device_combo.setCurrentIndex(max(select_idx, 0))
        self._record_device_combo.currentIndexChanged.connect(
            self._on_record_device_changed, Qt.UniqueConnection)

    def _on_record_device_changed(self, _index):
        name = self._record_device_combo.currentData()
        if not name:
            return
        if self._current_record_source() in ("microphone", "both"):
            set_last_mic_device_name(name)
        else:
            set_last_loopback_device_name(name)

    def _selected_record_device(self):
        name = self._record_device_combo.currentData()
        return next((d for d in self._recording_devices if d.name == name), None)

    def _default_loopback_device(self):
        devices = core.list_loopback_outputs()
        return next((d for d in devices if d.is_default), devices[0] if devices else None)

    def _on_record_clicked(self):
        if self._recording_controller is None:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        source = self._current_record_source()
        device = self._selected_record_device()
        if device is None:
            QMessageBox.information(self, tr("Record"), tr("No recording device available."))
            return

        devices = [(device, False)]  # (AudioDevice, is_loopback)
        if source == "system":
            devices = [(device, True)]
        elif source == "both":
            loopback_device = self._default_loopback_device()
            if loopback_device is None:
                QMessageBox.information(self, tr("Record"), tr("No recording device available."))
                return
            devices.append((loopback_device, True))

        core.ensure_workdirs()
        out_path = core.INPUT_DIR / f"Recording {datetime.now():%Y-%m-%d %H-%M-%S}.wav"

        controller = RecordingController(devices, out_path, exclusive=get_exclusive_default())
        controller.level.connect(self._level_meter.set_level)
        controller.error.connect(self._on_recording_error)
        controller.stopped.connect(self._on_recording_stopped)
        try:
            controller.start()
        except core.RecordingError as e:
            QMessageBox.warning(self, tr("Recording failed"), str(e))
            return

        self._recording_controller = controller
        self._elapsed_timer.start(500)
        self._record_btn.setText(tr("Stop"))
        self._record_source_combo.setEnabled(False)
        self._record_device_combo.setEnabled(False)

    def _stop_recording(self):
        if self._recording_controller is None:
            return
        self._recording_controller.stop()

    def _on_recording_error(self, message):
        QMessageBox.warning(self, tr("Recording"), message)
        self._stop_recording()

    def _on_recording_stopped(self, path_str):
        self._recording_controller = None
        self._elapsed_timer.stop()
        self._level_meter.reset()
        self._record_elapsed_label.setText("00:00")
        self._record_btn.setText(tr("Record"))
        self._record_source_combo.setEnabled(True)
        self._record_device_combo.setEnabled(True)
        self._on_record_source_changed(self._record_source_combo.currentIndex())
        self._refresh_file_list(select_all=False)
        self._select_file_by_path(Path(path_str))

    def _update_elapsed_label(self):
        if self._recording_controller is None:
            return
        secs = int(self._recording_controller.elapsed_seconds())
        self._record_elapsed_label.setText(f"{secs // 60:02d}:{secs % 60:02d}")

    def _select_file_by_path(self, path: Path):
        for row in range(self._file_table.rowCount()):
            item = self._file_table.item(row, 0)
            if item is not None and item.data(Qt.UserRole) == path:
                self._file_table.selectRow(row)
                self._file_table.scrollToItem(item)
                return

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
        header = self._file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col_idx in (1, 2, 3):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeToContents)
        self._file_table.itemSelectionChanged.connect(self._refresh_diarize_button)
        self._file_table.itemSelectionChanged.connect(self._refresh_play_button)
        self._file_table.itemDoubleClicked.connect(self._on_file_double_clicked)
        files_layout.addWidget(self._file_table, stretch=1)

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
        self._play_btn = QPushButton(tr("Play"))
        self._play_btn.clicked.connect(self._on_play_clicked)
        self._play_btn.setEnabled(False)
        file_btn_row.addWidget(self._add_btn)
        file_btn_row.addWidget(self._refresh_btn)
        file_btn_row.addWidget(self._delete_btn)
        file_btn_row.addWidget(self._play_btn)
        file_btn_row.addStretch()
        files_layout.addLayout(file_btn_row)

        # Hidden until the first Play, so it never takes up space otherwise.
        self._playback_row_widget = QWidget()
        playback_row = QHBoxLayout(self._playback_row_widget)
        playback_row.setContentsMargins(0, 0, 0, 0)
        self._playback_time_label = QLabel("0:00 / 0:00")
        self._playback_time_label.setProperty("hint", True)
        self._playback_slider = QSlider(Qt.Horizontal)
        self._playback_slider.sliderMoved.connect(self._on_playback_slider_moved)
        playback_row.addWidget(self._playback_time_label)
        playback_row.addWidget(self._playback_slider, stretch=1)
        self._playback_row_widget.setVisible(False)
        files_layout.addWidget(self._playback_row_widget)

        # Equal stretch with the transcript box below: a fixed 50/50 split
        # of the column regardless of whether there are files to list.
        col.addWidget(self._files_box, stretch=1)

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
        # Labels above fields, not side-by-side, so a longer translated label isn't
        # clipped by the fixed sidebar width.
        options_form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self._model_combo = QComboBox()
        self._lang_combo = QComboBox()
        self._lang_combo.addItem(tr("Auto-detect"), "auto")
        self._lang_combo.addItem(tr("Ukrainian"), "uk")
        default_lang = QSettings("MeetingScribe", "MeetingScribe").value("default_language", "auto")
        idx = self._lang_combo.findData(default_lang)
        self._lang_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
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
        self._diarize_checkbox.clicked.connect(self._on_diarize_checkbox_clicked)
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
        """Re-applies static text after a language change; content already
        written into the transcript view is left as-is."""
        self._settings_btn.setText(tr("Settings"))
        self._help_btn.setText(tr("Help"))
        self._how_to_use_action.setText(tr("How to use"))
        self._check_updates_action.setText(tr("Check for updates"))
        self._about_action.setText(tr("About"))
        self._exit_btn.setText(tr("Exit"))

        self._files_box.setTitle(tr("Files"))
        self._file_table.setHorizontalHeaderLabels(
            [tr("File"), tr("Size"), tr("Duration"), tr("Status")])
        self._empty_hint.setText(tr("No files yet - click \"Add files...\" or drop some into input\\"))
        self._add_btn.setText(tr("Add files..."))
        self._refresh_btn.setText(tr("Refresh"))
        self._delete_btn.setText(tr("Delete selected"))
        self._play_btn.setText(
            tr("Pause") if self._media_player.playbackState() == QMediaPlayer.PlayingState else tr("Play"))
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

        self._record_box.setTitle(tr("Record audio"))
        self._record_source_label.setText(tr("Source:"))
        self._record_device_label.setText(tr("Device:"))
        self._record_source_combo.setItemText(0, tr("Microphone"))
        self._record_source_combo.setItemText(1, tr("System audio"))
        self._record_source_combo.setItemText(2, tr("Microphone + System audio"))
        if self._recording_controller is None:
            self._record_btn.setText(tr("Record"))

    # --- refresh helpers -------------------------------------------------

    def _refresh_file_list(self, select_all=True):
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
            transcribed, diarizable = core.file_status(f)
            if not transcribed:
                status_text, status_color = tr("○ Not recognized"), None
            elif diarizable:
                status_text, status_color = tr("✓ Transcribed"), QColor("#b8860b")
            else:
                status_text, status_color = tr("✓✓ Diarized"), Qt.darkGreen

            name_item = QTableWidgetItem(f.name)
            name_item.setData(Qt.UserRole, f)
            size_item = QTableWidgetItem(f"{size_mb:.1f} MB")
            dur_item = QTableWidgetItem(dur_str)
            status_item = QTableWidgetItem(status_text)

            if status_color is not None:
                for it in (name_item, size_item, dur_item, status_item):
                    it.setForeground(status_color)

            self._file_table.setItem(row, 0, name_item)
            self._file_table.setItem(row, 1, size_item)
            self._file_table.setItem(row, 2, dur_item)
            self._file_table.setItem(row, 3, status_item)
        # Selection is the actual source of truth for what Start processes,
        # not a fallback - so it defaults to everything selected after an
        # action the user just took (add/delete/finished a run), but not on
        # startup, where the user should choose what to run themselves.
        if select_all:
            self._file_table.selectAll()

    def _refresh_model_choices(self):
        self._model_combo.clear()
        installed = core.installed_whisper_sizes()
        # Torch-free: never import torch in the main GUI process (see
        # core.has_diarization_support). Only used to pick heaviest-vs-lightest;
        # the real device is decided inside the isolated ML pipeline process.
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
        # Independent of has_token so a missing token and missing models both
        # surface together, since a fresh install commonly has neither yet.
        models_missing = has_package and not core.is_diarization_complete()
        # Cached for _on_diarize_checkbox_clicked, which needs to know
        # whether a click should be allowed to stick.
        self._diarize_available = has_package and has_token and not models_missing
        self._diarize_unavailable_reason = self._diarize_unavailable_message(
            has_package, has_token, models_missing)

        if not self._diarize_available:
            self._diarize_checkbox.setChecked(False)
        self._diarize_checkbox.setToolTip(self._diarize_unavailable_reason)
        self._num_speakers_spin.setEnabled(
            self._diarize_available and self._diarize_checkbox.isChecked())

        if models_missing and models_missing != self._diarize_models_missing_notified:
            # More visible nudge than the passive tooltip, since the
            # models-missing case is an easy one-click fix.
            pos = self._diarize_checkbox.mapToGlobal(self._diarize_checkbox.rect().topLeft())
            QToolTip.showText(pos, self._diarize_unavailable_reason, self._diarize_checkbox)
        self._diarize_models_missing_notified = models_missing
        self._refresh_diarize_button()

    def _on_diarize_checkbox_clicked(self, checked):
        """Stays enabled even when diarization isn't available yet, since a
        disabled QCheckBox never emits clicked and couldn't explain why;
        clicking it while unavailable snaps it back off and shows the reason."""
        if checked and not self._diarize_available:
            self._diarize_checkbox.setChecked(False)
            pos = self._diarize_checkbox.mapToGlobal(self._diarize_checkbox.rect().bottomLeft())
            QToolTip.showText(pos, self._diarize_unavailable_reason, self._diarize_checkbox)
            return
        self._num_speakers_spin.setEnabled(checked)
        QSettings("MeetingScribe", "MeetingScribe").setValue("diarize_default", checked)

    def _on_language_changed(self, _index):
        QSettings("MeetingScribe", "MeetingScribe").setValue(
            "default_language", self._lang_combo.currentData())

    def _refresh_diarize_button(self):
        """Enabled only once a file is recognized but not yet diarized, and
        diarization support is actually available. Shares its message with
        the checkbox (_diarize_unavailable_message) so the two never disagree."""
        has_token = bool(core.read_hf_token())
        has_package = core.has_diarization_support()
        models_missing = has_package and not core.is_diarization_complete()
        available = has_package and has_token and not models_missing
        selected = self._selected_files()
        diarizable = available and any(core.file_status(f)[1] for f in selected)
        self._diarize_btn.setEnabled(diarizable and self._worker is None)
        self._set_primary_run_button(self._diarize_btn if diarizable else self._transcribe_btn)
        if not available:
            self._diarize_btn.setToolTip(
                self._diarize_unavailable_message(has_package, has_token, models_missing))
        elif not diarizable:
            self._diarize_btn.setToolTip(
                tr("Select a recognized file that hasn't had speakers identified yet."))
        else:
            self._diarize_btn.setToolTip("")

    def _set_primary_run_button(self, primary_btn):
        """Highlights whichever action actually applies to the current
        selection - Identify speakers once a selected file is transcribed
        but not yet diarized, Recognize speech otherwise."""
        for btn in (self._transcribe_btn, self._diarize_btn):
            name = "primaryButton" if btn is primary_btn else ""
            if btn.objectName() != name:
                btn.setObjectName(name)
                btn.style().unpolish(btn)
                btn.style().polish(btn)

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

    def _show_how_to_use(self):
        QMessageBox.information(self, tr("How to use MeetingScribe"), tr(_HOW_TO_USE_TEXT))

    def _check_for_updates(self, manual=False):
        # Guards against the automatic startup check and a manual click
        # overlapping: each would otherwise reuse the same
        # self._update_check_thread/_worker slots, so the second call's
        # assignment could silently drop the first check's worker mid-flight
        # (see the note below) - and if both DO find an update, two "Update
        # available" QMessageBoxes could each try to go modal at once.
        if getattr(self, "_checking_updates", False):
            # Silent for the automatic path (nothing for the user to react
            # to); a manual click deserves an acknowledgement instead of
            # just doing nothing with no explanation.
            if manual:
                QMessageBox.information(
                    self, tr("Check for updates"), tr("Already checking - hang on a moment."))
            return
        self._checking_updates = True
        self._update_check_manual = manual  # read back by the two handlers below
        thread = QThread(self)
        worker = UpdateCheckWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Connected directly to bound methods of self (a real QObject whose
        # thread() Qt can see is the GUI thread), not a lambda: a lambda has
        # no thread affinity of its own, so AutoConnection can't tell this
        # needs queuing back to the GUI thread - even passing an explicit
        # Qt.QueuedConnection didn't help, since there's still no receiver
        # thread to queue it to. Without this, the callback ran in place on
        # worker's own QThread, where it then tried to build a QMessageBox
        # from a non-GUI thread and hard-froze the window (confirmed live
        # via a py-spy stack dump of the hang).
        worker.checked.connect(self._on_update_checked)
        worker.failed.connect(self._on_update_check_failed)
        worker.checked.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # Both kept alive on self until the thread finishes - worker had no
        # persistent reference here before (only the thread did), so it was
        # liable to be garbage-collected before its run() slot ever fired,
        # silently dropping the check. This is likely why some real-world
        # update checks (automatic or manual) appeared to just do nothing.
        self._update_check_thread = thread
        self._update_check_worker = worker
        thread.start()

    def _on_update_check_failed(self, error):
        self._checking_updates = False
        # The silent startup check stays silent on failure (e.g. offline) -
        # only a manually-requested check reports it, so it's never
        # mistaken for "you're up to date" when the check didn't complete.
        if self._update_check_manual:
            QMessageBox.warning(
                self, tr("Check for updates"),
                tr("Could not check for updates: {error}", error=error))

    def _on_update_checked(self, info):
        self._checking_updates = False
        manual = self._update_check_manual
        if info and info.get("installer_url"):
            box = QMessageBox(self)
            box.setWindowTitle(tr("Update available"))
            box.setText(tr("MeetingScribe {version} is available (you have v{current}).",
                            version=info["version"], current=core.VERSION))
            download_btn = box.addButton(tr("Download && Install"), QMessageBox.AcceptRole)
            box.addButton(tr("Open releases page"), QMessageBox.ActionRole)
            box.addButton(tr("Later"), QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked == download_btn:
                self._start_update_download(info)
            elif box.buttonRole(clicked) == QMessageBox.ActionRole:
                QDesktopServices.openUrl(QUrl(info["url"]))
        elif info:
            # A release exists but has no matching installer asset - fall
            # back to the plain releases-page link.
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

    def _start_update_download(self, info):
        dest = core.UPDATE_DOWNLOAD_DIR / info["installer_name"]
        dlg = UpdateDownloadDialog(info["installer_url"], dest, info["installer_size"], info["version"], self)
        dlg.start()
        dlg.exec()
        if dlg.installer_path is not None:
            self._run_update_now(dlg.installer_path, info["version"])
        elif dlg.error:
            QMessageBox.warning(self, tr("Download failed"),
                                 tr("Could not download the update: {error}", error=dlg.error))
        # Cancel falls through silently - core.download_installer already
        # cleaned up the partial download.

    def _run_update_now(self, installer_path, version):
        if self._worker is not None:
            QMessageBox.information(self, tr("Update"), tr(
                "A transcription is still running. Finish or cancel it, then try installing the update again."))
            return
        ans = QMessageBox.question(self, tr("Install update"), tr(
            "Install version {version} now? MeetingScribe will close and reopen.\n\n"
            "Windows may show a security prompt for the installer since it isn't "
            "code-signed - that's expected.", version=version))
        if ans != QMessageBox.Yes:
            return  # installer_path stays on disk; swept on the next launch
        log_path = core.UPDATE_DOWNLOAD_DIR / f"install-{version}.log"
        try:
            core.spawn_installer(installer_path, log_path)
        except OSError as e:
            QMessageBox.warning(self, tr("Update failed"),
                                 tr("Could not start the installer: {error}", error=str(e)))
            return
        self.close()

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

    # --- playback ------------------------------------------------------

    def _refresh_play_button(self):
        self._play_btn.setEnabled(len(self._selected_files()) == 1)

    def _on_file_double_clicked(self, _item):
        files = self._selected_files()
        if len(files) == 1:
            self._play_file(files[0])

    def _on_play_clicked(self):
        if self._media_player.playbackState() == QMediaPlayer.PlayingState:
            self._media_player.pause()
            return
        if self._media_player.playbackState() == QMediaPlayer.PausedState:
            self._media_player.play()
            return
        files = self._selected_files()
        if len(files) == 1:
            self._play_file(files[0])

    def _play_file(self, path: Path):
        self._media_player.setSource(QUrl.fromLocalFile(str(path)))
        self._media_player.play()
        self._playback_row_widget.setVisible(True)

    def _on_playback_position_changed(self, position_ms):
        if not self._playback_slider.isSliderDown():
            self._playback_slider.setValue(position_ms)
        self._update_playback_time_label(position_ms, self._media_player.duration())

    def _on_playback_duration_changed(self, duration_ms):
        self._playback_slider.setRange(0, duration_ms)
        self._update_playback_time_label(self._media_player.position(), duration_ms)

    def _on_playback_slider_moved(self, position_ms):
        self._media_player.setPosition(position_ms)

    def _on_playback_state_changed(self, state):
        self._play_btn.setText(tr("Pause") if state == QMediaPlayer.PlayingState else tr("Play"))

    def _update_playback_time_label(self, position_ms, duration_ms):
        pos = core.format_time(position_ms / 1000)
        dur = core.format_time(duration_ms / 1000) if duration_ms > 0 else "0:00"
        self._playback_time_label.setText(f"{pos} / {dur}")

    def _begin_run(self, file_count):
        if get_prevent_sleep_preference():
            core.set_sleep_prevention(True)
        self._transcript_view.clear()
        # Indeterminate (busy) until the first real progress update arrives -
        # a large model can take a long time to load, with no progress
        # events during that stretch, so a static "0%" bar reads as frozen.
        self._status_label.setText(tr("Starting..."))
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFormat("")
        self._overall_progress_bar.setRange(0, file_count)
        self._overall_progress_bar.setValue(0)
        self._progress_phase = None
        self._progress_phase_start = None
        self._current_file_index = 0
        self._current_file_body_pos = None
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
        # Unchecked = stop after recognition; file stays eligible for a
        # later, separate "Identify speakers" run.
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
        # Also echoed into the transcript pane - model loading in particular
        # can take a noticeable while before the first real segment of text
        # arrives, and this is what keeps that stretch from looking frozen.
        text = tr(msg)
        self._status_label.setText(text)
        self._transcript_view.append(text)

    def _on_progress(self, phase, current, total, label):
        if phase != self._progress_phase:
            # A new phase starts its own ETA clock; elapsed time from the
            # previous phase/file wouldn't apply to this one's scale.
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
        self._current_file_body_pos = self._transcript_view.document().characterCount()

    def _on_file_done(self, name, out_path):
        self._overall_progress_bar.setValue(self._current_file_index)
        # Replace the live per-segment dump with the actual saved transcript,
        # so a diarization pass (auto or the separate "Identify speakers" run)
        # is reflected here with speaker names instead of the plain text
        # that was streamed in during recognition.
        self._show_saved_transcript(out_path)
        self._current_file_body_pos = None
        self._transcript_view.append(tr("Saved: {path}", path=out_path))

    def _show_saved_transcript(self, out_path):
        if self._current_file_body_pos is None:
            return
        try:
            text = Path(out_path).read_text(encoding="utf-8")
        except OSError:
            return
        cursor = self._transcript_view.textCursor()
        cursor.setPosition(self._current_file_body_pos)
        cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText(text)

    def _on_file_failed(self, name, message):
        self._overall_progress_bar.setValue(self._current_file_index)
        self._current_file_body_pos = None
        self._transcript_view.append(f"{name}: {message}")

    def _on_finished(self):
        self._reset_run_state()
        self._status_label.setText(tr("Done."))
        self._refresh_file_list()

    def _on_cancelled(self):
        self._reset_run_state()
        self._status_label.setText(tr("Cancelled."))

    def _reset_run_state(self):
        core.set_sleep_prevention(False)
        self._transcribe_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._worker = None
        self._refresh_diarize_button()

    def closeEvent(self, event):
        if self._recording_controller is not None:
            ans = QMessageBox.question(
                self, tr("Recording in progress"), tr("A recording is still running. Stop it and quit?"))
            if ans != QMessageBox.Yes:
                event.ignore()
                return
            self._recording_controller.stop()
        if self._worker is not None:
            ans = QMessageBox.question(
                self, tr("Transcription running"), tr("A transcription is still running. Cancel it and quit?"))
            if ans != QMessageBox.Yes:
                event.ignore()
                return
            self._worker.terminate()
        event.accept()
