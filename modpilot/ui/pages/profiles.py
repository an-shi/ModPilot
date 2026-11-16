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
from ...i18n import tr

log = logging.getLogger(__name__)


class ProfilesPage(BasePage):
    title = tr('ui.pages.profiles.profile')
    subtitle = tr('ui.pages.profiles.save_import_and_quickly_switch_between_multiple_mod_05c592')

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
            tr('ui.pages.profiles.no_game_selected'),
            tr('ui.pages.profiles.after_adding_a_game_you_can_create_profiles_to_save_edd652'),
            "profiles",
            make_button(tr('ui.pages.profiles.go_to_game_management'), icon_name="gamepad", variant="primary", on_click=lambda: self.controller.go_to("GamesPage")),
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
            tr('ui.pages.profiles.save_current_as_profile'), icon_name="plus", variant="primary", on_click=self.create_profile
        )
        row.addWidget(self.create_button)
        self.apply_button = make_button(tr('ui.pages.profiles.apply_selected'), icon_name="play", on_click=self.apply_profile)
        row.addWidget(self.apply_button)
        self.rename_button = make_button(tr('ui.pages.profiles.rename'), icon_name="edit", on_click=self.rename_profile)
        row.addWidget(self.rename_button)
        self.duplicate_button = make_button(tr('ui.pages.profiles.copy'), icon_name="profiles", on_click=self.duplicate_profile)
        row.addWidget(self.duplicate_button)
        self.export_button = make_button(tr('ui.pages.profiles.export'), icon_name="download", on_click=self.export_profile)
        row.addWidget(self.export_button)
        self.import_button = make_button(tr('ui.pages.profiles.import'), icon_name="folder", on_click=self.import_profile)
        row.addWidget(self.import_button)
        self.delete_button = make_button(tr('ui.pages.profiles.delete'), icon_name="trash", variant="danger", on_click=self.delete_profile)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        self.refresh_button = make_button(tr('ui.pages.profiles.refresh'), icon_name="refresh", variant="tool", on_click=self.refresh)
        row.addWidget(self.refresh_button)
        toolbar.add_layout(row)
        hint = QLabel(
            tr('ui.pages.profiles.a_profile_records_the_enabled_state_and_version_of_e_328da9')
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        toolbar.add(hint)
        layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        table_card = Card()
        table_card.add(SectionHeader(tr('ui.pages.profiles.profile_list'), tr('ui.pages.profiles.click_to_view_entries')))
        self.profiles_hint = QLabel(
            tr('ui.pages.profiles.no_profiles_yet_click_save_current_as_profile_to_rec_9906ca')
        )
        self.profiles_hint.setObjectName("Muted")
        self.profiles_hint.setWordWrap(True)
        table_card.add(self.profiles_hint)
        self.table = QTableWidget()
        setup_table(
            self.table,
            [tr('ui.pages.profiles.name'), tr('ui.pages.profiles.mod_count'), tr('ui.pages.profiles.enabled'), tr('ui.pages.profiles.status'), tr('ui.pages.profiles.created')],
            stretch_column=0,
            row_height=38,
        )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        table_card.add(self.table, 1)
        splitter.addWidget(table_card)

        entries_card = Card()
        entries_header = SectionHeader(tr('ui.pages.profiles.profile_entries'), tr('ui.pages.profiles.a_warning_appears_when_versions_differ_but_the_enabl_ae78ca'))
        self.entries_hint = QLabel("")
        self.entries_hint.setObjectName("Muted")
        entries_header.add_action(self.entries_hint)
        entries_card.add(entries_header)
        self.entries_table = QTableWidget()
        setup_table(
            self.entries_table,
            ["Mod", tr('ui.pages.profiles.profile_version'), tr('ui.pages.profiles.enable')],
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
            log.exception(tr('ui.pages.profiles.failed_to_read_profile'))
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
            self.table.setItem(row, 3, cell(tr('ui.pages.profiles.current') if profile.is_active else "—"))
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
            self.entries_table.setItem(row, 2, cell(tr('ui.pages.profiles.enable') if entry.enabled else tr('ui.pages.profiles.disable')))
        self.entries_hint.setText(tr('ui.pages.profiles.0_entries', len(snapshot.entries)))

    def create_profile(self) -> None:
        if self.game_id is None:
            return
        name, ok = QInputDialog.getText(self, tr('ui.pages.profiles.save_profile'), tr('ui.pages.profiles.profile_name'))
        if not ok or not name.strip():
            return
        description, _ = QInputDialog.getText(self, tr('ui.pages.profiles.profile_description_optional'), tr('ui.pages.profiles.description'))
        try:
            profile = self.services.profiles.create_from_current(
                self.game_id, name.strip(), description.strip()
            )
        except Exception as exc:
            self.error(str(exc))
            return
        self.toast(tr('ui.pages.profiles.profile_created_0', profile.name), "success")
        self.refresh()
        self._select_profile(profile.id)
        self.controller.on_data_changed()

    def apply_profile(self) -> None:
        if self._selected is None:
            return
        if not self.confirm(
            tr('ui.pages.profiles.apply_profile'),
            tr('ui.pages.profiles.apply_0_modpilot_enables_or_disables_the_mods_listed_badedd', self._selected.name),
        ):
            return
        profile = self._selected
        self.apply_button.setEnabled(False)
        self.progress_label.setText(tr('ui.pages.profiles.applying_profile'))

        def progress(value: int, message: str) -> None:
            self.progress_label.setText(message)

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.apply_button.setEnabled(True)
            self.progress_label.setText("")
            message = tr('ui.pages.profiles.enabled_0_mods_and_disabled_1', len(result.enabled), len(result.disabled))
            if result.missing:
                message += tr('ui.pages.profiles.0_mods_in_this_profile_are_not_installed', len(result.missing)) + "\n".join(result.missing[:10])
            if result.warnings:
                message += tr('ui.pages.profiles.note') + "\n".join(result.warnings[:10])
            self.info(message, tr('ui.pages.profiles.profile_applied'))
            self.refresh()
            self.controller.on_data_changed()

        def failed(message: str) -> None:
            self.apply_button.setEnabled(True)
            self.progress_label.setText("")
            self.error(message)

        self.tasks.run(
            tr('ui.pages.profiles.apply_profile'),
            lambda progress_cb: self.services.profiles.apply(profile.id, progress_cb),
            with_progress=True,
            on_progress=progress,
            on_done=done,
            on_error=failed,
        )

    def rename_profile(self) -> None:
        if self._selected is None:
            return
        name, ok = QInputDialog.getText(self, tr('ui.pages.profiles.rename_profile'), tr('ui.pages.profiles.new_name'), text=self._selected.name)
        if not ok or not name.strip():
            return
        try:
            self.services.profiles.rename(self._selected.id, name.strip())
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()
        self.toast(tr('ui.pages.profiles.profile_renamed'), "success")

    def duplicate_profile(self) -> None:
        if self._selected is None:
            return
        name, ok = QInputDialog.getText(
            self, tr('ui.pages.profiles.copy_profile'), tr('ui.pages.profiles.new_profile_name'), text=tr('ui.pages.profiles.0_copies', self._selected.name)
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
        self.toast(tr('ui.pages.profiles.profile_copied'), "success")

    def export_profile(self) -> None:
        if self._selected is None:
            return
        default = self.services.paths.exports_dir / f"{self._selected.name}.json"
        path, _ = QFileDialog.getSaveFileName(self, tr('ui.pages.profiles.export_profile'), str(default), tr('ui.pages.profiles.configuration_file_json'))
        if not path:
            return
        try:
            destination = self.services.profiles.export(self._selected.id, Path(path))
        except Exception as exc:
            self.error(str(exc))
            return
        self.toast(tr('ui.pages.profiles.profile_exported_0', destination), "success")

    def import_profile(self) -> None:
        if self.game_id is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, tr('ui.pages.profiles.import_profile'), "", tr('ui.pages.profiles.configuration_file_json'))
        if not path:
            return
        try:
            profile = self.services.profiles.import_file(self.game_id, Path(path))
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()
        self._select_profile(profile.id)
        self.toast(tr('ui.pages.profiles.profile_imported_0', profile.name), "success")

    def delete_profile(self) -> None:
        if self._selected is None:
            return
        if not self.confirm(
            tr('ui.pages.profiles.delete_profile'),
            tr('ui.pages.profiles.delete_0_no_mod_files_will_be_deleted', self._selected.name),
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
        self.toast(tr('ui.pages.profiles.profile_deleted'), "success")
