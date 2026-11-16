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
from ..i18n import tr


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
        super().__init__(parent, tr('ui.dialogs.confirm_mod_installation'), 760, 560)
        warn = QLabel(
            tr('ui.dialogs.a_zip_backup_of_the_replaced_folders_is_created_befo_520aa8')
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
                tr('ui.dialogs.uniqueid_0_target_folder_1', entry.metadata.unique_id, entry.destination_name)
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
        cancel = make_button(tr('ui.dialogs.cancel'), on_click=self.reject)
        install = make_button(tr('ui.dialogs.backup_and_install'), icon_name="download", variant="primary", on_click=self.accept)
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
        super().__init__(parent, tr('ui.dialogs.backup_management'), 860, 560)
        self._backups = backups
        self._on_restore = on_restore
        self._on_delete = on_delete
        self.table = QTableWidget()
        setup_table(
            self.table,
            [tr('ui.dialogs.created'), tr('ui.dialogs.type'), tr('ui.dialogs.tag'), tr('ui.dialogs.size'), tr('ui.dialogs.status')],
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
            self.table.setItem(row, 4, cell(tr('ui.dialogs.available') if backup.status == "available" else backup.status))
        self.table.doubleClicked.connect(lambda _index: self.restore())
        self.add(self.table, 1)
        if not backups:
            empty = QLabel(tr('ui.dialogs.no_backups_yet_backups_are_created_automatically_whe_fede96'))
            empty.setObjectName("Muted")
            self.add(empty)
        close = make_button(tr('ui.dialogs.close'), on_click=self.reject)
        delete = make_button(tr('ui.dialogs.delete_backup'), icon_name="trash", variant="danger", on_click=self.delete)
        restore = make_button(tr('ui.dialogs.restore_selected_backup'), icon_name="backup", variant="primary", on_click=self.restore)
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
    def __init__(self, text: str, parent: QWidget | None = None, title: str = tr('ui.dialogs.raw_log')) -> None:
        super().__init__(parent, title, 980, 700)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        font = QFont("Cascadia Mono")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        editor.setFont(font)
        self.add(editor, 1)
        copy = make_button(tr('ui.dialogs.copy_all'), icon_name="save", on_click=lambda: self._copy(editor))
        close = make_button(tr('ui.dialogs.close'), variant="primary", on_click=self.reject)
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
        super().__init__(parent, tr('ui.dialogs.translation_pack_candidates'), 980, 640)
        intro = QLabel(
            tr('ui.dialogs.candidates_come_from_public_searches_on_nexus_mods_g_e089ef')
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
            [tr('ui.dialogs.source'), tr('ui.dialogs.title'), tr('ui.dialogs.compatibility'), tr('ui.dialogs.author'), tr('ui.dialogs.update_time')],
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
            empty = QLabel(tr('ui.dialogs.no_candidate_translation_packs_found_you_can_still_u_cc9913'))
            empty.setObjectName("Muted")
            self.add(empty)
        open_button = make_button(tr('ui.dialogs.open_selected_page'), icon_name="external", on_click=self.open_selected)
        open_button.setEnabled(bool(candidates))
        close = make_button(tr('ui.dialogs.close'), variant="primary", on_click=self.reject)
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
