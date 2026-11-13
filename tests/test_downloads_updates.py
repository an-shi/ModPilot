from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx
import pytest

from modpilot.domain.enums import DownloadStatus, UpdateStatus
from modpilot.services.download_manager import DownloadManager
from modpilot.services.nexus.client import NexusClient
from modpilot.services.update_service import UpdateService
from tests.conftest import TestEnv, make_zip

CONTENT = b"hello mod content\n" * 4096


def nexus_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/games/stardewvalley/mods/123.json":
        return httpx.Response(
            200,
            json={
                "mod_id": 123,
                "domain_name": "stardewvalley",
                "name": "Demo Mod",
                "version": "2.0.0",
                "author": "Tester",
                "description": "Demo",
            },
        )
    if path == "/v1/games/stardewvalley/mods/123/files.json":
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "file_id": 20,
                        "name": "Demo 2.0",
                        "file_name": "demo-2.zip",
                        "version": "2.0.0",
                        "mod_version": "2.0.0",
                        "category_name": "MAIN",
                        "size_kb": 1,
                        "uploaded_timestamp": 200,
                        "is_primary": True,
                        "md5": hashlib.md5(CONTENT).hexdigest(),
                    }
                ]
            },
        )
    if path == "/v1/games/stardewvalley/mods/123/files/20/download_link.json":
        return httpx.Response(200, json=[{"URI": "https://cdn.example.com/demo.zip"}])
    return httpx.Response(404, json={"message": path})


def cdn_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "cdn.example.com":
        return httpx.Response(
            200,
            content=CONTENT,
            headers={
                "Content-Type": "application/zip",
                "Content-Length": str(len(CONTENT)),
            },
        )
    return nexus_handler(request)


def wait_for(manager: DownloadManager, download_id: int, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = manager.get(download_id)
        if item.status in (DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            return item
        time.sleep(0.05)
    raise AssertionError("download did not finish in time")


@pytest.fixture
def manager(env: TestEnv):
    nexus = NexusClient(lambda: "test-key", transport=httpx.MockTransport(nexus_handler))
    events = []
    mgr = DownloadManager(
        env.db,
        env.paths,
        env.config,
        nexus,
        env.activity,
        on_event=events.append,
        transport=httpx.MockTransport(cdn_handler),
    )
    mgr.events = events  # type: ignore[attr-defined]
    yield mgr
    mgr.shutdown(wait=False)
    nexus.close()


def test_download_completes_and_verifies_md5(env: TestEnv, manager: DownloadManager) -> None:
    item = manager.enqueue(
        game_id=env.game_id,
        nexus_mod_id=123,
        file_id=20,
        mod_name="Demo Mod",
        file_name="demo.zip",
        expected_md5=hashlib.md5(CONTENT).hexdigest(),
    )
    finished = wait_for(manager, item.id)
    assert finished.status is DownloadStatus.COMPLETED, finished.error
    assert finished.progress == 100
    assert finished.archive_path
    assert Path(finished.archive_path).read_bytes() == CONTENT
    assert Path(finished.archive_path).with_name(Path(finished.archive_path).name + ".part").exists() is False
    assert any(event.status == "completed" for event in manager.events)  # type: ignore[attr-defined]


def test_download_queue_and_pause_listing(env: TestEnv, manager: DownloadManager) -> None:
    item = manager.enqueue(
        game_id=env.game_id,
        nexus_mod_id=123,
        file_id=20,
        mod_name="Demo Mod",
        file_name="demo.zip",
        expected_md5="",
    )
    finished = wait_for(manager, item.id)
    assert finished.status is DownloadStatus.COMPLETED
    rows = manager.list_for_game(env.game_id)
    assert rows and rows[0].id == item.id


class FakeDownloads:
    def __init__(self) -> None:
        self.enqueued: list[dict] = []

    def enqueue(self, **kwargs):  # type: ignore[no-untyped-def]
        self.enqueued.append(kwargs)
        return type("D", (), {"id": 99})()


def test_check_update_download_install_and_rollback(env: TestEnv) -> None:
    env.make_mod("Demo Mod", unique_id="Test.Demo", version="1.0.0", update_keys=["Nexus:123"])
    env.rescan()
    view = next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Demo")
    nexus = NexusClient(lambda: "test-key", transport=httpx.MockTransport(nexus_handler))
    fake_downloads = FakeDownloads()
    updates = UpdateService(
        env.db,
        env.registry,
        env.config,
        nexus,
        env.mods,
        fake_downloads,  # type: ignore[arg-type]
        env.activity,
    )
    result = updates.check_mod(view.id)
    assert result.status is UpdateStatus.AVAILABLE
    assert result.latest_version == "2.0.0"

    download_id = updates.download_update(view.id)
    assert download_id == 99
    assert fake_downloads.enqueued[0]["file_id"] == 20
    assert fake_downloads.enqueued[0]["expected_md5"]

    archive = make_zip(
        env.paths.staging_dir / "update.zip",
        {
            "Demo Mod/manifest.json": json.dumps(
                {
                    "Name": "Demo Mod",
                    "UniqueID": "Test.Demo",
                    "Version": "2.0.0",
                    "EntryDll": "Demo.dll",
                }
            ),
            "Demo Mod/version.txt": "2.0.0",
        },
    )
    backup_id = updates.install_downloaded_update(view.id, archive)
    assert backup_id
    updated = env.mods.get_mod(view.id)
    assert updated.version == "2.0.0"
    assert (env.mods_root / "Demo Mod" / "version.txt").read_text() == "2.0.0"
    history = updates.history(env.game_id)
    assert any(record.status == "installed" for record in history)

    updates.rollback_last_update(view.id)
    rolled_back = env.mods.get_mod(view.id)
    assert rolled_back.version == "1.0.0"
    manifest = json.loads((env.mods_root / "Demo Mod" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["Version"] == "1.0.0"
    nexus.close()
