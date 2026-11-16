from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

THEMES = {
    "dark": {
        "bg": "#0E1116",
        "sidebar": "#11151C",
        "surface": "#171C24",
        "surface2": "#1D2430",
        "surface3": "#232C3A",
        "border": "#2A3341",
        "border_soft": "#202836",
        "text": "#E7EBF3",
        "text_strong": "#FFFFFF",
        "muted": "#8B96A9",
        "subtle": "#5F6B7E",
        "accent": "#5B9DFF",
        "accent_hover": "#76ADFF",
        "accent_soft": "#1B2F4D",
        "accent_text": "#0B1220",
        "success": "#3DD68C",
        "success_soft": "#163527",
        "warning": "#F3B84B",
        "warning_soft": "#332714",
        "danger": "#FF6F7E",
        "danger_soft": "#3B1C23",
        "info": "#68C7E8",
        "info_soft": "#15303A",
        "selection": "#20334F",
        "shadow": "rgba(0, 0, 0, 0.35)",
    },
    "light": {
        "bg": "#F3F5F9",
        "sidebar": "#FFFFFF",
        "surface": "#FFFFFF",
        "surface2": "#F6F8FC",
        "surface3": "#EDF1F7",
        "border": "#D9E0EA",
        "border_soft": "#E8EDF4",
        "text": "#1C2432",
        "text_strong": "#0B1220",
        "muted": "#657186",
        "subtle": "#8B97AA",
        "accent": "#2563EB",
        "accent_hover": "#1D4ED8",
        "accent_soft": "#E7EFFF",
        "accent_text": "#FFFFFF",
        "success": "#0F9F6E",
        "success_soft": "#E3F8EF",
        "warning": "#B7791F",
        "warning_soft": "#FFF4DD",
        "danger": "#D64552",
        "danger_soft": "#FDECEE",
        "info": "#0E7490",
        "info_soft": "#E3F6FA",
        "selection": "#DCE8FF",
        "shadow": "rgba(18, 32, 56, 0.10)",
    },
}

_QSS = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI";
    font-size: 13px;
    color: @text;
    outline: none;
}
QMainWindow, QWidget#AppRoot, QStackedWidget#Pages, QWidget#Page {
    background: @bg;
}
QToolTip {
    background: @surface3;
    color: @text;
    border: 1px solid @border;
    padding: 6px 8px;
    border-radius: 6px;
}
QFrame#Sidebar {
    background: @sidebar;
    border-right: 1px solid @border_soft;
}
QLabel#Brand {
    font-size: 20px;
    font-weight: 700;
    color: @text_strong;
    letter-spacing: 0.4px;
}
QLabel#BrandSub {
    color: @subtle;
    font-size: 11px;
}
QLabel#NavSection {
    color: @subtle;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.2px;
    padding: 12px 16px 4px 16px;
}
QPushButton#NavButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    color: @muted;
    text-align: left;
    padding-left: 12px;
    padding-right: 34px;
    min-height: 38px;
    font-weight: 500;
}
QPushButton#NavButton:hover {
    background: @surface2;
    color: @text;
}
QPushButton#NavButton:checked {
    background: @accent_soft;
    color: @accent;
    font-weight: 600;
}
QPushButton#NavButton:checked:hover {
    background: @accent_soft;
}
QLabel#NavBadge {
    background: @accent;
    color: #FFFFFF;
    border-radius: 9px;
    padding: 0px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#SidebarVersion {
    color: @subtle;
    font-size: 11px;
}
QFrame#Header {
    background: @bg;
    border-bottom: 1px solid @border_soft;
}
QLabel#PageTitle {
    font-size: 22px;
    font-weight: 700;
    color: @text_strong;
}
QLabel#PageSubtitle {
    color: @muted;
    font-size: 12px;
}
QLabel#SectionTitle {
    font-size: 14px;
    font-weight: 650;
    color: @text_strong;
}
QLabel#Muted, QLabel[role="muted"] {
    color: @muted;
}
QLabel#Subtle, QLabel[role="subtle"] {
    color: @subtle;
    font-size: 11px;
}
QLabel#StatValue {
    font-size: 27px;
    font-weight: 700;
    color: @text_strong;
}
QLabel#StatLabel {
    color: @muted;
    font-size: 12px;
}
QLabel#StatHint {
    color: @subtle;
    font-size: 11px;
}
QLabel#EmptyTitle {
    font-size: 16px;
    font-weight: 650;
    color: @text_strong;
}
QLabel#EmptyText {
    color: @muted;
    font-size: 12px;
}
QFrame#Card {
    background: @surface;
    border: 1px solid @border_soft;
    border-radius: 12px;
}
QFrame#Card:hover {
    border-color: @border;
}
QFrame#CardFlat {
    background: @surface2;
    border: 1px solid @border_soft;
    border-radius: 10px;
}
QFrame#Toolbar {
    background: transparent;
}
QFrame#Divider {
    background: @border_soft;
    max-height: 1px;
    min-height: 1px;
}
QLabel#Badge {
    border-radius: 8px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}
QLabel#Badge[tone="success"] { background: @success_soft; color: @success; }
QLabel#Badge[tone="warning"] { background: @warning_soft; color: @warning; }
QLabel#Badge[tone="danger"] { background: @danger_soft; color: @danger; }
QLabel#Badge[tone="info"] { background: @info_soft; color: @info; }
QLabel#Badge[tone="accent"] { background: @accent_soft; color: @accent; }
QLabel#Badge[tone="muted"] { background: @surface2; color: @muted; }
QPushButton {
    background: @surface2;
    border: 1px solid @border;
    border-radius: 8px;
    padding: 0 14px;
    min-height: 34px;
    color: @text;
    font-weight: 500;
}
QPushButton:hover {
    background: @surface3;
    border-color: @border;
}
QPushButton:pressed {
    background: @surface;
}
QPushButton:disabled {
    color: @subtle;
    background: @surface2;
    border-color: @border_soft;
}
QPushButton[variant="primary"] {
    background: @accent;
    color: @accent_text;
    border: 1px solid @accent;
    font-weight: 600;
}
QPushButton[variant="primary"]:hover {
    background: @accent_hover;
    border-color: @accent_hover;
}
QPushButton[variant="primary"]:disabled {
    background: @surface3;
    color: @subtle;
    border-color: @border_soft;
}
QPushButton[variant="ghost"] {
    background: transparent;
    border-color: transparent;
    color: @muted;
}
QPushButton[variant="ghost"]:hover {
    background: @surface2;
    color: @text;
}
QPushButton[variant="danger"] {
    background: @danger_soft;
    color: @danger;
    border-color: transparent;
}
QPushButton[variant="danger"]:hover {
    background: @danger;
    color: #FFFFFF;
}
QPushButton[variant="tool"] {
    min-height: 30px;
    padding: 0 10px;
    background: transparent;
    border-color: transparent;
    color: @muted;
}
QPushButton[variant="tool"]:hover {
    background: @surface2;
    color: @text;
}
QPushButton:checked {
    background: @accent_soft;
    color: @accent;
}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
    background: @surface2;
    border: 1px solid @border;
    border-radius: 8px;
    padding: 0 10px;
    min-height: 34px;
    selection-background-color: @selection;
}
QComboBox {
    background: @surface2;
    border: 1px solid @border;
    border-radius: 8px;
    padding-left: 10px;
    padding-right: 26px;
    min-height: 34px;
}
QTextEdit, QPlainTextEdit {
    padding: 8px 10px;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: @accent;
    background: @surface;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    color: @subtle;
}
QLineEdit#GlobalSearch {
    background: @surface;
    border: 1px solid @border_soft;
    border-radius: 9px;
    min-height: 34px;
    padding-left: 32px;
    min-width: 220px;
}
QLineEdit#GlobalSearch:focus {
    border-color: @accent;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: @surface;
    border: 1px solid @border;
    border-radius: 8px;
    selection-background-color: @selection;
    padding: 4px;
    outline: none;
}
QCheckBox, QRadioButton {
    spacing: 8px;
    color: @text;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
}
QTableWidget, QTableView, QTreeWidget, QListWidget {
    background: transparent;
    border: none;
    gridline-color: transparent;
    selection-background-color: @selection;
    selection-color: @text;
    alternate-background-color: @surface2;
}
QTableWidget::item, QTableView::item {
    padding: 6px 8px;
    border-bottom: 1px solid @border_soft;
}
QTableWidget::item:selected, QTableView::item:selected {
    background: @selection;
    color: @text;
}
QHeaderView::section {
    background: @surface;
    color: @muted;
    border: none;
    border-bottom: 1px solid @border;
    padding: 8px;
    font-size: 11px;
    font-weight: 600;
}
QTableCornerButton::section { background: @surface; border: none; }
QListWidget::item {
    padding: 9px 10px;
    border-radius: 8px;
    margin: 1px 0;
}
QListWidget::item:hover { background: @surface2; }
QListWidget::item:selected { background: @selection; color: @text; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab {
    background: transparent;
    color: @muted;
    padding: 8px 14px;
    margin-right: 4px;
    border-radius: 7px;
    font-weight: 500;
}
QTabBar::tab:hover { background: @surface2; color: @text; }
QTabBar::tab:selected { background: @surface2; color: @accent; font-weight: 600; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: @border;
    min-height: 30px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover { background: @subtle; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
}
QScrollBar::handle:horizontal {
    background: @border;
    min-width: 30px;
    border-radius: 5px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QSplitter::handle { background: transparent; width: 8px; }
QProgressBar {
    background: @surface3;
    border: none;
    border-radius: 4px;
    min-height: 7px;
    max-height: 7px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: @accent;
    border-radius: 4px;
}
QStatusBar {
    background: @sidebar;
    border-top: 1px solid @border_soft;
    color: @muted;
}
QStatusBar::item { border: none; }
QMenu {
    background: @surface;
    border: 1px solid @border;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item { padding: 7px 28px 7px 12px; border-radius: 5px; }
QMenu::item:selected { background: @surface3; }
QMessageBox, QDialog {
    background: @bg;
}
QMessageBox QLabel { color: @text; }
QDialog QLabel#DialogTitle {
    font-size: 18px;
    font-weight: 700;
}
QCalendarWidget QWidget { background: @surface; }
QGroupBox {
    border: 1px solid @border_soft;
    border-radius: 10px;
    margin-top: 12px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: @muted;
}
QSlider::groove:horizontal { height: 4px; background: @surface3; border-radius: 2px; }
QSlider::handle:horizontal { background: @accent; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
"""


def theme_colors(theme: str) -> dict[str, str]:
    return THEMES.get(theme, THEMES["dark"])


def build_stylesheet(theme: str) -> str:
    import re

    colors = theme_colors(theme)
    # Replace exact tokens. A naive str.replace would turn @surface2 into
    # "#171C242" because @surface is a prefix of @surface2.
    return re.sub(
        r"@([a-z0-9_]+)",
        lambda match: colors.get(match.group(1), match.group(0)),
        _QSS,
    )


def apply_theme(app: QApplication, theme: str) -> None:
    app.setStyleSheet(build_stylesheet(theme))


def repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
