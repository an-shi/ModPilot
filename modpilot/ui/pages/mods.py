from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.paths import human_size
from ...domain.enums import ModKind, UpdateStatus
from ...domain.models import InstallPlan, ModView
from ..dialogs import BackupsDialog, InstallPreviewDialog
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
    make_icon,
    open_in_explorer,
    setup_table,
)
from .base import BasePage
from ...i18n import tr

log = logging.getLogger(__name__)


class ModsPage(BasePage):
    title = tr('ui.pages.mods.mod_management')
    subtitle = tr('ui.pages.mods.scan_install_enable_disable_and_check_dependencies_a_6dc375')

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        self._all_mods: list[ModView] = []
        self._by_id: dict[int, ModView] = {}
        self._selected_mod: ModView | None = None
        self._pending_plan: InstallPlan | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        toolbar = QFrame()
        toolbar.setObjectName("Toolbar")
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(8)
        row_one = QHBoxLayout()
        row_one.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('ui.pages.mods.search_name_author_uniqueid_or_nexus_id'))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        row_one.addWidget(self.search, 1)
        self.filter_combo = QComboBox()
        for label, value in (
            (tr('ui.pages.mods.all_mods'), "all"),
            (tr('ui.pages.mods.enabled'), "enabled"),
            (tr('ui.pages.mods.disabled'), "disabled"),
            (tr('ui.pages.mods.update_available'), "update"),
            (tr('ui.pages.mods.missing_dependencies'), "missing_deps"),
            (tr('ui.pages.mods.duplicate_installation'), "duplicate"),
            (tr('ui.pages.mods.not_translated'), "untranslated"),
            (tr('ui.pages.mods.content_pack'), "kind_content"),
            (tr('ui.pages.mods.code_mod'), "kind_code"),
            (tr('ui.pages.mods.framework_tool'), "kind_framework"),
            (tr('ui.pages.mods.file_missing'), "missing"),
        ):
            self.filter_combo.addItem(label, value)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        row_one.addWidget(self.filter_combo)
        self.sort_combo = QComboBox()
        for label, value in (
            (tr('ui.pages.mods.sort_by_name'), "name"),
            (tr('ui.pages.mods.sort_by_author'), "author"),
            (tr('ui.pages.mods.sort_by_size'), "size"),
            (tr('ui.pages.mods.sort_by_update_time'), "updated"),
        ):
            self.sort_combo.addItem(label, value)
        self.sort_combo.currentIndexChanged.connect(self._apply_filter)
        row_one.addWidget(self.sort_combo)
        toolbar_layout.addLayout(row_one)

        row_two = QHBoxLayout()
        row_two.setSpacing(8)
        self.scan_button = make_button(
            tr('ui.pages.mods.scan_mods'), icon_name="refresh", variant="primary", on_click=self.scan
        )
        self.install_button = make_button(
            tr('ui.pages.mods.install_archive'), icon_name="download", on_click=self.install_archive
        )
        row_two.addWidget(self.scan_button)
        row_two.addWidget(self.install_button)
        row_two.addWidget(
            make_button(tr('ui.pages.mods.enable_selected'), icon_name="check", on_click=lambda: self.bulk_toggle(True))
        )
        row_two.addWidget(
            make_button(tr('ui.pages.mods.disable_selected'), icon_name="pause", on_click=lambda: self.bulk_toggle(False))
        )
        self.uninstall_button = make_button(
            tr('ui.pages.mods.uninstall_selected'), icon_name="trash", variant="danger", on_click=self.uninstall_selected
        )
        row_two.addWidget(self.uninstall_button)
        self.backups_button = make_button(
            tr('ui.pages.mods.backup_management'), icon_name="backup", on_click=self.show_backups
        )
        row_two.addWidget(self.backups_button)
        row_two.addStretch(1)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("Muted")
        row_two.addWidget(self.progress_label)
        from PySide6.QtWidgets import QProgressBar

        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.hide()
        row_two.addWidget(self.progress)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        row_two.addWidget(self.count_label)
        toolbar_layout.addLayout(row_two)
        outer.addWidget(toolbar)

        self.body = QStackedWidget()
        self.empty = EmptyState(
            tr('ui.pages.mods.no_game_selected'),
            tr('ui.pages.mods.after_you_add_a_game_modpilot_scans_the_mods_folder_0c68e3'),
            "gamepad",
            make_button(
                tr('ui.pages.mods.go_to_game_management'), icon_name="gamepad", variant="primary",
                on_click=lambda: self.controller.go_to("GamesPage"),
            ),
        )
        self.body.addWidget(self.empty)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        table_card = Card()
        table_header = SectionHeader(tr('ui.pages.mods.mod_list'), tr('ui.pages.mods.supports_multi_select_batch_operations'))
        self.refresh_button = make_button(
            tr('ui.pages.mods.refresh_list'), icon_name="refresh", variant="tool", on_click=self.refresh
        )
        table_header.add_action(self.refresh_button)
        table_card.add(table_header)
        self.table = QTableWidget()
        setup_table(
            self.table,
            [tr('ui.pages.mods.name'), tr('ui.pages.mods.version'), tr('ui.pages.mods.status'), tr('ui.pages.mods.update'), tr('ui.pages.mods.translation'), "Nexus", tr('ui.pages.mods.author')],
            stretch_column=0,
            row_height=40,
        )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        table_card.add(self.table, 1)
        splitter.addWidget(table_card)

        details_card = Card()
        details_card.setMinimumWidth(390)
        details_scroll = QScrollArea()
        details_scroll.setWidgetResizable(True)
        details_scroll.setFrameShape(QFrame.NoFrame)
        details_content = QWidget()
        self.details_layout = QVBoxLayout(details_content)
        self.details_layout.setContentsMargins(0, 0, 6, 0)
        self.details_layout.setSpacing(10)
        details_scroll.setWidget(details_content)
        details_card.add(details_scroll, 1)
        splitter.addWidget(details_card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([660, 430])
        self.body.addWidget(splitter)
        outer.addWidget(self.body, 1)
        self._build_details()
        self.refresh()

    # ------------------------------------------------------------------
    # Data and filtering
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        game_id = self.game_id
        if game_id is None:
            self.body.setCurrentIndex(0)
            self.scan_button.setEnabled(False)
            self.install_button.setEnabled(False)
            self.count_label.setText("")
            return
        self.body.setCurrentIndex(1)
        self.scan_button.setEnabled(True)
        self.install_button.setEnabled(True)
        selected_id = self._selected_mod.id if self._selected_mod else None
        try:
            self._all_mods = self.services.mods.list_mods(game_id)
        except Exception as exc:
            log.exception(tr('ui.pages.mods.failed_to_read_mod_list'))
            self.error(tr('ui.pages.mods.failed_to_read_mod_list_0', exc))
            return
        self._by_id = {mod.id: mod for mod in self._all_mods}
        self._apply_filter()
        if selected_id and selected_id in self._by_id:
            self._select_mod(selected_id)
            self._selection_changed()
        elif not self._all_mods:
            self._selected_mod = None
            self._render_details(None)

    def _apply_filter(self) -> None:
        query = self.search.text().strip().casefold()
        filter_key = self.filter_combo.currentData()
        sort_key = self.sort_combo.currentData()
        items = list(self._all_mods)
        if query:
            items = [
                mod
                for mod in items
                if query
                in " ".join(
                    [
                        mod.name,
                        mod.author,
                        mod.unique_id,
                        str(mod.nexus_mod_id or ""),
                    ]
                ).casefold()
            ]
        if filter_key == "enabled":
            items = [mod for mod in items if mod.enabled and mod.is_present]
        elif filter_key == "disabled":
            items = [mod for mod in items if not mod.enabled and mod.is_present]
        elif filter_key == "update":
            items = [mod for mod in items if mod.update_status is UpdateStatus.AVAILABLE]
        elif filter_key == "missing_deps":
            items = [mod for mod in items if mod.missing_dependencies]
        elif filter_key == "duplicate":
            items = [mod for mod in items if mod.duplicate]
        elif filter_key == "untranslated":
            items = [mod for mod in items if mod.translation_eligible and not mod.translation_applied]
        elif filter_key == "kind_content":
            items = [mod for mod in items if mod.kind is ModKind.CONTENT_PACK]
        elif filter_key == "kind_code":
            items = [mod for mod in items if mod.kind is ModKind.CODE]
        elif filter_key == "kind_framework":
            items = [mod for mod in items if mod.kind is ModKind.FRAMEWORK]
        elif filter_key == "missing":
            items = [mod for mod in items if not mod.is_present]
        if sort_key == "name":
            items.sort(key=lambda mod: mod.name.casefold())
        elif sort_key == "author":
            items.sort(key=lambda mod: (mod.author.casefold(), mod.name.casefold()))
        elif sort_key == "size":
            items.sort(key=lambda mod: mod.size_bytes, reverse=True)
        elif sort_key == "updated":
            items.sort(key=lambda mod: mod.updated_at or mod.installed_at, reverse=True)
        self._render_table(items)

    def _render_table(self, items: list[ModView]) -> None:
        colors = theme_colors(self.controller._theme)
        self.table.blockSignals(True)
        clear_table(self.table)
        for mod in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = cell(mod.name, data=mod.id, tooltip=mod.unique_id)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, cell(mod.version))
            if not mod.is_present:
                status_text, status_color = tr('ui.pages.mods.file_missing'), colors["muted"]
            elif mod.enabled:
                status_text, status_color = tr('ui.pages.mods.enabled'), colors["success"]
            else:
                status_text, status_color = tr('ui.pages.mods.disabled'), colors["muted"]
            status_item = cell(status_text)
            status_item.setForeground(QColor(status_color))
            self.table.setItem(row, 2, status_item)
            if mod.update_status is UpdateStatus.AVAILABLE:
                update_text = tr('ui.pages.mods.update_to_0', mod.latest_version or '').strip()
                update_color = colors["warning"]
            elif mod.update_status is UpdateStatus.UP_TO_DATE:
                update_text, update_color = tr('ui.pages.mods.latest'), colors["success"]
            elif mod.update_status is UpdateStatus.ERROR:
                update_text, update_color = tr('ui.pages.mods.check_failed'), colors["danger"]
            elif mod.update_status is UpdateStatus.IGNORED:
                update_text, update_color = tr('ui.pages.mods.ignored'), colors["muted"]
            elif mod.update_status is UpdateStatus.DOWNLOADED:
                update_text, update_color = tr('ui.pages.mods.downloaded'), colors["info"]
            else:
                update_text, update_color = tr('ui.pages.mods.not_checked'), colors["subtle"]
            update_item = cell(update_text, tooltip=mod.update_error or "")
            update_item.setForeground(QColor(update_color))
            self.table.setItem(row, 3, update_item)
            if mod.translation_applied:
                translation_text = tr('ui.pages.mods.localized')
            elif mod.translation_eligible:
                translation_text = (
                    f"{mod.translation_done}/{mod.translation_total}"
                    if mod.translation_total
                    else tr('ui.pages.mods.translatable')
                )
            else:
                translation_text = "—"
            self.table.setItem(row, 4, cell(translation_text))
            self.table.setItem(row, 5, cell(str(mod.nexus_mod_id or "—")))
            self.table.setItem(row, 6, cell(mod.author))
            if not mod.is_present:
                for column in range(self.table.columnCount()):
                    item = self.table.item(row, column)
                    if item:
                        item.setForeground(QColor(colors["muted"]))
        self.table.blockSignals(False)
        self.count_label.setText(tr('ui.pages.mods.0_1_mods', len(items), len(self._all_mods)))
        if self._selected_mod and self._selected_mod.id in {mod.id for mod in items}:
            self._select_mod(self._selected_mod.id)
            self._selection_changed()
        elif items:
            self.table.selectRow(0)
            self._selection_changed()
        else:
            self._selected_mod = None
            self._render_details(None)

    def _select_mod(self, mod_id: int) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.UserRole) == mod_id:
                self.table.selectRow(row)
                self.table.scrollToItem(item)
                break

    def _selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            item = self.table.item(rows[0].row(), 0)
            mod_id = item.data(Qt.UserRole) if item else None
            self._selected_mod = self._by_id.get(mod_id) if mod_id else None
        else:
            self._selected_mod = None
        self._render_details(self._selected_mod)

    def apply_search(self, text: str) -> None:
        self.search.setText(text)
        self._apply_filter()

    # ------------------------------------------------------------------
    # Details pane
    # ------------------------------------------------------------------

    def _build_details(self) -> None:
        self.details_title = QLabel(tr('ui.pages.mods.select_a_mod'))
        self.details_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.details_title.setWordWrap(True)
        self.details_layout.addWidget(self.details_title)
        self.details_meta = QLabel("")
        self.details_meta.setObjectName("Muted")
        self.details_meta.setWordWrap(True)
        self.details_layout.addWidget(self.details_meta)
        self.details_badges = QHBoxLayout()
        self.details_badges.setSpacing(6)
        self.details_layout.addLayout(self.details_badges)
        self.details_path = QLabel("")
        self.details_path.setObjectName("Subtle")
        self.details_path.setWordWrap(True)
        self.details_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details_layout.addWidget(self.details_path)
        divider = QFrame()
        divider.setObjectName("Divider")
        self.details_layout.addWidget(divider)
        self.details_description = QLabel("")
        self.details_description.setWordWrap(True)
        self.details_description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details_layout.addWidget(self.details_description)
        self.deps_title = QLabel(tr('ui.pages.mods.dependencies'))
        self.deps_title.setStyleSheet("font-weight: 650; margin-top: 4px;")
        self.details_layout.addWidget(self.deps_title)
        self.deps_container = QWidget()
        self.deps_layout = QVBoxLayout(self.deps_container)
        self.deps_layout.setContentsMargins(0, 0, 0, 0)
        self.deps_layout.setSpacing(6)
        self.details_layout.addWidget(self.deps_container)
        self.details_error = QLabel("")
        self.details_error.setWordWrap(True)
        self.details_error.setStyleSheet("color: #FF6F7E;")
        self.details_layout.addWidget(self.details_error)

        policy_row = QHBoxLayout()
        policy_label = QLabel(tr('ui.pages.mods.update_policy'))
        policy_label.setObjectName("Muted")
        self.policy_combo = QComboBox()
        for label, value in (
            (tr('ui.pages.mods.inherit_global_settings'), "inherit"),
            (tr('ui.pages.mods.manual_check_only'), "manual"),
            (tr('ui.pages.mods.update_alerts'), "notify"),
            (tr('ui.pages.mods.download_after_confirmation'), "confirm_download"),
            (tr('ui.pages.mods.auto_download'), "auto_download"),
            (tr('ui.pages.mods.auto_download_and_install'), "auto_install"),
            (tr('ui.pages.mods.ignore_update'), "ignore"),
        ):
            self.policy_combo.addItem(label, value)
        self.policy_combo.currentIndexChanged.connect(self._policy_changed)
        policy_row.addWidget(policy_label)
        policy_row.addWidget(self.policy_combo, 1)
        self.details_layout.addLayout(policy_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.toggle_button = make_button(tr('ui.pages.mods.enable'), icon_name="play", variant="primary", on_click=self.toggle_selected)
        self.open_button = make_button(tr('ui.pages.mods.open_folder'), icon_name="folder", on_click=self.open_selected_folder)
        action_row.addWidget(self.toggle_button)
        action_row.addWidget(self.open_button)
        self.details_layout.addLayout(action_row)

        second_row = QHBoxLayout()
        second_row.setSpacing(6)
        self.check_button = make_button(tr('ui.pages.mods.check_updates'), icon_name="update", on_click=self.check_selected_update)
        self.nexus_button = make_button(tr('ui.pages.mods.nexus_page'), icon_name="external", on_click=self.open_nexus)
        self.backup_button = make_button(tr('ui.pages.mods.backup'), icon_name="backup", on_click=self.show_selected_backups)
        self.backup_button.setMinimumWidth(104)
        second_row.addWidget(self.check_button)
        second_row.addWidget(self.nexus_button)
        second_row.addWidget(self.backup_button)
        self.details_layout.addLayout(second_row)

        third_row = QHBoxLayout()
        third_row.setSpacing(6)
        self.pin_button = make_button(tr('ui.pages.mods.lock_version'), icon_name="shield", on_click=self.toggle_pin)
        self.ignore_button = make_button(tr('ui.pages.mods.ignore_version'), icon_name="x", on_click=self.ignore_update)
        third_row.addWidget(self.pin_button)
        third_row.addWidget(self.ignore_button)
        self.details_layout.addLayout(third_row)

        self.delete_button = make_button(
            tr('ui.pages.mods.uninstall_this_mod'), icon_name="trash", variant="danger", on_click=self.uninstall_selected
        )
        self.details_layout.addWidget(self.delete_button)
        self.details_layout.addStretch(1)
        self._render_details(None)

    def _render_details(self, mod: ModView | None) -> None:
        enabled = mod is not None
        for widget in (
            self.policy_combo,
            self.toggle_button,
            self.open_button,
            self.check_button,
            self.nexus_button,
            self.backup_button,
            self.pin_button,
            self.ignore_button,
            self.delete_button,
        ):
            widget.setEnabled(enabled)
        clear_layout(self.details_badges)
        clear_layout(self.deps_layout)
        if mod is None:
            self.details_title.setText(tr('ui.pages.mods.select_a_mod'))
            self.details_meta.setText(tr('ui.pages.mods.view_versions_dependencies_update_policies_and_trans_6e564e'))
            self.details_path.setText("")
            self.details_description.setText("")
            self.details_error.setText("")
            self.deps_title.setText(tr('ui.pages.mods.dependencies'))
            return
        self.details_title.setText(mod.name)
        self.details_meta.setText(
            f"{mod.version} · {mod.author or tr('ui.pages.mods.unknown_author')} · {mod.kind.label if isinstance(mod.kind, ModKind) else mod.kind}"
        )
        if not mod.is_present:
            self.details_badges.addWidget(Badge(tr('ui.pages.mods.file_missing'), "danger"))
        elif mod.enabled:
            self.details_badges.addWidget(Badge(tr('ui.pages.mods.enabled'), "success"))
        else:
            self.details_badges.addWidget(Badge(tr('ui.pages.mods.disabled'), "muted"))
        if mod.duplicate:
            self.details_badges.addWidget(Badge(tr('ui.pages.mods.duplicate_installation'), "danger"))
        if mod.missing_dependencies:
            self.details_badges.addWidget(Badge(tr('ui.pages.mods.missing_0_dependencies', len(mod.missing_dependencies)), "danger"))
        if mod.update_status is UpdateStatus.AVAILABLE:
            self.details_badges.addWidget(Badge(tr('ui.pages.mods.update_available_0', mod.latest_version), "warning"))
        if mod.pinned_version:
            self.details_badges.addWidget(Badge(tr('ui.pages.mods.lock_0', mod.pinned_version), "info"))
        if mod.ignored_version:
            self.details_badges.addWidget(Badge(tr('ui.pages.mods.ignore_0', mod.ignored_version), "muted"))
        self.details_badges.addStretch(1)
        self.details_path.setText(f"{mod.relative_path}\nUniqueID: {mod.unique_id}  ·  {human_size(mod.size_bytes)}")
        self.details_description.setText(mod.description or tr('ui.pages.mods.this_mod_has_no_description'))
        self.details_error.setText(mod.scan_error or mod.update_error or "")
        self.details_error.setVisible(bool(mod.scan_error or mod.update_error))
        self.deps_title.setText(
            tr('ui.pages.mods.dependencies_0', len(mod.dependencies))
            if mod.dependencies
            else tr('ui.pages.mods.dependencies_none_declared')
        )
        if not mod.dependencies:
            empty = QLabel(tr('ui.pages.mods.manifest_declares_no_dependencies'))
            empty.setObjectName("Muted")
            self.deps_layout.addWidget(empty)
        for dependency in mod.dependencies:
            row = QFrame()
            row.setObjectName("CardFlat")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(8, 5, 8, 5)
            layout.setSpacing(6)
            icon_label = QLabel()
            color = theme_colors(self.controller._theme)[
                "success" if dependency.satisfied else "danger" if dependency.required else "warning"
            ]
            icon_label.setPixmap(make_icon("check" if dependency.satisfied else "alert", color, 14).pixmap(14, 14))
            layout.addWidget(icon_label)
            text = dependency.unique_id + (
                f" ≥ {dependency.minimum_version}" if dependency.minimum_version else ""
            )
            label = QLabel(text)
            label.setToolTip(
                tr(
                    'ui.pages.mods.installed_01',
                    dependency.installed_version or tr('ui.pages.mods.not_installed'),
                    tr('ui.pages.mods.disabled_d7940d')
                    if dependency.installed_version and not dependency.enabled
                    else '',
                )
            )
            label.setWordWrap(True)
            layout.addWidget(label, 1)
            if dependency.satisfied:
                layout.addWidget(Badge(tr('ui.pages.mods.met'), "success"))
            elif dependency.installed_version:
                layout.addWidget(
                    Badge(tr('ui.pages.mods.not_enabled') if not dependency.enabled else tr('ui.pages.mods.version_mismatch'), "danger")
                )
            else:
                layout.addWidget(Badge(tr('ui.pages.mods.missing'), "danger"))
            self.deps_layout.addWidget(row)
        self.policy_combo.blockSignals(True)
        index = self.policy_combo.findData(mod.update_policy or "inherit")
        self.policy_combo.setCurrentIndex(max(0, index))
        self.policy_combo.blockSignals(False)
        self.toggle_button.setText(tr('ui.pages.mods.disable') if mod.enabled else tr('ui.pages.mods.enable'))
        self.toggle_button.setIcon(
            make_icon("pause" if mod.enabled else "play", "#FFFFFF", 16)
        )
        self.toggle_button.setEnabled(mod.is_present)
        self.check_button.setEnabled(bool(mod.nexus_mod_id))
        self.nexus_button.setEnabled(bool(mod.nexus_mod_id))
        self.pin_button.setText(tr('ui.pages.mods.unlock_version') if mod.pinned_version else tr('ui.pages.mods.lock_version'))
        self.ignore_button.setText(tr('ui.pages.mods.clear_ignore') if mod.ignored_version else tr('ui.pages.mods.ignore_version_aebc24'))
        self.delete_button.setEnabled(mod.is_present)

    def _text_color(self) -> str:
        return theme_colors(self.controller._theme)["text"]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _selected_ids(self) -> list[int]:
        rows = self.table.selectionModel().selectedRows()
        ids = []
        for index in rows:
            item = self.table.item(index.row(), 0)
            if item and item.data(Qt.UserRole):
                ids.append(int(item.data(Qt.UserRole)))
        return ids

    def scan(self) -> None:
        game_id = self.require_game()
        if game_id is None:
            return
        self.scan_button.setEnabled(False)
        self.progress.show()
        self.progress.setValue(0)
        self.progress_label.setText(tr('ui.pages.mods.scanning'))

        def progress(value: int, message: str) -> None:
            self.progress.setValue(value)
            self.progress_label.setText(message)

        def done(count) -> None:  # type: ignore[no-untyped-def]
            self.scan_button.setEnabled(True)
            self.progress.hide()
            self.progress_label.setText("")
            self.toast(tr('ui.pages.mods.scan_complete_0_mods_detected', count), "success")
            self.controller.on_data_changed()
            self.refresh()

        def failed(message: str) -> None:
            self.scan_button.setEnabled(True)
            self.progress.hide()
            self.progress_label.setText("")
            self.error(message)

        self.tasks.run(
            tr('ui.pages.mods.scan_mods'),
            lambda progress_cb: self.services.mods.scan_game(game_id, progress_cb),
            with_progress=True,
            on_done=done,
            on_error=failed,
            on_progress=progress,
        )

    def install_archive(self) -> None:
        game_id = self.require_game()
        if game_id is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            tr('ui.pages.mods.select_mod_archive'),
            "",
            tr('ui.pages.mods.mod_archives_zip_7z_tar_gz_all_files'),
        )
        if not path:
            return
        self.progress.show()
        self.progress.setRange(0, 0)
        self.progress_label.setText(tr('ui.pages.mods.analyzing_archive'))

        def done(plan) -> None:  # type: ignore[no-untyped-def]
            self.progress.hide()
            self.progress.setRange(0, 100)
            self.progress_label.setText("")
            self._pending_plan = plan
            dialog = InstallPreviewDialog(plan, self)
            if dialog.exec() != dialog.Accepted:
                self.services.mods.discard_preview(plan)
                self._pending_plan = None
                return
            self._execute_install(plan, game_id)

        def failed(message: str) -> None:
            self.progress.hide()
            self.progress.setRange(0, 100)
            self.progress_label.setText("")
            self.error(message)

        self.tasks.run(
            tr('ui.pages.mods.analyze_mod_archive'),
            lambda: self.services.mods.preview_install(game_id, Path(path)),
            on_done=done,
            on_error=failed,
        )

    def _execute_install(self, plan: InstallPlan, game_id: int) -> None:
        self.progress.show()
        self.progress.setRange(0, 100)
        self.progress_label.setText(tr('ui.pages.mods.installing'))

        def progress(value: int, message: str) -> None:
            self.progress.setValue(value)
            self.progress_label.setText(message)

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.progress.hide()
            self.progress_label.setText("")
            self._pending_plan = None
            backup_text = tr('ui.pages.mods.created_0_backups', len(result.backup_ids)) if result.backup_ids else ""
            self.toast(tr('ui.pages.mods.installed_0_mods1', len(result.installed_names) or len(plan.entries), backup_text), "success")
            if result.warnings:
                log.info(tr('ui.pages.mods.installation_warning_s'), result.warnings)
            self.controller.on_data_changed()
            self.refresh()

        def failed(message: str) -> None:
            self.progress.hide()
            self.progress_label.setText("")
            self._pending_plan = None
            self.services.mods.discard_preview(plan)
            self.error(message)

        self.tasks.run(
            tr('ui.pages.mods.install_mod'),
            lambda progress_cb: self.services.mods.install_plan(
                game_id,
                plan,
                allow_replace=True,
                allow_duplicate=True,
                progress=progress_cb,
            ),
            with_progress=True,
            on_done=done,
            on_error=failed,
            on_progress=progress,
        )

    def bulk_toggle(self, enabled: bool) -> None:
        ids = self._selected_ids()
        if not ids:
            self.toast(tr('ui.pages.mods.please_select_at_least_one_mod_first'), "warning")
            return
        self.progress.show()
        self.progress.setRange(0, 0)
        self.progress_label.setText(tr('ui.pages.mods.toggling_status'))

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.progress.hide()
            self.progress.setRange(0, 100)
            self.progress_label.setText("")
            if result.failed:
                self.error(
                    tr('ui.pages.mods.failed_to_toggle_some_mods')
                    + "\n".join(f"{name}: {message}" for name, message in result.failed[:8])
                )
            elif result.succeeded:
                self.toast(tr('ui.pages.mods.0_1_mods_c138bc', tr('ui.pages.mods.enable') if enabled else tr('ui.pages.mods.disable'), len(result.succeeded)), "success")
            else:
                self.toast(tr('ui.pages.mods.no_mods_need_a_status_change'), "info")
            self.controller.on_data_changed()
            self.refresh()

        def failed(message: str) -> None:
            self.progress.hide()
            self.progress.setRange(0, 100)
            self.progress_label.setText("")
            self.error(message)

        self.tasks.run(
            tr('ui.pages.mods.toggle_mod_status_in_bulk'),
            lambda: self.services.mods.bulk_set_enabled(ids, enabled),
            on_done=done,
            on_error=failed,
        )

    def toggle_selected(self) -> None:
        if not self._selected_mod:
            return
        self.bulk_toggle(not self._selected_mod.enabled)

    def uninstall_selected(self) -> None:
        mod = self._selected_mod
        if not mod:
            return
        if not self.confirm(
            tr('ui.pages.mods.uninstall_mod'),
            tr('ui.pages.mods.the_folder_for_0_will_be_backed_up_and_then_deleted_2cb6c1', mod.name),
            danger=True,
        ):
            return
        self.progress.show()
        self.progress.setRange(0, 0)
        self.progress_label.setText(tr('ui.pages.mods.uninstalling'))

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.progress.hide()
            self.progress.setRange(0, 100)
            self.progress_label.setText("")
            self.toast(
                tr('ui.pages.mods.uninstalled_0', result.mod_name) + (tr('ui.pages.mods.backed_up') if result.backup_id else ""),
                "success",
            )
            self.controller.on_data_changed()
            self.refresh()

        def failed(message: str) -> None:
            self.progress.hide()
            self.progress.setRange(0, 100)
            self.progress_label.setText("")
            self.error(message)

        self.tasks.run(
            tr('ui.pages.mods.uninstall_mod'),
            lambda: self.services.mods.uninstall(mod.id),
            on_done=done,
            on_error=failed,
        )

    def open_selected_folder(self) -> None:
        if not self._selected_mod or not self.current_game:
            return
        path = Path(self.current_game.mods_path) / self._selected_mod.relative_path
        if not path.exists():
            self.error(tr('ui.pages.mods.the_mods_folder_does_not_exist_and_may_have_been_del_36d580'))
            return
        open_in_explorer(str(path))

    def open_nexus(self) -> None:
        if self._selected_mod and self._selected_mod.nexus_mod_id and self.current_game:
            QDesktopServices.openUrl(
                QUrl(
                    f"https://www.nexusmods.com/{self.current_game.nexus_domain}/mods/"
                    f"{self._selected_mod.nexus_mod_id}"
                )
            )

    def check_selected_update(self) -> None:
        mod = self._selected_mod
        if not mod or not mod.nexus_mod_id:
            return
        self.check_button.setEnabled(False)
        self.check_button.setText(tr('ui.pages.mods.checking'))

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.check_button.setEnabled(True)
            self.check_button.setText(tr('ui.pages.mods.check_updates'))
            self.toast(result.message or tr('ui.pages.mods.check_complete'), "success" if result.status.value != "error" else "danger")
            self.refresh()
            self.controller.on_data_changed()

        def failed(message: str) -> None:
            self.check_button.setEnabled(True)
            self.check_button.setText(tr('ui.pages.mods.check_updates'))
            self.error(message)

        self.tasks.run(
            tr('ui.pages.mods.check_single_mod_update'),
            lambda: self.services.updates.check_mod(mod.id),
            on_done=done,
            on_error=failed,
        )

    def _policy_changed(self) -> None:
        if not self._selected_mod or not self.policy_combo.isEnabled():
            return
        policy = self.policy_combo.currentData()
        try:
            self.services.mods.set_update_policy(self._selected_mod.id, str(policy))
        except Exception as exc:
            self.error(str(exc))
            return
        self._selected_mod = self.services.mods.get_mod(self._selected_mod.id)
        self.toast(tr('ui.pages.mods.update_policy_set_to_0', self.policy_combo.currentText()), "success")

    def toggle_pin(self) -> None:
        mod = self._selected_mod
        if not mod:
            return
        try:
            if mod.pinned_version:
                self.services.mods.set_pinned_version(mod.id, None)
                self.toast(tr('ui.pages.mods.version_unlocked'), "success")
            else:
                self.services.mods.set_pinned_version(mod.id, mod.version)
                self.toast(tr('ui.pages.mods.locked_at_0', mod.version), "success")
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()

    def ignore_update(self) -> None:
        mod = self._selected_mod
        if not mod:
            return
        try:
            if mod.ignored_version:
                self.services.mods.clear_update_ignore(mod.id)
                self.toast(tr('ui.pages.mods.ignored_version_cleared'), "success")
            elif mod.latest_version:
                self.services.mods.ignore_version(mod.id, mod.latest_version)
                self.toast(tr('ui.pages.mods.version_0_ignored', mod.latest_version), "success")
            else:
                self.toast(tr('ui.pages.mods.please_check_for_updates_before_ignoring_a_specific_b26063'), "warning")
        except Exception as exc:
            self.error(str(exc))
            return
        self.refresh()

    def show_backups(self) -> None:
        if not self.game_id:
            return
        mod_id = self._selected_mod.id if self._selected_mod else None
        self._show_backups_dialog(mod_id)

    def show_selected_backups(self) -> None:
        if self._selected_mod:
            self._show_backups_dialog(self._selected_mod.id)

    def _show_backups_dialog(self, mod_id: int | None) -> None:
        if not self.game_id:
            return
        try:
            backups = self.services.backups.list_for_game(self.game_id, mod_id)
        except Exception as exc:
            self.error(str(exc))
            return

        def restore(backup) -> None:  # type: ignore[no-untyped-def]
            if not self.confirm(
                tr('ui.pages.mods.restore_backup'),
                tr('ui.pages.mods.restore_0_the_current_files_are_backed_up_automatica_073f46', backup.label),
                danger=True,
            ):
                return
            self.tasks.run(
                tr('ui.pages.mods.restore_backup'),
                lambda: self.services.mods.rollback(backup.id),
                on_done=lambda _result: self._after_restore(),
                on_error=self.error,
            )

        def delete(backup) -> None:  # type: ignore[no-untyped-def]
            if not self.confirm(tr('ui.pages.mods.delete_backup'), tr('ui.pages.mods.permanently_delete_backup_files_0', backup.path), danger=True):
                return
            try:
                self.services.backups.delete(backup.id)
                self.toast(tr('ui.pages.mods.backup_deleted'), "success")
            except Exception as exc:
                self.error(str(exc))

        BackupsDialog(backups, self, on_restore=restore, on_delete=delete).exec()

    def _after_restore(self) -> None:
        self.toast(tr('ui.pages.mods.backup_restored_and_rescan_complete'), "success")
        self.controller.on_data_changed()
        self.refresh()
