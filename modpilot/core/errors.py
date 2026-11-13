from __future__ import annotations


class ModPilotError(Exception):
    """Base class for expected, user-facing application errors."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return self.message


class ConfigError(ModPilotError):
    pass


class CredentialError(ModPilotError):
    pass


class ValidationError(ModPilotError):
    pass


class AdapterError(ModPilotError):
    pass


class GameNotFoundError(AdapterError):
    pass


class ModOperationError(ModPilotError):
    pass


class ArchiveError(ModPilotError):
    pass


class BackupError(ModPilotError):
    pass


class NexusError(ModPilotError):
    pass


class NexusAuthError(NexusError):
    pass


class NexusPremiumRequired(NexusError):
    pass


class NexusRateLimitError(NexusError):
    pass


class NexusNotFoundError(NexusError):
    pass


class DownloadError(ModPilotError):
    pass


class DownloadCancelled(DownloadError):
    pass


class TranslationError(ModPilotError):
    pass


class LlmError(TranslationError):
    pass


class DiagnosticError(ModPilotError):
    pass


class RepairError(ModPilotError):
    pass


class OperationCancelled(ModPilotError):
    pass
