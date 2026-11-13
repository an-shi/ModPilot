from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from ..main_window import MainWindow


class BasePage(QWidget):
    title = "页面"
    subtitle = ""

    def __init__(self, controller: MainWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.services = controller.services
        self.tasks = controller.tasks
        self._loaded = False
        self.game_id: int | None = None

    @property
    def current_game(self):  # type: ignore[no-untyped-def]
        return self.controller.current_game

    def activate(self) -> None:
        if not self._loaded:
            self._loaded = True
            self.refresh()
        else:
            self.refresh()

    def set_game(self, game) -> None:  # type: ignore[no-untyped-def]
        self.game_id = game.id if game else None
        if self._loaded and self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        raise NotImplementedError

    def on_data_changed(self) -> None:
        if self._loaded and self.isVisible():
            self.refresh()

    def toast(self, message: str, tone: str = "accent") -> None:
        self.controller.toast(message, tone)

    def error(self, message: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "操作失败", message)

    def info(self, message: str, title: str = "提示") -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(self, title, message)

    def confirm(self, title: str, message: str, *, danger: bool = False) -> bool:
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
        yes = box.addButton("确认", QMessageBox.AcceptRole)
        cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is yes

    def require_game(self) -> int | None:
        if self.game_id is None:
            self.error("请先在“游戏管理”中添加并选择一个游戏。")
            return None
        return self.game_id
