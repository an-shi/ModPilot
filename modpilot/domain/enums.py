from __future__ import annotations

from enum import StrEnum


class UpdatePolicy(StrEnum):
    MANUAL = "manual"
    NOTIFY = "notify"
    CONFIRM_DOWNLOAD = "confirm_download"
    AUTO_DOWNLOAD = "auto_download"
    AUTO_INSTALL = "auto_install"
    IGNORE = "ignore"

    @property
    def label(self) -> str:
        return {
            self.MANUAL: "仅手动检查",
            self.NOTIFY: "更新提醒",
            self.CONFIRM_DOWNLOAD: "确认后下载",
            self.AUTO_DOWNLOAD: "自动下载",
            self.AUTO_INSTALL: "自动下载并安装",
            self.IGNORE: "忽略更新",
        }[self]

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
        return {
            self.CODE: "代码 Mod",
            self.CONTENT_PACK: "内容包",
            self.FRAMEWORK: "框架/工具",
            self.UNKNOWN: "未知",
        }[self]


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
        return {
            self.INSTALL: "安装前备份",
            self.UPDATE: "更新前备份",
            self.UNINSTALL: "卸载前备份",
            self.TRANSLATION: "汉化前备份",
            self.REPAIR: "修复前备份",
            self.PROFILE: "配置档备份",
            self.MANUAL: "手动备份",
        }[self]


class DownloadStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            self.QUEUED: "排队中",
            self.DOWNLOADING: "下载中",
            self.PAUSED: "已暂停",
            self.COMPLETED: "已完成",
            self.FAILED: "失败",
            self.CANCELLED: "已取消",
        }[self]


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
        return {
            self.PENDING: "待翻译",
            self.TRANSLATED: "已翻译",
            self.CONFIRMED: "已确认",
            self.FAILED: "失败",
        }[self]


class TranslationOrigin(StrEnum):
    AI = "ai"
    CACHE = "cache"
    HUMAN = "human"
    EXISTING = "existing"
    NONE = "none"

    @property
    def label(self) -> str:
        return {
            self.AI: "AI 翻译",
            self.CACHE: "本地缓存",
            self.HUMAN: "人工翻译",
            self.EXISTING: "已有汉化",
            self.NONE: "—",
        }[self]


class LogSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def label(self) -> str:
        return {
            self.INFO: "提示",
            self.WARNING: "警告",
            self.ERROR: "错误",
            self.CRITICAL: "严重错误",
        }[self]

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
        return {
            self.CONFIRMED: "确定性证据",
            self.KNOWN: "已知问题",
            self.SUSPECTED: "疑似问题",
            self.UNKNOWN: "无法确定",
        }[self]


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
        return {
            self.MISSING_DEPENDENCY: "缺失依赖",
            self.VERSION_MISMATCH: "版本不兼容",
            self.DUPLICATE: "重复安装",
            self.LOG_ERROR: "日志错误",
            self.ENVIRONMENT: "游戏环境",
            self.COMPATIBILITY: "兼容性",
            self.FILE_CONFLICT: "文件冲突",
            self.CONFIG: "配置问题",
            self.OTHER: "其他",
        }[self]


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
        return {self.LOW: "低风险", self.MEDIUM: "中风险", self.HIGH: "高风险"}[self]
