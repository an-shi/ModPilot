from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from modpilot.core.archive import ArchiveError, extract_archive
from tests.conftest import TestEnv, make_zip


def test_validate_and_detect_installation(env: TestEnv) -> None:
    adapter = env.registry.get("stardew_valley")
    validation = adapter.validate_installation(env.install)
    assert validation.valid
    assert validation.mods_path == env.mods_root
    checks = adapter.check_environment(env.install)
    assert {check.key for check in checks} >= {"game_executable", "content", "mods_dir", "smapi"}
    assert all(check.ok for check in checks if check.key in ("game_executable", "content", "mods_dir"))
    summary = env.games.game_summary(env.game_id)
    assert summary.framework_installed is True


def test_scan_finds_enabled_and_disabled_mods(env: TestEnv) -> None:
    env.make_mod("Enabled Mod", unique_id="Test.Enabled")
    env.make_mod(".Disabled Mod", unique_id="Test.Disabled")
    invalid = env.mods_root / "Invalid"
    invalid.mkdir()
    (invalid / "manifest.json").write_text("{invalid", encoding="utf-8")
    adapter = env.registry.get("stardew_valley")
    scanned = adapter.scan_mods(env.install)
    by_id = {item.metadata.unique_id: item for item in scanned}
    assert "Test.Enabled" in by_id
    assert "Test.Disabled" in by_id
    assert by_id["Test.Enabled"].enabled is True
    assert by_id["Test.Disabled"].enabled is False
    invalid_item = next(item for item in scanned if item.directory == invalid)
    assert invalid_item.error
    assert invalid_item.metadata.unique_id.startswith("invalid:")


def test_enable_disable_renames_and_updates_nested(env: TestEnv) -> None:
    parent = env.make_mod("Parent", unique_id="Test.Parent")
    nested = parent / "Nested"
    nested.mkdir()
    (nested / "manifest.json").write_text(
        json.dumps({"Name": "Nested", "UniqueID": "Test.Nested", "Version": "1.0.0"}),
        encoding="utf-8",
    )
    env.rescan()
    parent_view = next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Parent")
    new_rel = env.mods.set_enabled(parent_view.id, False)
    assert new_rel == ".Parent"
    assert (env.mods_root / ".Parent").is_dir()
    updated = {view.unique_id: view for view in env.mods.list_mods(env.game_id)}
    assert updated["Test.Parent"].enabled is False
    assert updated["Test.Nested"].enabled is False
    assert updated["Test.Nested"].relative_path.startswith(".Parent/")
    new_rel = env.mods.set_enabled(updated["Test.Parent"].id, True)
    assert new_rel == "Parent"
    updated = {view.unique_id: view for view in env.mods.list_mods(env.game_id)}
    assert updated["Test.Parent"].enabled is True
    assert updated["Test.Nested"].enabled is True


def test_disable_nested_mod_only(env: TestEnv) -> None:
    parent = env.make_mod("Parent", unique_id="Test.Parent")
    nested = parent / "Nested"
    nested.mkdir()
    (nested / "manifest.json").write_text(
        json.dumps({"Name": "Nested", "UniqueID": "Test.Nested", "Version": "1.0.0"}),
        encoding="utf-8",
    )
    env.rescan()
    nested_view = next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Nested")
    env.mods.set_enabled(nested_view.id, False)
    assert (parent / ".Nested").is_dir()
    assert (parent / "manifest.json").is_file()
    assert (env.mods_root / "Parent").is_dir()


def test_plan_install_wrapper_and_root_archive(env: TestEnv) -> None:
    adapter = env.registry.get("stardew_valley")
    archive = make_zip(
        env.paths.cache_dir / "mod.zip",
        {
            "Nice Mod/manifest.json": json.dumps(
                {"Name": "Nice Mod", "UniqueID": "Test.Nice", "Version": "1.0.0"}
            ),
            "Nice Mod/data.json": "{}",
        },
    )
    staging = env.paths.staging_dir / "plan-a"
    staging.mkdir()
    extract_archive(archive, staging)
    plan = adapter.plan_install(archive, staging, env.mods_root)
    assert len(plan.entries) == 1
    assert plan.entries[0].destination_name == "Nice Mod"
    assert plan.entries[0].metadata.unique_id == "Test.Nice"

    root_archive = make_zip(
        env.paths.cache_dir / "root.zip",
        {
            "manifest.json": json.dumps(
                {"Name": "Root Mod", "UniqueID": "Test.Root", "Version": "1.0.0"}
            )
        },
    )
    staging2 = env.paths.staging_dir / "plan-b"
    staging2.mkdir()
    extract_archive(root_archive, staging2)
    plan2 = adapter.plan_install(root_archive, staging2, env.mods_root)
    assert plan2.entries[0].destination_name == "Root Mod"


def test_plan_install_rejects_non_mod_archive(env: TestEnv, tmp_path: Path) -> None:
    adapter = env.registry.get("stardew_valley")
    archive = make_zip(tmp_path / "not-a-mod.zip", {"readme.txt": "hello"})
    staging = tmp_path / "staging"
    staging.mkdir()
    extract_archive(archive, staging)
    with pytest.raises(ValueError, match="manifest"):
        adapter.plan_install(archive, staging, env.mods_root)


def test_zip_slip_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../outside.txt", "bad")
    with pytest.raises(ArchiveError, match="不安全|越界"):
        extract_archive(archive, tmp_path / "extract")


def test_stardew_translation_plan_write_and_validate(env: TestEnv) -> None:
    mod = env.make_mod(
        "Translatable",
        unique_id="Test.Translate",
        i18n={
            "greeting": "Hello {{name}}, welcome to {{farm}}!",
            "count": "You have {0} items.",
            "plain": "Good morning.",
        },
    )
    adapter = env.registry.get("stardew_valley")
    plan = adapter.build_translation_plan(mod)
    assert plan is not None
    assert len(plan.units) == 3
    assert plan.target_file.name == "zh.json"
    translations = {
        "greeting": "你好 {{name}}，欢迎来到 {{farm}}！",
        "count": "你有 {0} 件物品。",
        "plain": "早上好。",
    }
    assert adapter.validate_translation(plan, translations) == []
    result = adapter.write_translation(plan, translations)
    assert result.target_file.exists()
    data = json.loads(result.target_file.read_text(encoding="utf-8"))
    assert data["plain"] == "早上好。"
    bad = dict(translations)
    bad["greeting"] = "你好，欢迎！"
    assert adapter.validate_translation(plan, bad)
    with pytest.raises(Exception):
        adapter.write_translation(plan, bad)
