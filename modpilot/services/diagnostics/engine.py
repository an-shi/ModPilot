from __future__ import annotations

import logging
import re
from pathlib import Path

from ...adapters.base import AdapterRegistry, ProgressCallback
from ...config import ConfigStore
from ...core.errors import LlmError
from ...data.db import Database
from ...data.repositories import DiagnosticRepository, GameRepository
from ...domain.enums import (
    Certainty,
    DiagnosticSource,
    IssueCategory,
    LogSeverity,
)
from ...domain.models import (
    DiagnosticFinding,
    DiagnosticRunView,
    ModView,
    ParsedLog,
)
from ..activity import ActivityService
from ..mod_service import ModService
from ..translation.llm import LlmClient
from .rules import StaticRules
from ...i18n import current_language, tr

log = logging.getLogger(__name__)

MAX_AI_LOG_CHARS = 20000


class DiagnosticEngine:
    def __init__(
        self,
        db: Database,
        registry: AdapterRegistry,
        config: ConfigStore,
        llm: LlmClient,
        mods: ModService,
        activity: ActivityService,
    ) -> None:
        self.db = db
        self.registry = registry
        self.config = config
        self.llm = llm
        self.mods = mods
        self.activity = activity
        self.rules = StaticRules(db)

    def find_logs(self, game_id: int) -> list[Path]:
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError(tr('services.diagnostics.engine.no_game_has_been_added'))
            adapter = self.registry.get(game.adapter_key)
            return adapter.find_logs(Path(game.install_path))

    def default_log_path(self, game_id: int) -> Path | None:
        logs = self.find_logs(game_id)
        return logs[0] if logs else None

    def run(
        self,
        game_id: int,
        *,
        log_path: Path | None = None,
        use_ai: bool = False,
        trigger: str = "manual",
        progress: ProgressCallback | None = None,
    ) -> DiagnosticRunView:
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError(tr('services.diagnostics.engine.no_game_has_been_added'))
            adapter = self.registry.get(game.adapter_key)
            install_path = Path(game.install_path)
            game_name = game.name
            framework_version = game.framework_version

        if progress:
            progress(5, tr('services.diagnostics.engine.selecting_log'))
        if log_path is None:
            log_path = self.default_log_path(game_id)
        parsed: ParsedLog | None = None
        if log_path:
            path = Path(log_path)
            if not path.is_file():
                raise ValueError(tr('services.diagnostics.engine.log_file_does_not_exist_0', path))
            if progress:
                progress(15, tr('services.diagnostics.engine.parsing_smapi_log'))
            try:
                parsed = adapter.parse_log(path)
                framework_version = parsed.framework_version or framework_version
            except Exception as exc:
                log.warning(tr('services.diagnostics.engine.failed_to_parse_log_s'), exc)
                parsed = ParsedLog(
                    path=path,
                    log_type="unknown",
                    parse_warnings=[tr('services.diagnostics.engine.failed_to_parse_log_0', exc)],
                )

        if progress:
            progress(35, tr('services.diagnostics.engine.running_static_rule_analysis'))
        views = self.mods.list_mods(game_id)
        environment = adapter.check_environment(install_path)
        findings: list[DiagnosticFinding] = []
        findings.extend(self.rules.analyze_dependencies(game_id, views))
        findings.extend(self.rules.analyze_duplicates(views))
        findings.extend(
            self.rules.analyze_framework(game_id, views, framework_version, environment)
        )
        findings.extend(
            self.rules.analyze_known_issues(views, adapter.load_known_issue_rules())
        )
        if parsed is not None:
            findings.extend(self.rules.analyze_log(parsed))
        else:
            findings.append(
                DiagnosticFinding(
                    title=tr('services.diagnostics.engine.smapi_log_not_found'),
                    description=tr('services.diagnostics.engine.no_smapi_latest_txt_or_errorlogs_log_was_found_launc_4525af'),
                    severity=LogSeverity.WARNING,
                    certainty=Certainty.CONFIRMED,
                    category=IssueCategory.ENVIRONMENT,
                    evidence=[tr('services.diagnostics.engine.no_logs_found_in_appdata_stardewvalley_errorlogs_or_de1802')],
                    recommendation=tr('services.diagnostics.engine.launch_the_game_with_smapi_then_run_diagnostics_agai_83443e'),
                    confidence=0.9,
                    source=DiagnosticSource.RULE,
                    rule_id="log.missing",
                )
            )
        findings = self.rules.dedupe(findings)

        ai_error: str | None = None
        ai_used = False
        if use_ai:
            if progress:
                progress(55, tr('services.diagnostics.engine.requesting_ai_deep_analysis'))
            try:
                ai_findings, _ai_summary = self._analyze_with_ai(
                    game_name=game_name,
                    game_version=game.game_version,
                    framework_version=framework_version,
                    views=views,
                    parsed=parsed,
                    findings=findings,
                    log_path=log_path,
                )
                findings.extend(ai_findings)
                findings = self.rules.dedupe(findings)
                ai_used = bool(ai_findings)
                # A free-form AI summary is not displayed as a diagnosis because
                # it cannot be evidence-checked line by line. Only structured
                # findings with verified evidence are persisted and shown.
            except LlmError as exc:
                ai_error = str(exc)
                log.warning(tr('services.diagnostics.engine.ai_diagnostics_unavailable_s'), exc)
            except Exception as exc:
                ai_error = tr('services.diagnostics.engine.ai_diagnostics_failed_0', exc)
                log.exception(tr('services.diagnostics.engine.ai_diagnostics_error'))

        if progress:
            progress(85, tr('services.diagnostics.engine.generating_diagnostic_report'))
        summary = self._build_summary(findings, parsed, ai_used, ai_error)
        with self.db.session() as session:
            repo = DiagnosticRepository(session)
            run = repo.add_run(
                game_id=game_id,
                log_path=str(log_path) if log_path else None,
                trigger=trigger,
                summary=summary,
                report_json={
                    "log_summary": {
                        "path": str(log_path) if log_path else None,
                        "framework_version": parsed.framework_version if parsed else None,
                        "game_version": parsed.game_version if parsed else None,
                        "line_count": parsed.line_count if parsed else 0,
                        "loaded": len(parsed.loaded_mods) if parsed else 0,
                        "skipped": len(parsed.skipped_mods) if parsed else 0,
                    },
                    "ai_error": ai_error,
                },
                ai_used=ai_used,
                model=self.config.config.llm.model if ai_used else "",
            )
            for finding in findings:
                repo.add_issue(
                    run.id,
                    source=finding.source.value,
                    severity=finding.severity.value,
                    certainty=finding.certainty.value,
                    category=finding.category.value,
                    title=finding.title,
                    description=finding.description,
                    mods_json=finding.mods,
                    evidence_json=finding.evidence,
                    confidence=finding.confidence,
                    raw_excerpt=finding.raw_excerpt,
                    recommendation=finding.recommendation,
                    rule_id=finding.rule_id,
                )
            run_id = run.id
        if progress:
            progress(100, tr('services.diagnostics.engine.diagnostics_complete'))
        error_count = sum(
            1 for finding in findings if finding.severity.rank >= LogSeverity.ERROR.rank
        )
        self.activity.record(
            tr('services.diagnostics.engine.smart_diagnostics_complete_found_0_issues_1_errors', len(findings), error_count),
            kind="diagnostic",
            game_id=game_id,
            details={"run_id": run_id, "ai_used": ai_used, "ai_error": ai_error},
        )
        return self.get_run(run_id)

    def _build_summary(
        self,
        findings: list[DiagnosticFinding],
        parsed: ParsedLog | None,
        ai_used: bool,
        ai_error: str | None,
    ) -> str:
        errors = sum(1 for f in findings if f.severity is LogSeverity.ERROR)
        critical = sum(1 for f in findings if f.severity is LogSeverity.CRITICAL)
        confirmed = sum(1 for f in findings if f.certainty is Certainty.CONFIRMED)
        suspected = sum(1 for f in findings if f.certainty is Certainty.SUSPECTED)
        parts = [tr('services.diagnostics.engine.found_0_issues_1_critical_2_errors', len(findings), critical, errors)]
        parts.append(tr('services.diagnostics.engine.0_have_definitive_evidence_1_are_suspected_issues', confirmed, suspected))
        if parsed:
            parts.append(
                tr('services.diagnostics.engine.parsed_log_0_1_mods_loaded_2_skipped', parsed.path.name, len(parsed.loaded_mods), len(parsed.skipped_mods))
            )
        else:
            parts.append(tr('services.diagnostics.engine.smapi_log_was_not_parsed_only_static_metadata_checks_79e42d'))
        if ai_used:
            parts.append(tr('services.diagnostics.engine.ai_deep_analysis_is_enabled_ai_conclusions_are_for_r_0057bf'))
        elif ai_error:
            parts.append(tr('services.diagnostics.engine.ai_analysis_did_not_complete_0', ai_error))
        return "；".join(parts) + "。"

    def _analyze_with_ai(
        self,
        *,
        game_name: str,
        game_version: str | None,
        framework_version: str | None,
        views: list[ModView],
        parsed: ParsedLog | None,
        findings: list[DiagnosticFinding],
        log_path: Path | None,
    ) -> tuple[list[DiagnosticFinding], str]:
        if not self.llm.is_configured():
            raise LlmError(tr('services.diagnostics.engine.ai_model_or_api_key_is_not_configured'))
        context = self._build_ai_context(
            game_name=game_name,
            game_version=game_version,
            framework_version=framework_version,
            views=views,
            parsed=parsed,
            findings=findings,
            log_path=log_path,
        )
        output_language = "English" if current_language() == "en-US" else "Simplified Chinese"
        system = (
            "You are a careful Stardew Valley SMAPI troubleshooting analyst. "
            "Analyze ONLY the supplied evidence. Never invent mods, versions, log lines, "
            "URLs, or error causes. Do not claim two mods conflict merely because they "
            "appear in the same log. Distinguish confirmed facts, known documented issues, "
            "suspected issues, and unknowns. Return one JSON object with keys: summary "
            f"(string in {output_language}) and findings (array). Each finding must have: title, "
            "description, severity (info|warning|error|critical), certainty "
            "(confirmed|known|suspected|unknown), category "
            "(missing_dependency|version_mismatch|duplicate|log_error|environment|"
            "compatibility|file_conflict|config|other), affected_mods (array of names or "
            "UniqueIDs that appear in the provided install list), evidence (array of short "
            "quotes taken verbatim from the supplied evidence), recommendation, confidence "
            "(0 to 0.85). Return at most 8 findings. If evidence is insufficient, use "
            f"certainty unknown and explain what is needed. Use {output_language}."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": context},
        ]
        payload = self.llm.chat_json(messages, max_tokens=4096)
        summary = str(payload.get("summary") or "").strip()
        raw_findings = payload.get("findings")
        if not isinstance(raw_findings, list):
            raise LlmError(tr('services.diagnostics.engine.ai_did_not_return_a_findings_array'))
        known_names = {view.name.casefold(): view for view in views}
        known_ids = {view.unique_id.casefold(): view for view in views}
        normalized_context = self._normalize(context)
        ai_findings: list[DiagnosticFinding] = []
        dropped = 0
        for raw in raw_findings[:12]:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            description = str(raw.get("description") or "").strip()
            if not title:
                dropped += 1
                continue
            evidence_raw = raw.get("evidence") or []
            if isinstance(evidence_raw, str):
                evidence_raw = [evidence_raw]
            evidence: list[str] = []
            for item in evidence_raw if isinstance(evidence_raw, list) else []:
                text = str(item).strip()
                if not text:
                    continue
                if self._normalize(text) not in normalized_context:
                    continue
                evidence.append(text[:500])
            if not evidence:
                dropped += 1
                continue
            mods: list[str] = []
            raw_mods = raw.get("affected_mods") or []
            if isinstance(raw_mods, str):
                raw_mods = [raw_mods]
            for value in raw_mods if isinstance(raw_mods, list) else []:
                key = str(value).casefold()
                matched = known_names.get(key) or known_ids.get(key)
                if matched:
                    mods.append(matched.name)
            if not mods:
                matched_ids = [
                    uid for uid in known_ids if uid in self._normalize(" ".join(evidence))
                ]
                mods = [known_ids[uid].name for uid in matched_ids[:3]]
            severity = self._severity(raw.get("severity"))
            certainty = self._certainty(raw.get("certainty"))
            category = self._category(raw.get("category"))
            if category in (IssueCategory.COMPATIBILITY, IssueCategory.FILE_CONFLICT):
                combined = (title + " " + description).casefold()
                claims_conflict = any(
                    word in combined
                    for word in ("conflict", tr('services.diagnostics.engine.conflict'), tr('services.diagnostics.engine.incompatible'), "incompatible")
                )
                context_lower = normalized_context.casefold()
                explicit = any(
                    word in context_lower
                    for word in ("conflict", tr('services.diagnostics.engine.conflict'), "incompatible", "not compatible")
                )
                if claims_conflict and not explicit:
                    certainty = Certainty.SUSPECTED
            try:
                confidence = float(raw.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            confidence = max(0.05, min(0.85, confidence))
            ai_findings.append(
                DiagnosticFinding(
                    title=title[:300],
                    description=description[:4000],
                    severity=severity,
                    certainty=certainty,
                    category=category,
                    mods=list(dict.fromkeys(mods)),
                    evidence=evidence[:6],
                    recommendation=str(raw.get("recommendation") or "")[:4000],
                    confidence=confidence,
                    raw_excerpt="",
                    source=DiagnosticSource.AI,
                    rule_id="ai.finding",
                )
            )
        if dropped:
            log.info(tr('services.diagnostics.engine.d_ai_diagnostic_entries_lacked_verifiable_evidence_a_306dac'), dropped)
        if not ai_findings:
            raise LlmError(
                tr('services.diagnostics.engine.all_ai_findings_were_filtered_out_because_they_lacke_1f46d2')
            )
        return ai_findings, summary[:3000]

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    @staticmethod
    def _severity(value) -> LogSeverity:  # type: ignore[no-untyped-def]
        try:
            return LogSeverity(str(value).lower())
        except ValueError:
            return LogSeverity.WARNING

    @staticmethod
    def _certainty(value) -> Certainty:  # type: ignore[no-untyped-def]
        try:
            return Certainty(str(value).lower())
        except ValueError:
            return Certainty.UNKNOWN

    @staticmethod
    def _category(value) -> IssueCategory:  # type: ignore[no-untyped-def]
        try:
            return IssueCategory(str(value).lower())
        except ValueError:
            return IssueCategory.OTHER

    def _build_ai_context(
        self,
        *,
        game_name: str,
        game_version: str | None,
        framework_version: str | None,
        views: list[ModView],
        parsed: ParsedLog | None,
        findings: list[DiagnosticFinding],
        log_path: Path | None,
    ) -> str:
        install_lines = []
        for view in views:
            if not view.is_present:
                continue
            deps = ", ".join(
                f"{dep.unique_id}{'>=' + dep.minimum_version if dep.minimum_version else ''}"
                f"{'' if dep.satisfied else tr('services.diagnostics.engine.missing')}"
                for dep in view.dependencies
            )
            install_lines.append(
                f"- {view.name} | UniqueID={view.unique_id} | version={view.version} | "
                f"{'enabled' if view.enabled else 'disabled'} | nexus={view.nexus_mod_id or '-'}"
                + (f" | deps: {deps}" if deps else "")
            )
        finding_lines = [
            f"- [{finding.certainty.value}/{finding.severity.value}] {finding.title}: "
            f"{finding.description[:500]} | evidence: {'; '.join(finding.evidence[:3])[:600]}"
            for finding in findings[:25]
        ]
        log_excerpt = ""
        if log_path and Path(log_path).is_file():
            try:
                raw = Path(log_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                raw = ""
            limit = min(MAX_AI_LOG_CHARS, self.config.config.diagnostics_max_log_chars)
            if len(raw) > limit:
                keep = max(2000, limit // 3)
                raw = raw[:keep] + tr('services.diagnostics.engine.intermediate_log_omitted') + raw[-keep:]
            log_excerpt = raw
        skipped_lines = ""
        if parsed:
            skipped_lines = "\n".join(
                f"- {entry.get('name')} {entry.get('version', '')}: {entry.get('reason')}"
                for entry in parsed.skipped_mods
            )
        parts = [
            f"Game: {game_name}",
            f"Game version: {game_version or 'unknown'}",
            f"SMAPI version: {framework_version or (parsed.framework_version if parsed else None) or 'unknown'}",
            f"Log: {log_path or 'none'}",
            "",
            "Installed mods:",
            "\n".join(install_lines[:200]),
            "",
            "Deterministic findings (already verified from metadata/log):",
            "\n".join(finding_lines) or "- none",
            "",
            "Skipped mods from log:",
            skipped_lines or "- none",
            "",
            "Raw SMAPI log excerpt (truncated if large):",
            log_excerpt or "- none",
            "",
            "Return the JSON diagnosis now.",
        ]
        return "\n".join(parts)

    def get_run(self, run_id: int) -> DiagnosticRunView:
        with self.db.session() as session:
            repo = DiagnosticRepository(session)
            run = repo.get_run(run_id)
            if run is None:
                raise KeyError(tr('services.diagnostics.engine.diagnostic_record_does_not_exist'))
            issues = repo.issues(run_id)
            findings: list[DiagnosticFinding] = []
            for issue in issues:
                try:
                    severity = LogSeverity(issue.severity)
                except ValueError:
                    severity = LogSeverity.WARNING
                try:
                    certainty = Certainty(issue.certainty)
                except ValueError:
                    certainty = Certainty.UNKNOWN
                try:
                    category = IssueCategory(issue.category)
                except ValueError:
                    category = IssueCategory.OTHER
                try:
                    source = DiagnosticSource(issue.source)
                except ValueError:
                    source = DiagnosticSource.RULE
                findings.append(
                    DiagnosticFinding(
                        title=issue.title,
                        description=issue.description,
                        severity=severity,
                        certainty=certainty,
                        category=category,
                        mods=issue.mods_json or [],
                        evidence=issue.evidence_json or [],
                        recommendation=issue.recommendation,
                        confidence=issue.confidence,
                        raw_excerpt=issue.raw_excerpt,
                        source=source,
                        rule_id=issue.rule_id,
                    )
                )
            return DiagnosticRunView(
                id=run.id,
                game_id=run.game_id,
                log_path=run.log_path,
                trigger=run.trigger,
                status=run.status,
                summary=run.summary,
                ai_used=run.ai_used,
                model=run.model,
                findings=findings,
                created_at=run.created_at,
            )

    def latest(self, game_id: int) -> DiagnosticRunView | None:
        with self.db.session() as session:
            run = DiagnosticRepository(session).latest_run(game_id)
            if run is None:
                return None
            run_id = run.id
        return self.get_run(run_id)

    def history(self, game_id: int, limit: int = 20) -> list[DiagnosticRunView]:
        with self.db.session() as session:
            runs = DiagnosticRepository(session).list_runs(game_id, limit)
            ids = [run.id for run in runs]
        return [self.get_run(run_id) for run_id in ids]

    @staticmethod
    def read_log(log_path: Path, max_chars: int = 500_000) -> str:
        path = Path(log_path)
        if not path.is_file():
            return ""
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return tr('services.diagnostics.engine.failed_to_read_log_0', exc)
        if len(raw) > max_chars:
            return raw[: max_chars // 3] + tr('services.diagnostics.engine.log_too_long_middle_section_omitted') + raw[-max_chars * 2 // 3 :]
        return raw
