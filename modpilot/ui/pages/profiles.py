from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ...domain.models import ProfileView
from ..widgets import (
    Card,
    EmptyState,
    SectionHeader,
    cell,
    clear_table,
    make_button,
    setup_table,
)
from .base import BasePage

log = logging.getLogger(__name__)


class ProfilesPage(BasePage):
    title = "配置档"
    subtitle = "保存、导入和快速切换多套 Mod 启用方案（不复制 Mod 文件）"

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        self._profiles: list[ProfileView] = []
        self._selected: ProfileView | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self.body = QStackedWidget()
        outer.addWidget(self.body, 1)
        self.empty = EmptyState(
            "尚未选择游戏",
            "添加游戏后可创建配置档，保存当前 Mod 启用状态。",
            "profiles",
            make_button("前往游戏管理", icon_name="gamepad", variant="primary", on_click=lambda: self.controller.go_to("GamesPage")),
        )
        self.body.addWidget(self.empty)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.body.addWidget(content)

        toolbar = Card()
        row = QHBoxLayout()
        row.setSpacing(8)
        self.create_button = make_button(
            "保存当前为配置档", icon_name="plus", variant="primary", on_click=self.create_profile
        )
        row.addWidget(self.create_button)
        self.apply_button = make_button("应用选中", icon_name="play", on_click=self.apply_profile)
        row.addWidget(self.apply_button)
        self.rename_button = make_button("重命名", icon_name="edit", on_click=self.rename_profile)
        row.addWidget(self.rename_button)
        self.duplicate_button = make_button("复制", icon_name="profiles", on_click=self.duplicate_profile)
        row.addWidget(self.duplicate_button)
        self.export_button = make_button("导出", icon_name="download", on_click=self.export_profile)
        row.addWidget(self.export_button)
        self.import_button = make_button("导入", icon_name="folder", on_click=self.import_profile)
        row.addWidget(self.import_button)
        self.delete_button = make_button("删除", icon_name="trash", variant="danger", on_click=self.delete_profile)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        self.refresh_button = make_button("刷新", icon_name="refresh", variant="tool", on_click=self.refresh)
        row.addWidget(self.refresh_button)
        toolbar.add_layout(row)
        hint = QLabel(
            "配置档记录每个 Mod 的启用状态和版本。应用时只会切换配置档中列出的 Mod；"
            "未在配置档中的新 Mod 保持当前状态。缺失的 Mod 不会自动下载。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        toolbar.add(hint)
        layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        table_card = Card()
        table_card.add(SectionHeader("配置档列表", "点击查看条目"))
        self.profiles_hint = QLabel(
            "还没有配置档。点击“保存当前为配置档”记录当前 Mod 的启用状态，之后可一键切换。"
        )
        self.profiles_hint.setObjectName("Muted")
        self.profiles_hint.setWordWrap(True)
        table_card.add(self.profiles_hint)
        self.table = QTableWidget()
        setup_table(
            self.table,
            ["名称", "Mod 数", "启用数", "状态", "创建时间"],
            stretch_column=0,
            row_height=38,
        )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        table_card.add(self.table, 1)
        splitter.addWidget(table_card)

        entries_card = Card()
        entries_header = SectionHeader("配置档条目", "版本不一致时会给出警告，但仍会应用启用状态")
        self.entries_hint = QLabel("")
        self.entries_hint.setObjectName("Muted")
        entries_header.add_action(self.entries_hint)
        entries_card.add(entries_header)
        self.entries_table = QTableWidget()
        setup_table(
            self.entries_table,
            ["Mod", "配置档版本", "启用"],
            stretch_column=0,
            row_height=34,
        )
        entries_card.add(self.entries_table, 1)
        splitter.addWidget(entries_card)
        splitter.setSizes([300, 330])
        layout.addWidget(splitter, 1)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("Muted")
        layout.addWidget(self.progress_label)

    def refresh(self) -> None:
        if self.game_id is None:
            self.body.setCurrentIndex(0)
            return
        self.body.setCurrentIndex(1)
        selected_id = self._selected.id if self._selected else None
        try:
            self._profiles = self.services.profiles.list(self.game_id)
        except Exception as exc:
            log.exception("读取配置档失败")
            self.error(str(exc))
            return
        self.table.blockSignals(True)
        clear_table(self.table)
        for profile in self._profiles:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, cell(profile.name, data=profile.id, tooltip=profile.description))
            self.table.setItem(row, 1, cell(str(profile.mod_count)))
            self.table.setItem(row, 2, cell(str(profile.enabled_count)))
            self.table.setItem(row, 3, cell("当前" if profile.is_active else "—"))
            self.table.setItem(row, 4, cell(profile.created_at.strftime("%Y-%m-%d %H:%M")))
        self.table.blockSignals(False)
        self.profiles_hint.setVisible(not self._profiles)
        self._update_buttons()
        if selected_id:
            self._select_profile(selected_id)
        elif self._profiles:
            self.table.selectRow(0)
        else:
            self._selected = None
            clear_table(self.entries_table)
            self.entries_hint.setText("")

    def _update_buttons(self) -> None:
        enabled = bool(self._profiles)
        for button in (
            self.apply_button,
            self.rename_button,
            self.duplicate_button,
            self.export_button,
            self.delete_button,
        ):
            button.setEnabled(enabled)
        self.import_button.setEnabled(self.game_id is not None)

    def _select_profile(self, profile_id: int) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.UserRole) == profile_id:
                self.table.selectRow(row)
                return

    def _selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._selected = None
            self._update_buttons()
            clear_table(self.entries_table)
            return
        item = self.table.item(rows[0].row(), 0)
        profile_id = item.data(Qt.UserRole) if item else None
        self._selected = next((profile for profile in self._profiles if profile.id == profile_id), None)
        self._update_buttons()
        self._show_entries()

    def _show_entries(self) -> None:
        clear_table(self.entries_table)
        if self._selected is None:
            self.entries_hint.setText("")
            return
        try:
            snapshot = self.services.profiles.get_snapshot(self._selected.id)
        except Exception as exc:
            self.error(str(exc))
            return
        for entry in snapshot.entries:
            row = self.entries_table.rowCount()
            self.entries_table.insertRow(row)
            self.entries_table.setItem(row, 0, cell(entry.name, tooltip=entry.unique_id))
            self.entries_table.setItem(row, 1, cell(entry.version))
            self.entries_table.setItem(row, 2, cell("启用" if entry.enabled else "禁用"))
        self.entries_hint.setText(f"{len(snapshot.entries)} 个条目")

    def create_profile(self) -> None:
        if self.game_id is None:
            return
        name, ok = QInputDialog.getText(self, "保存配置档", "配置档名称：")
        if not ok or not name.strip():
            return
        description, _ = QInputDialog.getText(self, "配置档描述（可选）", "描述：")
        try:
            profile = self.services.profiles.create_from_current(
                self.game_id, name.strip(), description.strip()
            )
        except Exception as exc:
            self.error(str(exc))
            return
        self.toast(f"已创建配置档：{profile.name}", "success")
        self.refresh()
        self._select_profile(profile.id)
        self.controller.on_data_changed()

    def apply_profile(self) -> None:
        if self._selected is None:
            return
        if not self.confirm(
            "应用配置档",
            f"应用“{self._selected.name}”？\n\n"
            "ModPilot 会启用/禁用配置档中列出的 Mod。未列出的 Mod 保持现状。",
        ):
            return
        profile = self._selected
        self.apply_button.setEnabled(False)
        self.progress_label.setText("正在应用配置档…")

        def progress(value: int, message: str) -> None:
            self.progress_label.setText(message)

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.apply_button.setEnabled(True)
            self.progress_label.setText("")
            message = f"已启用 {len(result.enabled)} 个、禁用 {len(result.disabled)} 个 Mod。"
            if result.missing:
                message += f"\n\n配置档中有 {len(result.missing)} 个 Mod 未安装：\n" + "\n".join(result.missing[:10])
            if result.warnings:
                message += "\n\n注意：\n" + "\n".join(result.warnings[:10])
            self.info(message, "配置档已应用")
            self.refresh()
            self.controller.on_data_changed()

        def failed(message: str) -> None:
            self.apply_button.setEnabled(True)
            self.progress_label.setText("")
            self.error(message)

        self.tasks.run(
            "应用配置档",
            lambda progress_cb: self.services.profiles.apply(profile.id, progress_cb),
            with_progress=True,
            on_progress=progress,
            on_done=done,
            on_error=failed,
        )

    def rename_profile(self) -> None:
        if self._selected is None:
            return
        name, ok = QInputDialog.getText(self, "重命名配置档", "新名称：", text=self._selected.name)
        if not ok or not name.strip():
            return
        try:
            self.services.profiles.rename(self._selected.id, name.strip())
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()
        self.toast("配置档已重命名。", "success")

    def duplicate_profile(self) -> None:
        if self._selected is None:
            return
        name, ok = QInputDialog.getText(
            self, "复制配置档", "新配置档名称：", text=f"{self._selected.name} 副本"
        )
        if not ok or not name.strip():
            return
        try:
            profile = self.services.profiles.duplicate(self._selected.id, name.strip())
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()
        self._select_profile(profile.id)
        self.toast("配置档已复制。", "success")

    def export_profile(self) -> None:
        if self._selected is None:
            return
        default = self.services.paths.exports_dir / f"{self._selected.name}.json"
        path, _ = QFileDialog.getSaveFileName(self, "导出配置档", str(default), "配置文件 (*.json)")
        if not path:
            return
        try:
            destination = self.services.profiles.export(self._selected.id, Path(path))
        except Exception as exc:
            self.error(str(exc))
            return
        self.toast(f"配置档已导出：{destination}", "success")

    def import_profile(self) -> None:
        if self.game_id is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入配置档", "", "配置文件 (*.json)")
        if not path:
            return
        try:
            profile = self.services.profiles.import_file(self.game_id, Path(path))
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()
        self._select_profile(profile.id)
        self.toast(f"已导入配置档：{profile.name}", "success")

    def delete_profile(self) -> None:
        if self._selected is None:
            return
        if not self.confirm(
            "删除配置档",
            f"删除“{self._selected.name}”？\n\n不会删除任何 Mod 文件。",
            danger=True,
        ):
            return
        try:
            self.services.profiles.delete(self._selected.id)
        except Exception as exc:
            self.error(str(exc))
            return
        self._selected = None
        self.refresh()
        self.toast("配置档已删除。", "success")
