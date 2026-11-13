from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from modpilot.core.backup import MANIFEST_NAME, BackupManager
from modpilot.core.errors import BackupError


def test_backup_and_restore_directory(tmp_path: Path) -> None:
    root = tmp_path / "Mods"
    mod = root / "Demo"
    mod.mkdir(parents=True)
    (mod / "manifest.json").write_text('{"Name":"Demo"}', encoding="utf-8")
    (mod / "data").mkdir()
    (mod / "data" / "file.txt").write_text("v1", encoding="utf-8")

    manager = BackupManager(tmp_path / "backups")
    info = manager.create(root, [mod], kind="install", label="Demo")
    assert info.path.is_file()
    assert info.manifest["items"] == ["Demo"]
    assert info.size_bytes > 0

    (mod / "data" / "file.txt").write_text("v2", encoding="utf-8")
    (mod / "new.txt").write_text("new", encoding="utf-8")
    manager.restore(info.path)
    assert (mod / "data" / "file.txt").read_text() == "v1"
    assert not (mod / "new.txt").exists()


def test_backup_single_file_and_inspect(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "i18n" / "default.json"
    target.parent.mkdir()
    target.write_text("{}", encoding="utf-8")
    manager = BackupManager(tmp_path / "backups")
    info = manager.create(root, [target.parent], kind="translation", label="i18n", note="test")
    inspected = manager.inspect(info.path)
    assert inspected.manifest["kind"] == "translation"
    assert inspected.manifest["note"] == "test"
    assert "i18n" in inspected.manifest["items"]


def test_backup_manifest_rejects_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            MANIFEST_NAME,
            json.dumps(
                {
                    "manifest_version": 1,
                    "created_at": "2026-01-01T00:00:00Z",
                    "kind": "manual",
                    "label": "evil",
                    "note": "",
                    "root": str(tmp_path),
                    "items": ["../outside"],
                    "extra": {},
                }
            ),
        )
    manager = BackupManager(tmp_path / "backups")
    with pytest.raises(BackupError, match="不安全"):
        manager.read_manifest(archive)


def test_backup_reports_missing_source(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    manager = BackupManager(tmp_path / "backups")
    with pytest.raises(BackupError, match="不存在"):
        manager.create(root, [root / "nope"], kind="manual", label="nope")
