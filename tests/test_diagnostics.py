from __future__ import annotations

from pathlib import Path

from modpilot.domain.enums import Certainty, DiagnosticSource, IssueCategory
from modpilot.services.diagnostics.engine import DiagnosticEngine
from modpilot.services.repair_service import RepairService
from tests.conftest import TestEnv


class FakeLlm:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {
            "summary": "日志显示一个缺失依赖，另一个 Mod 结构不完整。",
            "findings": [
                {
                    "title": "缺失内容包依赖",
                    "description": "Broken Mod 需要 Content Patcher。",
                    "severity": "error",
                    "certainty": "confirmed",
                    "category": "missing_dependency",
                    "affected_mods": ["Broken Mod"],
                    "evidence": ["Broken Mod 1.0.0"],
                    "recommendation": "安装 Content Patcher。",
                    "confidence": 0.8,
                }
            ],
        }
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def chat_json(self, messages, max_tokens=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.payload


def make_engine(env: TestEnv, llm=None) -> DiagnosticEngine:
    return DiagnosticEngine(
        env.db,
        env.registry,
        env.config,
        llm or FakeLlm(),
        env.mods,
        env.activity,
    )


def test_static_and_log_diagnosis(env: TestEnv, sample_log: Path) -> None:
    env.make_mod(".Framework", unique_id="Test.Framework", version="1.0.0")
    env.make_mod(
        "Needs Framework",
        unique_id="Test.Needs",
        dependencies=[{"UniqueID": "Test.Framework"}],
    )
    env.make_mod("Copy A", unique_id="Test.Duplicate")
    env.make_mod("Copy B", unique_id="Test.Duplicate")
    env.rescan()
    engine = make_engine(env)
    run = engine.run(env.game_id, log_path=sample_log, use_ai=False)
    categories = {finding.category for finding in run.findings}
    assert IssueCategory.MISSING_DEPENDENCY in categories
    assert IssueCategory.DUPLICATE in categories
    assert any(finding.source is DiagnosticSource.LOG for finding in run.findings)
    assert any(finding.source is DiagnosticSource.RULE for finding in run.findings)
    assert not run.ai_used
    assert run.summary
    history = engine.history(env.game_id)
    assert history and history[0].id == run.id
    latest = engine.latest(env.game_id)
    assert latest and latest.id == run.id


def test_ai_findings_are_evidence_checked(env: TestEnv, sample_log: Path) -> None:
    env.make_mod("Broken Mod", unique_id="Test.Broken")
    env.rescan()
    llm = FakeLlm()
    engine = make_engine(env, llm)
    run = engine.run(env.game_id, log_path=sample_log, use_ai=True)
    assert llm.calls == 1
    assert run.ai_used
    ai_findings = [
        finding
        for finding in run.findings
        if finding.source is DiagnosticSource.AI and finding.rule_id != "ai.summary"
    ]
    assert ai_findings
    assert ai_findings[0].mods == ["Broken Mod"]
    assert ai_findings[0].certainty in (Certainty.CONFIRMED, Certainty.SUSPECTED)


def test_ai_fabricated_evidence_is_rejected(env: TestEnv, sample_log: Path) -> None:
    env.make_mod("Broken Mod", unique_id="Test.Broken")
    env.rescan()
    llm = FakeLlm(
        {
            "summary": "invented",
            "findings": [
                {
                    "title": "Fake",
                    "description": "Fake cause",
                    "severity": "error",
                    "certainty": "confirmed",
                    "category": "compatibility",
                    "affected_mods": ["Broken Mod", "Not Installed Mod"],
                    "evidence": ["THIS EVIDENCE DOES NOT EXIST IN THE LOG"],
                    "recommendation": "Fake remedy",
                    "confidence": 0.99,
                }
            ],
        }
    )
    engine = make_engine(env, llm)
    run = engine.run(env.game_id, log_path=sample_log, use_ai=True)
    assert not run.ai_used
    assert run.findings
    assert all(finding.source is not DiagnosticSource.AI for finding in run.findings)
    # ai_error is stored in the run's report JSON, surfaced through summary.
    assert "AI 分析未完成" in run.summary


def test_repair_plan_uses_explicit_nexus_dependency_link(env: TestEnv, tmp_path: Path) -> None:
    env.make_mod("Needs CP", unique_id="Test.NeedsCP")
    env.rescan()
    log = tmp_path / "dependency-log.txt"
    log.write_text(
        """[12:00:00 INFO  SMAPI] SMAPI 4.0.0 with Stardew Valley 1.6.8
[12:00:00 ERROR SMAPI] Skipped mods
[12:00:00 ERROR SMAPI]    - Needs CP 1.0.0 because it requires mods which aren't installed (Content Patcher: https://www.nexusmods.com/stardewvalley/mods/1915).
""",
        encoding="utf-8",
    )
    engine = make_engine(env)
    run = engine.run(env.game_id, log_path=log, use_ai=False)
    repairs = RepairService(
        env.db, env.paths, env.mods, env.backups, env.nexus, env.activity
    )
    plan = repairs.create_plan(run.id)
    install_steps = [step for step in plan.plan.steps if step.action == "install_dependency"]
    assert install_steps
    assert install_steps[0].params["nexus_mod_id"] == 1915
    assert install_steps[0].automatic is True


def test_repair_plan_disables_duplicate_and_verifies(env: TestEnv, sample_log: Path) -> None:
    env.make_mod("Copy A", unique_id="Test.Duplicate")
    env.make_mod("Copy B", unique_id="Test.Duplicate")
    env.rescan()
    engine = make_engine(env)
    run = engine.run(env.game_id, log_path=sample_log, use_ai=False)
    repairs = RepairService(
        env.db, env.paths, env.mods, env.backups, env.nexus, env.activity
    )
    plan = repairs.create_plan(run.id)
    disable_steps = [step for step in plan.plan.steps if step.action == "disable_mod"]
    assert disable_steps
    result = repairs.apply_plan(plan.id, [disable_steps[0].id])
    assert result.status in ("applied", "failed")
    views = [view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Duplicate"]
    assert sum(1 for view in views if view.enabled) <= 1
