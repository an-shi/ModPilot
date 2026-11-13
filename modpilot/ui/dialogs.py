from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.paths import human_size
from ..domain.models import BackupView, InstallPlan, TranslationCandidate
from .widgets import (
    Card,
    cell,
    make_button,
    make_scroll_content,
    setup_table,
)


class DialogBase(QDialog):
    def __init__(self, parent: QWidget | None, title: str, width: int = 720, height: int = 520) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(width, height)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(20, 18, 20, 18)
        self.layout_.setSpacing(12)
        title_label = QLabel(title)
        title_label.setObjectName("DialogTitle")
        self.layout_.addWidget(title_label)

    def add(self, widget: QWidget, stretch: int = 0) -> None:
        self.layout_.addWidget(widget, stretch)

    def add_layout(self, layout, stretch: int = 0) -> None:  # type: ignore[no-untyped-def]
        self.layout_.addLayout(layout, stretch)

    def add_buttons(self, *buttons: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        for button in buttons:
            row.addWidget(button)
        self.add_layout(row)
        return row


class InstallPreviewDialog(DialogBase):
    def __init__(self, plan: InstallPlan, parent: QWidget | None = None) -> None:
        super().__init__(parent, "确认安装 Mod", 760, 560)
        warn = QLabel(
            "安装前会对被替换的文件夹创建 ZIP 备份。你可以随时在备份管理中恢复。"
        )
        warn.setObjectName("Muted")
        warn.setWordWrap(True)
        self.add(warn)
        scroll_wrapper, content = make_scroll_content(self)
        for entry in plan.entries:
            card = Card(flat=True)
            name = QLabel(f"{entry.metadata.name}  {entry.metadata.version}")
            name.setStyleSheet("font-weight: 650; font-size: 14px;")
            card.add(name)
            meta = QLabel(
                f"UniqueID: {entry.metadata.unique_id}\n目标文件夹: {entry.destination_name}"
            )
            meta.setObjectName("Muted")
            meta.setWordWrap(True)
            card.add(meta)
            for warning in entry.warnings:
                badge = QLabel("⚠ " + warning)
                badge.setWordWrap(True)
                badge.setStyleSheet("color: #F3B84B;")
                card.add(badge)
            content.addWidget(card)
        for warning in plan.warnings:
            label = QLabel("⚠ " + warning)
            label.setWordWrap(True)
            content.addWidget(label)
        content.addStretch(1)
        self.add(scroll_wrapper, 1)
        cancel = make_button("取消", on_click=self.reject)
        install = make_button("备份并安装", icon_name="download", variant="primary", on_click=self.accept)
        self.add_buttons(cancel, install)


class BackupsDialog(DialogBase):
    def __init__(
        self,
        backups: list[BackupView],
        parent: QWidget | None = None,
        *,
        on_restore=None,  # type: ignore[no-untyped-def]
        on_delete=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(parent, "备份管理", 860, 560)
        self._backups = backups
        self._on_restore = on_restore
        self._on_delete = on_delete
        self.table = QTableWidget()
        setup_table(
            self.table,
            ["创建时间", "类型", "标签", "大小", "状态"],
            stretch_column=2,
            row_height=36,
        )
        for backup in backups:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, cell(backup.created_at.strftime("%Y-%m-%d %H:%M"), data=backup.id))
            self.table.setItem(row, 1, cell(backup.kind.label))
            self.table.setItem(row, 2, cell(backup.label, tooltip=backup.note or backup.path))
            self.table.setItem(row, 3, cell(human_size(backup.size_bytes)))
            self.table.setItem(row, 4, cell("可用" if backup.status == "available" else backup.status))
        self.table.doubleClicked.connect(lambda _index: self.restore())
        self.add(self.table, 1)
        if not backups:
            empty = QLabel("暂无备份。安装、更新、卸载或汉化时会自动创建备份。")
            empty.setObjectName("Muted")
            self.add(empty)
        close = make_button("关闭", on_click=self.reject)
        delete = make_button("删除备份", icon_name="trash", variant="danger", on_click=self.delete)
        restore = make_button("恢复选中备份", icon_name="backup", variant="primary", on_click=self.restore)
        delete.setEnabled(bool(backups and on_delete))
        restore.setEnabled(bool(backups and on_restore))
        self.add_buttons(delete, restore, close)

    def _selected(self) -> BackupView | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        if 0 <= index < len(self._backups):
            return self._backups[index]
        return None

    def restore(self) -> None:
        backup = self._selected()
        if backup and self._on_restore:
            self._on_restore(backup)
            self.accept()

    def delete(self) -> None:
        backup = self._selected()
        if backup and self._on_delete:
            self._on_delete(backup)
            self._backups = [item for item in self._backups if item.id != backup.id]
            self.table.removeRow(self.table.currentRow())
            if not self._backups:
                self.reject()


class RawLogDialog(DialogBase):
    def __init__(self, text: str, parent: QWidget | None = None, title: str = "原始日志") -> None:
        super().__init__(parent, title, 980, 700)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        font = QFont("Cascadia Mono")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        editor.setFont(font)
        self.add(editor, 1)
        copy = make_button("复制全部", icon_name="save", on_click=lambda: self._copy(editor))
        close = make_button("关闭", variant="primary", on_click=self.reject)
        self.add_buttons(copy, close)

    @staticmethod
    def _copy(editor: QPlainTextEdit) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(editor.toPlainText())


class TranslationPackDialog(DialogBase):
    def __init__(
        self,
        candidates: list[TranslationCandidate],
        warnings: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, "可用汉化包候选", 980, 640)
        intro = QLabel(
            "候选来源为 Nexus Mods / GitHub 的公开搜索。ModPilot 只根据名称、UniqueID、Nexus ID 与关键词进行匹配，"
            "不会自动断言汉化包完全兼容。请核对版本后打开网页下载，再通过“导入汉化包”应用。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        self.add(intro)
        if warnings:
            warning_label = QLabel("；".join(warnings))
            warning_label.setWordWrap(True)
            warning_label.setStyleSheet("color: #F3B84B;")
            self.add(warning_label)
        self.table = QTableWidget()
        setup_table(
            self.table,
            ["来源", "标题", "兼容性", "作者", "更新时间"],
            stretch_column=1,
            row_height=36,
        )
        for candidate in candidates:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, cell(candidate.source.upper(), data=candidate))
            self.table.setItem(row, 1, cell(candidate.title, tooltip=candidate.description))
            self.table.setItem(row, 2, cell(candidate.compatibility, tooltip="；".join(candidate.match_reasons)))
            self.table.setItem(row, 3, cell(candidate.author))
            self.table.setItem(row, 4, cell(candidate.updated[:19]))
        self.table.doubleClicked.connect(lambda _index: self.open_selected())
        self.add(self.table, 1)
        if not candidates:
            empty = QLabel("没有找到候选汉化包。仍可使用 AI 翻译或导入本地汉化文件。")
            empty.setObjectName("Muted")
            self.add(empty)
        open_button = make_button("打开选中页面", icon_name="external", on_click=self.open_selected)
        open_button.setEnabled(bool(candidates))
        close = make_button("关闭", variant="primary", on_click=self.reject)
        self.add_buttons(open_button, close)

    def _selected_candidate(self) -> TranslationCandidate | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item else None

    def open_selected(self) -> None:
        candidate = self._selected_candidate()
        if candidate and candidate.url:
            QDesktopServices.openUrl(QUrl(candidate.url))
