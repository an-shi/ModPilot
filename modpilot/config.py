from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .version import APP_NAME
from .i18n import tr


def _default_data_dir() -> Path:
    override = os.environ.get("MODPILOT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


class AppPaths:
    """All runtime paths used by ModPilot.

    Everything is intentionally kept under one local directory so the app has no
    server dependency and is easy to inspect, back up or remove.
    """

    def __init__(self, base: Path | None = None) -> None:
        self.base = (base or _default_data_dir()).expanduser().resolve()
        self.db_path = self.base / "modpilot.db"
        self.config_path = self.base / "config.json"
        self.logs_dir = self.base / "logs"
        self.backups_dir = self.base / "backups"
        self.downloads_dir = self.base / "downloads"
        self.exports_dir = self.base / "exports"
        self.cache_dir = self.base / "cache"
        self.staging_dir = self.cache_dir / "staging"
        self.temp_dir = self.cache_dir / "tmp"
        self.ensure()

    def ensure(self) -> None:
        for directory in (
            self.base,
            self.logs_dir,
            self.backups_dir,
            self.downloads_dir,
            self.exports_dir,
            self.cache_dir,
            self.staging_dir,
            self.temp_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def resolve_downloads(self, configured: str | None) -> Path:
        if not configured:
            return self.downloads_dir
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = self.base / path
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


class LlmConfig(BaseModel):
    """OpenAI-compatible chat completion configuration.

    The API key is never stored here. It lives in Windows Credential Manager
    (or the DPAPI fallback) and is retrieved only when a request is made.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    requires_api_key: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=64, le=131072)
    timeout_seconds: int = Field(default=90, ge=5, le=600)
    json_mode: bool = True
    verify_ssl: bool = True
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError(tr('config.api_address_cannot_be_empty'))
        local_http = value.startswith(
            ("http://localhost", "http://127.0.0.1", "http://[::1]")
        )
        if not (value.startswith("https://") or local_http):
            raise ValueError(tr('config.api_address_must_use_https_http_is_allowed_for_local_3c781b'))
        return value

    @field_validator("extra_headers")
    @classmethod
    def _validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        blocked_exact = {"host", "content-length"}
        blocked_fragments = (
            "authorization",
            "api-key",
            "apikey",
            "api_key",
            "token",
            "secret",
            "cookie",
        )
        for key in value:
            lower = key.lower()
            if lower in blocked_exact or any(fragment in lower for fragment in blocked_fragments):
                raise ValueError(
                    tr('config.extra_request_headers_cannot_contain_the_credential_465add', key)
                )
        return value

    @field_validator("extra_body")
    @classmethod
    def _validate_extra_body(cls, value: dict[str, Any]) -> dict[str, Any]:
        blocked_fragments = ("api_key", "apikey", "api-key", "token", "secret")
        for key in value:
            if any(fragment in str(key).lower() for fragment in blocked_fragments):
                raise ValueError(tr('config.extra_parameters_cannot_contain_the_credential_field_fba3d2', key))
        return value


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: Literal["dark", "light"] = "dark"
    ui_language: Literal["zh-CN", "en-US"] = "zh-CN"
    update_policy: Literal[
        "manual", "notify", "confirm_download", "auto_download", "auto_install", "ignore"
    ] = "notify"
    check_updates_on_startup: bool = True
    update_check_interval_hours: int = Field(default=12, ge=0, le=24 * 30)
    download_dir: str | None = None
    translation_batch_size: int = Field(default=35, ge=1, le=200)
    diagnostics_max_log_chars: int = Field(default=24000, ge=4000, le=200000)
    confirm_before_delete: bool = True
    llm: LlmConfig = Field(default_factory=LlmConfig)
    active_game_id: int | None = None

    def redacted_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ConfigStore:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self._config: AppConfig | None = None
        self.load_error: str | None = None

    @property
    def config(self) -> AppConfig:
        if self._config is None:
            self.load()
        assert self._config is not None
        return self._config

    def load(self) -> AppConfig:
        self.load_error = None
        if not self.paths.config_path.exists():
            self._config = AppConfig()
            return self._config
        try:
            data = json.loads(self.paths.config_path.read_text(encoding="utf-8"))
            self._config = AppConfig.model_validate(data)
        except Exception as exc:  # corrupt config should never prevent startup
            self.load_error = tr('config.could_not_read_the_configuration_file_default_settin_d6ec10', exc)
            broken = self.paths.config_path.with_suffix(".json.invalid")
            try:
                self.paths.config_path.replace(broken)
            except OSError:
                pass
            self._config = AppConfig()
        return self._config

    def save(self, config: AppConfig | None = None) -> None:
        if config is not None:
            self._config = config
        if self._config is None:
            self._config = AppConfig()
        self.paths.base.mkdir(parents=True, exist_ok=True)
        temp = self.paths.config_path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(self._config.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, self.paths.config_path)

    def update(self, **changes: Any) -> AppConfig:
        config = self.config.model_copy(update=changes)
        # Re-validate so a bad value cannot silently enter the config file.
        config = AppConfig.model_validate(config.model_dump())
        self.save(config)
        return config


_REDACTION_PATTERNS = [
    # Bearer must run first: the generic header rule would otherwise consume
    # the word "Bearer" and leave the token itself in place.
    re.compile(r"(?i)(Bearer)\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(api[-_ ]?key|apikey|authorization|token)\s*[:=]\s*[^\s,;\"']+"),
]


def redact_secrets(text: str, secrets: list[str] | None = None) -> str:
    """Remove obvious credentials from log/export text."""

    redacted = text
    for secret in secrets or []:
        if secret and len(secret) >= 6:
            redacted = redacted.replace(secret, "[REDACTED]")
    for pattern in _REDACTION_PATTERNS:
        redacted = pattern.sub(lambda m: f"{m.group(1)}: [REDACTED]", redacted)
    return redacted
