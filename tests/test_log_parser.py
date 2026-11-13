from __future__ import annotations

from pathlib import Path

from modpilot.adapters.stardew.log_parser import SmapiLogParser
from modpilot.domain.enums import IssueCategory, LogSeverity


def test_parse_smapi_log(sample_log: Path) -> None:
    parsed = SmapiLogParser().parse(sample_log)
    assert parsed.framework_version == "4.0.0"
    assert parsed.game_version == "1.6.8"
    assert len(parsed.loaded_mods) == 2
    assert parsed.loaded_mods[0]["name"] == "Good Mod"
    assert len(parsed.skipped_mods) == 2
    assert parsed.skipped_mods[0]["name"] == "Broken Mod"
    assert "Pathoschild.ContentPatcher" in parsed.issues[0].missing_dependencies
    assert any(issue.category is IssueCategory.MISSING_DEPENDENCY for issue in parsed.issues)
    assert any(issue.category is IssueCategory.CONFIG for issue in parsed.issues)
    content_patch = next(issue for issue in parsed.issues if "Content Patcher" in issue.mods)
    assert content_patch.stack_trace
    assert "Pack Name" in content_patch.mods
    assert parsed.issues[0].severity in (LogSeverity.ERROR, LogSeverity.WARNING)


def test_parse_empty_or_missing_log(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    parsed = SmapiLogParser().parse(empty)
    assert parsed.line_count == 0
    assert parsed.issues == []
    missing = SmapiLogParser().parse(tmp_path / "nope.txt")
    assert missing.parse_warnings


def test_parse_manifest_invalid_reason(tmp_path: Path) -> None:
    log = tmp_path / "SMAPI-latest.txt"
    log.write_text(
        """[12:00:00 INFO  SMAPI] SMAPI 4.0.0 with Stardew Valley 1.6.8
[12:00:00 ERROR SMAPI] Skipped mods
[12:00:00 ERROR SMAPI]    - Bad Manifest 1.0.0 because its manifest is invalid.
[12:00:00 ERROR SMAPI]    - Old API 2.0.0 because it requires API version 4.1.0, but you have 4.0.0.
""",
        encoding="utf-8",
    )
    parsed = SmapiLogParser().parse(log)
    assert len(parsed.skipped_mods) == 2
    assert any(issue.category is IssueCategory.CONFIG for issue in parsed.issues)
    assert any(issue.category is IssueCategory.VERSION_MISMATCH for issue in parsed.issues)
