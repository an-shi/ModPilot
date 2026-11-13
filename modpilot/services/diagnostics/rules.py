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
                            title=f"缺少必需依赖：{dependency.unique_id}",
                            description=(
                                f"{view.name} 声明了必需依赖 {label}，但当前 Mods 目录中没有找到该依赖。"
                                "这通常会导致 Mod 在 SMAPI 加载阶段被跳过。"
                            ),
                            severity=LogSeverity.ERROR,
                            certainty=Certainty.CONFIRMED,
                            category=IssueCategory.MISSING_DEPENDENCY,
                            mods=[view.name, dependency.unique_id],
                            evidence=[f"{view.name} manifest 依赖：{label}"],
                            recommendation=f"安装并启用 {dependency.unique_id}，然后重新扫描。",
                            confidence=0.98,
                            source=DiagnosticSource.RULE,
                            rule_id="dependency.missing",
                        )
                    )
                elif not dependency.enabled:
                    findings.append(
                        DiagnosticFinding(
                            title=f"必需依赖未启用：{dependency.unique_id}",
                            description=(
                                f"{view.name} 依赖 {dependency.unique_id}，该依赖已安装但处于禁用状态。"
                            ),
                            severity=LogSeverity.ERROR,
                            certainty=Certainty.CONFIRMED,
                            category=IssueCategory.MISSING_DEPENDENCY,
                            mods=[view.name, dependency.unique_id],
                            evidence=[f"{view.name} manifest 依赖：{label}", "依赖已安装但已禁用"],
                            recommendation=f"启用 {dependency.unique_id}。",
                            confidence=0.98,
                            source=DiagnosticSource.RULE,
                            rule_id="dependency.disabled",
                        )
                    )
                elif not dependency.satisfied:
                    findings.append(
                        DiagnosticFinding(
                            title=f"依赖版本不满足：{dependency.unique_id}",
                            description=(
                                f"{view.name} 需要 {label}，当前安装版本为 "
                                f"{dependency.installed_version}。"
                            ),
                            severity=LogSeverity.ERROR,
                            certainty=Certainty.CONFIRMED,
                            category=IssueCategory.VERSION_MISMATCH,
                            mods=[view.name, dependency.unique_id],
                            evidence=[
                                f"要求 {label}",
                                f"已安装 {dependency.installed_version}",
                            ],
                            recommendation=f"将 {dependency.unique_id} 更新到满足要求的版本。",
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
            paths = [f"{item.relative_path}（{item.version}{'，启用' if item.enabled else '，禁用'}）" for item in items]
            findings.append(
                DiagnosticFinding(
                    title=f"重复安装：{items[0].name}",
                    description=(
                        f"UniqueID {unique_id} 在 Mods 目录中存在 {len(items)} 个副本。"
                        "重复安装通常会造成版本混乱，但不是唯一原因，请保留一个副本。"
                    ),
                    severity=LogSeverity.ERROR,
                    certainty=Certainty.CONFIRMED,
                    category=IssueCategory.DUPLICATE,
                    mods=[item.name for item in items],
                    evidence=paths,
                    recommendation="禁用并移除多余的副本，仅保留一个版本。",
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
                            "安装或修复 SMAPI。"
                            if check.key == "smapi"
                            else "检查游戏安装目录与运行环境。"
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
                        title=f"SMAPI 版本过低：{mod.name}",
                        description=(
                            f"{mod.name} 要求 SMAPI ≥ {minimum}，当前 SMAPI 版本为 {framework_version}。"
                        ),
                        severity=LogSeverity.ERROR,
                        certainty=Certainty.CONFIRMED,
                        category=IssueCategory.VERSION_MISMATCH,
                        mods=[mod.name],
                        evidence=[
                            f"manifest MinimumApiVersion: {minimum}",
                            f"当前 SMAPI: {framework_version}",
                        ],
                        recommendation="更新 SMAPI 到 Mod 要求的版本或更高。",
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
                    title=str(rule.get("title") or "已知兼容性问题"),
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
                        "已安装：" + "、".join(f"{by_id[uid].name} {by_id[uid].version}" for uid in required_ids),
                        f"规则来源：{source_url}" if source_url else "规则来源未提供",
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
            mods = [str(entry.get("name") or "未知 Mod")]
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
                    title=f"Mod 无法加载：{mods[0]}",
                    description=reason,
                    severity=LogSeverity.ERROR,
                    certainty=Certainty.CONFIRMED,
                    category=category,
                    mods=mods,
                    evidence=[str(entry.get("raw") or reason)[:1200]],
                    recommendation=str(entry.get("recommendation") or "根据日志原因修复后重启游戏。"),
                    confidence=0.95,
                    source=DiagnosticSource.LOG,
                    rule_id="log.skipped",
                )
            )
        for issue in parsed.issues:
            if any(issue.message.startswith(f"{entry.get('name')} 无法加载") for entry in parsed.skipped_mods):
                continue
            # A single stack trace is evidence of an error, not proof of a
            # conflict between every mod mentioned in the same line.
            certainty = Certainty.SUSPECTED if len(issue.mods) <= 1 else Certainty.UNKNOWN
            findings.append(
                DiagnosticFinding(
                    title=f"日志错误：{issue.mods[0] if issue.mods else 'SMAPI'}",
                    description=issue.message,
                    severity=issue.severity,
                    certainty=certainty,
                    category=issue.category,
                    mods=issue.mods,
                    evidence=[
                        (f"日志第 {issue.line_number} 行：" if issue.line_number else "")
                        + (issue.raw[:1600] or issue.message)
                    ],
                    recommendation=(
                        "查看完整堆栈，优先禁用最近安装/更新的 Mod 做对照测试。"
                        "单条日志中同时出现多个 Mod 并不等于它们存在冲突。"
                    ),
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
