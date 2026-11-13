from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ...domain.models import DashboardSummary
from ..theme import theme_colors
from ..widgets import (
    Badge,
    Card,
    EmptyState,
    SectionHeader,
    StatCard,
    cell,
    clear_layout,
    make_button,
    make_scroll_content,
    open_in_explorer,
    setup_table,
)
from .base import BasePage

log = logging.getLogger(__name__)


class DashboardPage(BasePage):
    title = "总览"
    subtitle = "游戏环境、Mod 状态与最近活动一览"

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        self._summary: DashboardSummary | None = None
        wrapper, self.content = make_scroll_content(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrapper)

        self.hero = Card()
        hero_row = QHBoxLayout()
        hero_row.setSpacing(16)
        hero_text = QVBoxLayout()
        hero_text.setSpacing(4)
        self.hero_title = QLabel("开始使用 ModPilot")
        self.hero_title.setObjectName("PageTitle")
        self.hero_path = QLabel("添加一个游戏安装目录以开始。")
        self.hero_path.setObjectName("Muted")
        self.hero_path.setWordWrap(True)
        hero_text.addWidget(self.hero_title)
        hero_text.addWidget(self.hero_path)
        self.hero_meta = QHBoxLayout()
        self.hero_meta.setSpacing(8)
        hero_text.addLayout(self.hero_meta)
        hero_row.addLayout(hero_text, 1)
        self.scan_button = make_button(
            "扫描 Mods", icon_name="refresh", variant="primary", on_click=self.scan_mods
        )
        self.check_button = make_button(
            "检查更新", icon_name="update", on_click=self.controller.run_update_check
        )
        self.open_button = make_button(
            "打开游戏目录", icon_name="folder", on_click=self.open_game_folder
        )
        hero_row.addWidget(self.scan_button)
        hero_row.addWidget(self.check_button)
        hero_row.addWidget(self.open_button)
        self.hero.add_layout(hero_row)
        self.content.addWidget(self.hero)

        self.empty = EmptyState(
            "还没有添加游戏",
            "ModPilot 需要知道游戏安装目录，才能扫描 Mod、检查更新并解析日志。",
            "gamepad",
            make_button(
                "添加游戏",
                icon_name="plus",
                variant="primary",
                on_click=lambda: self.controller.go_to("GamesPage"),
            ),
        )
        self.content.addWidget(self.empty)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_games = StatCard("已管理游戏", "0", "本地游戏安装", "gamepad", "accent")
        self.stat_mods = StatCard("已安装 Mod", "0", "当前游戏", "mods", "info")
        self.stat_enabled = StatCard("已启用", "0", "实际会被加载", "check", "success")
        self.stat_updates = StatCard("待更新", "0", "已关联 Nexus", "update", "warning")
        self.stat_issues = StatCard("诊断问题", "0", "最近一次诊断", "alert", "danger")
        for card in (
            self.stat_games,
            self.stat_mods,
            self.stat_enabled,
            self.stat_updates,
            self.stat_issues,
        ):
            stats.addWidget(card, 1)
        self.stats_widget = QWidget()
        self.stats_widget.setLayout(stats)
        self.content.addWidget(self.stats_widget)

        middle = QHBoxLayout()
        middle.setSpacing(16)
        env_card = Card()
        env_header = SectionHeader("游戏环境", "目录、版本与 SMAPI 状态")
        self.env_refresh = make_button(
            "刷新", icon_name="refresh", variant="tool", on_click=self.refresh
        )
        env_header.add_action(self.env_refresh)
        env_card.add(env_header)
        self.env_container = QWidget()
        self.env_layout = QVBoxLayout(self.env_container)
        self.env_layout.setContentsMargins(0, 0, 0, 0)
        self.env_layout.setSpacing(10)
        env_card.add(self.env_container)
        self.env_hint = QLabel("")
        self.env_hint.setObjectName("Subtle")
        self.env_hint.setWordWrap(True)
        env_card.add(self.env_hint)
        middle.addWidget(env_card, 3)

        activity_card = Card()
        activity_header = SectionHeader("最近活动", "安装、更新、诊断与备份记录")
        activity_card.add(activity_header)
        self.activity_table = QTableWidget()
        setup_table(self.activity_table, ["时间", "事件"], stretch_column=1, row_height=34)
        self.activity_table.setMinimumHeight(230)
        activity_card.add(self.activity_table, 1)
        middle.addWidget(activity_card, 4)
        self.middle_widget = QWidget()
        self.middle_widget.setLayout(middle)
        self.content.addWidget(self.middle_widget)

        quick = Card()
        quick_header = SectionHeader("快捷入口", "常用工作流")
        quick.add(quick_header)
        quick_row = QHBoxLayout()
        quick_row.setSpacing(10)
        quick_row.addWidget(
            make_button(
                "汉化中心", icon_name="translate", on_click=lambda: self.controller.go_to("TranslationPage")
            )
        )
        quick_row.addWidget(
            make_button(
                "智能诊断", icon_name="diagnose", on_click=lambda: self.controller.go_to("DiagnosticsPage")
            )
        )
        quick_row.addWidget(
            make_button(
                "配置档", icon_name="profiles", on_click=lambda: self.controller.go_to("ProfilesPage")
            )
        )
        quick_row.addStretch(1)
        quick.add_layout(quick_row)
        self.content.addWidget(quick)
        self.content.addStretch(1)

        self.refresh()

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        try:
            summary = self.services.dashboard.summary()
        except Exception as exc:
            log.exception("刷新总览失败")
            self.error(f"读取总览失败：{exc}")
            return
        self._summary = summary
        self.empty.setVisible(summary.game is None)
        self.stats_widget.setVisible(summary.game is not None)
        self.middle_widget.setVisible(summary.game is not None)
        if summary.game is None:
            self.hero_title.setText("开始使用 ModPilot")
            self.hero_path.setText("添加一个游戏安装目录以开始。")
            self.scan_button.setEnabled(False)
            self.check_button.setEnabled(False)
            self.open_button.setEnabled(False)
            return
        self.scan_button.setEnabled(True)
        self.check_button.setEnabled(True)
        self.open_button.setEnabled(True)
        game = summary.game
        self.hero_title.setText(game.name)
        self.hero_path.setText(game.install_path)
        clear_layout(self.hero_meta)
        if game.game_version:
            self.hero_meta.addWidget(Badge(f"游戏 {game.game_version}", "info"))
        if game.framework_installed:
            label = f"SMAPI {game.framework_version}" if game.framework_version else "SMAPI 已安装"
            self.hero_meta.addWidget(Badge(label, "success"))
        else:
            self.hero_meta.addWidget(Badge("未检测到 SMAPI", "warning"))
        if game.last_scan_at:
            self.hero_meta.addWidget(
                Badge(f"上次扫描 {game.last_scan_at.strftime('%m-%d %H:%M')}", "muted")
            )
        self.hero_meta.addStretch(1)

        games = self.services.games.list_games()
        self.stat_games.set_value(len(games))
        self.stat_mods.set_value(summary.mod_count)
        self.stat_enabled.set_value(summary.enabled_count)
        self.stat_updates.set_value(summary.update_count)
        self.stat_issues.set_value(summary.issue_count)

        self._render_environment(summary)
        self._render_activity(summary)

    def _render_environment(self, summary: DashboardSummary) -> None:
        clear_layout(self.env_layout)
        for check in summary.environment:
            row = QFrame()
            row.setObjectName("CardFlat")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(10, 8, 10, 8)
            colors = theme_colors(self.controller._theme)
            dot = QLabel()
            dot.setFixedSize(8, 8)
            color = colors["success"] if check.ok else colors["danger" if check.severity.value in ("error", "critical") else "warning"]
            dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
            layout.addWidget(dot)
            label = QLabel(check.label)
            label.setMinimumWidth(92)
            layout.addWidget(label)
            detail = QLabel(check.detail)
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            layout.addWidget(detail, 1)
            self.env_layout.addWidget(row)
        if not summary.environment:
            self.env_hint.setText("暂无环境信息。")
        else:
            bad = sum(1 for check in summary.environment if not check.ok)
            self.env_hint.setText(
                "环境检查通过。" if bad == 0 else f"有 {bad} 项需要处理；管理 Mod 不一定要求全部通过。"
            )

    def _render_activity(self, summary: DashboardSummary) -> None:
        self.activity_table.setRowCount(0)
        for activity in summary.recent_activity:
            row = self.activity_table.rowCount()
            self.activity_table.insertRow(row)
            self.activity_table.setItem(
                row, 0, cell(activity.created_at.strftime("%m-%d %H:%M"))
            )
            message = activity.message
            self.activity_table.setItem(row, 1, cell(message, tooltip=message))

    # ------------------------------------------------------------------

    def scan_mods(self) -> None:
        game_id = self.require_game()
        if game_id is None:
            return
        self.scan_button.setEnabled(False)
        self.scan_button.setText("扫描中…")
        self.toast("正在扫描 Mods 目录…")
        self.tasks.run(
            "扫描 Mods",
            lambda: self.services.mods.scan_game(game_id),
            with_progress=False,
            on_done=lambda count: self._scan_done(count),
            on_error=self._scan_error,
        )

    def _scan_done(self, count) -> None:  # type: ignore[no-untyped-def]
        self.scan_button.setEnabled(True)
        self.scan_button.setText("扫描 Mods")
        self.toast(f"扫描完成：识别 {count} 个 Mod。", "success")
        self.controller.on_data_changed()

    def _scan_error(self, message: str) -> None:
        self.scan_button.setEnabled(True)
        self.scan_button.setText("扫描 Mods")
        self.toast(f"扫描失败：{message}", "danger")

    def open_game_folder(self) -> None:
        if self.current_game:
            open_in_explorer(self.current_game.install_path)
