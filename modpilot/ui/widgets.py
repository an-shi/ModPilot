from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .icons import icon
from .theme import theme_colors
from ..i18n import tr


def current_theme() -> str:
    app = QApplication.instance()
    theme = app.property("theme") if app else None
    return str(theme or "dark")


def text_color() -> str:
    return theme_colors(current_theme())["text"]


def muted_color() -> str:
    return theme_colors(current_theme())["muted"]


def make_icon(name: str, color: str | None = None, size: int = 18):
    return icon(name, color or text_color(), size)


def make_button(
    text: str,
    *,
    icon_name: str | None = None,
    variant: str | None = None,
    on_click: Callable[[], Any] | None = None,
    tooltip: str | None = None,
    icon_color: str | None = None,
) -> QPushButton:
    button = QPushButton(text)
    if icon_name:
        button.setIcon(make_icon(icon_name, icon_color or text_color(), 17))
    if variant:
        button.setProperty("variant", variant)
    button.setCursor(Qt.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    if on_click:
        button.clicked.connect(lambda _checked=False: on_click())
    return button


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, *, flat: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("CardFlat" if flat else "Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(18, 16, 18, 16)
        self._layout.setSpacing(12)

    def layout(self) -> QVBoxLayout:  # type: ignore[override]
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0) -> None:
        self._layout.addWidget(widget, stretch)

    def add_layout(self, layout, stretch: int = 0) -> None:  # type: ignore[no-untyped-def]
        self._layout.addLayout(layout, stretch)


class PageHeader(QWidget):
    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("PageSubtitle")
        self.subtitle_label.setWordWrap(True)
        texts.addWidget(self.title_label)
        if subtitle:
            texts.addWidget(self.subtitle_label)
        outer.addLayout(texts, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        outer.addLayout(self.actions)

    def add_action(self, button: QPushButton) -> None:
        self.actions.addWidget(button)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))


class SectionHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        texts.addWidget(label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Subtle")
            texts.addWidget(sub)
        outer.addLayout(texts, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        outer.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)


class Badge(QLabel):
    def __init__(self, text: str = "", tone: str = "muted", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Badge")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        from .theme import repolish

        repolish(self)

    def set_status(self, text: str, tone: str) -> None:
        self.setText(text)
        self.set_tone(tone)


class StatCard(Card):
    def __init__(
        self,
        label: str,
        value: str = "—",
        hint: str = "",
        icon_name: str = "info",
        tone: str = "accent",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumHeight(112)
        colors = theme_colors(current_theme())
        row = QHBoxLayout()
        row.setSpacing(12)
        icon_label = QLabel()
        icon_label.setFixedSize(40, 40)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setPixmap(make_icon(icon_name, colors.get(tone, colors["accent"]), 22).pixmap(22, 22))
        icon_label.setStyleSheet(
            f"background: {colors.get(tone + '_soft', colors['surface2'])}; border-radius: 10px;"
        )
        row.addWidget(icon_label, 0, Qt.AlignTop)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("StatValue")
        self.label_label = QLabel(label)
        self.label_label.setObjectName("StatLabel")
        text_layout.addWidget(self.value_label)
        text_layout.addWidget(self.label_label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("StatHint")
            hint_label.setWordWrap(True)
            text_layout.addWidget(hint_label)
        row.addLayout(text_layout, 1)
        self.add_layout(row)

    def set_value(self, value: str | int) -> None:
        self.value_label.setText(str(value))


class EmptyState(QWidget):
    def __init__(
        self,
        title: str,
        text: str = "",
        icon_name: str = "package",
        action: QPushButton | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 40, 24, 40)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignCenter)
        icon_label = QLabel()
        icon_label.setPixmap(make_icon(icon_name, muted_color(), 42).pixmap(42, 42))
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)
        title_label = QLabel(title)
        title_label.setObjectName("EmptyTitle")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        if text:
            text_label = QLabel(text)
            text_label.setObjectName("EmptyText")
            text_label.setAlignment(Qt.AlignCenter)
            text_label.setWordWrap(True)
            text_label.setMaximumWidth(520)
            layout.addWidget(text_label, 0, Qt.AlignCenter)
        if action:
            layout.addWidget(action, 0, Qt.AlignCenter)


class ClickableCard(Card):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Toast(QFrame):
    def __init__(self, parent: QWidget, message: str, tone: str = "accent") -> None:
        super().__init__(parent)
        colors = theme_colors(current_theme())
        self.setObjectName("Card")
        self.setStyleSheet(
            f"QFrame#Card {{ background: {colors['surface3']}; border: 1px solid {colors.get(tone, colors['accent'])};"
            f" border-radius: 10px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        icon_label = QLabel()
        icon_name = {"danger": "alert", "warning": "alert", "success": "check"}.get(tone, "info")
        icon_label.setPixmap(make_icon(icon_name, colors.get(tone, colors["accent"]), 18).pixmap(18, 18))
        layout.addWidget(icon_label)
        label = QLabel(message)
        label.setWordWrap(True)
        label.setMaximumWidth(420)
        layout.addWidget(label, 1)
        self.adjustSize()
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        QTimer.singleShot(3800, self._hide)

    def show_at(self, position: QPoint) -> None:
        self.move(position)
        self.show()
        self.raise_()
        self._fade.start()

    def _hide(self) -> None:
        if not self.isVisible():
            return
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.deleteLater)
        self._fade.start()


def show_toast(parent: QWidget, message: str, tone: str = "accent") -> None:
    window = parent.window()
    toast = Toast(window, message, tone)
    margin = 24
    toast.show_at(QPoint(window.width() - toast.width() - margin, window.height() - toast.height() - 58))
    # Reposition after layout settles and on resize during its short lifetime.
    QTimer.singleShot(
        10,
        lambda: toast.move(
            window.width() - toast.width() - margin,
            window.height() - toast.height() - 58,
        )
        if toast.isVisible()
        else None,
    )


def setup_table(
    table: QTableWidget,
    headers: list[str],
    *,
    stretch_column: int | None = None,
    row_height: int = 38,
) -> None:
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setShowGrid(False)
    table.setAlternatingRowColors(False)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.ExtendedSelection)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setWordWrap(False)
    table.setSortingEnabled(False)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    for index in range(len(headers)):
        header.setSectionResizeMode(index, header.ResizeMode.ResizeToContents)
    if stretch_column is not None:
        header.setSectionResizeMode(stretch_column, header.ResizeMode.Stretch)
    table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
    table.setVerticalScrollMode(QTableWidget.ScrollPerPixel)


def cell(text: str, *, data: Any = None, tooltip: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if data is not None:
        item.setData(Qt.UserRole, data)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def make_scroll_content(parent: QWidget) -> tuple[QWidget, QVBoxLayout]:
    from PySide6.QtWidgets import QScrollArea

    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 6, 0)
    layout.setSpacing(16)
    scroll.setWidget(content)
    # Return a widget whose only child is the scroll area for easy page layout.
    wrapper = QWidget(parent)
    wrapper_layout = QVBoxLayout(wrapper)
    wrapper_layout.setContentsMargins(0, 0, 0, 0)
    wrapper_layout.addWidget(scroll)
    return wrapper, layout


def set_busy(button: QPushButton, busy: bool, busy_text: str = tr('ui.widgets.processing')) -> None:
    if busy:
        if not button.property("_original_text"):
            button.setProperty("_original_text", button.text())
        button.setEnabled(False)
        button.setText(busy_text)
    else:
        button.setEnabled(True)
        original = button.property("_original_text")
        if original:
            button.setText(str(original))


def clear_table(table: QTableWidget) -> None:
    table.setRowCount(0)


def clear_layout(layout) -> None:  # type: ignore[no-untyped-def]
    """Remove widgets immediately so stale widgets cannot overlay new content."""

    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def open_in_explorer(path: str) -> None:
    import os
    import subprocess

    if not path:
        return
    target = os.path.normpath(path)
    if os.path.exists(target):
        if os.path.isdir(target):
            subprocess.Popen(["explorer", target])
        else:
            subprocess.Popen(["explorer", "/select,", target])
