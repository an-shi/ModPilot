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
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ...domain.enums import Certainty, LogSeverity, RepairStatus
from ...domain.models import DiagnosticFinding, DiagnosticRunView, RepairPlanView
from ..dialogs import BackupsDialog, RawLogDialog
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
    setup_table,
)
from .base import BasePage
from ...i18n import tr

log = logging.getLogger(__name__)


class DiagnosticsPage(BasePage):
    title = tr('ui.pages.diagnostics.smart_diagnosis')
    subtitle = tr('ui.pages.diagnostics.four_layer_diagnosis_log_parsing_static_rules_ai_dee_6fd2a0')

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        self._run: DiagnosticRunView | None = None
        self._findings: list[DiagnosticFinding] = []
        self._runs: list[DiagnosticRunView] = []
        self._plan: RepairPlanView | None = None
        self._logs: list[Path] = []
        self._manual_log: Path | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self.body = QStackedWidget()
        outer.addWidget(self.body, 1)
        self.empty = EmptyState(
            tr('ui.pages.diagnostics.no_game_selected'),
            tr('ui.pages.diagnostics.add_a_game_to_read_smapi_logs_and_run_smart_diagnosi_059cea'),
            "diagnose",
            make_button(tr('ui.pages.diagnostics.go_to_game_management'), icon_name="gamepad", variant="primary", on_click=lambda: self.controller.go_to("GamesPage")),
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
        label = QLabel(tr('ui.pages.diagnostics.logs'))
        label.setStyleSheet("font-weight: 650;")
        row.addWidget(label)
        self.log_combo = QComboBox()
        self.log_combo.setMinimumWidth(300)
        row.addWidget(self.log_combo, 1)
        self.browse_button = make_button(tr('ui.pages.diagnostics.select_log'), icon_name="folder", on_click=self.browse_log)
        row.addWidget(self.browse_button)
        self.raw_button = make_button(tr('ui.pages.diagnostics.view_raw_log'), icon_name="external", on_click=self.show_raw_log)
        row.addWidget(self.raw_button)
        self.run_button = make_button(
            tr('ui.pages.diagnostics.quick_diagnosis'), icon_name="diagnose", on_click=lambda: self.run_diagnosis(False)
        )
        self.ai_button = make_button(
            tr('ui.pages.diagnostics.ai_deep_diagnosis'), icon_name="sparkles", variant="primary", on_click=lambda: self.run_diagnosis(True)
        )
        row.addWidget(self.run_button)
        row.addWidget(self.ai_button)
        self.history_combo = QComboBox()
        self.history_combo.setMinimumWidth(180)
        self.history_combo.currentIndexChanged.connect(self._history_changed)
        row.addWidget(self.history_combo)
        toolbar.add_layout(row)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("Muted")
        self.meta_label.setWordWrap(True)
        toolbar.add(self.meta_label)
        self.progress = QProgressBar()
        self.progress.hide()
        toolbar.add(self.progress)
        layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        top = QSplitter(Qt.Horizontal)
        top.setChildrenCollapsible(False)

        issues_card = Card()
        issues_header = SectionHeader(tr('ui.pages.diagnostics.detected_issues'), tr('ui.pages.diagnostics.every_conclusion_includes_evidence_and_a_certainty_l_20d28c'))
        self.issues_table = QTableWidget()
        setup_table(
            self.issues_table,
            [tr('ui.pages.diagnostics.severity'), tr('ui.pages.diagnostics.issue'), tr('ui.pages.diagnostics.type'), tr('ui.pages.diagnostics.certainty'), tr('ui.pages.diagnostics.affected_mods'), tr('ui.pages.diagnostics.confidence')],
            stretch_column=1,
            row_height=38,
        )
        self.issues_table.itemSelectionChanged.connect(self._issue_selection_changed)
        issues_card.add(issues_header)
        issues_card.add(self.issues_table, 1)
        top.addWidget(issues_card)

        detail_card = Card()
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.NoFrame)
        detail_content = QWidget()
        self.detail_layout = QVBoxLayout(detail_content)
        self.detail_layout.setContentsMargins(0, 0, 6, 0)
        self.detail_layout.setSpacing(9)
        self.detail_scroll.setWidget(detail_content)
        detail_card.add(self.detail_scroll, 1)
        self.detail_title = QLabel(tr('ui.pages.diagnostics.select_a_diagnosis_issue'))
        self.detail_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.detail_title.setWordWrap(True)
        self.detail_title.setParent(detail_content)
        self.detail_layout.addWidget(self.detail_title)
        self.detail_badges = QHBoxLayout()
        self.detail_layout.addLayout(self.detail_badges)
        self.detail_description = QLabel("")
        self.detail_description.setWordWrap(True)
        self.detail_description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail_layout.addWidget(self.detail_description)
        self.evidence_label = QLabel(tr('ui.pages.diagnostics.evidence'))
        self.evidence_label.setStyleSheet("font-weight: 650;")
        self.detail_layout.addWidget(self.evidence_label)
        self.evidence_text = QPlainTextEdit()
        self.evidence_text.setReadOnly(True)
        self.evidence_text.setMaximumHeight(140)
        self.detail_layout.addWidget(self.evidence_text)
        self.recommendation_label = QLabel("")
        self.recommendation_label.setWordWrap(True)
        self.recommendation_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail_layout.addWidget(self.recommendation_label)
        self.raw_excerpt = QPlainTextEdit()
        self.raw_excerpt.setReadOnly(True)
        self.raw_excerpt.setMaximumHeight(160)
        self.raw_excerpt.hide()
        self.detail_layout.addWidget(self.raw_excerpt)
        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("Muted")
        self.detail_meta.setWordWrap(True)
        self.detail_layout.addWidget(self.detail_meta)
        self.detail_layout.addStretch(1)
        top.addWidget(detail_card)
        top.setSizes([800, 430])
        splitter.addWidget(top)

        repair_card = Card()
        repair_header = SectionHeader(tr('ui.pages.diagnostics.repair_plan'), tr('ui.pages.diagnostics.confirm_before_running_back_up_affected_content_befo_213123'))
        self.plan_button = make_button(
            tr('ui.pages.diagnostics.generate_repair_plan'), icon_name="shield", variant="primary", on_click=self.create_plan
        )
        self.apply_plan_button = make_button(
            tr('ui.pages.diagnostics.run_selected_steps'), icon_name="play", on_click=self.apply_plan
        )
        self.apply_plan_button.setEnabled(False)
        self.plan_backups_button = make_button(
            tr('ui.pages.diagnostics.view_related_backups'), icon_name="backup", on_click=self.show_backups
        )
        repair_header.add_action(self.plan_button)
        repair_header.add_action(self.apply_plan_button)
        repair_header.add_action(self.plan_backups_button)
        repair_card.add(repair_header)
        self.plan_status = QLabel(tr('ui.pages.diagnostics.run_a_diagnosis_first_then_generate_a_repair_plan'))
        self.plan_status.setObjectName("Muted")
        self.plan_status.setWordWrap(True)
        repair_card.add(self.plan_status)
        self.plan_table = QTableWidget()
        setup_table(
            self.plan_table,
            [tr('ui.pages.diagnostics.run'), tr('ui.pages.diagnostics.step'), tr('ui.pages.diagnostics.risk'), tr('ui.pages.diagnostics.method'), tr('ui.pages.diagnostics.details')],
            stretch_column=4,
            row_height=36,
        )
        self.plan_table.setMinimumHeight(150)
        self.plan_table.cellDoubleClicked.connect(self._open_plan_step)
        repair_card.add(self.plan_table, 1)
        splitter.addWidget(repair_card)
        splitter.setSizes([430, 280])
        layout.addWidget(splitter, 1)

        self._render_run(None)
        self.refresh()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self.game_id is None:
            self.body.setCurrentIndex(0)
            return
        self.body.setCurrentIndex(1)
        self._load_logs()
        try:
            self._runs = self.services.diagnostics.history(self.game_id, 15)
        except Exception:
            log.exception(tr('ui.pages.diagnostics.failed_to_read_diagnosis_history'))
            self._runs = []
        self._render_history_combo()
        latest = self._runs[0] if self._runs else None
        self._render_run(latest)
        self._update_ai_controls()

    def _load_logs(self) -> None:
        try:
            self._logs = self.services.diagnostics.find_logs(self.game_id) if self.game_id else []
        except Exception:
            self._logs = []
        self.log_combo.blockSignals(True)
        self.log_combo.clear()
        self.log_combo.addItem(tr('ui.pages.diagnostics.auto_select_latest_log'), None)
        for index, path in enumerate(self._logs):
            try:
                mtime = path.stat().st_mtime
                from datetime import datetime

                stamp = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
            except OSError:
                stamp = ""
            self.log_combo.addItem(f"{path.name}  ·  {stamp}", str(path))
        if self._manual_log:
            self.log_combo.addItem(tr('ui.pages.diagnostics.0_manually_selected', self._manual_log.name), str(self._manual_log))
        self.log_combo.blockSignals(False)
        self.meta_label.setText(
            tr('ui.pages.diagnostics.found_0_smapi_logs', len(self._logs))
            if self._logs
            else tr('ui.pages.diagnostics.smapi_log_not_found_automatically_use_select_log_to_0de83b')
        )

    def _render_history_combo(self) -> None:
        self.history_combo.blockSignals(True)
        self.history_combo.clear()
        if not self._runs:
            self.history_combo.addItem(tr('ui.pages.diagnostics.no_records_yet'), None)
        for index, run in enumerate(self._runs):
            label = (tr('ui.pages.diagnostics.latest') if index == 0 else "") + run.created_at.strftime("%m-%d %H:%M") + tr('ui.pages.diagnostics.0_issues', len(run.findings))
            self.history_combo.addItem(label, run.id)
        self.history_combo.setEnabled(bool(self._runs))
        self.history_combo.blockSignals(False)

    def _history_changed(self) -> None:
        run_id = self.history_combo.currentData()
        if run_id is None:
            return
        run = next((item for item in self._runs if item.id == run_id), None)
        if run:
            self._render_run(run)

    def _update_ai_controls(self) -> None:
        configured = self.services.llm_configured
        self.ai_button.setEnabled(configured)
        self.ai_button.setToolTip(
            tr('ui.pages.diagnostics.analyze_structured_logs_and_mod_metadata_with_the_co_d5750b')
            if configured
            else tr('ui.pages.diagnostics.no_ai_model_is_configured_go_to_settings')
        )

    # ------------------------------------------------------------------
    # Diagnosis run
    # ------------------------------------------------------------------

    def browse_log(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr('ui.pages.diagnostics.select_smapi_log'), "", tr('ui.pages.diagnostics.log_files_txt_log_all_files'))
        if not path:
            return
        self._manual_log = Path(path)
        self.log_combo.addItem(tr('ui.pages.diagnostics.0_manually_selected', Path(path).name), path)
        self.log_combo.setCurrentIndex(self.log_combo.count() - 1)
        self.meta_label.setText(tr('ui.pages.diagnostics.log_selected_0', path))

    def run_diagnosis(self, use_ai: bool) -> None:
        game_id = self.require_game()
        if game_id is None:
            return
        if use_ai and not self.services.llm_configured:
            self.error(tr('ui.pages.diagnostics.no_ai_model_or_api_key_is_configured_yet_go_to_setti_3a475f'))
            return
        log_value = self.log_combo.currentData()
        log_path = Path(log_value) if log_value else None
        if use_ai and not self.confirm(
            tr('ui.pages.diagnostics.ai_deep_diagnosis'),
            tr('ui.pages.diagnostics.0_will_be_used_to_analyze_logs_and_mod_metadata_log_073ca3', self.services.config.config.llm.model),
        ):
            return
        self._set_busy(True)
        self.progress.show()
        self.progress.setValue(0)
        self.meta_label.setText(tr('ui.pages.diagnostics.running_diagnosis'))

        def progress(value: int, message: str) -> None:
            self.progress.setValue(value)
            self.meta_label.setText(message)

        def done(run) -> None:  # type: ignore[no-untyped-def]
            self._set_busy(False)
            self.progress.hide()
            self._runs = self.services.diagnostics.history(game_id, 15)
            self._render_history_combo()
            self._render_run(run)
            self._update_ai_controls()
            self.toast(tr('ui.pages.diagnostics.diagnosis_complete_0_issues_found', len(run.findings)), "success")
            self.controller.on_data_changed()

        def failed(message: str) -> None:
            self._set_busy(False)
            self.progress.hide()
            self.meta_label.setText(tr('ui.pages.diagnostics.diagnosis_failed'))
            self.error(message)

        self.tasks.run(
            tr('ui.pages.diagnostics.smart_diagnosis'),
            lambda progress_cb: self.services.diagnostics.run(
                game_id,
                log_path=log_path,
                use_ai=use_ai,
                trigger="ai" if use_ai else "manual",
                progress=progress_cb,
            ),
            with_progress=True,
            on_progress=progress,
            on_done=done,
            on_error=failed,
        )

    def _set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy)
        self.ai_button.setEnabled(not busy and self.services.llm_configured)
        self.browse_button.setEnabled(not busy)
        self.plan_button.setEnabled(not busy)
        if not busy:
            self.plan_button.setEnabled(self._run is not None)

    def _render_run(self, run: DiagnosticRunView | None) -> None:
        self._run = run
        self._findings = run.findings if run else []
        self._plan = None
        self.populate_plan(None)
        self._render_issues()
        if run is None:
            self.meta_label.setText(tr('ui.pages.diagnostics.no_diagnosis_records_to_display_yet_click_quick_diag_bcb9a5'))
            self.plan_button.setEnabled(False)
            self.raw_button.setEnabled(bool(self._logs))
            self._render_issue(None)
            return
        self.plan_button.setEnabled(True)
        self.raw_button.setEnabled(bool(run.log_path))
        ai_text = tr('ui.pages.diagnostics.ai_involved') if run.ai_used else tr('ui.pages.diagnostics.rule_log_analysis')
        self.meta_label.setText(
            tr('ui.pages.diagnostics.0_record_1_2_3', run.summary, run.id, run.created_at.strftime('%Y-%m-%d %H:%M'), ai_text)
            + (tr('ui.pages.diagnostics.model_0', run.model) if run.model else "")
            + (tr('ui.pages.diagnostics.log_0', Path(run.log_path).name) if run.log_path else tr('ui.pages.diagnostics.no_log'))
        )
        try:
            plan = self.services.repairs.latest_for_run(run.id)
            self.populate_plan(plan)
        except Exception as exc:
            log.warning(tr('ui.pages.diagnostics.failed_to_read_repair_plan_s'), exc)
        if self._findings:
            self.issues_table.selectRow(0)
        else:
            self._render_issue(None)

    def _render_issues(self) -> None:
        colors = theme_colors(self.controller._theme)
        self.issues_table.blockSignals(True)
        clear_table(self.issues_table)
        for index, finding in enumerate(self._findings):
            row = self.issues_table.rowCount()
            self.issues_table.insertRow(row)
            severity = cell(finding.severity.label)
            severity.setForeground(
                QColor(
                    colors["danger"]
                    if finding.severity.rank >= LogSeverity.ERROR.rank
                    else colors["warning"]
                    if finding.severity is LogSeverity.WARNING
                    else colors["muted"]
                )
            )
            self.issues_table.setItem(row, 0, severity)
            self.issues_table.setItem(row, 1, cell(finding.title, tooltip=finding.description))
            self.issues_table.setItem(row, 2, cell(finding.category.label))
            certainty = cell(finding.certainty.label)
            certainty.setForeground(
                QColor(
                    colors["success"]
                    if finding.certainty is Certainty.CONFIRMED
                    else colors["info"]
                    if finding.certainty in (Certainty.KNOWN, Certainty.SUSPECTED)
                    else colors["muted"]
                )
            )
            self.issues_table.setItem(row, 3, certainty)
            self.issues_table.setItem(row, 4, cell("、".join(finding.mods) if finding.mods else "—"))
            self.issues_table.setItem(row, 5, cell(f"{int(finding.confidence * 100)}%"))
        self.issues_table.blockSignals(False)

    def _issue_selection_changed(self) -> None:
        rows = self.issues_table.selectionModel().selectedRows()
        if not rows:
            self._render_issue(None)
            return
        index = rows[0].row()
        if 0 <= index < len(self._findings):
            self._render_issue(self._findings[index])

    def _render_issue(self, finding: DiagnosticFinding | None) -> None:
        clear_layout(self.detail_badges)
        if finding is None:
            self.detail_title.setText(tr('ui.pages.diagnostics.select_a_diagnosis_issue'))
            self.detail_description.setText(tr('ui.pages.diagnostics.the_issue_cause_evidence_and_recommended_solutions_w_74f152'))
            self.evidence_text.setPlainText("")
            self.recommendation_label.setText("")
            self.raw_excerpt.hide()
            self.detail_meta.setText("")
            return
        self.detail_title.setText(finding.title)
        tone = {
            LogSeverity.INFO: "info",
            LogSeverity.WARNING: "warning",
            LogSeverity.ERROR: "danger",
            LogSeverity.CRITICAL: "danger",
        }[finding.severity]
        self.detail_badges.addWidget(Badge(finding.severity.label, tone))
        self.detail_badges.addWidget(Badge(finding.certainty.label, "accent" if finding.certainty is Certainty.CONFIRMED else "muted"))
        self.detail_badges.addWidget(Badge(finding.category.label, "muted"))
        self.detail_badges.addWidget(Badge(tr('ui.pages.diagnostics.confidence_0', int(finding.confidence * 100)), "muted"))
        self.detail_badges.addStretch(1)
        self.detail_description.setText(finding.description)
        self.evidence_text.setPlainText("\n\n".join(finding.evidence) or tr('ui.pages.diagnostics.no_evidence_provided'))
        self.recommendation_label.setText(
            tr('ui.pages.diagnostics.suggestion') + finding.recommendation if finding.recommendation else tr('ui.pages.diagnostics.no_automatic_suggestions_please_check_the_raw_log')
        )
        self.raw_excerpt.setPlainText(finding.raw_excerpt)
        self.raw_excerpt.setVisible(bool(finding.raw_excerpt))
        self.detail_meta.setText(
            tr('ui.pages.diagnostics.source_0', {'rule': tr('ui.pages.diagnostics.static_rule'), 'log': tr('ui.pages.diagnostics.log_parsing'), 'ai': tr('ui.pages.diagnostics.ai_analysis')}.get(finding.source.value, finding.source.value))
            + (tr('ui.pages.diagnostics.rule_0', finding.rule_id) if finding.rule_id else "")
            + (tr('ui.pages.diagnostics.affected_0', '、'.join(finding.mods)) if finding.mods else "")
        )

    def show_raw_log(self) -> None:
        path = None
        if self._run and self._run.log_path:
            path = Path(self._run.log_path)
        elif self._manual_log:
            path = self._manual_log
        elif self._logs:
            path = self._logs[0]
        if path is None:
            self.toast(tr('ui.pages.diagnostics.no_log_files_to_view'), "warning")
            return
        text = self.services.diagnostics.read_log(path) or tr('ui.pages.diagnostics.log_file_is_empty')
        RawLogDialog(text, self, title=tr('ui.pages.diagnostics.raw_log_0', path.name)).exec()

    # ------------------------------------------------------------------
    # Repair
    # ------------------------------------------------------------------

    def create_plan(self) -> None:
        if not self._run:
            self.toast(tr('ui.pages.diagnostics.please_run_diagnosis_first'), "warning")
            return
        self.plan_button.setEnabled(False)
        self.plan_status.setText(tr('ui.pages.diagnostics.generating_repair_plan'))
        self.tasks.run(
            tr('ui.pages.diagnostics.generate_repair_plan'),
            lambda: self.services.repairs.create_plan(self._run.id),
            on_done=self._plan_created,
            on_error=self._plan_failed,
        )

    def _plan_created(self, plan: RepairPlanView) -> None:  # type: ignore[no-untyped-def]
        self.plan_button.setEnabled(True)
        self.populate_plan(plan)
        self.toast(tr('ui.pages.diagnostics.repair_plan_generated_review_it_then_execute'), "success")

    def _plan_failed(self, message: str) -> None:
        self.plan_button.setEnabled(True)
        self.plan_status.setText(tr('ui.pages.diagnostics.failed_to_generate_repair_plan'))
        self.error(message)

    def populate_plan(self, plan: RepairPlanView | None) -> None:
        self._plan = plan
        clear_table(self.plan_table)
        self.apply_plan_button.setEnabled(bool(plan and plan.plan.steps))
        if plan is None:
            self.plan_status.setText(tr('ui.pages.diagnostics.run_a_diagnosis_first_then_generate_a_repair_plan'))
            return
        status_map = {
            "planned": tr('ui.pages.diagnostics.pending'),
            "applied": tr('ui.pages.diagnostics.executed'),
            "failed": tr('ui.pages.diagnostics.execution_failed'),
            "rolled_back": tr('ui.pages.diagnostics.rolled_back'),
        }
        self.plan_status.setText(
            tr('ui.pages.diagnostics.0_status_1', plan.plan.summary, status_map.get(plan.status, plan.status))
            + (f"\n{plan.result}" if plan.result else "")
            + (tr('ui.pages.diagnostics.warning') + "；".join(plan.plan.warnings) if plan.plan.warnings else "")
        )
        for step in plan.plan.steps:
            row = self.plan_table.rowCount()
            self.plan_table.insertRow(row)
            check = cell("")
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(
                Qt.Checked if step.selected and plan.status == RepairStatus.PLANNED.value else Qt.Unchecked
            )
            check.setData(Qt.UserRole, step.id)
            self.plan_table.setItem(row, 0, check)
            self.plan_table.setItem(row, 1, cell(step.title, tooltip=step.description))
            self.plan_table.setItem(row, 2, cell(step.risk.label))
            self.plan_table.setItem(row, 3, cell(tr('ui.pages.diagnostics.auto') if step.automatic else tr('ui.pages.diagnostics.manual')))
            status = step.message or (tr('ui.pages.diagnostics.manual_action_required') if not step.automatic else tr('ui.pages.diagnostics.automatic'))
            if step.success is True:
                status = "✓ " + status
            elif step.success is False:
                status = "✕ " + status
            url = str(step.params.get("url") or "")
            if url and not step.automatic:
                status += tr('ui.pages.diagnostics.double_click_to_open_link')
            self.plan_table.setItem(
                row,
                4,
                cell(
                    status,
                    tooltip=step.description + (f"\n{url}" if url else ""),
                ),
            )
        if plan.status != RepairStatus.PLANNED.value:
            self.apply_plan_button.setEnabled(False)

    def _open_plan_step(self, row: int, _column: int) -> None:
        if self._plan is None or row < 0 or row >= len(self._plan.plan.steps):
            return
        step = self._plan.plan.steps[row]
        url = str(step.params.get("url") or "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def apply_plan(self) -> None:
        plan = self._plan
        if plan is None:
            return
        selected: list[str] = []
        for row in range(self.plan_table.rowCount()):
            item = self.plan_table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                selected.append(str(item.data(Qt.UserRole)))
        if not selected:
            self.toast(tr('ui.pages.diagnostics.no_repair_steps_selected'), "warning")
            return
        if not self.confirm(
            tr('ui.pages.diagnostics.run_repair_plan'),
            tr('ui.pages.diagnostics.this_will_run_0_repair_steps_automatic_steps_change_b3578a', len(selected)),
            danger=True,
        ):
            return
        self.apply_plan_button.setEnabled(False)
        self.plan_status.setText(tr('ui.pages.diagnostics.executing_repair_plan'))
        self.progress.show()
        self.progress.setValue(0)

        def progress(value: int, message: str) -> None:
            self.progress.setValue(value)
            self.plan_status.setText(message)

        def done(result_plan) -> None:  # type: ignore[no-untyped-def]
            self.progress.hide()
            self.populate_plan(result_plan)
            self.apply_plan_button.setEnabled(result_plan.status == RepairStatus.PLANNED.value)
            self.toast(
                tr('ui.pages.diagnostics.repair_completed') if result_plan.status == RepairStatus.APPLIED.value else tr('ui.pages.diagnostics.repair_result_0', result_plan.status),
                "success" if result_plan.status == RepairStatus.APPLIED.value else "warning",
            )
            self.refresh()
            self.controller.on_data_changed()

        def failed(message: str) -> None:
            self.progress.hide()
            self.plan_status.setText(tr('ui.pages.diagnostics.repair_failed'))
            self.error(message)
            self.apply_plan_button.setEnabled(True)

        self.tasks.run(
            tr('ui.pages.diagnostics.run_repair_plan'),
            lambda progress_cb: self.services.repairs.apply_plan(plan.id, selected, progress_cb),
            with_progress=True,
            on_progress=progress,
            on_done=done,
            on_error=failed,
        )

    def show_backups(self) -> None:
        if not self.game_id:
            return
        mod_id = None
        if self._plan and self._plan.backup_id:
            try:
                backup = self.services.backups.get(self._plan.backup_id)
                mod_id = backup.mod_id
            except Exception:
                pass
        try:
            backups = self.services.backups.list_for_game(self.game_id, mod_id)
        except Exception as exc:
            self.error(str(exc))
            return

        def restore(backup) -> None:  # type: ignore[no-untyped-def]
            if not self.confirm(tr('ui.pages.diagnostics.restore_backup'), tr('ui.pages.diagnostics.restore_0_a_safety_backup_will_be_created_first', backup.label), danger=True):
                return
            self.tasks.run(
                tr('ui.pages.diagnostics.restore_backup'),
                lambda: self.services.mods.rollback(backup.id),
                on_done=lambda _result: self._after_restore(),
                on_error=self.error,
            )

        BackupsDialog(backups, self, on_restore=restore).exec()

    def _after_restore(self) -> None:
        self.toast(tr('ui.pages.diagnostics.backup_restored'), "success")
        self.refresh()
        self.controller.on_data_changed()
