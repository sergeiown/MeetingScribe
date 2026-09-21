"""Modal dialogs: naming an unidentified speaker."""

import core

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QRadioButton, QButtonGroup, QDialogButtonBox, QTextEdit,
)

from .i18n import tr


class SpeakerNameDialog(QDialog):
    """Ask what to do with one unidentified speaker: name them (optionally
    overwriting or versioning an existing entry). No skip - the field starts
    pre-filled with an auto-generated name, and the user either keeps it or
    replaces it with their own; Save is the only way out. Sets .choice to a
    core.SpeakerNameChoice before closing."""

    def __init__(self, label, samples, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Unidentified speaker"))
        self.setModal(True)
        self._label = label
        self.choice = None
        self._existing_path = None
        self._default_name = core.base_speaker_name(label)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{label}</b>"))

        samples_text = QTextEdit()
        samples_text.setReadOnly(True)
        samples_text.setMaximumHeight(120)
        if samples:
            lines = [f'{core.format_time(ts)}  "{txt[:80]}"' for ts, txt in samples]
        else:
            lines = [tr("(no text samples)")]
        samples_text.setPlainText("\n".join(lines))
        layout.addWidget(samples_text)

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
