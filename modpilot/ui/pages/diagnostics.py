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

log = logging.getLogger(__name__)


class DiagnosticsPage(BasePage):
    title = "智能诊断"
    subtitle = "四层诊断：日志解析、静态规则、AI 深度分析与可确认的修复计划"

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
            "尚未选择游戏",
            "添加游戏后才能读取 SMAPI 日志并运行智能诊断。",
            "diagnose",
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
        label = QLabel("日志")
        label.setStyleSheet("font-weight: 650;")
        row.addWidget(label)
        self.log_combo = QComboBox()
        self.log_combo.setMinimumWidth(300)
        row.addWidget(self.log_combo, 1)
        self.browse_button = make_button("选择日志…", icon_name="folder", on_click=self.browse_log)
        row.addWidget(self.browse_button)
        self.raw_button = make_button("查看原始日志", icon_name="external", on_click=self.show_raw_log)
        row.addWidget(self.raw_button)
        self.run_button = make_button(
            "快速诊断", icon_name="diagnose", on_click=lambda: self.run_diagnosis(False)
        )
        self.ai_button = make_button(
            "AI 深度诊断", icon_name="sparkles", variant="primary", on_click=lambda: self.run_diagnosis(True)
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
        issues_header = SectionHeader("诊断问题", "所有结论都附带证据与确定性等级")
        self.issues_table = QTableWidget()
        setup_table(
            self.issues_table,
            ["级别", "问题", "类型", "确定性", "涉及 Mod", "置信度"],
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
        self.detail_title = QLabel("选择一个诊断问题")
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
        self.evidence_label = QLabel("证据")
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
        repair_header = SectionHeader("修复计划", "执行前需确认；修复前备份受影响内容，执行后验证")
        self.plan_button = make_button(
            "生成修复计划", icon_name="shield", variant="primary", on_click=self.create_plan
        )
        self.apply_plan_button = make_button(
            "执行选中步骤", icon_name="play", on_click=self.apply_plan
        )
        self.apply_plan_button.setEnabled(False)
        self.plan_backups_button = make_button(
            "查看相关备份", icon_name="backup", on_click=self.show_backups
        )
        repair_header.add_action(self.plan_button)
        repair_header.add_action(self.apply_plan_button)
        repair_header.add_action(self.plan_backups_button)
        repair_card.add(repair_header)
        self.plan_status = QLabel("先运行诊断，再生成修复计划。")
        self.plan_status.setObjectName("Muted")
        self.plan_status.setWordWrap(True)
        repair_card.add(self.plan_status)
        self.plan_table = QTableWidget()
        setup_table(
            self.plan_table,
            ["执行", "步骤", "风险", "方式", "说明"],
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
            log.exception("读取诊断历史失败")
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
        self.log_combo.addItem("自动选择最新日志", None)
        for index, path in enumerate(self._logs):
            try:
                mtime = path.stat().st_mtime
                from datetime import datetime

                stamp = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
            except OSError:
                stamp = ""
            self.log_combo.addItem(f"{path.name}  ·  {stamp}", str(path))
        if self._manual_log:
            self.log_combo.addItem(f"{self._manual_log.name}（手动选择）", str(self._manual_log))
        self.log_combo.blockSignals(False)
        self.meta_label.setText(
            f"找到 {len(self._logs)} 个 SMAPI 日志。"
            if self._logs
            else "未自动找到 SMAPI 日志；可使用“选择日志…”手动指定。"
        )

    def _render_history_combo(self) -> None:
        self.history_combo.blockSignals(True)
        self.history_combo.clear()
        if not self._runs:
            self.history_combo.addItem("尚无诊断记录", None)
        for index, run in enumerate(self._runs):
            label = ("最新 · " if index == 0 else "") + run.created_at.strftime("%m-%d %H:%M") + f" · {len(run.findings)} 个问题"
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
            "使用已配置的 AI 模型分析结构化日志与 Mod 元数据"
            if configured
            else "尚未配置 AI 模型，请前往设置"
        )

    # ------------------------------------------------------------------
    # Diagnosis run
    # ------------------------------------------------------------------

    def browse_log(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 SMAPI 日志", "", "日志文件 (*.txt *.log);;所有文件 (*.*)")
        if not path:
            return
        self._manual_log = Path(path)
        self.log_combo.addItem(f"{Path(path).name}（手动选择）", path)
        self.log_combo.setCurrentIndex(self.log_combo.count() - 1)
        self.meta_label.setText(f"已选择日志：{path}")

    def run_diagnosis(self, use_ai: bool) -> None:
        game_id = self.require_game()
        if game_id is None:
            return
        if use_ai and not self.services.llm_configured:
            self.error("尚未配置 AI 模型与 API Key。请前往设置，或先运行快速诊断。")
            return
        log_value = self.log_combo.currentData()
        log_path = Path(log_value) if log_value else None
        if use_ai and not self.confirm(
            "AI 深度诊断",
            f"将使用 {self.services.config.config.llm.model} 分析日志与 Mod 元数据。\n\n"
            "日志内容会发送到你配置的 AI API。AI 结论会经过证据校验，不能替代原始日志判断。",
        ):
            return
        self._set_busy(True)
        self.progress.show()
        self.progress.setValue(0)
        self.meta_label.setText("正在诊断…")

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
            self.toast(f"诊断完成：发现 {len(run.findings)} 个问题。", "success")
            self.controller.on_data_changed()

        def failed(message: str) -> None:
            self._set_busy(False)
            self.progress.hide()
            self.meta_label.setText("诊断失败。")
            self.error(message)

        self.tasks.run(
            "智能诊断",
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
            self.meta_label.setText("尚无可显示的诊断记录。点击“快速诊断”开始。")
            self.plan_button.setEnabled(False)
            self.raw_button.setEnabled(bool(self._logs))
            self._render_issue(None)
            return
        self.plan_button.setEnabled(True)
        self.raw_button.setEnabled(bool(run.log_path))
        ai_text = "AI 已参与" if run.ai_used else "规则/日志分析"
        self.meta_label.setText(
            f"{run.summary}\n"
            f"记录 #{run.id} · {run.created_at.strftime('%Y-%m-%d %H:%M')} · {ai_text}"
            + (f" · 模型 {run.model}" if run.model else "")
            + (f" · 日志 {Path(run.log_path).name}" if run.log_path else " · 无日志")
        )
        try:
            plan = self.services.repairs.latest_for_run(run.id)
            self.populate_plan(plan)
        except Exception as exc:
            log.warning("读取修复计划失败：%s", exc)
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
            self.detail_title.setText("选择一个诊断问题")
            self.detail_description.setText("问题原因、证据与推荐解决方案会显示在这里。")
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
        self.detail_badges.addWidget(Badge(f"置信度 {int(finding.confidence * 100)}%", "muted"))
        self.detail_badges.addStretch(1)
        self.detail_description.setText(finding.description)
        self.evidence_text.setPlainText("\n\n".join(finding.evidence) or "未提供证据。")
        self.recommendation_label.setText(
            "建议：" + finding.recommendation if finding.recommendation else "暂无自动建议，请查看原始日志。"
        )
        self.raw_excerpt.setPlainText(finding.raw_excerpt)
        self.raw_excerpt.setVisible(bool(finding.raw_excerpt))
        self.detail_meta.setText(
            f"来源：{ {'rule': '静态规则', 'log': '日志解析', 'ai': 'AI 分析'}.get(finding.source.value, finding.source.value) }"
            + (f" · 规则 {finding.rule_id}" if finding.rule_id else "")
            + (f" · 涉及：{'、'.join(finding.mods)}" if finding.mods else "")
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
            self.toast("没有可查看的日志文件。", "warning")
            return
        text = self.services.diagnostics.read_log(path) or "日志文件为空。"
        RawLogDialog(text, self, title=f"原始日志 · {path.name}").exec()

    # ------------------------------------------------------------------
    # Repair
    # ------------------------------------------------------------------

    def create_plan(self) -> None:
        if not self._run:
            self.toast("请先运行诊断。", "warning")
            return
        self.plan_button.setEnabled(False)
        self.plan_status.setText("正在生成修复计划…")
        self.tasks.run(
            "生成修复计划",
            lambda: self.services.repairs.create_plan(self._run.id),
            on_done=self._plan_created,
            on_error=self._plan_failed,
        )

    def _plan_created(self, plan: RepairPlanView) -> None:  # type: ignore[no-untyped-def]
        self.plan_button.setEnabled(True)
        self.populate_plan(plan)
        self.toast("修复计划已生成，请确认后执行。", "success")

    def _plan_failed(self, message: str) -> None:
        self.plan_button.setEnabled(True)
        self.plan_status.setText("生成修复计划失败。")
        self.error(message)

    def populate_plan(self, plan: RepairPlanView | None) -> None:
        self._plan = plan
        clear_table(self.plan_table)
        self.apply_plan_button.setEnabled(bool(plan and plan.plan.steps))
        if plan is None:
            self.plan_status.setText("先运行诊断，再生成修复计划。")
            return
        status_map = {
            "planned": "待执行",
            "applied": "已执行",
            "failed": "执行失败",
            "rolled_back": "已回滚",
        }
        self.plan_status.setText(
            f"{plan.plan.summary}\n状态：{status_map.get(plan.status, plan.status)}"
            + (f"\n{plan.result}" if plan.result else "")
            + ("\n警告：" + "；".join(plan.plan.warnings) if plan.plan.warnings else "")
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
            self.plan_table.setItem(row, 3, cell("自动" if step.automatic else "手动"))
            status = step.message or ("需手动操作" if not step.automatic else "可自动执行")
            if step.success is True:
                status = "✓ " + status
            elif step.success is False:
                status = "✕ " + status
            url = str(step.params.get("url") or "")
            if url and not step.automatic:
                status += "（双击打开链接）"
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
            self.toast("没有选择修复步骤。", "warning")
            return
        if not self.confirm(
            "执行修复计划",
            f"将执行 {len(selected)} 个修复步骤。\n\n"
            "自动步骤会修改 Mod 状态或安装缺失依赖；执行前会备份受影响内容，执行后会重新扫描验证。\n"
            "手动步骤不会自动执行。确定继续吗？",
            danger=True,
        ):
            return
        self.apply_plan_button.setEnabled(False)
        self.plan_status.setText("正在执行修复计划…")
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
                "修复执行完成。" if result_plan.status == RepairStatus.APPLIED.value else f"修复结果：{result_plan.status}",
                "success" if result_plan.status == RepairStatus.APPLIED.value else "warning",
            )
            self.refresh()
            self.controller.on_data_changed()

        def failed(message: str) -> None:
            self.progress.hide()
            self.plan_status.setText("修复执行失败。")
            self.error(message)
            self.apply_plan_button.setEnabled(True)

        self.tasks.run(
            "执行修复计划",
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
            if not self.confirm("恢复备份", f"恢复“{backup.label}”？恢复前会创建安全备份。", danger=True):
                return
            self.tasks.run(
                "恢复备份",
                lambda: self.services.mods.rollback(backup.id),
                on_done=lambda _result: self._after_restore(),
                on_error=self.error,
            )

        BackupsDialog(backups, self, on_restore=restore).exec()

    def _after_restore(self) -> None:
        self.toast("备份恢复完成。", "success")
        self.refresh()
        self.controller.on_data_changed()
