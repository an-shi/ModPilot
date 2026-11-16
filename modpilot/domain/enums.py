from __future__ import annotations

from enum import StrEnum

from ..i18n import tr


class UpdatePolicy(StrEnum):
    MANUAL = "manual"
    NOTIFY = "notify"
    CONFIRM_DOWNLOAD = "confirm_download"
    AUTO_DOWNLOAD = "auto_download"
    AUTO_INSTALL = "auto_install"
    IGNORE = "ignore"

    @property
    def label(self) -> str:
        return tr({
            self.MANUAL: 'domain.enums.manual_check_only',
            self.NOTIFY: 'domain.enums.update_alerts',
            self.CONFIRM_DOWNLOAD: 'domain.enums.download_after_confirmation',
            self.AUTO_DOWNLOAD: 'domain.enums.auto_download',
            self.AUTO_INSTALL: 'domain.enums.auto_download_and_install',
            self.IGNORE: 'domain.enums.ignore_update',
        }[self])

    @property
    def may_download(self) -> bool:
        return self in (self.AUTO_DOWNLOAD, self.AUTO_INSTALL)

    @property
    def may_install(self) -> bool:
        return self is self.AUTO_INSTALL


class UpdateStatus(StrEnum):
    UNKNOWN = "unknown"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    DOWNLOADED = "downloaded"
    INSTALLED = "installed"
    ERROR = "error"
    IGNORED = "ignored"


class ModKind(StrEnum):
    CODE = "code"
    CONTENT_PACK = "content_pack"
    FRAMEWORK = "framework"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return tr({
            self.CODE: 'domain.enums.code_mod',
            self.CONTENT_PACK: 'domain.enums.content_pack',
            self.FRAMEWORK: 'domain.enums.framework_tool',
            self.UNKNOWN: 'domain.enums.unknown',
        }[self])


class BackupKind(StrEnum):
    INSTALL = "install"
    UPDATE = "update"
    UNINSTALL = "uninstall"
    TRANSLATION = "translation"
    REPAIR = "repair"
    PROFILE = "profile"
    MANUAL = "manual"

    @property
    def label(self) -> str:
        return tr({
            self.INSTALL: 'domain.enums.backup_before_install',
            self.UPDATE: 'domain.enums.back_up_before_update',
            self.UNINSTALL: 'domain.enums.backup_before_uninstall',
            self.TRANSLATION: 'domain.enums.backup_before_translation',
            self.REPAIR: 'domain.enums.backup_before_repair',
            self.PROFILE: 'domain.enums.profile_backup',
            self.MANUAL: 'domain.enums.manual_backup',
        }[self])


class DownloadStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return tr({
            self.QUEUED: 'domain.enums.queued',
            self.DOWNLOADING: 'domain.enums.downloading',
            self.PAUSED: 'domain.enums.paused',
            self.COMPLETED: 'domain.enums.completed',
            self.FAILED: 'domain.enums.failed',
            self.CANCELLED: 'domain.enums.cancelled',
        }[self])


class DownloadKind(StrEnum):
    MOD = "mod"
    UPDATE = "update"
    TRANSLATION = "translation"


class TranslationStatus(StrEnum):
    PENDING = "pending"
    TRANSLATED = "translated"
    CONFIRMED = "confirmed"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return tr({
            self.PENDING: 'domain.enums.pending_translation',
            self.TRANSLATED: 'domain.enums.translated',
            self.CONFIRMED: 'domain.enums.confirmed',
            self.FAILED: 'domain.enums.failed',
        }[self])


class TranslationOrigin(StrEnum):
    AI = "ai"
    CACHE = "cache"
    HUMAN = "human"
    EXISTING = "existing"
    NONE = "none"

    @property
    def label(self) -> str:
        return tr({
            self.AI: 'domain.enums.ai_translation',
            self.CACHE: 'domain.enums.local_cache',
            self.HUMAN: 'domain.enums.manual_translation',
            self.EXISTING: 'domain.enums.existing_translation',
            self.NONE: "—",
        }[self])


class LogSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def label(self) -> str:
        return tr({
            self.INFO: 'domain.enums.tip',
            self.WARNING: 'domain.enums.warning',
            self.ERROR: 'domain.enums.error',
            self.CRITICAL: 'domain.enums.critical',
        }[self])

    @property
    def rank(self) -> int:
        return {self.INFO: 0, self.WARNING: 1, self.ERROR: 2, self.CRITICAL: 3}[self]


class Certainty(StrEnum):
    CONFIRMED = "confirmed"
    KNOWN = "known"
    SUSPECTED = "suspected"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return tr({
            self.CONFIRMED: 'domain.enums.definitive_evidence',
            self.KNOWN: 'domain.enums.known_issue',
            self.SUSPECTED: 'domain.enums.possible_issue',
            self.UNKNOWN: 'domain.enums.uncertain',
        }[self])


class IssueCategory(StrEnum):
    MISSING_DEPENDENCY = "missing_dependency"
    VERSION_MISMATCH = "version_mismatch"
    DUPLICATE = "duplicate"
    LOG_ERROR = "log_error"
    ENVIRONMENT = "environment"
    COMPATIBILITY = "compatibility"
    FILE_CONFLICT = "file_conflict"
    CONFIG = "config"
    OTHER = "other"

    @property
    def label(self) -> str:
        return tr({
            self.MISSING_DEPENDENCY: 'domain.enums.missing_dependencies',
            self.VERSION_MISMATCH: 'domain.enums.incompatible_version',
            self.DUPLICATE: 'domain.enums.duplicate_installation',
            self.LOG_ERROR: 'domain.enums.log_error',
            self.ENVIRONMENT: 'domain.enums.game_environment',
            self.COMPATIBILITY: 'domain.enums.compatibility',
            self.FILE_CONFLICT: 'domain.enums.file_conflict',
            self.CONFIG: 'domain.enums.configuration_issue',
            self.OTHER: 'domain.enums.other',
        }[self])


class DiagnosticSource(StrEnum):
    LOG = "log"
    RULE = "rule"
    AI = "ai"


class RepairStatus(StrEnum):
    PLANNED = "planned"
    APPLIED = "applied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def label(self) -> str:
        return tr({self.LOW: 'domain.enums.low_risk', self.MEDIUM: 'domain.enums.medium_risk', self.HIGH: 'domain.enums.high_risk'}[self])
