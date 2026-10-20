"""Modal dialogs: naming an unidentified speaker."""

from pathlib import Path

import core

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QRadioButton, QButtonGroup, QDialogButtonBox, QTextEdit,
)

from .i18n import tr

# A little before the segment starts and after it ends, so the excerpt isn't
# clipped mid-word by timestamps that are only approximate.
_LEAD_IN_S = 0.3
_TAIL_S = 0.3


class _SampleView(QTextEdit):
    """Read-only list of a speaker's sample lines, one paragraph per sample;
    emits the clicked line's index (and can highlight the one being played)."""

    line_clicked = Signal(int)

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self._clickable = False

    def set_clickable(self, clickable):
        self._clickable = clickable
        self.viewport().setCursor(Qt.PointingHandCursor if clickable else Qt.IBeamCursor)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if not self._clickable or event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        # cursorForPosition() maps a click below the last line onto that
        # last line, which would silently trigger the wrong sample.
        content_height = self.document().documentLayout().documentSize().height()
        if pos.y() + self.verticalScrollBar().value() > content_height:
            return
        self.line_clicked.emit(self.cursorForPosition(pos).blockNumber())

    def highlight_line(self, index):
        selections = []
        block = self.document().findBlockByNumber(index) if index is not None else None
        if block is not None and block.isValid():
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(QColor(61, 120, 220, 80))
            selection.format.setProperty(QTextFormat.FullWidthSelection, True)
            selections.append(selection)
        self.setExtraSelections(selections)


class SpeakerNameDialog(QDialog):
    """Ask what to do with one unidentified speaker: name them (optionally
    overwriting or versioning an existing entry). No skip - the field starts
    pre-filled with an auto-generated name, and the user either keeps it or
    replaces it with their own; Save is the only way out. Sets .choice to a
    core.SpeakerNameChoice before closing.

    samples: [(start_s, end_s, text), ...]. index/total: which unnamed
    speaker of the file this is. audio_path, if given, lets the user click a
    sample line to hear that excerpt (after a confirmation)."""

    def __init__(self, label, samples, index=1, total=1, audio_path=None, on_before_play=None):
        # Deliberately parentless: a dialog owned by the main window always
        # stays on top of it on Windows and can't be minimized separately,
        # which covered the file list and the Play controls the user needs
        # to listen to the audio this speaker came from. As its own
        # top-level window it gets a taskbar button and a minimize button,
        # and clicking the main window brings that one to the front.
        super().__init__(None)
        self.setWindowFlags(
            Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)
        progress = tr("({index} of {total})", index=index, total=total)
        self.setWindowTitle(f"{tr('Unidentified speaker')} {progress}")
        self.setMinimumWidth(480)
        # Not modal - the user needs to be able to click back into the main
        # window and use Play to listen to the file this speaker came from
        # while deciding on a name, without this dialog blocking that input.
        # exec() below still blocks the calling code (the worker thread's
        # decide() call is waiting on it via a queued signal) regardless of
        # modality - modality only ever controls whether OTHER windows can
        # receive input while this one is open, not whether exec() blocks.
        self.setModal(False)
        self._label = label
        self.choice = None
        self._existing_path = None
        self._default_name = core.base_speaker_name(label)
        self._samples = list(samples)
        self._on_before_play = on_before_play

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{label}</b> {progress}"))

        self._view = _SampleView()
        self._view.setMaximumHeight(180)
        if self._samples:
            lines = [f'{core.format_time(start)}  "{txt[:80]}"' for start, _end, txt in self._samples]
        else:
            lines = [tr("(no text samples)")]
        self._view.setPlainText("\n".join(lines))
        layout.addWidget(self._view)

        self._player = None
        self._awaiting_load = False
        self._start_ms = 0
        self._stop_ms = 0
        audio_path = Path(audio_path) if audio_path else None
        if audio_path is not None and audio_path.exists() and self._samples:
            self._audio_path = audio_path
            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_output)
            self._player.mediaStatusChanged.connect(self._on_media_status)
            self._player.positionChanged.connect(self._on_position)
            self._player.playbackStateChanged.connect(self._on_playback_state)
            self._view.line_clicked.connect(self._on_line_clicked)
            self._view.set_clickable(True)
            self._view.setToolTip(tr("Click a line to listen to that part of the recording."))

        layout.addWidget(QLabel(tr("Name:")))
        self._name_edit = QLineEdit(self._default_name)
        self._name_edit.selectAll()
        self._name_edit.textChanged.connect(self._on_name_changed)
        layout.addWidget(self._name_edit)

        self._existing_label = QLabel()
        self._existing_label.setWordWrap(True)
        layout.addWidget(self._existing_label)

        action_row = QHBoxLayout()
        self._action_group = QButtonGroup(self)
        self._radio_overwrite = QRadioButton(tr("Overwrite"))
        self._radio_add_version = QRadioButton(tr("Add new version"))
        self._radio_add_version.setChecked(True)
        for rb in (self._radio_overwrite, self._radio_add_version):
            self._action_group.addButton(rb)
            action_row.addWidget(rb)
        layout.addLayout(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        self._ok_btn = buttons.button(QDialogButtonBox.Ok)
        self._ok_btn.setText(tr("Save"))
        buttons.accepted.connect(self._on_accept)
        layout.addWidget(buttons)

        self._on_name_changed(self._default_name)

    # --- excerpt playback ---------------------------------------------

    def _on_line_clicked(self, index):
        if self._player is None or not (0 <= index < len(self._samples)):
            return
        start, end, _txt = self._samples[index]
        answer = QMessageBox.question(
            self, tr("Listen to excerpt"),
            tr("Listen to this excerpt ({time})?", time=core.format_time(start)))
        if answer != QMessageBox.Yes:
            return
        if self._on_before_play is not None:
            self._on_before_play()
        self._start_ms = max(0, int((start - _LEAD_IN_S) * 1000))
        self._stop_ms = int((end + _TAIL_S) * 1000)
        self._view.highlight_line(index)
        url = QUrl.fromLocalFile(str(self._audio_path))
        loaded = (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia, QMediaPlayer.EndOfMedia)
        if self._player.source() == url and self._player.mediaStatus() in loaded:
            self._begin_playback()
        else:
            self._awaiting_load = True
            self._player.setSource(url)

    def _on_media_status(self, status):
        # Seeking before the media has loaded gets ignored, so the actual
        # seek + play waits for it.
        if self._awaiting_load and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self._awaiting_load = False
            self._begin_playback()

    def _begin_playback(self):
        self._player.setPosition(self._start_ms)
        self._player.play()

    def _on_position(self, position_ms):
        if self._player.playbackState() == QMediaPlayer.PlayingState and position_ms >= self._stop_ms:
            self._player.pause()

    def _on_playback_state(self, state):
        if state != QMediaPlayer.PlayingState:
            self._view.highlight_line(None)

    def done(self, result):
        # Releases the OS-level handle on the audio file.
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        super().done(result)

    # --- naming -------------------------------------------------------

    def _set_version_controls_visible(self, visible):
        self._existing_label.setVisible(visible)
        self._radio_overwrite.setVisible(visible)
        self._radio_add_version.setVisible(visible)

    def _on_name_changed(self, text):
        name = core.base_speaker_name(text.strip())
        self._ok_btn.setEnabled(bool(name))
        existing = core.latest_versioned_file(name) if name else None
        self._existing_path = existing
        if existing:
            self._existing_label.setText(tr('"{name}" already in database ({file}).', name=name, file=existing.name))
        self._set_version_controls_visible(existing is not None)

    def _on_accept(self):
        name = core.base_speaker_name(self._name_edit.text().strip()) or self._default_name
        if self._existing_path is not None:
            action = "overwrite" if self._radio_overwrite.isChecked() else "add_version"
            target = self._existing_path if action == "overwrite" else None
            self.choice = core.SpeakerNameChoice(self._label, name, action, target)
        else:
            self.choice = core.SpeakerNameChoice(self._label, name, "new")
        self.accept()

    def reject(self):
        # X/Escape - route through the same no-skip path as Save, using
        # whatever name (default or edited) is currently in the field.
        self._on_accept()
