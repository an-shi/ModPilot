from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adapter_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    install_path: Mapped[str] = mapped_column(Text)
    mods_path: Mapped[str] = mapped_column(Text)
    nexus_domain: Mapped[str] = mapped_column(String(80), default="")
    game_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    framework_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Mod(Base):
    __tablename__ = "mods"
    __table_args__ = (
        UniqueConstraint("game_id", "relative_path", name="uq_mod_game_path"),
        Index("ix_mod_game_unique", "game_id", "unique_id"),
        Index("ix_mod_game_enabled", "game_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    unique_id: Mapped[str] = mapped_column(String(300))
    name: Mapped[str] = mapped_column(String(300))
    author: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[str] = mapped_column(String(100), default="0.0.0")
    description: Mapped[str] = mapped_column(Text, default="")
    folder_name: Mapped[str] = mapped_column(String(300))
    relative_path: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True)
    kind: Mapped[str] = mapped_column(String(40), default="unknown")
    nexus_mod_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    source_url: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    installed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # Update tracking
    update_policy: Mapped[str] = mapped_column(String(40), default="inherit")
    latest_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latest_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_file_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latest_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    update_status: Mapped[str] = mapped_column(String(40), default="unknown")
    update_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ignored_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pinned_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_update_backup_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ModDependency(Base):
    __tablename__ = "mod_dependencies"
    __table_args__ = (Index("ix_dep_mod", "mod_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mod_id: Mapped[int] = mapped_column(ForeignKey("mods.id", ondelete="CASCADE"), index=True)
    unique_id: Mapped[str] = mapped_column(String(300))
    minimum_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    kind: Mapped[str] = mapped_column(String(40), default="required")


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("game_id", "name", name="uq_profile_game_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ProfileEntry(Base):
    __tablename__ = "profile_entries"
    __table_args__ = (UniqueConstraint("profile_id", "unique_id", name="uq_profile_entry"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    unique_id: Mapped[str] = mapped_column(String(300))
    name: Mapped[str] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(100), default="")
    folder_name: Mapped[str] = mapped_column(String(300), default="")
    relative_path: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Backup(Base):
    __tablename__ = "backups"
    __table_args__ = (Index("ix_backup_game", "game_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    mod_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(Text, default="")
    path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(30), default="available")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Download(Base):
    __tablename__ = "downloads"
    __table_args__ = (Index("ix_download_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    nexus_mod_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mod_name: Mapped[str] = mapped_column(String(300), default="")
    file_name: Mapped[str] = mapped_column(String(500), default="")
    archive_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_md5: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_bytes: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), default="mod")
    target_mod_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UpdateRecord(Base):
    __tablename__ = "update_records"
    __table_args__ = (Index("ix_update_mod", "mod_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    mod_id: Mapped[int] = mapped_column(ForeignKey("mods.id", ondelete="CASCADE"), index=True)
    old_version: Mapped[str] = mapped_column(String(100), default="")
    new_version: Mapped[str] = mapped_column(String(100), default="")
    file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="available")
    strategy: Mapped[str] = mapped_column(String(40), default="manual")
    message: Mapped[str] = mapped_column(Text, default="")
    backup_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TranslationRecord(Base):
    __tablename__ = "translations"
    __table_args__ = (
        UniqueConstraint("mod_id", "language", "source_file", "key", name="uq_translation_unit"),
        Index("ix_translation_mod", "mod_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mod_id: Mapped[int] = mapped_column(ForeignKey("mods.id", ondelete="CASCADE"), index=True)
    language: Mapped[str] = mapped_column(String(20), default="zh")
    source_file: Mapped[str] = mapped_column(Text)
    target_file: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(String(500))
    source_text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    origin: Mapped[str] = mapped_column(String(30), default="none")
    model: Mapped[str] = mapped_column(String(200), default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TranslationCache(Base):
    __tablename__ = "translation_cache"
    __table_args__ = (UniqueConstraint("game_key", "language", "source_hash", name="uq_cache_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_key: Mapped[str] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(20))
    source_hash: Mapped[str] = mapped_column(String(64))
    source_text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(30), default="ai")
    model: Mapped[str] = mapped_column(String(200), default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TranslationPack(Base):
    __tablename__ = "translation_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mod_id: Mapped[int] = mapped_column(ForeignKey("mods.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(400))
    url: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[str] = mapped_column(String(100), default="")
    compatibility: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DiagnosticRun(Base):
    __tablename__ = "diagnostic_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    log_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str] = mapped_column(String(40), default="manual")
    status: Mapped[str] = mapped_column(String(30), default="completed")
    summary: Mapped[str] = mapped_column(Text, default="")
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ai_used: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(200), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DiagnosticIssue(Base):
    __tablename__ = "diagnostic_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_runs.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(20))
    certainty: Mapped[str] = mapped_column(String(30))
    category: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    mods_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(default=0.5)
    raw_excerpt: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class RepairPlan(Base):
    __tablename__ = "repair_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    diagnostic_run_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_runs.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="planned")
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[str] = mapped_column(Text, default="")
    backup_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Activity(Base):
    __tablename__ = "activity"
    __table_args__ = (Index("ix_activity_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(60))
    level: Mapped[str] = mapped_column(String(20), default="info")
    message: Mapped[str] = mapped_column(Text)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InstallRecord(Base):
    __tablename__ = "install_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    mod_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="success")
    backup_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
