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

log = logging.getLogger(__name__)


class TranslationPage(BasePage):
    title = "汉化中心"
    subtitle = "现有汉化包优先，其次本地翻译缓存，最后使用可配置 AI 翻译"

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
            "尚未选择游戏",
            "添加游戏后，ModPilot 会检测支持 i18n/汉化的 Mod。",
            "translate",
            make_button("前往游戏管理", icon_name="gamepad", variant="primary", on_click=lambda: self.controller.go_to("GamesPage")),
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
            "AI 设置", icon_name="settings", on_click=lambda: self.controller.go_to("SettingsPage")
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
        left_header = SectionHeader("可汉化 Mod", "检测到 i18n 源文件的 Mod")
        self.reload_button = make_button(
            "重新检测", icon_name="refresh", variant="tool", on_click=self.load_overview
        )
        left_header.add_action(self.reload_button)
        left.add(left_header)
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("搜索 Mod…")
        self.mod_search.setClearButtonEnabled(True)
        self.mod_search.textChanged.connect(lambda _text: self._render_overview())
        left.add(self.mod_search)
        self.mod_table = QTableWidget()
        setup_table(
            self.mod_table,
            ["Mod", "进度", "状态"],
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
        self.editor_title = QLabel("选择一个可汉化 Mod")
        self.editor_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        editor_layout.addWidget(self.editor_title)
        self.editor_meta = QLabel("")
        self.editor_meta.setObjectName("Muted")
        self.editor_meta.setWordWrap(True)
        editor_layout.addWidget(self.editor_meta)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.ai_all_button = make_button(
            "AI 翻译未完成项", icon_name="sparkles", variant="primary", on_click=self.translate_all
        )
        self.ai_selected_button = make_button(
            "重新翻译选中", icon_name="refresh", on_click=self.retranslate_selected
        )
        self.apply_button = make_button(
            "应用已保存译文", icon_name="save", on_click=self.apply_saved
        )
        self.export_button = make_button("导出译文", icon_name="download", on_click=self.export_units)
        self.remove_button = make_button(
            "移除汉化文件", icon_name="trash", variant="danger", on_click=self.remove_translation
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
            make_button("导入汉化包…", icon_name="folder", on_click=self.import_pack)
        )
        second_toolbar.addWidget(
            make_button("搜索在线汉化包", icon_name="search", on_click=self.search_packs)
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
            ["Key", "原文", "译文", "状态", "来源", "模型", "时间", "确认"],
            stretch_column=1,
            row_height=34,
        )
        self.unit_table.itemSelectionChanged.connect(self._unit_selection_changed)
        editor_split.addWidget(self.unit_table)

        editor_card = Card(flat=True)
        key_row = QHBoxLayout()
        key_label = QLabel("条目")
        key_label.setObjectName("Muted")
        key_row.addWidget(key_label)
        self.unit_key = QLabel("—")
        self.unit_key.setTextInteractionFlags(Qt.TextSelectableByMouse)
        key_row.addWidget(self.unit_key, 1)
        self.unit_status = Badge("—", "muted")
        key_row.addWidget(self.unit_status)
        editor_card.add_layout(key_row)
        source_label = QLabel("原文")
        source_label.setObjectName("Muted")
        editor_card.add(source_label)
        self.source_edit = QPlainTextEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setMaximumHeight(88)
        editor_card.add(self.source_edit)
        translation_label = QLabel("译文（会保留占位符与 JSON 结构）")
        translation_label.setObjectName("Muted")
        editor_card.add(translation_label)
        self.translation_edit = QPlainTextEdit()
        self.translation_edit.setMaximumHeight(110)
        self.translation_edit.setPlaceholderText("在此编辑译文…")
        editor_card.add(self.translation_edit)
        editor_actions = QHBoxLayout()
        self.confirm_check = QCheckBox("人工确认")
        editor_actions.addWidget(self.confirm_check)
        self.save_button = make_button("保存译文", icon_name="save", variant="primary", on_click=lambda: self.save_unit(False))
        self.save_confirm_button = make_button("保存并确认", icon_name="check", on_click=lambda: self.save_unit(True))
        self.retranslate_button = make_button("重新 AI 翻译", icon_name="sparkles", on_click=self.retranslate_selected)
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
        self.tabs.addTab(editor_tab, "翻译编辑")

        # Packs tab ------------------------------------------------------
        packs_tab = QWidget()
        packs_layout = QVBoxLayout(packs_tab)
        packs_layout.setContentsMargins(4, 8, 4, 4)
        packs_layout.setSpacing(8)
        pack_toolbar = QHBoxLayout()
        self.pack_search_button = make_button(
            "搜索 Nexus / GitHub 汉化包", icon_name="search", variant="primary", on_click=self.search_packs
        )
        self.pack_import_button = make_button("导入已下载汉化包…", icon_name="folder", on_click=self.import_pack)
        self.pack_open_button = make_button("打开选中页面", icon_name="external", on_click=self.open_pack)
        pack_toolbar.addWidget(self.pack_search_button)
        pack_toolbar.addWidget(self.pack_import_button)
        pack_toolbar.addWidget(self.pack_open_button)
        pack_toolbar.addStretch(1)
        packs_layout.addLayout(pack_toolbar)
        self.pack_hint = QLabel(
            "汉化包搜索只根据公开元数据给出候选，不自动保证版本兼容。"
            "Nexus 下载遵循官方权限；非 Premium 用户请在网页下载后导入。"
        )
        self.pack_hint.setObjectName("Muted")
        self.pack_hint.setWordWrap(True)
        packs_layout.addWidget(self.pack_hint)
        self.pack_table = QTableWidget()
        setup_table(
            self.pack_table,
            ["来源", "标题", "兼容性判断", "作者", "更新时间"],
            stretch_column=1,
            row_height=38,
        )
        self.pack_table.doubleClicked.connect(lambda _index: self.open_pack())
        packs_layout.addWidget(self.pack_table, 1)
        self.pack_details = QLabel("")
        self.pack_details.setObjectName("Muted")
        self.pack_details.setWordWrap(True)
        packs_layout.addWidget(self.pack_details)
        self.tabs.addTab(packs_tab, "汉化包搜索")
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
                f"AI 翻译已就绪：{self.services.config.config.llm.model}（{self.services.config.config.llm.base_url}）"
            )
            self.ai_settings_button.setText("AI 设置")
        else:
            self.ai_status_label.setText(
                "未配置 AI 模型。仍可搜索/导入现有汉化包，或在下方手动编辑译文；配置 API 后可批量 AI 翻译。"
            )
            self.ai_settings_button.setText("配置 AI")
        try:
            total, confirmed = self.services.translation.cache_stats()
            self.cache_label.setText(f"本地翻译缓存 {total} 条 · 已确认 {confirmed} 条")
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
            self.left_hint.setText(f"检测失败：{message}")
            self.toast(f"检测可汉化 Mod 失败：{message}", "danger")

        self.tasks.run(
            "检测可汉化 Mod",
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
                status_text = "不可用"
            elif item.applied:
                status_text = "已应用"
            elif item.translated_units:
                status_text = "已有译文"
            else:
                status_text = "待翻译"
            self.mod_table.setItem(row, 2, cell(status_text))
        self.mod_table.blockSignals(False)
        self.left_hint.setText(
            "正在检测…" if loading else f"{len(items)} 个 Mod 支持标准翻译文件。"
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
        self.editor_title.setText(overview.mod_name if overview else "正在加载…")
        self.editor_meta.setText("正在读取 i18n 文件并同步本地译文记录…")
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
            self.editor_meta.setText(f"读取翻译文件失败：{message}")
            self.toast(f"读取翻译文件失败：{message}", "danger")

        self.tasks.run(
            "读取翻译文件",
            lambda: self.services.translation.get_units(mod_id),
            on_done=done,
            on_error=failed,
        )

    def _update_editor_header(self, overview: TranslationOverview | None) -> None:
        if overview is None:
            return
        self.editor_title.setText(overview.mod_name)
        applied = "已生成目标文件" if overview.applied else "尚未生成目标文件"
        self.editor_meta.setText(
            f"{overview.source_file} → {overview.target_file} · {overview.total_units} 条 · {applied}"
            + (f"\n{overview.message}" if overview.message else "")
        )
        self.editor_status.setText(
            f"译文 {overview.translated_units}/{overview.total_units}，已确认 {overview.confirmed_units}"
        )

    def _render_units(self, *, loading: bool = False) -> None:
        selected_id = self._selected_unit_id
        colors = theme_colors(self.controller._theme)
        self.unit_table.blockSignals(True)
        clear_table(self.unit_table)
        if loading:
            self.editor_status.setText("正在加载译文…")
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
            self.error("请先在设置中配置 AI 模型与 API Key。")
            return
        pending = [
            unit
            for unit in self._units
            if not unit.translated_text.strip() and not unit.confirmed
        ]
        if not pending:
            self.toast("当前 Mod 没有需要翻译的条目。若要覆盖已有译文，请选择条目并点击“重新翻译选中”。", "info")
            return
        if not self.confirm(
            "AI 翻译",
            f"将使用模型 {self.services.config.config.llm.model} 翻译 {len(pending)} 条未完成文本。\n\n"
            "占位符和 JSON 结构会在写入前校验；翻译结果先保存到本地数据库。",
        ):
            return
        self._begin_progress("正在准备翻译…")

        def progress(value: int, message: str) -> None:
            self.translate_progress.setValue(value)
            self.editor_status.setText(message)

        def done(count) -> None:  # type: ignore[no-untyped-def]
            self._end_progress()
            self.toast(f"AI 翻译完成，新增 {count} 条并已校验写入。", "success")
            self._refresh_after_change(mod_id)

        def failed(message: str) -> None:
            self._end_progress()
            self.unit_error.setText(message)
            self.error(message)

        self.tasks.run(
            "AI 翻译",
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
            self.error("请先在设置中配置 AI 模型与 API Key。")
            return
        if unit.confirmed and not self.confirm(
            "重新翻译已确认条目",
            "该条目已人工确认。重新 AI 翻译会覆盖译文并取消确认，确定继续吗？",
            danger=True,
        ):
            return
        self._begin_progress("正在重新翻译选中条目…")

        def done(count) -> None:  # type: ignore[no-untyped-def]
            self._end_progress()
            self.toast("条目已重新翻译。" if count else "没有可翻译的内容。", "success")
            self._refresh_after_change(mod_id)

        def failed(message: str) -> None:
            self._end_progress()
            self.error(message)

        self.tasks.run(
            "重新翻译条目",
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
            backup = f"（已创建备份 #{result.backup_id}）" if result.backup_id else ""
            self.toast(f"译文已保存并写入目标文件{backup}。", "success")
            self._refresh_after_change(self._selected_mod_id or 0, keep_unit=unit.id)

        def failed(message: str) -> None:
            self.save_button.setEnabled(True)
            self.save_confirm_button.setEnabled(True)
            self.error(message)

        self.tasks.run(
            "保存译文",
            lambda: self.services.translation.save_unit(unit.id, text, confirmed=use_confirm),
            on_done=done,
            on_error=failed,
        )

    def apply_saved(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            return
        self._begin_progress("正在应用已保存译文…")

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self._end_progress()
            self.toast(f"已写入 {result.translated_units} 条译文。", "success")
            self._refresh_after_change(mod_id)

        def failed(message: str) -> None:
            self._end_progress()
            self.error(message)

        self.tasks.run(
            "应用译文",
            lambda: self.services.translation.apply_saved(mod_id),
            on_done=done,
            on_error=failed,
        )

    def remove_translation(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            return
        if not self.confirm(
            "移除汉化文件",
            "删除生成的 i18n/zh.json？\n\n原始 Mod 文件不会被修改；已保存的译文记录仍保留，可再次应用。",
            danger=True,
        ):
            return
        self.tasks.run(
            "移除汉化文件",
            lambda: self.services.translation.remove_translation(mod_id),
            on_done=lambda _result: self._after_remove(mod_id),
            on_error=self.error,
        )

    def _after_remove(self, mod_id: int) -> None:
        self.toast("已移除生成的汉化文件，原始 Mod 未修改。", "success")
        self._refresh_after_change(mod_id)

    def export_units(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            return
        overview = next((item for item in self._overview if item.mod_id == mod_id), None)
        default_name = f"{(overview.mod_name if overview else 'translation')}-zh.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出译文", str(self.services.paths.exports_dir / default_name), "JSON (*.json)"
        )
        if not path:
            return
        self.tasks.run(
            "导出译文",
            lambda: self.services.translation.export_units(mod_id, Path(path)),
            on_done=lambda destination: self.toast(f"译文已导出：{destination}", "success"),
            on_error=self.error,
        )

    def import_pack(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            self.toast("请先选择要应用汉化的 Mod。", "warning")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入汉化包", "", "压缩包 (*.zip *.7z *.tar.gz);;所有文件 (*.*)"
        )
        if not path:
            return
        if not self.confirm(
            "导入汉化包",
            "汉化包会通过键名与占位符校验后写入 i18n/zh.json。\n\n"
            "ModPilot 不能自动证明汉化包适用于当前 Mod 版本，请进入游戏确认效果。",
        ):
            return
        self._begin_progress("正在导入汉化包…")

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self._end_progress()
            message = f"已导入 {result.translated_units} 条译文。"
            if result.warnings:
                message += "\n\n" + "\n".join(result.warnings)
            self.info(message, "汉化包已应用")
            self._refresh_after_change(mod_id)

        def failed(message: str) -> None:
            self._end_progress()
            self.error(message)

        self.tasks.run(
            "导入汉化包",
            lambda: self.services.translation.import_pack_archive(mod_id, Path(path)),
            on_done=done,
            on_error=failed,
        )

    def search_packs(self) -> None:
        mod_id = self._selected_mod_id
        if mod_id is None:
            self.toast("请先选择 Mod。", "warning")
            return
        self.pack_search_button.setEnabled(False)
        self.pack_search_button.setText("搜索中…")
        self.pack_hint.setText("正在搜索 Nexus 与 GitHub；中文关键词可能需要较长时间…")
        self.tabs.setCurrentIndex(1)

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self.pack_search_button.setEnabled(True)
            self.pack_search_button.setText("搜索 Nexus / GitHub 汉化包")
            self._candidates = result.candidates
            self._render_candidates()
            if result.warnings:
                self.pack_hint.setText("；".join(result.warnings))
            else:
                self.pack_hint.setText(
                    f"找到 {len(result.candidates)} 个候选。请打开页面核对 Mod 版本后再下载。"
                )
            if result.candidates:
                TranslationPackDialog(result.candidates, result.warnings, self).exec()

        def failed(message: str) -> None:
            self.pack_search_button.setEnabled(True)
            self.pack_search_button.setText("搜索 Nexus / GitHub 汉化包")
            self.pack_hint.setText("搜索失败。")
            self.error(message)

        self.tasks.run(
            "搜索汉化包",
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
            self.pack_details.setText("暂无候选。可点击上方按钮搜索，或直接导入已下载的汉化包。")

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
                f"{candidate.title}\n来源：{candidate.source} · {candidate.compatibility}\n"
                f"匹配依据：{'；'.join(candidate.match_reasons) or '—'}"
            )
        else:
            self.toast("请选择一个候选汉化包。", "warning")

    def _refresh_after_change(self, mod_id: int, *, keep_unit: int | None = None) -> None:
        self.load_overview()
        self._update_ai_status()
        if mod_id and mod_id == self._selected_mod_id:
            if keep_unit:
                self._selected_unit_id = keep_unit
            self._load_units(mod_id)
