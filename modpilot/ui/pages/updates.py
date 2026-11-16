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
from ...i18n import tr

log = logging.getLogger(__name__)

POLICY_LABELS = {
    "inherit": tr('ui.pages.updates.inherit_global'),
    UpdatePolicy.MANUAL.value: tr('ui.pages.updates.manual_check_only'),
    UpdatePolicy.NOTIFY.value: tr('ui.pages.updates.update_alerts'),
    UpdatePolicy.CONFIRM_DOWNLOAD.value: tr('ui.pages.updates.download_after_confirmation'),
    UpdatePolicy.AUTO_DOWNLOAD.value: tr('ui.pages.updates.auto_download'),
    UpdatePolicy.AUTO_INSTALL.value: tr('ui.pages.updates.auto_download_and_install'),
    UpdatePolicy.IGNORE.value: tr('ui.pages.updates.ignore_update'),
}


class UpdatesPage(BasePage):
    title = tr('ui.pages.updates.update_center')
    subtitle = tr('ui.pages.updates.each_mod_can_have_its_own_notify_download_and_instal_9d50b7')

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
            tr('ui.pages.updates.no_game_selected'),
            tr('ui.pages.updates.after_adding_a_game_and_linking_nexus_you_can_check_dfedaf'),
            "update",
            make_button(tr('ui.pages.updates.go_to_game_management'), icon_name="gamepad", variant="primary", on_click=lambda: self.controller.go_to("GamesPage")),
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
        label = QLabel(tr('ui.pages.updates.global_update_policy'))
        label.setStyleSheet("font-weight: 650;")
        settings_row.addWidget(label)
        self.global_policy = QComboBox()
        for value in ("manual", "notify", "confirm_download", "auto_download", "auto_install", "ignore"):
            self.global_policy.addItem(POLICY_LABELS[value], value)
        self.global_policy.currentIndexChanged.connect(self._global_policy_changed)
        settings_row.addWidget(self.global_policy)
        self.check_all_button = make_button(
            tr('ui.pages.updates.check_all_updates'), icon_name="refresh", variant="primary", on_click=self.check_all
        )
        settings_row.addWidget(self.check_all_button)
        settings_row.addStretch(1)
        self.schedule_label = QLabel("")
        self.schedule_label.setObjectName("Muted")
        settings_row.addWidget(self.schedule_label)
        settings_card.add_layout(settings_row)
        self.policy_hint = QLabel(
            tr('ui.pages.updates.auto_download_install_only_fetches_files_through_the_febce1')
        )
        self.policy_hint.setObjectName("Muted")
        self.policy_hint.setWordWrap(True)
        settings_card.add(self.policy_hint)
        layout.addWidget(settings_card)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        table_card = Card()
        table_header = SectionHeader(tr('ui.pages.updates.mod_update_status'), tr('ui.pages.updates.select_a_row_to_view_details_and_update_history'))
        filter_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('ui.pages.updates.search_mods'))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search, 1)
        self.filter = QComboBox()
        for label, value in (
            (tr('ui.pages.updates.all'), "all"),
            (tr('ui.pages.updates.update_available'), "available"),
            (tr('ui.pages.updates.latest'), "current"),
            (tr('ui.pages.updates.check_failed'), "error"),
            (tr('ui.pages.updates.ignored'), "ignored"),
            (tr('ui.pages.updates.locked'), "pinned"),
        ):
            self.filter.addItem(label, value)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter)
        self.refresh_button = make_button(
            tr('ui.pages.updates.refresh'), icon_name="refresh", variant="tool", on_click=self.refresh
        )
        filter_row.addWidget(self.refresh_button)
        table_header.add_action(make_button(tr('ui.pages.updates.go_to_download_center'), icon_name="download", on_click=lambda: self.controller.go_to("DownloadsPage")))
        table_card.add(table_header)
        table_card.add_layout(filter_row)
        self.table = QTableWidget()
        setup_table(
            self.table,
            ["Mod", tr('ui.pages.updates.current_version'), tr('ui.pages.updates.latest_version'), tr('ui.pages.updates.update_policy'), tr('ui.pages.updates.status'), tr('ui.pages.updates.version_lock')],
            stretch_column=0,
            row_height=38,
        )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        table_card.add(self.table, 1)
        splitter.addWidget(table_card)

        details_wrapper, details_layout = make_scroll_content(splitter)
        details_card = Card()
        self.details_title = QLabel(tr('ui.pages.updates.select_a_mod'))
        self.details_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        details_card.add(self.details_title)
        self.details_status = QLabel(tr('ui.pages.updates.view_available_updates_update_history_and_rollback'))
        self.details_status.setWordWrap(True)
        self.details_status.setObjectName("Muted")
        details_card.add(self.details_status)
        badges = QHBoxLayout()
        self.badges_layout = badges
        details_card.add_layout(badges)
        policy_row = QHBoxLayout()
        policy_label = QLabel(tr('ui.pages.updates.policy_for_this_mod'))
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
        self.check_button = make_button(tr('ui.pages.updates.check_selected'), icon_name="refresh", on_click=self.check_selected)
        self.download_button = make_button(tr('ui.pages.updates.download_update'), icon_name="download", on_click=self.download_selected)
        self.install_button = make_button(tr('ui.pages.updates.install_downloaded'), icon_name="package", variant="primary", on_click=self.install_downloaded)
        self.pin_button = make_button(tr('ui.pages.updates.lock_version'), icon_name="shield", on_click=self.toggle_pin)
        self.ignore_button = make_button(tr('ui.pages.updates.ignore_version'), icon_name="x", on_click=self.ignore_selected)
        self.rollback_button = make_button(tr('ui.pages.updates.roll_back_latest_update'), icon_name="backup", variant="danger", on_click=self.rollback_selected)
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
        history_header = SectionHeader(tr('ui.pages.updates.update_history'), tr('ui.pages.updates.last_50_records'))
        details_card.add(history_header)
        self.history_table = QTableWidget()
        setup_table(
            self.history_table,
            [tr('ui.pages.updates.time'), tr('ui.pages.updates.change'), tr('ui.pages.updates.status'), tr('ui.pages.updates.details')],
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
            tr('ui.pages.updates.auto_check_is_off') if hours <= 0 else tr('ui.pages.updates.auto_check_every_0_hours', hours)
        )
        selected_id = self._selected.id if self._selected else None
        try:
            self._all_mods = [view for view in self.services.mods.list_mods(self.game_id) if view.is_present]
            self._history = self.services.updates.history(self.game_id)
        except Exception as exc:
            log.exception(tr('ui.pages.updates.failed_to_read_update_information'))
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
                UpdateStatus.AVAILABLE: tr('ui.pages.updates.update_to_0', mod.latest_version or '').strip(),
                UpdateStatus.UP_TO_DATE: tr('ui.pages.updates.latest'),
                UpdateStatus.ERROR: tr('ui.pages.updates.check_failed'),
                UpdateStatus.IGNORED: tr('ui.pages.updates.ignored'),
                UpdateStatus.DOWNLOADED: tr('ui.pages.updates.downloaded'),
                UpdateStatus.UNKNOWN: tr('ui.pages.updates.not_checked'),
            }.get(mod.update_status, tr('ui.pages.updates.not_checked'))
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
            self.details_title.setText(tr('ui.pages.updates.select_a_mod'))
            self.details_status.setText(tr('ui.pages.updates.view_available_updates_update_history_and_rollback'))
            self.action_hint.setText("")
            clear_table(self.history_table)
            return
        self.details_title.setText(mod.name)
        self.details_status.setText(
            f"{mod.version} → {mod.latest_version or tr('ui.pages.updates.unknown')} · Nexus ID {mod.nexus_mod_id or tr('ui.pages.updates.not_linked')}"
        )
        if mod.update_status is UpdateStatus.AVAILABLE:
            self.badges_layout.addWidget(Badge(tr('ui.pages.updates.updates_available'), "warning"))
        if mod.pinned_version:
            self.badges_layout.addWidget(Badge(tr('ui.pages.updates.lock_0', mod.pinned_version), "info"))
        if mod.ignored_version:
            self.badges_layout.addWidget(Badge(tr('ui.pages.updates.ignore_0', mod.ignored_version), "muted"))
        if not mod.nexus_mod_id:
            self.badges_layout.addWidget(Badge(tr('ui.pages.updates.not_linked_to_nexus'), "danger"))
        self.badges_layout.addStretch(1)
        self.mod_policy.blockSignals(True)
        index = self.mod_policy.findData(mod.update_policy or "inherit")
        self.mod_policy.setCurrentIndex(max(0, index))
        self.mod_policy.blockSignals(False)
        self.pin_button.setText(tr('ui.pages.updates.unlock_version') if mod.pinned_version else tr('ui.pages.updates.lock_version'))
        self.ignore_button.setText(tr('ui.pages.updates.clear_ignore') if mod.ignored_version else tr('ui.pages.updates.ignore_version_aebc24'))
        self.check_button.setEnabled(bool(mod.nexus_mod_id))
        self.download_button.setEnabled(mod.update_status is UpdateStatus.AVAILABLE)
        self.rollback_button.setEnabled(True)
        self.install_button.setEnabled(self._has_completed_download(mod))
        if mod.update_error:
            self.action_hint.setText(mod.update_error)
        elif mod.update_status is UpdateStatus.AVAILABLE:
            self.action_hint.setText(
                tr('ui.pages.updates.0_is_available_to_download_the_update_will_back_up_t_b65afc', mod.latest_file_name or tr('ui.pages.updates.latest_file'))
            )
        elif mod.update_status is UpdateStatus.UP_TO_DATE:
            self.action_hint.setText(tr('ui.pages.updates.the_current_version_is_up_to_date_or_the_version_is_1229a5'))
        else:
            self.action_hint.setText(tr('ui.pages.updates.click_check_selected_to_get_the_latest_version_info_5825c1'))
        self._render_history(mod)

    def _render_history(self, mod: ModView) -> None:
        clear_table(self.history_table)
        for record in [item for item in self._history if item.mod_id == mod.id][:50]:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            self.history_table.setItem(row, 0, cell(record.created_at.strftime("%Y-%m-%d %H:%M")))
            self.history_table.setItem(row, 1, cell(f"{record.old_version} → {record.new_version}"))
            status_map = {
                "available": tr('ui.pages.updates.available'),
                "downloading": tr('ui.pages.updates.downloading'),
                "installed": tr('ui.pages.updates.installed'),
                "failed": tr('ui.pages.updates.failed'),
                "rolled_back": tr('ui.pages.updates.rolled_back'),
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
        self.toast(tr('ui.pages.updates.global_update_policy_set_to_0', self.global_policy.currentText()), "success")
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
            self.error(tr('ui.pages.updates.please_configure_the_nexus_api_key_in_settings_first'))
            return
        self.check_all_button.setEnabled(False)
        self.check_all_button.setText(tr('ui.pages.updates.checking'))
        self.policy_hint.setText(tr('ui.pages.updates.checking_for_updates'))
        self.tasks.run(
            tr('ui.pages.updates.check_all_updates'),
            lambda progress_cb: self.services.updates.check_all(game_id, progress_cb),
            with_progress=True,
            on_progress=lambda value, message: self.policy_hint.setText(message),
            on_done=self._check_all_done,
            on_error=self._check_all_failed,
        )

    def _check_all_done(self, results) -> None:  # type: ignore[no-untyped-def]
        self.check_all_button.setEnabled(True)
        self.check_all_button.setText(tr('ui.pages.updates.check_all_updates'))
        available = sum(1 for result in results if result.status is UpdateStatus.AVAILABLE)
        errors = sum(1 for result in results if result.status is UpdateStatus.ERROR)
        self.toast(
            tr('ui.pages.updates.check_complete_0_updatable_1_failed', available, errors),
            "success" if not errors else "warning",
        )
        self.policy_hint.setText(
            tr('ui.pages.updates.auto_download_tasks_have_been_added_to_the_download_24809b')
            if any(result.status is UpdateStatus.AVAILABLE for result in results)
            else tr('ui.pages.updates.no_updates_available')
        )
        self.refresh()
        self.controller.on_data_changed()

    def _check_all_failed(self, message: str) -> None:
        self.check_all_button.setEnabled(True)
        self.check_all_button.setText(tr('ui.pages.updates.check_all_updates'))
        self.policy_hint.setText(tr('ui.pages.updates.failed_to_check_for_updates'))
        self.error(message)

    def check_selected(self) -> None:
        mod = self._selected
        if not mod:
            return
        if not mod.nexus_mod_id:
            self.error(tr('ui.pages.updates.this_mod_is_not_linked_to_a_nexus_mod_id'))
            return
        self.check_button.setEnabled(False)
        self.action_hint.setText(tr('ui.pages.updates.checking_481ee2'))

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.check_button.setEnabled(True)
            self.toast(result.message or tr('ui.pages.updates.check_complete'), "success" if result.status is not UpdateStatus.ERROR else "danger")
            self.refresh()
            self.controller.on_data_changed()

        self.tasks.run(
            tr('ui.pages.updates.check_mod_updates'),
            lambda: self.services.updates.check_mod(mod.id),
            on_done=done,
            on_error=lambda message: self._action_failed(message),
        )

    def download_selected(self) -> None:
        mod = self._selected
        if not mod:
            return
        if not self.services.nexus_configured:
            self.error(tr('ui.pages.updates.please_configure_the_nexus_api_key_first'))
            return
        self.download_button.setEnabled(False)
        self.action_hint.setText(tr('ui.pages.updates.creating_download_task'))

        def done(download_id) -> None:  # type: ignore[no-untyped-def]
            self.action_hint.setText(tr('ui.pages.updates.download_task_added_to_download_center'))
            self.toast(tr('ui.pages.updates.download_task_queued'), "success")
            self.refresh()
            self.controller.on_data_changed()

        self.tasks.run(
            tr('ui.pages.updates.download_mod_updates'),
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
            self.error(tr('ui.pages.updates.no_completed_update_downloads_found_please_download_25ee37'))
            return
        if not self.confirm(
            tr('ui.pages.updates.install_update'),
            tr('ui.pages.updates.update_0_from_1_to_2_a_backup_is_created_automatical_7e42ce', mod.name, mod.version, mod.latest_version),
        ):
            return
        self.install_button.setEnabled(False)
        self.action_hint.setText(tr('ui.pages.updates.installing_updates'))

        def done(_result) -> None:  # type: ignore[no-untyped-def]
            self.install_button.setEnabled(True)
            self.toast(tr('ui.pages.updates.0_updated', mod.name), "success")
            self.refresh()
            self.controller.on_data_changed()

        self.tasks.run(
            tr('ui.pages.updates.install_update'),
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
                self.toast(tr('ui.pages.updates.version_unlocked'), "success")
            else:
                self.services.mods.set_pinned_version(mod.id, mod.version)
                self.toast(tr('ui.pages.updates.version_0_locked', mod.version), "success")
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
                self.toast(tr('ui.pages.updates.ignored_version_cleared'), "success")
            elif mod.latest_version:
                self.services.mods.ignore_version(mod.id, mod.latest_version)
                self.toast(tr('ui.pages.updates.version_0_ignored', mod.latest_version), "success")
            else:
                self.toast(tr('ui.pages.updates.please_check_for_updates_before_ignoring_a_specific_b26063'), "warning")
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()

    def rollback_selected(self) -> None:
        mod = self._selected
        if not mod:
            return
        if not self.confirm(
            tr('ui.pages.updates.roll_back_update'),
            tr('ui.pages.updates.restore_0_to_the_backup_made_before_the_most_recent_4f7d4f', mod.name),
            danger=True,
        ):
            return
        self.rollback_button.setEnabled(False)

        def done(_result) -> None:  # type: ignore[no-untyped-def]
            self.rollback_button.setEnabled(True)
            self.toast(tr('ui.pages.updates.0_rolled_back', mod.name), "success")
            self.refresh()
            self.controller.on_data_changed()

        self.tasks.run(
            tr('ui.pages.updates.roll_back_update'),
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
