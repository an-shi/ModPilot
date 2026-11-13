from __future__ import annotations

import json
from pathlib import Path

import pytest

from modpilot.core.errors import ModOperationError
from tests.conftest import TestEnv, make_zip


def test_scan_and_missing_dependency(env: TestEnv) -> None:
    env.make_mod("Framework", unique_id="Test.Framework", version="2.0.0")
    env.make_mod(
        "Needs Framework",
        unique_id="Test.Needs",
        dependencies=[{"UniqueID": "Test.Framework", "MinimumVersion": "3.0.0"}],
    )
    env.rescan()
    views = {view.unique_id: view for view in env.mods.list_mods(env.game_id)}
    assert views["Test.Needs"].missing_dependencies
    assert views["Test.Needs"].missing_dependencies[0].unique_id == "Test.Framework"
    assert views["Test.Needs"].missing_dependencies[0].installed_version == "2.0.0"


def test_disabled_dependency_is_reported(env: TestEnv) -> None:
    env.make_mod(".Framework", unique_id="Test.Framework", version="1.0.0")
    env.make_mod(
        "Needs Framework",
        unique_id="Test.Needs",
        dependencies=[{"UniqueID": "Test.Framework"}],
    )
    env.rescan()
    view = next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Needs")
    missing = view.missing_dependencies[0]
    assert missing.installed_version == "1.0.0"
    assert missing.enabled is False
    assert missing.satisfied is False


def test_duplicate_install_detected(env: TestEnv) -> None:
    env.make_mod("Copy A", unique_id="Test.Duplicate")
    env.make_mod("Copy B", unique_id="Test.Duplicate")
    env.rescan()
    views = [view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Duplicate"]
    assert len(views) == 2
    assert all(view.duplicate for view in views)


def test_install_archive_and_replace_with_backup(env: TestEnv) -> None:
    def make_archive(version: str) -> Path:
        return make_zip(
            env.paths.staging_dir / f"mod-{version}.zip",
            {
                "Demo Mod/manifest.json": json.dumps(
                    {
                        "Name": "Demo Mod",
                        "UniqueID": "Test.Demo",
                        "Version": version,
                        "Dependencies": [],
                    }
                ),
                "Demo Mod/version.txt": version,
            },
        )

    archive = make_archive("1.0.0")
    plan = env.mods.preview_install(env.game_id, archive)
    assert plan.entries[0].existing is False
    result = env.mods.install_plan(env.game_id, plan)
    assert (env.mods_root / "Demo Mod" / "version.txt").read_text() == "1.0.0"
    assert result.installed_names == ["Demo Mod"]
    views = {view.unique_id: view for view in env.mods.list_mods(env.game_id)}
    assert views["Test.Demo"].version == "1.0.0"

    archive2 = make_archive("2.0.0")
    plan2 = env.mods.preview_install(env.game_id, archive2)
    assert plan2.entries[0].existing is True
    assert plan2.entries[0].duplicate_of
    with pytest.raises(ModOperationError, match="确认替换"):
        env.mods.install_plan(env.game_id, plan2)
    env.mods.discard_preview(plan2)

    plan3 = env.mods.preview_install(env.game_id, archive2)
    result3 = env.mods.install_plan(
        env.game_id, plan3, allow_replace=True, allow_duplicate=True
    )
    assert result3.backup_ids
    assert (env.mods_root / "Demo Mod" / "version.txt").read_text() == "2.0.0"
    assert env.backups.list_for_game(env.game_id)


def test_install_blocks_until_duplicate_confirmed(env: TestEnv) -> None:
    env.make_mod("Already", unique_id="Test.Same")
    env.rescan()
    archive = make_zip(
        env.paths.staging_dir / "same.zip",
        {
            "Other Folder/manifest.json": json.dumps(
                {"Name": "Other", "UniqueID": "Test.Same", "Version": "2.0.0"}
            )
        },
    )
    plan = env.mods.preview_install(env.game_id, archive)
    assert plan.entries[0].duplicate_of
    with pytest.raises(ModOperationError, match="确认替换|UniqueID"):
        env.mods.install_plan(env.game_id, plan)
    env.mods.discard_preview(plan)


def test_uninstall_and_restore(env: TestEnv) -> None:
    env.make_mod("Removable", unique_id="Test.Remove", version="1.0.0")
    env.rescan()
    view = next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Remove")
    result = env.mods.uninstall(view.id)
    assert result.backup_id
    assert not (env.mods_root / "Removable").exists()
    assert not next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Remove").is_present

    env.mods.rollback(result.backup_id)
    assert (env.mods_root / "Removable" / "manifest.json").is_file()
    restored = next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Remove")
    assert restored.is_present


def test_bulk_enable_disable(env: TestEnv) -> None:
    env.make_mod("A", unique_id="Test.A")
    env.make_mod("B", unique_id="Test.B")
    env.rescan()
    ids = [view.id for view in env.mods.list_mods(env.game_id)]
    result = env.mods.bulk_set_enabled(ids, False)
    assert len(result.succeeded) == 2
    assert not result.failed
    assert all(not view.enabled for view in env.mods.list_mods(env.game_id))
    result = env.mods.bulk_set_enabled(ids, True)
    assert len(result.succeeded) == 2
    assert all(view.enabled for view in env.mods.list_mods(env.game_id))


def test_profiles_create_apply_export_import(env: TestEnv) -> None:
    env.make_mod("A", unique_id="Test.A")
    env.make_mod("B", unique_id="Test.B")
    env.rescan()
    views = {view.unique_id: view for view in env.mods.list_mods(env.game_id)}
    env.mods.set_enabled(views["Test.B"].id, False)
    profile = env.profiles.create_from_current(env.game_id, "Profile One")
    assert profile.mod_count == 2
    assert profile.enabled_count == 1
    assert profile.is_active

    env.mods.set_enabled(views["Test.A"].id, False)
    env.mods.set_enabled(views["Test.B"].id, True)
    result = env.profiles.apply(profile.id)
    assert not result.warnings or all("缺失" not in warning for warning in result.warnings)
    views = {view.unique_id: view for view in env.mods.list_mods(env.game_id)}
    assert views["Test.A"].enabled is True
    assert views["Test.B"].enabled is False

    destination = env.paths.exports_dir / "profile.json"
    env.profiles.export(profile.id, destination)
    assert destination.is_file()
    imported = env.profiles.import_file(env.game_id, destination)
    assert imported.name.startswith("Profile One")
    assert imported.mod_count == 2
    snapshot = env.profiles.get_snapshot(imported.id)
    assert {entry.unique_id for entry in snapshot.entries} == {"Test.A", "Test.B"}


def test_profile_import_rejects_wrong_game(env: TestEnv, tmp_path: Path) -> None:
    payload = {
        "schema_version": 1,
        "game_key": "skyrim",
        "game_name": "Skyrim",
        "exported_at": "2026-01-01T00:00:00Z",
        "entries": [],
    }
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="不能导入"):
        env.profiles.import_file(env.game_id, path)
