from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ...domain.enums import DownloadKind, DownloadStatus, UpdatePolicy, UpdateStatus
from ...domain.models import ModView, UpdateRecordView
from ..theme import theme_colors
from ..widgets import (
    Badge,
    Card,
    EmptyState,
    SectionHeader,
    cell,
    clear_layout,
    clear_table,
    make_button,
    make_scroll_content,
    setup_table,
)
from .base import BasePage

log = logging.getLogger(__name__)

POLICY_LABELS = {
    "inherit": "继承全局",
    UpdatePolicy.MANUAL.value: "仅手动检查",
    UpdatePolicy.NOTIFY.value: "更新提醒",
    UpdatePolicy.CONFIRM_DOWNLOAD.value: "确认后下载",
    UpdatePolicy.AUTO_DOWNLOAD.value: "自动下载",
    UpdatePolicy.AUTO_INSTALL.value: "自动下载并安装",
    UpdatePolicy.IGNORE.value: "忽略更新",
}


class UpdatesPage(BasePage):
    title = "更新中心"
    subtitle = "每个 Mod 可独立选择提醒、下载与安装策略；更新前自动备份"

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        self._all_mods: list[ModView] = []
        self._history: list[UpdateRecordView] = []
        self._selected: ModView | None = None
        self._by_id: dict[int, ModView] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        self.body = QStackedWidget()
        outer.addWidget(self.body, 1)
        self.empty = EmptyState(
            "尚未选择游戏",
            "添加游戏并关联 Nexus 后，即可检查 Mod 更新。",
            "update",
            make_button("前往游戏管理", icon_name="gamepad", variant="primary", on_click=lambda: self.controller.go_to("GamesPage")),
        )
        self.body.addWidget(self.empty)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.body.addWidget(content)

        settings_card = Card()
        settings_row = QHBoxLayout()
        settings_row.setSpacing(10)
        label = QLabel("全局更新策略")
        label.setStyleSheet("font-weight: 650;")
        settings_row.addWidget(label)
        self.global_policy = QComboBox()
        for value in ("manual", "notify", "confirm_download", "auto_download", "auto_install", "ignore"):
            self.global_policy.addItem(POLICY_LABELS[value], value)
        self.global_policy.currentIndexChanged.connect(self._global_policy_changed)
        settings_row.addWidget(self.global_policy)
        self.check_all_button = make_button(
            "检查全部更新", icon_name="refresh", variant="primary", on_click=self.check_all
        )
        settings_row.addWidget(self.check_all_button)
        settings_row.addStretch(1)
        self.schedule_label = QLabel("")
        self.schedule_label.setObjectName("Muted")
        settings_row.addWidget(self.schedule_label)
        settings_card.add_layout(settings_row)
        self.policy_hint = QLabel(
            "自动下载/安装仅通过 Nexus 官方 API 获取文件；非 Premium 账号可能需要在网页下载后导入。"
            "更新前会备份受影响目录，安装后会校验版本。"
        )
        self.policy_hint.setObjectName("Muted")
        self.policy_hint.setWordWrap(True)
        settings_card.add(self.policy_hint)
        layout.addWidget(settings_card)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        table_card = Card()
        table_header = SectionHeader("Mod 更新状态", "选择一行查看详情与更新历史")
        filter_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索 Mod…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search, 1)
        self.filter = QComboBox()
        for label, value in (
            ("全部", "all"),
            ("有更新", "available"),
            ("最新", "current"),
            ("检查失败", "error"),
            ("已忽略", "ignored"),
            ("已锁定", "pinned"),
        ):
            self.filter.addItem(label, value)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter)
        self.refresh_button = make_button(
            "刷新", icon_name="refresh", variant="tool", on_click=self.refresh
        )
        filter_row.addWidget(self.refresh_button)
        table_header.add_action(make_button("前往下载中心", icon_name="download", on_click=lambda: self.controller.go_to("DownloadsPage")))
        table_card.add(table_header)
        table_card.add_layout(filter_row)
        self.table = QTableWidget()
        setup_table(
            self.table,
            ["Mod", "当前版本", "最新版本", "更新策略", "状态", "版本锁定"],
            stretch_column=0,
            row_height=38,
        )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        table_card.add(self.table, 1)
        splitter.addWidget(table_card)

        details_wrapper, details_layout = make_scroll_content(splitter)
        details_card = Card()
        self.details_title = QLabel("选择一个 Mod")
        self.details_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        details_card.add(self.details_title)
        self.details_status = QLabel("查看可选更新、更新历史与回滚。")
        self.details_status.setWordWrap(True)
        self.details_status.setObjectName("Muted")
        details_card.add(self.details_status)
        badges = QHBoxLayout()
        self.badges_layout = badges
        details_card.add_layout(badges)
        policy_row = QHBoxLayout()
        policy_label = QLabel("此 Mod 的策略")
        policy_label.setObjectName("Muted")
        policy_row.addWidget(policy_label)
        self.mod_policy = QComboBox()
        for value in ("inherit", "manual", "notify", "confirm_download", "auto_download", "auto_install", "ignore"):
            self.mod_policy.addItem(POLICY_LABELS[value], value)
        self.mod_policy.currentIndexChanged.connect(self._mod_policy_changed)
        policy_row.addWidget(self.mod_policy)
        policy_row.addStretch(1)
        details_card.add_layout(policy_row)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.check_button = make_button("检查选中", icon_name="refresh", on_click=self.check_selected)
        self.download_button = make_button("下载更新", icon_name="download", on_click=self.download_selected)
        self.install_button = make_button("安装已下载", icon_name="package", variant="primary", on_click=self.install_downloaded)
        self.pin_button = make_button("锁定当前版本", icon_name="shield", on_click=self.toggle_pin)
        self.ignore_button = make_button("忽略版本", icon_name="x", on_click=self.ignore_selected)
        self.rollback_button = make_button("回滚最近更新", icon_name="backup", variant="danger", on_click=self.rollback_selected)
        for button in (
            self.check_button,
            self.download_button,
            self.install_button,
            self.pin_button,
            self.ignore_button,
            self.rollback_button,
        ):
            actions.addWidget(button)
        details_card.add_layout(actions)
        self.action_hint = QLabel("")
        self.action_hint.setWordWrap(True)
        self.action_hint.setObjectName("Muted")
        details_card.add(self.action_hint)
        history_header = SectionHeader("更新历史", "最近 50 条记录")
        details_card.add(history_header)
        self.history_table = QTableWidget()
        setup_table(
            self.history_table,
            ["时间", "变更", "状态", "说明"],
            stretch_column=3,
            row_height=32,
        )
        self.history_table.setMinimumHeight(150)
        details_card.add(self.history_table, 1)
        details_layout.addWidget(details_card)
        details_layout.addStretch(1)
        splitter.addWidget(details_wrapper)
        splitter.setSizes([390, 320])
        layout.addWidget(splitter, 1)

    def refresh(self) -> None:
        if self.game_id is None:
            self.body.setCurrentIndex(0)
            return
        self.body.setCurrentIndex(1)
        self.global_policy.blockSignals(True)
        index = self.global_policy.findData(self.services.config.config.update_policy)
        self.global_policy.setCurrentIndex(max(0, index))
        self.global_policy.blockSignals(False)
        hours = self.services.config.config.update_check_interval_hours
        self.schedule_label.setText(
            "自动检查已关闭" if hours <= 0 else f"每 {hours} 小时自动检查"
        )
        selected_id = self._selected.id if self._selected else None
        try:
            self._all_mods = [view for view in self.services.mods.list_mods(self.game_id) if view.is_present]
            self._history = self.services.updates.history(self.game_id)
        except Exception as exc:
            log.exception("读取更新信息失败")
            self.error(str(exc))
            return
        self._by_id = {mod.id: mod for mod in self._all_mods}
        self._apply_filter()
        if selected_id and selected_id in self._by_id:
            self._select_mod(selected_id)

    def _apply_filter(self) -> None:
        query = self.search.text().strip().casefold()
        key = self.filter.currentData()
        items = list(self._all_mods)
        if query:
            items = [
                mod
                for mod in items
                if query in f"{mod.name} {mod.author} {mod.unique_id}".casefold()
            ]
        if key == "available":
            items = [mod for mod in items if mod.update_status is UpdateStatus.AVAILABLE]
        elif key == "current":
            items = [mod for mod in items if mod.update_status is UpdateStatus.UP_TO_DATE]
        elif key == "error":
            items = [mod for mod in items if mod.update_status is UpdateStatus.ERROR]
        elif key == "ignored":
            items = [mod for mod in items if mod.update_status is UpdateStatus.IGNORED]
        elif key == "pinned":
            items = [mod for mod in items if mod.pinned_version]
        items.sort(
            key=lambda mod: (
                0 if mod.update_status is UpdateStatus.AVAILABLE else 1,
                mod.name.casefold(),
            )
        )
        colors = theme_colors(self.controller._theme)
        self.table.blockSignals(True)
        clear_table(self.table)
        for mod in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, cell(mod.name, data=mod.id, tooltip=mod.unique_id))
            self.table.setItem(row, 1, cell(mod.version))
            self.table.setItem(row, 2, cell(mod.latest_version or "—"))
            policy = POLICY_LABELS.get(mod.update_policy, mod.update_policy)
            self.table.setItem(row, 3, cell(policy))
            status_text = {
                UpdateStatus.AVAILABLE: f"可更新 {mod.latest_version or ''}".strip(),
                UpdateStatus.UP_TO_DATE: "最新",
                UpdateStatus.ERROR: "检查失败",
                UpdateStatus.IGNORED: "已忽略",
                UpdateStatus.DOWNLOADED: "已下载",
                UpdateStatus.UNKNOWN: "未检查",
            }.get(mod.update_status, "未检查")
            item = cell(status_text, tooltip=mod.update_error or "")
            item.setForeground(
                QColor(
                    colors["warning"]
                    if mod.update_status is UpdateStatus.AVAILABLE
                    else colors["danger"]
                    if mod.update_status is UpdateStatus.ERROR
                    else colors["success"]
                    if mod.update_status is UpdateStatus.UP_TO_DATE
                    else colors["muted"]
                )
            )
            self.table.setItem(row, 4, item)
            self.table.setItem(row, 5, cell(mod.pinned_version or "—"))
        self.table.blockSignals(False)
        if self._selected and self._selected.id in {mod.id for mod in items}:
            self._select_mod(self._selected.id)
        elif items:
            self.table.selectRow(0)
        else:
            self._selected = None
            self._render_details(None)

    def _select_mod(self, mod_id: int) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.UserRole) == mod_id:
                self.table.selectRow(row)
                break

    def _selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._selected = None
            self._render_details(None)
            return
        item = self.table.item(rows[0].row(), 0)
        mod_id = item.data(Qt.UserRole) if item else None
        self._selected = self._by_id.get(mod_id) if mod_id else None
        self._render_details(self._selected)

    def _render_details(self, mod: ModView | None) -> None:
        enabled = mod is not None
        for button in (
            self.check_button,
            self.download_button,
            self.install_button,
            self.pin_button,
            self.ignore_button,
            self.rollback_button,
            self.mod_policy,
        ):
            button.setEnabled(enabled)
        clear_layout(self.badges_layout)
        if mod is None:
            self.details_title.setText("选择一个 Mod")
            self.details_status.setText("查看可选更新、更新历史与回滚。")
            self.action_hint.setText("")
            clear_table(self.history_table)
            return
        self.details_title.setText(mod.name)
        self.details_status.setText(
            f"{mod.version} → {mod.latest_version or '未知'} · Nexus ID {mod.nexus_mod_id or '未关联'}"
        )
        if mod.update_status is UpdateStatus.AVAILABLE:
            self.badges_layout.addWidget(Badge("有可用更新", "warning"))
        if mod.pinned_version:
            self.badges_layout.addWidget(Badge(f"锁定 {mod.pinned_version}", "info"))
        if mod.ignored_version:
            self.badges_layout.addWidget(Badge(f"忽略 {mod.ignored_version}", "muted"))
        if not mod.nexus_mod_id:
            self.badges_layout.addWidget(Badge("未关联 Nexus", "danger"))
        self.badges_layout.addStretch(1)
        self.mod_policy.blockSignals(True)
        index = self.mod_policy.findData(mod.update_policy or "inherit")
        self.mod_policy.setCurrentIndex(max(0, index))
        self.mod_policy.blockSignals(False)
        self.pin_button.setText("解除版本锁定" if mod.pinned_version else "锁定当前版本")
        self.ignore_button.setText("清除忽略" if mod.ignored_version else "忽略此版本")
        self.check_button.setEnabled(bool(mod.nexus_mod_id))
        self.download_button.setEnabled(mod.update_status is UpdateStatus.AVAILABLE)
        self.rollback_button.setEnabled(True)
        self.install_button.setEnabled(self._has_completed_download(mod))
        if mod.update_error:
            self.action_hint.setText(mod.update_error)
        elif mod.update_status is UpdateStatus.AVAILABLE:
            self.action_hint.setText(
                f"可下载 {mod.latest_file_name or '最新文件'}。更新将备份当前文件夹并校验安装后的版本。"
            )
        elif mod.update_status is UpdateStatus.UP_TO_DATE:
            self.action_hint.setText("当前版本已是最新，或已锁定版本。")
        else:
            self.action_hint.setText("点击“检查选中”从 Nexus 获取最新版本信息。")
        self._render_history(mod)

    def _render_history(self, mod: ModView) -> None:
        clear_table(self.history_table)
        for record in [item for item in self._history if item.mod_id == mod.id][:50]:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            self.history_table.setItem(row, 0, cell(record.created_at.strftime("%Y-%m-%d %H:%M")))
            self.history_table.setItem(row, 1, cell(f"{record.old_version} → {record.new_version}"))
            status_map = {
                "available": "可用",
                "downloading": "下载中",
                "installed": "已安装",
                "failed": "失败",
                "rolled_back": "已回滚",
            }
            self.history_table.setItem(row, 2, cell(status_map.get(record.status, record.status)))
            self.history_table.setItem(row, 3, cell(record.message, tooltip=record.message))

    def _has_completed_download(self, mod: ModView) -> bool:
        if not self.game_id:
            return False
        try:
            return any(
                download.kind is DownloadKind.UPDATE
                and download.target_mod_id == mod.id
                and download.status is DownloadStatus.COMPLETED
                for download in self.services.downloads.list_for_game(self.game_id)
            )
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _global_policy_changed(self) -> None:
        if not self.global_policy.isEnabled():
            return
        value = str(self.global_policy.currentData())
        try:
            self.services.config.update(update_policy=value)
        except Exception as exc:
            self.error(str(exc))
            return
        self.toast(f"全局更新策略已设为：{self.global_policy.currentText()}", "success")
        self.refresh()

    def _mod_policy_changed(self) -> None:
        if not self._selected or not self.mod_policy.isEnabled():
            return
        value = str(self.mod_policy.currentData())
        try:
            self.services.mods.set_update_policy(self._selected.id, value)
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()

    def check_all(self) -> None:
        game_id = self.require_game()
        if game_id is None:
            return
        if not self.services.nexus_configured:
            self.error("请先在设置中配置 Nexus API Key。")
            return
        self.check_all_button.setEnabled(False)
        self.check_all_button.setText("检查中…")
        self.policy_hint.setText("正在检查更新…")
        self.tasks.run(
            "检查全部更新",
            lambda progress_cb: self.services.updates.check_all(game_id, progress_cb),
            with_progress=True,
            on_progress=lambda value, message: self.policy_hint.setText(message),
            on_done=self._check_all_done,
            on_error=self._check_all_failed,
        )

    def _check_all_done(self, results) -> None:  # type: ignore[no-untyped-def]
        self.check_all_button.setEnabled(True)
        self.check_all_button.setText("检查全部更新")
        available = sum(1 for result in results if result.status is UpdateStatus.AVAILABLE)
        errors = sum(1 for result in results if result.status is UpdateStatus.ERROR)
        self.toast(
            f"检查完成：{available} 个可更新，{errors} 个失败。",
            "success" if not errors else "warning",
        )
        self.policy_hint.setText(
            "自动下载任务已按各 Mod 策略加入下载队列。"
            if any(result.status is UpdateStatus.AVAILABLE for result in results)
            else "当前没有可用更新。"
        )
        self.refresh()
        self.controller.on_data_changed()

    def _check_all_failed(self, message: str) -> None:
        self.check_all_button.setEnabled(True)
        self.check_all_button.setText("检查全部更新")
        self.policy_hint.setText("检查更新失败。")
        self.error(message)

    def check_selected(self) -> None:
        mod = self._selected
        if not mod:
            return
        if not mod.nexus_mod_id:
            self.error("该 Mod 未关联 Nexus Mod ID。")
            return
        self.check_button.setEnabled(False)
        self.action_hint.setText("正在检查…")

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.check_button.setEnabled(True)
            self.toast(result.message or "检查完成。", "success" if result.status is not UpdateStatus.ERROR else "danger")
            self.refresh()
            self.controller.on_data_changed()

        self.tasks.run(
            "检查 Mod 更新",
            lambda: self.services.updates.check_mod(mod.id),
            on_done=done,
            on_error=lambda message: self._action_failed(message),
        )

    def download_selected(self) -> None:
        mod = self._selected
        if not mod:
            return
        if not self.services.nexus_configured:
            self.error("请先配置 Nexus API Key。")
            return
        self.download_button.setEnabled(False)
        self.action_hint.setText("正在创建下载任务…")

        def done(download_id) -> None:  # type: ignore[no-untyped-def]
            self.action_hint.setText("下载任务已加入下载中心。")
            self.toast("下载任务已加入队列。", "success")
            self.refresh()
            self.controller.on_data_changed()

        self.tasks.run(
            "下载 Mod 更新",
            lambda: self.services.updates.download_update(mod.id),
            on_done=done,
            on_error=lambda message: self._action_failed(message),
        )

    def install_downloaded(self) -> None:
        mod = self._selected
        if not mod or not self.game_id:
            return
        completed = next(
            (
                download
                for download in self.services.downloads.list_for_game(self.game_id)
                if download.kind is DownloadKind.UPDATE
                and download.target_mod_id == mod.id
                and download.status is DownloadStatus.COMPLETED
            ),
            None,
        )
        if completed is None:
            self.error("没有找到已完成的更新下载。请先下载更新。")
            return
        if not self.confirm(
            "安装更新",
            f"将 {mod.name} 从 {mod.version} 更新到 {mod.latest_version}。\n\n"
            "安装前自动备份，安装后校验版本；依赖不满足时会阻止安装。",
        ):
            return
        self.install_button.setEnabled(False)
        self.action_hint.setText("正在安装更新…")

        def done(_result) -> None:  # type: ignore[no-untyped-def]
            self.install_button.setEnabled(True)
            self.toast(f"{mod.name} 更新完成。", "success")
            self.refresh()
            self.controller.on_data_changed()

        self.tasks.run(
            "安装更新",
            lambda: self.services.updates.install_update_from_download(completed.id),
            on_done=done,
            on_error=lambda message: self._action_failed(message),
        )

    def toggle_pin(self) -> None:
        mod = self._selected
        if not mod:
            return
        try:
            if mod.pinned_version:
                self.services.mods.set_pinned_version(mod.id, None)
                self.toast("已解除版本锁定。", "success")
            else:
                self.services.mods.set_pinned_version(mod.id, mod.version)
                self.toast(f"已锁定版本 {mod.version}。", "success")
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()

    def ignore_selected(self) -> None:
        mod = self._selected
        if not mod:
            return
        try:
            if mod.ignored_version:
                self.services.mods.clear_update_ignore(mod.id)
                self.toast("已清除忽略版本。", "success")
            elif mod.latest_version:
                self.services.mods.ignore_version(mod.id, mod.latest_version)
                self.toast(f"已忽略版本 {mod.latest_version}。", "success")
            else:
                self.toast("请先检查更新，再忽略具体版本。", "warning")
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()

    def rollback_selected(self) -> None:
        mod = self._selected
        if not mod:
            return
        if not self.confirm(
            "回滚更新",
            f"将 {mod.name} 恢复到最近一次更新前备份？\n\n回滚本身也会先创建安全备份。",
            danger=True,
        ):
            return
        self.rollback_button.setEnabled(False)

        def done(_result) -> None:  # type: ignore[no-untyped-def]
            self.rollback_button.setEnabled(True)
            self.toast(f"{mod.name} 已回滚。", "success")
            self.refresh()
            self.controller.on_data_changed()

        self.tasks.run(
            "回滚更新",
            lambda: self.services.updates.rollback_last_update(mod.id),
            on_done=done,
            on_error=lambda message: self._action_failed(message),
        )

    def _action_failed(self, message: str) -> None:
        self.action_hint.setText(message)
        self.check_button.setEnabled(True)
        self.download_button.setEnabled(self._selected is not None)
        self.install_button.setEnabled(self._selected is not None)
        self.rollback_button.setEnabled(self._selected is not None)
        self.error(message)
