from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    BackupKind,
    Certainty,
    DiagnosticSource,
    DownloadKind,
    DownloadStatus,
    IssueCategory,
    LogSeverity,
    ModKind,
    RiskLevel,
    TranslationOrigin,
    TranslationStatus,
    UpdateStatus,
)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)


# ---------------------------------------------------------------------------
# Adapter / game models
# ---------------------------------------------------------------------------


class ModDependencyInfo(DomainModel):
    unique_id: str
    minimum_version: str | None = None
    required: bool = True
    kind: Literal["required", "optional", "content_pack", "framework"] = "required"


class ModMetadata(DomainModel):
    unique_id: str
    name: str
    author: str = ""
    version: str = "0.0.0"
    description: str = ""
    nexus_mod_id: int | None = None
    dependencies: list[ModDependencyInfo] = Field(default_factory=list)
    update_keys: list[str] = Field(default_factory=list)
    minimum_api_version: str | None = None
    content_pack_for: ModDependencyInfo | None = None
    kind: ModKind = ModKind.UNKNOWN
    manifest_path: Path | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ScannedMod(DomainModel):
    metadata: ModMetadata
    directory: Path
    relative_path: str
    enabled: bool
    size_bytes: int = 0
    file_count: int = 0
    error: str | None = None


class GameInstallation(DomainModel):
    adapter_key: str
    path: Path
    display_name: str
    source: str = "自动检测"
    valid: bool = True
    message: str = ""
    game_version: str | None = None


class GameValidation(DomainModel):
    valid: bool
    message: str
    install_path: Path
    mods_path: Path | None = None
    game_version: str | None = None
    framework_version: str | None = None


class EnvironmentCheck(DomainModel):
    key: str
    label: str
    ok: bool
    detail: str
    severity: LogSeverity = LogSeverity.WARNING


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


class InstallEntry(DomainModel):
    source_dir: Path
    destination_name: str
    metadata: ModMetadata
    existing: bool = False
    duplicate_of: str | None = None
    existing_mod_id: int | None = None
    existing_version: str | None = None
    warnings: list[str] = Field(default_factory=list)


class InstallPlan(DomainModel):
    archive_path: Path
    staging_dir: Path
    mods_root: Path
    entries: list[InstallEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class InstallResult(DomainModel):
    installed_mod_ids: list[int] = Field(default_factory=list)
    installed_names: list[str] = Field(default_factory=list)
    backup_ids: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class UninstallResult(DomainModel):
    mod_name: str
    backup_id: int | None = None


class BulkResult(DomainModel):
    succeeded: list[str] = Field(default_factory=list)
    failed: list[tuple[str, str]] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Nexus / updates / downloads
# ---------------------------------------------------------------------------


class NexusFile(DomainModel):
    file_id: int
    name: str
    version: str = ""
    category: str = ""
    category_name: str = ""
    description: str = ""
    size_kb: int = 0
    uploaded_timestamp: int = 0
    mod_version: str = ""
    is_primary: bool = False
    file_name: str = ""
    md5: str = ""

    @property
    def size_bytes(self) -> int:
        return self.size_kb * 1024

    @property
    def uploaded_at(self) -> datetime | None:
        if not self.uploaded_timestamp:
            return None
        from datetime import UTC

        return datetime.fromtimestamp(self.uploaded_timestamp, tz=UTC)


class NexusRequirement(DomainModel):
    mod_id: int | None = None
    mod_name: str
    url: str = ""
    external: bool = False
    notes: str = ""


class NexusModInfo(DomainModel):
    mod_id: int
    game_domain: str
    name: str
    summary: str = ""
    description: str = ""
    version: str = ""
    author: str = ""
    uploaded_by: str = ""
    category: str = ""
    picture_url: str = ""
    nexus_url: str = ""
    updated_timestamp: int = 0
    created_timestamp: int = 0
    downloads: int = 0
    endorsements: int = 0
    adult: bool = False
    requirements: list[NexusRequirement] = Field(default_factory=list)

    @property
    def updated_at(self) -> datetime | None:
        if not self.updated_timestamp:
            return None
        from datetime import UTC

        return datetime.fromtimestamp(self.updated_timestamp, tz=UTC)


class NexusSearchResult(DomainModel):
    mod_id: int
    name: str
    author: str = ""
    summary: str = ""
    picture_url: str = ""
    updated_at: str = ""
    downloads: int = 0
    endorsements: int = 0


class UpdateCheckResult(DomainModel):
    mod_id: int
    name: str
    installed_version: str
    latest_version: str | None = None
    latest_file_id: int | None = None
    latest_file_name: str | None = None
    status: UpdateStatus = UpdateStatus.UNKNOWN
    message: str = ""
    requires_premium: bool = False


class UpdateRecordView(DomainModel):
    id: int
    mod_id: int
    mod_name: str
    old_version: str
    new_version: str
    file_id: int | None = None
    file_name: str | None = None
    status: str
    strategy: str
    message: str = ""
    backup_id: int | None = None
    created_at: datetime
    applied_at: datetime | None = None


class DownloadView(DomainModel):
    id: int
    game_id: int
    nexus_mod_id: int | None = None
    file_id: int | None = None
    mod_name: str
    file_name: str
    archive_path: str | None = None
    status: DownloadStatus
    progress: int
    downloaded_bytes: int
    total_bytes: int
    error: str | None = None
    kind: DownloadKind
    target_mod_id: int | None = None
    retries: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Mod / game views
# ---------------------------------------------------------------------------


class DependencyStatus(DomainModel):
    unique_id: str
    minimum_version: str | None = None
    required: bool = True
    installed_version: str | None = None
    satisfied: bool = False
    enabled: bool = False


class ModView(DomainModel):
    id: int
    game_id: int
    unique_id: str
    name: str
    author: str
    version: str
    description: str
    folder_name: str
    relative_path: str
    enabled: bool
    is_present: bool
    kind: ModKind
    nexus_mod_id: int | None = None
    dependencies: list[DependencyStatus] = Field(default_factory=list)
    missing_dependencies: list[DependencyStatus] = Field(default_factory=list)
    duplicate: bool = False
    latest_version: str | None = None
    latest_file_id: int | None = None
    latest_file_name: str | None = None
    update_status: UpdateStatus = UpdateStatus.UNKNOWN
    update_policy: str = "inherit"
    ignored_version: str | None = None
    pinned_version: str | None = None
    update_error: str | None = None
    translation_total: int = 0
    translation_done: int = 0
    translation_applied: bool = False
    translation_eligible: bool = False
    scan_error: str | None = None
    size_bytes: int = 0
    installed_at: datetime | None = None
    updated_at: datetime | None = None


class GameSummary(DomainModel):
    id: int
    adapter_key: str
    name: str
    install_path: str
    mods_path: str
    nexus_domain: str
    game_version: str | None = None
    framework_version: str | None = None
    framework_installed: bool = False
    mod_count: int = 0
    enabled_count: int = 0
    update_count: int = 0
    issue_count: int = 0
    translation_pending: int = 0
    last_scan_at: datetime | None = None
    created_at: datetime | None = None


class AdapterInfo(DomainModel):
    key: str
    name: str
    description: str
    nexus_domain: str
    capabilities: list[str] = Field(default_factory=list)
    available: bool = True


class DashboardSummary(DomainModel):
    game: GameSummary | None = None
    mod_count: int = 0
    enabled_count: int = 0
    update_count: int = 0
    issue_count: int = 0
    translation_pending: int = 0
    environment: list[EnvironmentCheck] = Field(default_factory=list)
    recent_activity: list[ActivityView] = Field(default_factory=list)


class ActivityView(DomainModel):
    id: int
    kind: str
    level: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    game_id: int | None = None


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


class ProfileEntryData(DomainModel):
    unique_id: str
    name: str
    version: str
    folder_name: str
    relative_path: str
    enabled: bool


class ProfileSnapshot(DomainModel):
    schema_version: int = 1
    game_key: str
    game_name: str
    profile_name: str = ""
    description: str = ""
    exported_at: datetime
    entries: list[ProfileEntryData] = Field(default_factory=list)


class ProfileView(DomainModel):
    id: int
    game_id: int
    name: str
    description: str
    is_active: bool
    mod_count: int
    enabled_count: int
    created_at: datetime
    updated_at: datetime


class ProfileApplyResult(DomainModel):
    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


class TranslationUnit(DomainModel):
    key: str
    source_text: str
    context: str = ""


class TranslationPlan(DomainModel):
    source_file: Path
    target_file: Path
    units: list[TranslationUnit]
    existing_translations: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TranslationUnitView(DomainModel):
    id: int | None = None
    key: str
    source_text: str
    translated_text: str = ""
    status: TranslationStatus = TranslationStatus.PENDING
    origin: TranslationOrigin = TranslationOrigin.NONE
    model: str = ""
    confirmed: bool = False
    updated_at: datetime | None = None
    error: str | None = None


class TranslationOverview(DomainModel):
    mod_id: int
    mod_name: str
    version: str
    source_file: str
    target_file: str
    total_units: int
    translated_units: int
    confirmed_units: int
    applied: bool
    eligible: bool
    message: str = ""


class TranslationCandidate(DomainModel):
    source: Literal["nexus", "github", "local"]
    title: str
    url: str
    author: str = ""
    version: str = ""
    updated: str = ""
    description: str = ""
    compatibility: str = ""
    score: int = 0
    match_reasons: list[str] = Field(default_factory=list)
    needs_manual_download: bool = True


class TranslationApplyResult(DomainModel):
    target_file: Path
    translated_units: int
    backup_id: int | None = None
    warnings: list[str] = Field(default_factory=list)


class TranslationPackSearchResult(DomainModel):
    candidates: list[TranslationCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Diagnostics / repair
# ---------------------------------------------------------------------------


class LogIssue(DomainModel):
    severity: LogSeverity
    category: IssueCategory
    message: str
    raw: str = ""
    line_number: int = 0
    mods: list[str] = Field(default_factory=list)
    missing_dependencies: list[str] = Field(default_factory=list)
    stack_trace: str = ""
    confidence: float = 0.7


class ParsedLog(DomainModel):
    path: Path
    log_type: str
    game_version: str | None = None
    framework_version: str | None = None
    loaded_mods: list[dict[str, Any]] = Field(default_factory=list)
    skipped_mods: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[LogIssue] = Field(default_factory=list)
    line_count: int = 0
    truncated: bool = False
    parse_warnings: list[str] = Field(default_factory=list)


class RuleFinding(DomainModel):
    rule_id: str
    title: str
    description: str
    severity: LogSeverity
    certainty: Certainty
    category: IssueCategory
    mods: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommendation: str = ""
    confidence: float = 0.8
    source: DiagnosticSource = DiagnosticSource.RULE


class DiagnosticFinding(DomainModel):
    title: str
    description: str
    severity: LogSeverity
    certainty: Certainty
    category: IssueCategory
    mods: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommendation: str = ""
    confidence: float = 0.5
    raw_excerpt: str = ""
    source: DiagnosticSource = DiagnosticSource.RULE
    rule_id: str | None = None


class DiagnosticReport(DomainModel):
    summary: str = ""
    findings: list[DiagnosticFinding] = Field(default_factory=list)
    ai_used: bool = False
    ai_error: str | None = None
    log_path: str | None = None
    generated_at: datetime | None = None


class DiagnosticRunView(DomainModel):
    id: int
    game_id: int
    log_path: str | None = None
    trigger: str
    status: str
    summary: str = ""
    ai_used: bool = False
    model: str = ""
    findings: list[DiagnosticFinding] = Field(default_factory=list)
    created_at: datetime


class RepairStep(DomainModel):
    id: str
    title: str
    description: str
    risk: RiskLevel
    automatic: bool = False
    requires_network: bool = False
    action: Literal[
        "enable_mod",
        "disable_mod",
        "update_mod",
        "install_dependency",
        "restore_backup",
        "clean_temp",
        "open_url",
        "manual",
    ] = "manual"
    params: dict[str, Any] = Field(default_factory=dict)
    findings: list[str] = Field(default_factory=list)
    selected: bool = True
    executed: bool = False
    success: bool | None = None
    message: str = ""


class RepairPlanData(DomainModel):
    summary: str
    steps: list[RepairStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RepairPlanView(DomainModel):
    id: int
    game_id: int
    diagnostic_run_id: int
    status: str
    plan: RepairPlanData
    result: str = ""
    backup_id: int | None = None
    created_at: datetime
    applied_at: datetime | None = None


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------


class BackupView(DomainModel):
    id: int
    game_id: int
    mod_id: int | None = None
    kind: BackupKind
    label: str
    note: str
    path: str
    size_bytes: int
    status: str
    created_at: datetime
    restored_at: datetime | None = None


DashboardSummary.model_rebuild()
