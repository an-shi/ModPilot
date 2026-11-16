from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from ...core.versions import compare_versions, version_satisfies
from ...data.db import Database
from ...data.repositories import ModRepository
from ...domain.enums import Certainty, DiagnosticSource, IssueCategory, LogSeverity
from ...domain.models import (
    DiagnosticFinding,
    EnvironmentCheck,
    ModView,
    ParsedLog,
)
from ...i18n import tr

log = logging.getLogger(__name__)


def _severity_rank(value: str) -> LogSeverity:
    try:
        return LogSeverity(value)
    except ValueError:
        return LogSeverity.ERROR


class StaticRules:
    """Deterministic checks. Every finding cites concrete metadata/log evidence.

    This class never treats two mods merely appearing near each other in a log
    as a conflict. Conflict wording is reserved for explicit log statements,
    known issue rules with a verifiable source, and structural duplication.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def analyze_dependencies(
        self,
        game_id: int,
        views: list[ModView],
    ) -> list[DiagnosticFinding]:
        findings: list[DiagnosticFinding] = []
        present = [view for view in views if view.is_present]
        for view in present:
            if not view.enabled:
                continue
            for dependency in view.dependencies:
                if not dependency.required:
                    continue
                label = (
                    f"{dependency.unique_id}"
                    + (
                        f" ≥ {dependency.minimum_version}"
                        if dependency.minimum_version
                        else ""
                    )
                )
                if dependency.installed_version is None:
                    findings.append(
                        DiagnosticFinding(
                            title=tr('services.diagnostics.rules.missing_required_dependency_0', dependency.unique_id),
                            description=tr('services.diagnostics.rules.0_declares_required_dependency_1_but_it_was_not_foun_481781', view.name, label),
                            severity=LogSeverity.ERROR,
                            certainty=Certainty.CONFIRMED,
                            category=IssueCategory.MISSING_DEPENDENCY,
                            mods=[view.name, dependency.unique_id],
                            evidence=[tr('services.diagnostics.rules.0_manifest_dependencies_1', view.name, label)],
                            recommendation=tr('services.diagnostics.rules.install_and_enable_0_then_rescan', dependency.unique_id),
                            confidence=0.98,
                            source=DiagnosticSource.RULE,
                            rule_id="dependency.missing",
                        )
                    )
                elif not dependency.enabled:
                    findings.append(
                        DiagnosticFinding(
                            title=tr('services.diagnostics.rules.required_dependency_not_enabled_0', dependency.unique_id),
                            description=tr('services.diagnostics.rules.0_depends_on_1_which_is_installed_but_disabled', view.name, dependency.unique_id),
                            severity=LogSeverity.ERROR,
                            certainty=Certainty.CONFIRMED,
                            category=IssueCategory.MISSING_DEPENDENCY,
                            mods=[view.name, dependency.unique_id],
                            evidence=[tr('services.diagnostics.rules.0_manifest_dependencies_1', view.name, label), tr('services.diagnostics.rules.dependency_installed_but_disabled')],
                            recommendation=tr('services.diagnostics.rules.enable_0', dependency.unique_id),
                            confidence=0.98,
                            source=DiagnosticSource.RULE,
                            rule_id="dependency.disabled",
                        )
                    )
                elif not dependency.satisfied:
                    findings.append(
                        DiagnosticFinding(
                            title=tr('services.diagnostics.rules.dependency_version_does_not_meet_requirements_0', dependency.unique_id),
                            description=tr('services.diagnostics.rules.0_requires_1_but_the_installed_version_is_2', view.name, label, dependency.installed_version),
                            severity=LogSeverity.ERROR,
                            certainty=Certainty.CONFIRMED,
                            category=IssueCategory.VERSION_MISMATCH,
                            mods=[view.name, dependency.unique_id],
                            evidence=[
                                tr('services.diagnostics.rules.requires_0', label),
                                tr('services.diagnostics.rules.installed_0', dependency.installed_version),
                            ],
                            recommendation=tr('services.diagnostics.rules.update_0_to_a_version_that_meets_the_requirements', dependency.unique_id),
                            confidence=0.96,
                            source=DiagnosticSource.RULE,
                            rule_id="dependency.version",
                        )
                    )
        return findings

    def analyze_duplicates(self, views: list[ModView]) -> list[DiagnosticFinding]:
        groups: dict[str, list[ModView]] = defaultdict(list)
        for view in views:
            if view.is_present:
                groups[view.unique_id].append(view)
        findings: list[DiagnosticFinding] = []
        for unique_id, items in groups.items():
            if len(items) < 2:
                continue
            paths = [f"{item.relative_path}（{item.version}{tr('services.diagnostics.rules.enabled') if item.enabled else tr('services.diagnostics.rules.disabled')}）" for item in items]
            findings.append(
                DiagnosticFinding(
                    title=tr('services.diagnostics.rules.duplicate_installation_0', items[0].name),
                    description=tr('services.diagnostics.rules.uniqueid_0_has_1_copies_in_the_mods_folder_duplicate_30a050', unique_id, len(items)),
                    severity=LogSeverity.ERROR,
                    certainty=Certainty.CONFIRMED,
                    category=IssueCategory.DUPLICATE,
                    mods=[item.name for item in items],
                    evidence=paths,
                    recommendation=tr('services.diagnostics.rules.disable_and_remove_extra_copies_keeping_only_one_ver_1600ad'),
                    confidence=0.99,
                    source=DiagnosticSource.RULE,
                    rule_id="duplicate.install",
                )
            )
        return findings

    def analyze_framework(
        self,
        game_id: int,
        views: list[ModView],
        framework_version: str | None,
        environment: list[EnvironmentCheck] | None = None,
    ) -> list[DiagnosticFinding]:
        findings: list[DiagnosticFinding] = []
        for check in environment or []:
            if check.ok:
                continue
            if check.key in (
                "smapi",
                "game_executable",
                "game_version",
                "content",
                "mods_dir",
                "mods_writable",
            ):
                findings.append(
                    DiagnosticFinding(
                        title=check.label,
                        description=check.detail,
                        severity=check.severity,
                        certainty=Certainty.CONFIRMED,
                        category=IssueCategory.ENVIRONMENT,
                        evidence=[f"{check.label}: {check.detail}"],
                        recommendation=(
                            tr('services.diagnostics.rules.install_or_repair_smapi')
                            if check.key == "smapi"
                            else tr('services.diagnostics.rules.check_the_game_installation_directory_and_runtime_en_4423f0')
                        ),
                        confidence=0.95,
                        source=DiagnosticSource.RULE,
                        rule_id=f"environment.{check.key}",
                    )
                )

        with self.db.session() as session:
            mods = ModRepository(session).list_for_game(game_id, include_missing=False)
        for mod in mods:
            minimum = (mod.metadata_json or {}).get("minimum_api_version")
            if not minimum or not framework_version:
                continue
            if not version_satisfies(framework_version, minimum):
                findings.append(
                    DiagnosticFinding(
                        title=tr('services.diagnostics.rules.smapi_version_is_too_old_0', mod.name),
                        description=tr('services.diagnostics.rules.0_requires_smapi_1_but_the_current_smapi_version_is_aca0a3', mod.name, minimum, framework_version),
                        severity=LogSeverity.ERROR,
                        certainty=Certainty.CONFIRMED,
                        category=IssueCategory.VERSION_MISMATCH,
                        mods=[mod.name],
                        evidence=[
                            f"manifest MinimumApiVersion: {minimum}",
                            tr('services.diagnostics.rules.current_smapi_0', framework_version),
                        ],
                        recommendation=tr('services.diagnostics.rules.update_smapi_to_the_version_required_by_the_mod_or_n_afe6b4'),
                        confidence=0.97,
                        source=DiagnosticSource.RULE,
                        rule_id="framework.minimum_api",
                    )
                )
        return findings

    def analyze_known_issues(
        self,
        views: list[ModView],
        rules: list[dict[str, Any]],
    ) -> list[DiagnosticFinding]:
        by_id = {view.unique_id.casefold(): view for view in views if view.is_present}
        findings: list[DiagnosticFinding] = []
        for rule in rules:
            required_ids = [str(item).casefold() for item in rule.get("match_unique_ids", [])]
            if not required_ids or not all(uid in by_id for uid in required_ids):
                continue
            max_versions = rule.get("max_versions") or {}
            applicable = True
            for uid, max_version in max_versions.items():
                view = by_id.get(str(uid).casefold())
                if view is None:
                    applicable = False
                    break
                comparison = compare_versions(view.version, str(max_version))
                if comparison is None or comparison > 0:
                    applicable = False
                    break
            if not applicable:
                continue
            source_url = str(rule.get("source") or "")
            findings.append(
                DiagnosticFinding(
                    title=str(rule.get("title") or tr('services.diagnostics.rules.known_compatibility_issues')),
                    description=str(rule.get("description") or ""),
                    severity=_severity_rank(str(rule.get("severity") or "warning")),
                    certainty=Certainty.KNOWN,
                    category=(
                        IssueCategory(str(rule.get("category")))
                        if str(rule.get("category")) in IssueCategory._value2member_map_
                        else IssueCategory.COMPATIBILITY
                    ),
                    mods=[by_id[uid].name for uid in required_ids],
                    evidence=[
                        tr('services.diagnostics.rules.installed') + "、".join(f"{by_id[uid].name} {by_id[uid].version}" for uid in required_ids),
                        tr('services.diagnostics.rules.rule_source_0', source_url) if source_url else tr('services.diagnostics.rules.rule_source_not_provided'),
                    ],
                    recommendation=str(rule.get("recommendation") or ""),
                    confidence=0.9,
                    source=DiagnosticSource.RULE,
                    rule_id=str(rule.get("id") or "known_issue"),
                )
            )
        return findings

    def analyze_log(self, parsed: ParsedLog) -> list[DiagnosticFinding]:
        findings: list[DiagnosticFinding] = []
        for entry in parsed.skipped_mods:
            category = IssueCategory.LOG_ERROR
            reason = str(entry.get("reason") or "")
            lower = reason.lower()
            mods = [str(entry.get("name") or tr('services.diagnostics.rules.unknown_mod'))]
            mods.extend(str(dep) for dep in entry.get("missing_dependencies", []) if dep)
            if (
                entry.get("missing_dependencies")
                or "not installed" in lower
                or "isn't installed" in lower
                or "aren't installed" in lower
                or "requires mods" in lower
                or "missing" in lower
            ):
                category = IssueCategory.MISSING_DEPENDENCY
            elif "not compatible" in lower or "game version" in lower:
                category = IssueCategory.COMPATIBILITY
            elif "api version" in lower or "newer version of smapi" in lower:
                category = IssueCategory.VERSION_MISMATCH
            elif "manifest" in lower:
                category = IssueCategory.OTHER
            elif "duplicate" in lower:
                category = IssueCategory.DUPLICATE
            findings.append(
                DiagnosticFinding(
                    title=tr('services.diagnostics.rules.mod_failed_to_load_0', mods[0]),
                    description=reason,
                    severity=LogSeverity.ERROR,
                    certainty=Certainty.CONFIRMED,
                    category=category,
                    mods=mods,
                    evidence=[str(entry.get("raw") or reason)[:1200]],
                    recommendation=str(entry.get("recommendation") or tr('services.diagnostics.rules.fix_the_issue_based_on_the_log_cause_then_restart_th_9c25af')),
                    confidence=0.95,
                    source=DiagnosticSource.LOG,
                    rule_id="log.skipped",
                )
            )
        for issue in parsed.issues:
            if any(issue.message.startswith(tr('services.diagnostics.rules.0_could_not_be_loaded', entry.get('name'))) for entry in parsed.skipped_mods):
                continue
            # A single stack trace is evidence of an error, not proof of a
            # conflict between every mod mentioned in the same line.
            certainty = Certainty.SUSPECTED if len(issue.mods) <= 1 else Certainty.UNKNOWN
            findings.append(
                DiagnosticFinding(
                    title=tr('services.diagnostics.rules.log_error_0', issue.mods[0] if issue.mods else 'SMAPI'),
                    description=issue.message,
                    severity=issue.severity,
                    certainty=certainty,
                    category=issue.category,
                    mods=issue.mods,
                    evidence=[
                        (tr('services.diagnostics.rules.log_line_0', issue.line_number) if issue.line_number else "")
                        + (issue.raw[:1600] or issue.message)
                    ],
                    recommendation=tr('services.diagnostics.rules.review_the_full_stack_trace_and_first_try_disabling_60c944'),
                    confidence=issue.confidence,
                    source=DiagnosticSource.LOG,
                    rule_id="log.error",
                )
            )
        return findings

    @staticmethod
    def dedupe(findings: list[DiagnosticFinding]) -> list[DiagnosticFinding]:
        result: list[DiagnosticFinding] = []
        seen: set[tuple] = set()
        for finding in findings:
            key = (
                finding.source.value,
                finding.category.value,
                finding.title.casefold(),
                tuple(sorted(mod.casefold() for mod in finding.mods)),
                tuple(finding.evidence[:1]),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(finding)
        result.sort(
            key=lambda item: (
                -item.severity.rank,
                item.certainty != Certainty.CONFIRMED,
                item.certainty != Certainty.KNOWN,
                item.title.casefold(),
            )
        )
        return result
