"""Application-wide stylesheet, generated for whichever theme (light/dark)
the user picked (or, by default, whatever matches the OS setting)."""

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

_THEME_KEY = "theme"  # "auto" | "light" | "dark", stored via QSettings
_SPLASH_KEY = "show_splash"
_PREVENT_SLEEP_KEY = "prevent_sleep"
_CHECK_ICON = str((Path(__file__).parent / "assets" / "check.svg").resolve()).replace("\\", "/")


def get_theme_preference() -> str:
    return QSettings("MeetingScribe", "MeetingScribe").value(_THEME_KEY, "auto")


def set_theme_preference(value: str) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_THEME_KEY, value)


def get_show_splash_preference() -> bool:
    return QSettings("MeetingScribe", "MeetingScribe").value(_SPLASH_KEY, True, type=bool)


def set_show_splash_preference(value: bool) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_SPLASH_KEY, value)


def get_prevent_sleep_preference() -> bool:
    return QSettings("MeetingScribe", "MeetingScribe").value(_PREVENT_SLEEP_KEY, True, type=bool)


def set_prevent_sleep_preference(value: bool) -> None:
    QSettings("MeetingScribe", "MeetingScribe").setValue(_PREVENT_SLEEP_KEY, value)


def _system_is_dark() -> bool:
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    # Unknown (older Qt/OS): fall back to the palette's own brightness.
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def resolve_dark_mode() -> bool:
    pref = get_theme_preference()
    if pref == "dark":
        return True
    if pref == "light":
        return False
    return _system_is_dark()


def apply_theme() -> None:
    """Re-applies the stylesheet for the current theme; safe to call any
    time, e.g. after a Settings change or a live OS theme switch."""
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(build_stylesheet(dark=resolve_dark_mode()))


def _colors(dark: bool) -> dict:
    if dark:
        return dict(
            bg="#1e1f22", card="#2b2d31", row_bg="#313338", border="#3f4147", text="#e3e5e8",
            hint="#9a9ca3", accent="#4c8dff", accent_border="#3d75d6", accent_text="#ffffff",
            accent_disabled="#37507e", accent_disabled_text="#9fb3d9",
            btn_bg="#3a3c42", btn_hover="#45474e", btn_pressed="#4d4f57",
            btn_disabled_bg="#2f3136", btn_disabled_text="#6b6d73",
            input_bg="#2b2d31", input_disabled_bg="#26272b",
            selection_bg="#3a5a9b", selection_text="#ffffff", hover_bg="#33353a",
            checkbox_unchecked_border="#5a5d64",
        )
    return dict(
        bg="#f3f4f6", card="#ffffff", row_bg="#f7f8fa", border="#dde1e6", text="#2e3440",
        hint="#6b7280", accent="#2f6fed", accent_border="#2a63d4", accent_text="#ffffff",
        accent_disabled="#b7c8f2", accent_disabled_text="#eef2fc",
        btn_bg="#eceff2", btn_hover="#e2e6ec", btn_pressed="#d6dae1",
        btn_disabled_bg="#f1f2f4", btn_disabled_text="#a5a9b0",
        input_bg="#ffffff", input_disabled_bg="#f5f6f7",
        selection_bg="#cfe0fb", selection_text="#12305c", hover_bg="#f3f6fb",
        checkbox_unchecked_border="#c7cbd1",
    )


def build_stylesheet(dark: bool) -> str:
    c = _colors(dark)
    return f"""
* {{
    font-family: "Segoe UI", sans-serif;
    font-size: 10pt;
    color: {c['text']};
}}

QMainWindow, QDialog {{
    background: {c['bg']};
}}

QToolBar {{
    background: {c['card']};
    border: none;
    border-bottom: 1px solid {c['border']};
    spacing: 6px;
    padding: 4px 6px;
}}

QGroupBox {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    margin-top: 8px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: -1px;
    padding: 0 6px;
    color: {c['hint']};
    font-size: 8.5pt;
    font-weight: 500;
    background: {c['bg']};
}}
QGroupBox QLabel, QGroupBox QCheckBox, QGroupBox QRadioButton {{
    font-weight: 400;
}}

QLabel {{ background: transparent; }}
QLabel[hint="true"] {{ color: {c['hint']}; font-weight: 400; }}

QFrame#modelRow {{
    background: {c['row_bg']};
    border: 1px solid {c['border']};
    border-radius: 6px;
}}

QPushButton {{
    background: {c['btn_bg']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 8px 18px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {c['btn_hover']}; }}
QPushButton:pressed {{ background: {c['btn_pressed']}; }}
QPushButton:disabled {{ color: {c['btn_disabled_text']}; background: {c['btn_disabled_bg']}; border-color: {c['border']}; }}

QPushButton#primaryButton {{
    background: {c['accent']};
    border: 1px solid {c['accent_border']};
    color: {c['accent_text']};
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background: {c['accent_border']}; }}
QPushButton#primaryButton:disabled {{ background: {c['accent_disabled']}; border-color: {c['accent_disabled']}; color: {c['accent_disabled_text']}; }}

QTableWidget, QTreeWidget, QTextEdit {{
    background: {c['input_bg']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 4px;
    gridline-color: transparent;
    alternate-background-color: transparent;
}}
QHeaderView::section {{
    background: {c['card']};
    color: {c['hint']};
    border: none;
    border-bottom: 1px solid {c['border']};
    padding: 6px 8px;
    font-weight: 600;
}}

QComboBox, QSpinBox, QLineEdit {{
    background: {c['input_bg']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 20px;
}}
QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled {{
    color: {c['btn_disabled_text']};
    background: {c['input_disabled_bg']};
}}

QProgressBar {{
    border: 1px solid {c['border']};
    border-radius: 6px;
    background: {c['input_bg']};
    text-align: center;
    min-height: 18px;
}}
QProgressBar::chunk {{
    background: {c['accent']};
    border-radius: 5px;
}}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}

QCheckBox::indicator:unchecked {{
    border: 1px solid {c['checkbox_unchecked_border']};
    border-radius: 4px;
    background: {c['input_bg']};
}}
QCheckBox::indicator:checked {{
    border: 1px solid {c['accent_border']};
    border-radius: 4px;
    background: {c['accent']};
    image: url({_CHECK_ICON});
}}
QCheckBox::indicator:disabled {{
    border-color: {c['border']};
    background: {c['input_disabled_bg']};
}}

QRadioButton::indicator {{ border-radius: 8px; }}
QRadioButton::indicator:unchecked {{
    border: 1px solid {c['checkbox_unchecked_border']};
    border-radius: 8px;
    background: {c['input_bg']};
}}
QRadioButton::indicator:checked {{
    border: 1px solid {c['accent_border']};
    border-radius: 8px;
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {c['accent']}, stop:0.45 {c['accent']}, stop:0.55 {c['input_bg']}, stop:1 {c['input_bg']});
}}
QRadioButton::indicator:disabled {{
    border-color: {c['border']};
    background: {c['input_disabled_bg']};
}}

QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {c['hint']};
    width: 0; height: 0;
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    border: 1px solid {c['border']};
    border-radius: 6px;
    background: {c['card']};
    selection-background-color: {c['selection_bg']};
    selection-color: {c['selection_text']};
    outline: none;
    padding: 4px;
}}

QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border: none; background: transparent; }}
QSpinBox::up-arrow {{
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {c['hint']}; width: 0; height: 0;
}}
QSpinBox::down-arrow {{
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {c['hint']}; width: 0; height: 0;
}}

QTableWidget::item, QTreeWidget::item {{
    padding: 6px 8px;
    border-radius: 4px;
}}
QTableWidget::item:hover, QTreeWidget::item:hover {{
    background: {c['hover_bg']};
}}
QTableWidget::item:selected, QTreeWidget::item:selected {{
    background: {c['selection_bg']};
    color: {c['selection_text']};
}}
QTableWidget, QTreeWidget {{
    outline: none;
    selection-background-color: {c['selection_bg']};
    selection-color: {c['selection_text']};
}}
QTableWidget::item:focus, QTreeWidget::item:focus {{
    outline: none;
    border: none;
    background: {c['selection_bg']};
    color: {c['selection_text']};
}}

QTabWidget::pane {{
    border: 1px solid {c['border']};
    border-radius: 8px;
    background: {c['card']};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    padding: 8px 16px;
    color: {c['hint']};
    font-weight: 500;
}}
QTabBar::tab:selected {{
    color: {c['accent']};
    border-bottom: 2px solid {c['accent']};
}}
"""
