from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.enums import ModKind
from ..domain.models import (
    AdapterInfo,
    EnvironmentCheck,
    GameInstallation,
    GameValidation,
    InstallPlan,
    ModMetadata,
    ParsedLog,
    ScannedMod,
    TranslationApplyResult,
    TranslationPlan,
)
from ..i18n import tr

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True)
class AdapterCapabilities:
    scan: bool = True
    install: bool = True
    enable_disable: bool = True
    dependencies: bool = True
    log_parsing: bool = False
    environment_check: bool = False
    translation: bool = False
    conflict_rules: bool = False
    game_version: bool = False

    def labels(self) -> list[str]:
        mapping = {
            "scan": "adapters.base.mod_scan",
            "install": "adapters.base.install_uninstall",
            "enable_disable": "adapters.base.enable_disable",
            "dependencies": "adapters.base.dependency_resolution",
            "log_parsing": "adapters.base.log_parsing",
            "environment_check": "adapters.base.environment_check",
            "translation": "adapters.base.ai_translation",
            "conflict_rules": "adapters.base.conflict_rules",
            "game_version": "adapters.base.version_detection",
        }
        return [
            tr(message)
            for attribute, message in mapping.items()
            if getattr(self, attribute)
        ]


class GameAdapter(ABC):
    """Contract between the game-agnostic engine and one game.

    Adapters may know about their game's files and frameworks. The core must
    not. All methods should raise :class:`AdapterError` with user-readable
    messages for expected failures.
    """

    key: str = ""
    # These are message IDs. Use :attr:`display_name` and
    # :attr:`display_description` for text shown to the user.
    name: str = ""
    description: str = ""
    nexus_domain: str = ""
    steam_app_id: str | None = None
    capabilities: AdapterCapabilities = AdapterCapabilities()

    @property
    def display_name(self) -> str:
        return tr(self.name) if self.name else self.key

    @property
    def display_description(self) -> str:
        return tr(self.description) if self.description else ""

    @abstractmethod
    def detect_installations(self) -> list[GameInstallation]:
        """Best-effort discovery of installed copies of the game."""

    @abstractmethod
    def validate_installation(self, path: Path) -> GameValidation:
        """Check that *path* is a usable game installation."""

    @abstractmethod
    def resolve_mods_dir(self, install_path: Path) -> Path:
        """Return the directory containing mods, without creating it."""

    @abstractmethod
    def scan_mods(self, install_path: Path, progress: ProgressCallback | None = None) -> list[ScannedMod]:
        """Discover installed mods, including disabled ones."""

    @abstractmethod
    def plan_install(
        self,
        archive_path: Path,
        staging_dir: Path,
        mods_root: Path,
    ) -> InstallPlan:
        """Map extracted archive contents to mod destinations.

        The staging directory has already been safely extracted by the engine.
        Implementations must reject archives that do not contain a valid mod.
        """

    @abstractmethod
    def set_enabled(self, mods_root: Path, relative_path: str, enabled: bool) -> str:
        """Toggle a mod and return its new relative path.

        The operation must be reversible without touching file contents.
        """

    def check_environment(self, install_path: Path) -> list[EnvironmentCheck]:
        return []

    def find_logs(self, install_path: Path) -> list[Path]:
        return []

    def parse_log(self, log_path: Path) -> ParsedLog:
        raise NotImplementedError(
            tr(
                'adapters.base.the_0_adapter_does_not_implement_log_parsing_yet',
                self.display_name,
            )
        )

    def get_game_version(self, install_path: Path) -> str | None:
        return None

    def get_framework_version(self, install_path: Path) -> str | None:
        return None

    def is_framework_installed(self, install_path: Path) -> bool:
        return self.get_framework_version(install_path) is not None

    def is_translatable(self, metadata: ModMetadata, mod_dir: Path) -> bool:
        return False

    def is_translation_applied(self, mod_dir: Path, language: str = "zh") -> bool:
        return False

    def build_translation_plan(self, mod_dir: Path, language: str = "zh") -> TranslationPlan | None:
        return None

    def validate_translation(
        self,
        plan: TranslationPlan,
        translations: dict[str, str],
    ) -> list[str]:
        return []

    def write_translation(
        self,
        plan: TranslationPlan,
        translations: dict[str, str],
    ) -> TranslationApplyResult:
        raise NotImplementedError(
            tr(
                'adapters.base.the_0_adapter_does_not_implement_writing_chinese_loc_ef430d',
                self.display_name,
            )
        )

    def load_known_issue_rules(self) -> list[dict[str, Any]]:
        return []

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            key=self.key,
            name=self.display_name,
            description=self.display_description,
            nexus_domain=self.nexus_domain,
            capabilities=self.capabilities.labels(),
        )

    @staticmethod
    def metadata_fallback(directory: Path, *, error: str, enabled: bool, relative_path: str) -> ScannedMod:
        """Represent an invalid/broken mod folder so the UI can surface it."""

        metadata = ModMetadata(
            unique_id=f"invalid:{relative_path.replace('\\', '/')}",
            name=directory.name,
            version="",
            kind=ModKind.UNKNOWN,
            manifest_path=directory / "manifest.json" if (directory / "manifest.json").exists() else None,
            extra={"scan_error": error},
        )
        return ScannedMod(
            metadata=metadata,
            directory=directory,
            relative_path=relative_path,
            enabled=enabled,
            error=error,
        )


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, GameAdapter] = {}

    def register(self, adapter: GameAdapter) -> None:
        if not adapter.key:
            raise ValueError(tr('adapters.base.adapter_must_declare_a_key'))
        if adapter.key in self._adapters:
            raise ValueError(tr('adapters.base.duplicate_adapter_registration_0', adapter.key))
        self._adapters[adapter.key] = adapter

    def get(self, key: str) -> GameAdapter:
        try:
            return self._adapters[key]
        except KeyError as exc:
            raise KeyError(tr('adapters.base.unknown_game_adapter_0', key)) from exc

    def try_get(self, key: str) -> GameAdapter | None:
        return self._adapters.get(key)

    def all(self) -> list[GameAdapter]:
        return sorted(self._adapters.values(), key=lambda a: a.name)

    def infos(self) -> list[AdapterInfo]:
        return [adapter.info() for adapter in self.all()]
