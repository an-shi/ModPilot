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
from ...i18n import tr

log = logging.getLogger(__name__)


class DashboardPage(BasePage):
    title = tr('ui.pages.dashboard.overview')
    subtitle = tr('ui.pages.dashboard.overview_of_game_environments_mod_status_and_recent_cfb790')

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
        self.hero_title = QLabel(tr('ui.pages.dashboard.get_started_with_modpilot'))
        self.hero_title.setObjectName("PageTitle")
        self.hero_path = QLabel(tr('ui.pages.dashboard.add_a_game_installation_folder_to_get_started'))
        self.hero_path.setObjectName("Muted")
        self.hero_path.setWordWrap(True)
        hero_text.addWidget(self.hero_title)
        hero_text.addWidget(self.hero_path)
        self.hero_meta = QHBoxLayout()
        self.hero_meta.setSpacing(8)
        hero_text.addLayout(self.hero_meta)
        hero_row.addLayout(hero_text, 1)
        self.scan_button = make_button(
            tr('ui.pages.dashboard.scan_mods'), icon_name="refresh", variant="primary", on_click=self.scan_mods
        )
        self.check_button = make_button(
            tr('ui.pages.dashboard.check_updates'), icon_name="update", on_click=self.controller.run_update_check
        )
        self.open_button = make_button(
            tr('ui.pages.dashboard.open_game_folder'), icon_name="folder", on_click=self.open_game_folder
        )
        hero_row.addWidget(self.scan_button)
        hero_row.addWidget(self.check_button)
        hero_row.addWidget(self.open_button)
        self.hero.add_layout(hero_row)
        self.content.addWidget(self.hero)

        self.empty = EmptyState(
            tr('ui.pages.dashboard.no_games_added_yet'),
            tr('ui.pages.dashboard.modpilot_needs_the_game_install_directory_to_scan_mo_f0d8e4'),
            "gamepad",
            make_button(
                tr('ui.pages.dashboard.add_game'),
                icon_name="plus",
                variant="primary",
                on_click=lambda: self.controller.go_to("GamesPage"),
            ),
        )
        self.content.addWidget(self.empty)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_games = StatCard(tr('ui.pages.dashboard.managed_games'), "0", tr('ui.pages.dashboard.local_game_installation'), "gamepad", "accent")
        self.stat_mods = StatCard(tr('ui.pages.dashboard.installed_mods'), "0", tr('ui.pages.dashboard.current_game'), "mods", "info")
        self.stat_enabled = StatCard(tr('ui.pages.dashboard.enabled'), "0", tr('ui.pages.dashboard.actually_loaded'), "check", "success")
        self.stat_updates = StatCard(tr('ui.pages.dashboard.updates'), "0", tr('ui.pages.dashboard.nexus_linked'), "update", "warning")
        self.stat_issues = StatCard(tr('ui.pages.dashboard.detected_issues'), "0", tr('ui.pages.dashboard.last_diagnosis'), "alert", "danger")
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
        env_header = SectionHeader(tr('ui.pages.dashboard.game_environment'), tr('ui.pages.dashboard.directory_version_and_smapi_status'))
        self.env_refresh = make_button(
            tr('ui.pages.dashboard.refresh'), icon_name="refresh", variant="tool", on_click=self.refresh
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
        activity_header = SectionHeader(tr('ui.pages.dashboard.recent_activity'), tr('ui.pages.dashboard.installation_update_diagnosis_and_backup_records'))
        activity_card.add(activity_header)
        self.activity_table = QTableWidget()
        setup_table(self.activity_table, [tr('ui.pages.dashboard.time'), tr('ui.pages.dashboard.event')], stretch_column=1, row_height=34)
        self.activity_table.setMinimumHeight(230)
        activity_card.add(self.activity_table, 1)
        middle.addWidget(activity_card, 4)
        self.middle_widget = QWidget()
        self.middle_widget.setLayout(middle)
        self.content.addWidget(self.middle_widget)

        quick = Card()
        quick_header = SectionHeader(tr('ui.pages.dashboard.shortcuts'), tr('ui.pages.dashboard.common_workflows'))
        quick.add(quick_header)
        quick_row = QHBoxLayout()
        quick_row.setSpacing(10)
        quick_row.addWidget(
            make_button(
                tr('ui.pages.dashboard.translation_center'), icon_name="translate", on_click=lambda: self.controller.go_to("TranslationPage")
            )
        )
        quick_row.addWidget(
            make_button(
                tr('ui.pages.dashboard.smart_diagnosis'), icon_name="diagnose", on_click=lambda: self.controller.go_to("DiagnosticsPage")
            )
        )
        quick_row.addWidget(
            make_button(
                tr('ui.pages.dashboard.profile'), icon_name="profiles", on_click=lambda: self.controller.go_to("ProfilesPage")
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
            log.exception(tr('ui.pages.dashboard.failed_to_refresh_overview'))
            self.error(tr('ui.pages.dashboard.failed_to_read_overview_0', exc))
            return
        self._summary = summary
        self.empty.setVisible(summary.game is None)
        self.stats_widget.setVisible(summary.game is not None)
        self.middle_widget.setVisible(summary.game is not None)
        if summary.game is None:
            self.hero_title.setText(tr('ui.pages.dashboard.get_started_with_modpilot'))
            self.hero_path.setText(tr('ui.pages.dashboard.add_a_game_installation_folder_to_get_started'))
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
            self.hero_meta.addWidget(Badge(tr('ui.pages.dashboard.game_0', game.game_version), "info"))
        if game.framework_installed:
            label = f"SMAPI {game.framework_version}" if game.framework_version else tr('ui.pages.dashboard.smapi_installed')
            self.hero_meta.addWidget(Badge(label, "success"))
        else:
            self.hero_meta.addWidget(Badge(tr('ui.pages.dashboard.smapi_not_detected'), "warning"))
        if game.last_scan_at:
            self.hero_meta.addWidget(
                Badge(tr('ui.pages.dashboard.last_scan_0', game.last_scan_at.strftime('%m-%d %H:%M')), "muted")
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
            dot = QLabel()
            dot.setObjectName("EnvDot")
            dot.setFixedSize(8, 8)
            dot.setProperty(
                "tone",
                "success"
                if check.ok
                else (
                    "danger"
                    if check.severity.value in ("error", "critical")
                    else "warning"
                ),
            )
            layout.addWidget(dot)
            label = QLabel(check.label)
            label.setMinimumWidth(148)
            label.setToolTip(check.label)
            layout.addWidget(label)
            detail = QLabel(check.detail)
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            layout.addWidget(detail, 1)
            self.env_layout.addWidget(row)
        if not summary.environment:
            self.env_hint.setText(tr('ui.pages.dashboard.no_environment_information'))
        else:
            bad = sum(1 for check in summary.environment if not check.ok)
            self.env_hint.setText(
                tr('ui.pages.dashboard.environment_check_passed') if bad == 0 else tr('ui.pages.dashboard.0_items_need_attention_managing_mods_does_not_requir_cf5dbd', bad)
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
        self.scan_button.setText(tr('ui.pages.dashboard.scanning'))
        self.toast(tr('ui.pages.dashboard.scanning_mods_folder'))
        self.tasks.run(
            tr('ui.pages.dashboard.scan_mods'),
            lambda: self.services.mods.scan_game(game_id),
            with_progress=False,
            on_done=lambda count: self._scan_done(count),
            on_error=self._scan_error,
        )

    def _scan_done(self, count) -> None:  # type: ignore[no-untyped-def]
        self.scan_button.setEnabled(True)
        self.scan_button.setText(tr('ui.pages.dashboard.scan_mods'))
        self.toast(tr('ui.pages.dashboard.scan_complete_0_mods_detected', count), "success")
        self.controller.on_data_changed()

    def _scan_error(self, message: str) -> None:
        self.scan_button.setEnabled(True)
        self.scan_button.setText(tr('ui.pages.dashboard.scan_mods'))
        self.toast(tr('ui.pages.dashboard.scan_failed_0', message), "danger")

    def open_game_folder(self) -> None:
        if self.current_game:
            open_in_explorer(self.current_game.install_path)
