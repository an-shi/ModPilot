from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from modpilot.config import AppConfig, LlmConfig, redact_secrets
from modpilot.core.credentials import (
    CredentialStore,
    DpapiBackend,
    InMemorySecretBackend,
    NEXUS_SECRET,
)
from modpilot.core.errors import BackupError


def test_redact_secrets() -> None:
    text = (
        "api_key=abcd1234567890 "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.secret "
        "nexus=abcd1234567890"
    )
    cleaned = redact_secrets(text, ["abcd1234567890"])
    assert "abcd1234567890" not in cleaned
    assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned
    assert "[REDACTED]" in cleaned


def test_llm_config_validation() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        LlmConfig(base_url="http://example.com/v1")
    assert LlmConfig(base_url="http://127.0.0.1:11434/v1").base_url.startswith("http://127")
    with pytest.raises(ValueError, match="请求头"):
        LlmConfig(extra_headers={"Authorization": "Bearer nope"})
    with pytest.raises(ValueError, match="请求头"):
        LlmConfig(extra_headers={"X-API-Key": "nope"})
    with pytest.raises(ValueError, match="额外参数"):
        LlmConfig(extra_body={"api_key": "nope"})


def test_secrets_are_not_in_config_export(tmp_path: Path) -> None:
    backend = InMemorySecretBackend()
    store = CredentialStore(tmp_path, backend=backend)
    store.set(NEXUS_SECRET, "super-secret-value")
    assert store.get(NEXUS_SECRET) == "super-secret-value"
    config = AppConfig()
    exported = json.dumps(config.redacted_dict(), ensure_ascii=False)
    assert "super-secret-value" not in exported
    assert "api_key" not in config.redacted_dict()["llm"]
    store.delete(NEXUS_SECRET)
    assert store.get(NEXUS_SECRET) is None


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_roundtrip(tmp_path: Path) -> None:
    backend = DpapiBackend(tmp_path / "credentials.dat")
    backend.set(NEXUS_SECRET, "dpapi-secret")
    assert backend.get(NEXUS_SECRET) == "dpapi-secret"
    raw = (tmp_path / "credentials.dat").read_text(encoding="utf-8")
    assert "dpapi-secret" not in raw
    backend.delete(NEXUS_SECRET)
    assert backend.get(NEXUS_SECRET) is None


def test_corrupt_backup_is_rejected(env) -> None:  # type: ignore[no-untyped-def]
    target = env.mods_root / "BackupMe"
    target.mkdir()
    (target / "file.txt").write_text("data", encoding="utf-8")
    backup = env.backups.create(
        game_id=env.game_id,
        root=env.mods_root,
        items=[target],
        kind="manual",
        label="BackupMe",
    )
    Path(backup.path).write_bytes(b"corrupted")
    with pytest.raises(BackupError, match="完整性|备份"):
        env.backups.restore(backup.id)
