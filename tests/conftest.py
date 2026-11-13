from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from modpilot.adapters.base import AdapterRegistry
from modpilot.adapters.stardew.adapter import StardewValleyAdapter
from modpilot.config import AppConfig, AppPaths, ConfigStore
from modpilot.core.credentials import CredentialStore, InMemorySecretBackend
from modpilot.data.db import Database
from modpilot.services.activity import ActivityService
from modpilot.services.backup_service import BackupService
from modpilot.services.game_service import GameService
from modpilot.services.mod_service import ModService
from modpilot.services.nexus.client import NexusClient
from modpilot.services.profile_service import ProfileService


@dataclass
class TestEnv:
    __test__ = False

    paths: AppPaths
    config: ConfigStore
    credentials: CredentialStore
    db: Database
    install: Path
    mods_root: Path
    activity: ActivityService
    backups: BackupService
    registry: AdapterRegistry
    games: GameService
    mods: ModService
    profiles: ProfileService
    nexus: NexusClient
    game_id: int

    def make_mod(
        self,
        folder: str,
        *,
        unique_id: str,
        name: str | None = None,
        version: str = "1.0.0",
        author: str = "Test Author",
        dependencies: list[dict] | None = None,
        update_keys: list[str] | None = None,
        i18n: dict[str, str] | None = None,
        entry_dll: str | None = "TestMod.dll",
        content_pack_for: dict | None = None,
    ) -> Path:
        root = self.mods_root / folder
        root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "Name": name or folder,
            "Author": author,
            "Version": version,
            "Description": f"{folder} description",
            "UniqueID": unique_id,
            "UpdateKeys": update_keys or [],
            "Dependencies": dependencies or [],
        }
        if entry_dll:
            manifest["EntryDll"] = entry_dll
        if content_pack_for:
            manifest["ContentPackFor"] = content_pack_for
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if i18n is not None:
            (root / "i18n").mkdir(exist_ok=True)
            (root / "i18n" / "default.json").write_text(
                json.dumps(i18n, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return root

    def rescan(self) -> int:
        return self.mods.scan_game(self.game_id)


def _handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, json={"message": "not used in this test"})


@pytest.fixture
def env(tmp_path: Path) -> TestEnv:
    paths = AppPaths(tmp_path / "data")
    config = ConfigStore(paths)
    config.save(AppConfig())
    credentials = CredentialStore(paths.base, backend=InMemorySecretBackend())
    db = Database(paths.db_path)
    db.create_all()
    activity = ActivityService(db)
    backups = BackupService(db, paths, activity)
    registry = AdapterRegistry()
    registry.register(StardewValleyAdapter())
    nexus = NexusClient(
        lambda: None,
        transport=httpx.MockTransport(_handler),
    )
    games = GameService(db, registry, config, activity)
    mods = ModService(db, registry, paths, backups, activity)
    profiles = ProfileService(db, mods, activity)

    install = tmp_path / "Stardew Valley"
    install.mkdir()
    (install / "Stardew Valley.exe").write_bytes(b"stub")
    (install / "StardewModdingAPI.exe").write_bytes(b"stub")
    (install / "Content").mkdir()
    mods_root = install / "Mods"
    mods_root.mkdir()
    game = games.add_game("stardew_valley", install)
    return TestEnv(
        paths=paths,
        config=config,
        credentials=credentials,
        db=db,
        install=install,
        mods_root=mods_root,
        activity=activity,
        backups=backups,
        registry=registry,
        games=games,
        mods=mods,
        profiles=profiles,
        nexus=nexus,
        game_id=game.id,
    )


def make_zip(path: Path, files: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


@pytest.fixture
def sample_log(tmp_path: Path) -> Path:
    content = """[18:00:00 INFO  SMAPI] SMAPI 4.0.0 with Stardew Valley 1.6.8
[18:00:00 INFO  SMAPI] Mods go here: C:\\Games\\Stardew Valley\\Mods
[18:00:00 INFO  SMAPI] Loaded 2 mods:
[18:00:00 INFO  SMAPI]    Good Mod 1.2.0 by Alice | Test mod
[18:00:00 INFO  SMAPI]    Duplicate Mod 2.0.0 by Bob | Another mod
[18:00:00 ERROR SMAPI] Skipped mods
[18:00:00 ERROR SMAPI] --------------------------------------------------
[18:00:00 ERROR SMAPI]    These mods could not be added to your game.
[18:00:00 ERROR SMAPI]    - Broken Mod 1.0.0 because it requires mods which aren't installed (Pathoschild.ContentPatcher, Some.Library).
[18:00:00 ERROR SMAPI]    - Empty Mod 1.0.0 because it contains files, but none of them are manifest.json.
[18:00:01 ERROR Content Patcher] Error loading content pack 'Pack Name': patch failed
   at StardewValley.GameData.Foo()
"""
    path = tmp_path / "SMAPI-latest.txt"
    path.write_text(content, encoding="utf-8")
    return path
