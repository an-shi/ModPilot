from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...domain.enums import TranslationStatus
from ...domain.models import TranslationCandidate, TranslationOverview, TranslationUnitView
from ..dialogs import TranslationPackDialog
from ..theme import theme_colors
from ..widgets import (
    Badge,
    Card,
    EmptyState,
    SectionHeader,
    cell,
    clear_table,
    make_button,
    make_icon,
    setup_table,
)
from .base import BasePage
from ...i18n import tr

log = logging.getLogger(__name__)


class TranslationPage(BasePage):
    title = tr('ui.pages.translation.translation_center')
    subtitle = tr('ui.pages.translation.existing_packs_local_cache_then_configurable_ai_tran_8cb19e')

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        self._overview: list[TranslationOverview] = []
        self._units: list[TranslationUnitView] = []
        self._candidates: list[TranslationCandidate] = []
        self._selected_mod_id: int | None = None
        self._selected_unit_id: int | None = None
        self._loading_units_for: int | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self.body = QStackedWidget()
        outer.addWidget(self.body, 1)
        self.empty = EmptyState(
            tr('ui.pages.translation.no_game_selected'),
            tr('ui.pages.translation.after_you_add_a_game_modpilot_detects_mods_that_supp_370ca0'),
            "translate",
            make_button(tr('ui.pages.translation.go_to_game_management'), icon_name="gamepad", variant="primary", on_click=lambda: self.controller.go_to("GamesPage")),
        )
        self.body.addWidget(self.empty)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.body.addWidget(content)

        self.ai_banner = Card()
        banner_row = QHBoxLayout()
        self.ai_status_icon = QLabel()
        banner_row.addWidget(self.ai_status_icon)
        self.ai_status_label = QLabel("")
        self.ai_status_label.setWordWrap(True)
        banner_row.addWidget(self.ai_status_label, 1)
        self.ai_settings_button = make_button(
            tr('ui.pages.translation.ai_settings'), icon_name="settings", on_click=lambda: self.controller.go_to("SettingsPage")
        )
        banner_row.addWidget(self.ai_settings_button)
        self.cache_label = QLabel("")
        self.cache_label.setObjectName("Muted")
        banner_row.addWidget(self.cache_label)
        self.ai_banner.add_layout(banner_row)
        layout.addWidget(self.ai_banner)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        left = Card()
        left_header = SectionHeader(tr('ui.pages.translation.translatable_mods'), tr('ui.pages.translation.mods_with_i18n_source_files_detected'))
        self.reload_button = make_button(
            tr('ui.pages.translation.re_detect'), icon_name="refresh", variant="tool", on_click=self.load_overview
        )
        left_header.add_action(self.reload_button)
        left.add(left_header)
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText(tr('ui.pages.translation.search_mods'))
        self.mod_search.setClearButtonEnabled(True)
        self.mod_search.textChanged.connect(lambda _text: self._render_overview())
        left.add(self.mod_search)
        self.mod_table = QTableWidget()
        setup_table(
            self.mod_table,
            ["Mod", tr('ui.pages.translation.progress'), tr('ui.pages.translation.status')],
            stretch_column=0,
            row_height=40,
        )
        self.mod_table.itemSelectionChanged.connect(self._mod_selection_changed)
        left.add(self.mod_table, 1)
        self.left_hint = QLabel("")
        self.left_hint.setObjectName("Muted")
        self.left_hint.setWordWrap(True)
        left.add(self.left_hint)
        splitter.addWidget(left)

        right = Card()
        self.tabs = QTabWidget()
        right.add(self.tabs, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([310, 820])
        layout.addWidget(splitter, 1)

        # Editor tab -----------------------------------------------------
        editor_tab = QWidget()
        editor_layout = QVBoxLayout(editor_tab)
        editor_layout.setContentsMargins(4, 8, 4, 4)
        editor_layout.setSpacing(8)
        self.editor_title = QLabel(tr('ui.pages.translation.select_a_translatable_mod'))
        self.editor_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        editor_layout.addWidget(self.editor_title)
        self.editor_meta = QLabel("")
        self.editor_meta.setObjectName("Muted")
        self.editor_meta.setWordWrap(True)
        editor_layout.addWidget(self.editor_meta)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.ai_all_button = make_button(
            tr('ui.pages.translation.translate_pending'), icon_name="sparkles", variant="primary", on_click=self.translate_all
        )
        self.ai_selected_button = make_button(
            tr('ui.pages.translation.retranslate'), icon_name="refresh", on_click=self.retranslate_selected
        )
        self.apply_button = make_button(
            tr('ui.pages.translation.apply_saved'), icon_name="save", on_click=self.apply_saved
        )
        self.export_button = make_button(tr('ui.pages.translation.export'), icon_name="download", on_click=self.export_units)
        self.remove_button = make_button(
            tr('ui.pages.translation.remove_file'), icon_name="trash", variant="danger", on_click=self.remove_translation
        )
        for button in (
            self.ai_all_button,
            self.ai_selected_button,
            self.apply_button,
            self.export_button,
            self.remove_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        editor_layout.addLayout(toolbar)
        second_toolbar = QHBoxLayout()
        second_toolbar.setSpacing(6)
        second_toolbar.addWidget(
            make_button(tr('ui.pages.translation.import_pack'), icon_name="folder", on_click=self.import_pack)
        )
        second_toolbar.addWidget(
            make_button(tr('ui.pages.translation.search_packs'), icon_name="search", on_click=self.search_packs)
        )
        self.editor_status = QLabel("")
        self.editor_status.setObjectName("Muted")
        self.editor_status.setWordWrap(True)
        second_toolbar.addWidget(self.editor_status, 1)
        editor_layout.addLayout(second_toolbar)
        self.translate_progress = QProgressBar()
        self.translate_progress.hide()
        editor_layout.addWidget(self.translate_progress)

        editor_split = QSplitter(Qt.Vertical)
        editor_split.setChildrenCollapsible(False)
        self.unit_table = QTableWidget()
        setup_table(
            self.unit_table,
            ["Key", tr('ui.pages.translation.source'), tr('ui.pages.translation.translation'), tr('ui.pages.translation.status'), tr('ui.pages.translation.source_c63f79'), tr('ui.pages.translation.model'), tr('ui.pages.translation.time'), tr('ui.pages.translation.confirm')],
            stretch_column=1,
            row_height=34,
        )
        self.unit_table.itemSelectionChanged.connect(self._unit_selection_changed)
        editor_split.addWidget(self.unit_table)

        editor_card = Card(flat=True)
        key_row = QHBoxLayout()
        key_label = QLabel(tr('ui.pages.translation.entry'))
        key_label.setObjectName("Muted")
        key_row.addWidget(key_label)
        self.unit_key = QLabel("—")
        self.unit_key.setTextInteractionFlags(Qt.TextSelectableByMouse)
        key_row.addWidget(self.unit_key, 1)
        self.unit_status = Badge("—", "muted")
        key_row.addWidget(self.unit_status)
        editor_card.add_layout(key_row)
        source_label = QLabel(tr('ui.pages.translation.source'))
        source_label.setObjectName("Muted")
        editor_card.add(source_label)
        self.source_edit = QPlainTextEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setMaximumHeight(88)
        editor_card.add(self.source_edit)
        translation_label = QLabel(tr('ui.pages.translation.translation_preserves_placeholders_and_json_structur_cdc513'))
        translation_label.setObjectName("Muted")
        editor_card.add(translation_label)
        self.translation_edit = QPlainTextEdit()
        self.translation_edit.setMaximumHeight(110)
        self.translation_edit.setPlaceholderText(tr('ui.pages.translation.edit_translation_here'))
        editor_card.add(self.translation_edit)
        editor_actions = QHBoxLayout()
        self.confirm_check = QCheckBox(tr('ui.pages.translation.manual_confirmation'))
        editor_actions.addWidget(self.confirm_check)
        self.save_button = make_button(tr('ui.pages.translation.save_translation'), icon_name="save", variant="primary", on_click=lambda: self.save_unit(False))
        self.save_confirm_button = make_button(tr('ui.pages.translation.save_and_confirm'), icon_name="check", on_click=lambda: self.save_unit(True))
        self.retranslate_button = make_button(tr('ui.pages.translation.retranslate_789877'), icon_name="sparkles", on_click=self.retranslate_selected)
        editor_actions.addWidget(self.save_button)
        editor_actions.addWidget(self.save_confirm_button)
        editor_actions.addWidget(self.retranslate_button)
        editor_actions.addStretch(1)
        self.unit_error = QLabel("")
        self.unit_error.setStyleSheet("color: #FF6F7E;")
        self.unit_error.setWordWrap(True)
        editor_actions.addWidget(self.unit_error, 1)
        editor_card.add_layout(editor_actions)
        editor_split.addWidget(editor_card)
        editor_split.setSizes([450, 320])
        editor_layout.addWidget(editor_split, 1)
        self.tabs.addTab(editor_tab, tr('ui.pages.translation.translation_editor'))

        # Packs tab ------------------------------------------------------
        packs_tab = QWidget()
        packs_layout = QVBoxLayout(packs_tab)
        packs_layout.setContentsMargins(4, 8, 4, 4)
        packs_layout.setSpacing(8)
        pack_toolbar = QHBoxLayout()
        self.pack_search_button = make_button(
            tr('ui.pages.translation.search_nexus_github_translation_packs'), icon_name="search", variant="primary", on_click=self.search_packs
        )
        self.pack_import_button = make_button(tr('ui.pages.translation.import_downloaded_translation_pack'), icon_name="folder", on_click=self.import_pack)
        self.pack_open_button = make_button(tr('ui.pages.translation.open_selected_page'), icon_name="external", on_click=self.open_pack)
        pack_toolbar.addWidget(self.pack_search_button)
        pack_toolbar.addWidget(self.pack_import_button)
        pack_toolbar.addWidget(self.pack_open_button)
        pack_toolbar.addStretch(1)
        packs_layout.addLayout(pack_toolbar)
        self.pack_hint = QLabel(
            tr('ui.pages.translation.translation_pack_search_suggests_candidates_from_pub_9ae403')
        )
        self.pack_hint.setObjectName("Muted")
        self.pack_hint.setWordWrap(True)
        packs_layout.addWidget(self.pack_hint)
        self.pack_table = QTableWidget()
        setup_table(
            self.pack_table,
            [tr('ui.pages.translation.source_c63f79'), tr('ui.pages.translation.title'), tr('ui.pages.translation.compatibility_assessment'), tr('ui.pages.translation.author'), tr('ui.pages.translation.update_time')],
            stretch_column=1,
            row_height=38,
        )
        self.pack_table.doubleClicked.connect(lambda _index: self.open_pack())
        packs_layout.addWidget(self.pack_table, 1)
        self.pack_details = QLabel("")
        self.pack_details.setObjectName("Muted")
        self.pack_details.setWordWrap(True)
        packs_layout.addWidget(self.pack_details)
        self.tabs.addTab(packs_tab, tr('ui.pages.translation.pack_search'))
        self._set_editor_enabled(False)
        self.refresh()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self.game_id is None:
            self.body.setCurrentIndex(0)
            return
        self.body.setCurrentIndex(1)
        self._update_ai_status()
        if not self._overview:
            self._render_overview(loading=True)
        self.load_overview()

    def _update_ai_status(self) -> None:
        configured = self.services.llm_configured
        colors = theme_colors(self.controller._theme)
        self.ai_status_icon.setPixmap(
            make_icon("sparkles" if configured else "alert", colors["success" if configured else "warning"], 20).pixmap(20, 20)
        )
        if configured:
            self.ai_status_label.setText(
                tr('ui.pages.translation.ai_translation_ready_0_1', self.services.config.config.llm.model, self.services.config.config.llm.base_url)
            )
            self.ai_settings_button.setText(tr('ui.pages.translation.ai_settings'))
        else:
            self.ai_status_label.setText(
                tr('ui.pages.translation.no_ai_model_configured_you_can_still_search_for_or_i_737f38')
            )
            self.ai_settings_button.setText(tr('ui.pages.translation.configure_ai'))
        try:
            total, confirmed = self.services.translation.cache_stats()
            self.cache_label.setText(tr('ui.pages.translation.0_cached_translations_1_confirmed', total, confirmed))
        except Exception:
            self.cache_label.setText("")
        self.ai_all_button.setEnabled(configured and self._selected_mod_id is not None)
        self.ai_selected_button.setEnabled(configured and self._selected_unit_id is not None)
        self.retranslate_button.setEnabled(configured and self._selected_unit_id is not None)

    def load_overview(self) -> None:
        if self.game_id is None:
            return
        game_id = self.game_id
        self._render_overview(loading=True)
        self.reload_button.setEnabled(False)

        def done(overview) -> None:  # type: ignore[no-untyped-def]
            if self.game_id != game_id:
                return
            self._overview = overview
            self.reload_button.setEnabled(True)
            self._render_overview()
            self._update_ai_status()

        def failed(message: str) -> None:
            self.reload_button.setEnabled(True)
            self.left_hint.setText(tr('ui.pages.translation.detection_failed_0', message))
            self.toast(tr('ui.pages.translation.failed_to_detect_translatable_mods_0', message), "danger")

        self.tasks.run(
            tr('ui.pages.translation.detect_translatable_mods'),
            lambda: self.services.translation.overview(game_id),
            on_done=done,
            on_error=failed,
        )

    def _render_overview(self, *, loading: bool = False) -> None:
        query = self.mod_search.text().strip().casefold() if hasattr(self, "mod_search") else ""
        selected_id = self._selected_mod_id
        self.mod_table.blockSignals(True)
        clear_table(self.mod_table)
        items = [
            item
            for item in self._overview
            if not query or query in item.mod_name.casefold()
        ]
        for item in items:
            row = self.mod_table.rowCount()
            self.mod_table.insertRow(row)
            title = item.mod_name
            self.mod_table.setItem(row, 0, cell(title, data=item.mod_id, tooltip=item.message or item.source_file))
            progress_text = (
                f"{item.translated_units}/{item.total_units}" if item.total_units else "—"
            )
            self.mod_table.setItem(row, 1, cell(progress_text))
            if not item.eligible:
                status_text = tr('ui.pages.translation.unavailable')
            elif item.applied:
                status_text = tr('ui.pages.translation.applied')
            elif item.translated_units:
                status_text = tr('ui.pages.translation.translation_exists')
            else:
                status_text = tr('ui.pages.translation.pending_translation')
            self.mod_table.setItem(row, 2, cell(status_text))
        self.mod_table.blockSignals(False)
        self.left_hint.setText(
            tr('ui.pages.translation.detecting') if loading else tr('ui.pages.translation.0_mods_support_standard_translation_files', len(items))
        )
        if selected_id:
            self._select_mod(selected_id)
        elif items:
            self.mod_table.selectRow(0)

    def _select_mod(self, mod_id: int) -> None:
        for row in range(self.mod_table.rowCount()):
            item = self.mod_table.item(row, 0)
            if item and item.data(Qt.UserRole) == mod_id:
                self.mod_table.selectRow(row)
                return

    def _mod_selection_changed(self) -> None:
        rows = self.mod_table.selectionModel().selectedRows()
        if not rows:
            self._selected_mod_id = None
            self._units = []
            self._render_units()
            self._set_editor_enabled(False)
            return
        item = self.mod_table.item(rows[0].row(), 0)
        mod_id = item.data(Qt.UserRole) if item else None
        if not mod_id:
            return
        self._selected_mod_id = int(mod_id)
        self._load_units(self._selected_mod_id)

    def _load_units(self, mod_id: int) -> None:
        self._loading_units_for = mod_id
        self._units = []
        self._render_units(loading=True)
        self._set_editor_enabled(False)
        overview = next((item for item in self._overview if item.mod_id == mod_id), None)
        self.editor_title.setText(overview.mod_name if overview else tr('ui.pages.translation.loading'))
        self.editor_meta.setText(tr('ui.pages.translation.reading_i18n_files_and_syncing_local_translation_rec_d53af3'))
        self.tabs.setCurrentIndex(0)
        self._selected_unit_id = None

        def done(units) -> None:  # type: ignore[no-untyped-def]
            if self._selected_mod_id != mod_id:
                return
            self._units = units
            self._render_units()
            self._update_editor_header(overview)
            self._set_editor_enabled(bool(units))
            if units:
                self._select_unit(units[0].id)
            proxy = self.services.translation.cached_candidates(mod_id)
            self._candidates = proxy
            self._render_candidates()

        def failed(message: str) -> None:
            self.editor_meta.setText(tr('ui.pages.translation.failed_to_read_translation_file_0', message))
            self.toast(tr('ui.pages.translation.failed_to_read_translation_file_0', message), "danger")

        self.tasks.run(
            tr('ui.pages.translation.read_translation_file'),
            lambda: self.services.translation.get_units(mod_id),
            on_done=done,
            on_error=failed,
        )

    def _update_editor_header(self, overview: TranslationOverview | None) -> None:
        if overview is None:
            return
        self.editor_title.setText(overview.mod_name)
        applied = tr('ui.pages.translation.target_file_generated') if overview.applied else tr('ui.pages.translation.target_file_not_generated_yet')
        self.editor_meta.setText(
            tr('ui.pages.translation.0_1_2_entries_3', overview.source_file, overview.target_file, overview.total_units, applied)
            + (f"\n{overview.message}" if overview.message else "")
        )
        self.editor_status.setText(
            tr('ui.pages.translation.translation_0_1_2_confirmed', overview.translated_units, overview.total_units, overview.confirmed_units)
        )

    def _render_units(self, *, loading: bool = False) -> None:
        selected_id = self._selected_unit_id
        colors = theme_colors(self.controller._theme)
        self.unit_table.blockSignals(True)
        clear_table(self.unit_table)
        if loading:
            self.editor_status.setText(tr('ui.pages.translation.loading_translations'))
        for unit in self._units:
            row = self.unit_table.rowCount()
            self.unit_table.insertRow(row)
            self.unit_table.setItem(row, 0, cell(unit.key, data=unit.id, tooltip=unit.key))
            self.unit_table.setItem(row, 1, cell(unit.source_text, tooltip=unit.source_text))
            self.unit_table.setItem(row, 2, cell(unit.translated_text, tooltip=unit.translated_text))
            status_item = cell(unit.status.label)
            status_item.setForeground(
                QColor(
                    colors["success"]
                    if unit.status is TranslationStatus.CONFIRMED
                    else colors["warning"]
                    if unit.status is TranslationStatus.FAILED
                    else colors["accent"]
                    if unit.status is TranslationStatus.TRANSLATED
                    else colors["muted"]
                )
            )
            self.unit_table.setItem(row, 3, status_item)
            self.unit_table.setItem(row, 4, cell(unit.origin.label))
            self.unit_table.setItem(row, 5, cell(unit.model or "—"))
            self.unit_table.setItem(
                row,
                6,
                cell(unit.updated_at.strftime("%m-%d %H:%M") if unit.updated_at else "—"),
            )
            self.unit_table.setItem(row, 7, cell("✓" if unit.confirmed else ""))
            if unit.error:
                for column in range(self.unit_table.columnCount()):
                    item = self.unit_table.item(row, column)
                    if item:
                        item.setToolTip(f"{item.toolTip()}\n{unit.error}".strip())
        self.unit_table.blockSignals(False)
        if selected_id:
            self._select_unit(selected_id)
        elif self._units:
            self._select_unit(self._units[0].id)
        else:
            self._render_unit(None)

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (
            self.save_button,
            self.save_confirm_button,
            self.translation_edit,
            self.confirm_check,
            self.apply_button,
            self.export_button,
            self.remove_button,
            self.pack_import_button,
            self.pack_search_button,
        ):
            widget.setEnabled(enabled)
        configured = self.services.llm_configured
        self.ai_all_button.setEnabled(enabled and configured)
        self.ai_selected_button.setEnabled(
            enabled and configured and self._selected_unit_id is not None
        )
        self.retranslate_button.setEnabled(
            enabled and configured and self._selected_unit_id is not None
        )
        if not enabled:
            self.unit_key.setText("—")
            self.source_edit.clear()
            self.translation_edit.clear()
            self.unit_status.setText("—")
            self.unit_status.set_tone("muted")
            self.unit_error.setText("")

    def _select_unit(self, unit_id: int) -> None:
        for row in range(self.unit_table.rowCount()):
            item = self.unit_table.item(row, 0)
            if item and item.data(Qt.UserRole) == unit_id:
                self.unit_table.selectRow(row)
                self.unit_table.scrollToItem(item)
                return

    def _unit_selection_changed(self) -> None:
        rows = self.unit_table.selectionModel().selectedRows()
        if not rows:
            self._render_unit(None)
            return
        item = self.unit_table.item(rows[0].row(), 0)
        unit_id = item.data(Qt.UserRole) if item else None
        unit = next((unit for unit in self._units if unit.id == unit_id), None)
        self._render_unit(unit)

    def _render_unit(self, unit: TranslationUnitView | None) -> None:
        self._selected_unit_id = unit.id if unit else None
        enabled = unit is not None
        self.translation_edit.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.save_confirm_button.setEnabled(enabled)
        self.confirm_check.setEnabled(enabled)
        self.retranslate_button.setEnabled(enabled and self.services.llm_configured)
        if unit is None:
            self.unit_key.setText("—")
            self.source_edit.clear()
            self.translation_edit.clear()
            self.unit_status.setText("—")
            self.unit_status.set_tone("muted")
            self.unit_error.setText("")
            self.confirm_check.setChecked(False)
            return
        self.unit_key.setText(unit.key)
        self.source_edit.setPlainText(unit.source_text)
        self.translation_edit.setPlainText(unit.translated_text)
        self.confirm_check.setChecked(unit.confirmed)
        self.unit_status.setText(unit.status.label)
        self.unit_status.set_tone(
            "success"
            if unit.status is TranslationStatus.CONFIRMED
            else "danger"
            if unit.status is TranslationStatus.FAILED
            else "accent"
            if unit.status is TranslationStatus.TRANSLATED
            else "muted"
        )
        self.unit_error.setText(unit.error or "")
        self._update_ai_status()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _begin_progress(self, text: str) -> None:
        self.translate_progress.setRange(0, 100)
        self.translate_progress.setValue(0)
        self.translate_progress.show()
        self.editor_status.setText(text)

    def _end_progress(self) -> None:
        self.translate_progress.hide()

    def translate_all(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            return
        if not self.services.llm_configured:
            self.error(tr('ui.pages.translation.please_configure_an_ai_model_and_api_key_in_settings_69ec9e'))
            return
        pending = [
            unit
            for unit in self._units
            if not unit.translated_text.strip() and not unit.confirmed
        ]
        if not pending:
            self.toast(tr('ui.pages.translation.the_current_mod_has_no_entries_to_translate_to_overw_b4a9c4'), "info")
            return
        if not self.confirm(
            tr('ui.pages.translation.ai_translation'),
            tr('ui.pages.translation.model_0_will_be_used_to_translate_1_unfinished_entri_f6f2e1', self.services.config.config.llm.model, len(pending)),
        ):
            return
        self._begin_progress(tr('ui.pages.translation.preparing_translation'))

        def progress(value: int, message: str) -> None:
            self.translate_progress.setValue(value)
            self.editor_status.setText(message)

        def done(count) -> None:  # type: ignore[no-untyped-def]
            self._end_progress()
            self.toast(tr('ui.pages.translation.ai_translation_complete_0_new_entries_added_and_veri_1c410d', count), "success")
            self._refresh_after_change(mod_id)

        def failed(message: str) -> None:
            self._end_progress()
            self.unit_error.setText(message)
            self.error(message)

        self.tasks.run(
            tr('ui.pages.translation.ai_translation'),
            lambda progress_cb: self.services.translation.translate_mod(mod_id, progress=progress_cb),
            with_progress=True,
            on_progress=progress,
            on_done=done,
            on_error=failed,
        )

    def retranslate_selected(self) -> None:
        unit_id = self._selected_unit_id
        mod_id = self._selected_mod_id
        if unit_id is None or mod_id is None:
            return
        unit = next((unit for unit in self._units if unit.id == unit_id), None)
        if unit is None:
            return
        if not self.services.llm_configured:
            self.error(tr('ui.pages.translation.please_configure_an_ai_model_and_api_key_in_settings_69ec9e'))
            return
        if unit.confirmed and not self.confirm(
            tr('ui.pages.translation.retranslate_confirmed_entries'),
            tr('ui.pages.translation.this_entry_has_been_manually_confirmed_re_running_ai_ed64b4'),
            danger=True,
        ):
            return
        self._begin_progress(tr('ui.pages.translation.re_translating_selected_entries'))

        def done(count) -> None:  # type: ignore[no-untyped-def]
            self._end_progress()
            self.toast(tr('ui.pages.translation.entry_retranslated') if count else tr('ui.pages.translation.nothing_to_translate'), "success")
            self._refresh_after_change(mod_id)

        def failed(message: str) -> None:
            self._end_progress()
            self.error(message)

        self.tasks.run(
            tr('ui.pages.translation.retranslate_entries'),
            lambda: self.services.translation.translate_mod(mod_id, keys={unit.key}, force=True),
            on_done=done,
            on_error=failed,
        )

    def save_unit(self, confirmed: bool) -> None:
        unit = next((unit for unit in self._units if unit.id == self._selected_unit_id), None)
        if unit is None or self._selected_mod_id is None:
            return
        text = self.translation_edit.toPlainText()
        use_confirm = bool(confirmed or self.confirm_check.isChecked())
        self.save_button.setEnabled(False)
        self.save_confirm_button.setEnabled(False)

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.save_button.setEnabled(True)
            self.save_confirm_button.setEnabled(True)
            backup = tr('ui.pages.translation.created_backup_0', result.backup_id) if result.backup_id else ""
            self.toast(tr('ui.pages.translation.translation_saved_and_written_to_target_file_0', backup), "success")
            self._refresh_after_change(self._selected_mod_id or 0, keep_unit=unit.id)

        def failed(message: str) -> None:
            self.save_button.setEnabled(True)
            self.save_confirm_button.setEnabled(True)
            self.error(message)

        self.tasks.run(
            tr('ui.pages.translation.save_translation'),
            lambda: self.services.translation.save_unit(unit.id, text, confirmed=use_confirm),
            on_done=done,
            on_error=failed,
        )

    def apply_saved(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            return
        self._begin_progress(tr('ui.pages.translation.applying_saved_translations'))

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self._end_progress()
            self.toast(tr('ui.pages.translation.wrote_0_translations', result.translated_units), "success")
            self._refresh_after_change(mod_id)

        def failed(message: str) -> None:
            self._end_progress()
            self.error(message)

        self.tasks.run(
            tr('ui.pages.translation.apply_translations'),
            lambda: self.services.translation.apply_saved(mod_id),
            on_done=done,
            on_error=failed,
        )

    def remove_translation(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            return
        if not self.confirm(
            tr('ui.pages.translation.remove_file'),
            tr('ui.pages.translation.delete_generated_i18n_zh_json_original_mod_files_wil_85885a'),
            danger=True,
        ):
            return
        self.tasks.run(
            tr('ui.pages.translation.remove_file'),
            lambda: self.services.translation.remove_translation(mod_id),
            on_done=lambda _result: self._after_remove(mod_id),
            on_error=self.error,
        )

    def _after_remove(self, mod_id: int) -> None:
        self.toast(tr('ui.pages.translation.generated_translation_files_removed_the_original_mod_1eabac'), "success")
        self._refresh_after_change(mod_id)

    def export_units(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            return
        overview = next((item for item in self._overview if item.mod_id == mod_id), None)
        default_name = f"{(overview.mod_name if overview else 'translation')}-zh.json"
        path, _ = QFileDialog.getSaveFileName(
            self, tr('ui.pages.translation.export'), str(self.services.paths.exports_dir / default_name), "JSON (*.json)"
        )
        if not path:
            return
        self.tasks.run(
            tr('ui.pages.translation.export'),
            lambda: self.services.translation.export_units(mod_id, Path(path)),
            on_done=lambda destination: self.toast(tr('ui.pages.translation.translation_exported_0', destination), "success"),
            on_error=self.error,
        )

    def import_pack(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            self.toast(tr('ui.pages.translation.please_select_the_mods_to_apply_the_translation_pack_8186cd'), "warning")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr('ui.pages.translation.import_translation_pack'), "", tr('ui.pages.translation.archives_zip_7z_tar_gz_all_files')
        )
        if not path:
            return
        if not self.confirm(
            tr('ui.pages.translation.import_translation_pack'),
            tr('ui.pages.translation.translation_packs_are_written_to_i18n_zh_json_after_e8c919'),
        ):
            return
        self._begin_progress(tr('ui.pages.translation.importing_translation_pack'))

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self._end_progress()
            message = tr('ui.pages.translation.imported_0_translations', result.translated_units)
            if result.warnings:
                message += "\n\n" + "\n".join(result.warnings)
            self.info(message, tr('ui.pages.translation.translation_pack_applied'))
            self._refresh_after_change(mod_id)

        def failed(message: str) -> None:
            self._end_progress()
            self.error(message)

        self.tasks.run(
            tr('ui.pages.translation.import_translation_pack'),
            lambda: self.services.translation.import_pack_archive(mod_id, Path(path)),
            on_done=done,
            on_error=failed,
        )

    def search_packs(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            self.toast(tr('ui.pages.translation.please_select_a_mod_first'), "warning")
            return
        self.pack_search_button.setEnabled(False)
        self.pack_search_button.setText(tr('ui.pages.translation.searching'))
        self.pack_hint.setText(tr('ui.pages.translation.searching_nexus_and_github_chinese_keywords_may_take_d8403f'))
        self.tabs.setCurrentIndex(1)

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.pack_search_button.setEnabled(True)
            self.pack_search_button.setText(tr('ui.pages.translation.search_nexus_github_translation_packs'))
            self._candidates = result.candidates
            self._render_candidates()
            if result.warnings:
                self.pack_hint.setText("；".join(result.warnings))
            else:
                self.pack_hint.setText(
                    tr('ui.pages.translation.found_0_candidates_open_the_page_to_verify_the_mod_v_8325cd', len(result.candidates))
                )
            if result.candidates:
                TranslationPackDialog(result.candidates, result.warnings, self).exec()

        def failed(message: str) -> None:
            self.pack_search_button.setEnabled(True)
            self.pack_search_button.setText(tr('ui.pages.translation.search_nexus_github_translation_packs'))
            self.pack_hint.setText(tr('ui.pages.translation.search_failed'))
            self.error(message)

        self.tasks.run(
            tr('ui.pages.translation.search_translation_packs'),
            lambda: self.services.translation.find_packs(mod_id),
            on_done=done,
            on_error=failed,
        )

    def _render_candidates(self) -> None:
        clear_table(self.pack_table)
        for candidate in self._candidates:
            row = self.pack_table.rowCount()
            self.pack_table.insertRow(row)
            self.pack_table.setItem(row, 0, cell(candidate.source.upper(), data=candidate))
            self.pack_table.setItem(row, 1, cell(candidate.title, tooltip=candidate.description))
            self.pack_table.setItem(row, 2, cell(candidate.compatibility, tooltip="；".join(candidate.match_reasons)))
            self.pack_table.setItem(row, 3, cell(candidate.author))
            self.pack_table.setItem(row, 4, cell(candidate.updated[:19]))
        self.pack_open_button.setEnabled(bool(self._candidates))
        if not self._candidates:
            self.pack_details.setText(tr('ui.pages.translation.no_candidates_yet_click_the_button_above_to_search_o_91d465'))

    def _selected_candidate(self) -> TranslationCandidate | None:
        rows = self.pack_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.pack_table.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item else None

    def open_pack(self) -> None:
        candidate = self._selected_candidate()
        if candidate and candidate.url:
            QDesktopServices.openUrl(QUrl(candidate.url))
            self.pack_details.setText(
                tr('ui.pages.translation.0_source_1_2_match_basis_3', candidate.title, candidate.source, candidate.compatibility, '；'.join(candidate.match_reasons) or '—')
            )
        else:
            self.toast(tr('ui.pages.translation.please_select_a_candidate_translation_pack'), "warning")

    def _refresh_after_change(self, mod_id: int, *, keep_unit: int | None = None) -> None:
        self.load_overview()
        self._update_ai_status()
        if mod_id and mod_id == self._selected_mod_id:
            if keep_unit:
                self._selected_unit_id = keep_unit
            self._load_units(mod_id)
