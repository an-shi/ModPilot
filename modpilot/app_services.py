from __future__ import annotations

import logging
from pathlib import Path

from .adapters.base import AdapterRegistry
from .adapters.stardew.adapter import StardewValleyAdapter
from .config import AppPaths, ConfigStore
from .core.credentials import NEXUS_SECRET, CredentialStore, SecretBackend
from .core.logging_setup import setup_logging
from .data.db import Database
from .services.activity import ActivityService
from .services.backup_service import BackupService
from .services.dashboard_service import DashboardService
from .services.diagnostics.engine import DiagnosticEngine
from .services.download_manager import DownloadManager
from .services.game_service import GameService
from .services.mod_service import ModService
from .services.nexus.client import NexusClient
from .services.profile_service import ProfileService
from .services.repair_service import RepairService
from .services.translation.engine import TranslationEngine
from .services.translation.llm import LlmClient
from .services.update_service import UpdateService
from .version import VERSION

log = logging.getLogger(__name__)


class AppServices:
    """Composition root shared by the UI and headless tests."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        credential_backend: SecretBackend | None = None,
    ) -> None:
        self.paths = AppPaths(data_dir)
        self.credentials = CredentialStore(self.paths.base, backend=credential_backend)
        self.config = ConfigStore(self.paths)
        self.config.load()
        self.log_path = setup_logging(
            self.paths.logs_dir,
            secret_provider=self.credentials.known_secrets,
        )
        log.info("ModPilot %s 启动，数据目录：%s", VERSION, self.paths.base)
        if self.config.load_error:
            log.warning(self.config.load_error)
        self.db = Database(self.paths.db_path)
        self.db.create_all()

        self.registry = AdapterRegistry()
        self.registry.register(StardewValleyAdapter())

        self.activity = ActivityService(self.db)
        self.backups = BackupService(self.db, self.paths, self.activity)
        self.nexus = NexusClient(lambda: self.credentials.get(NEXUS_SECRET))
        self.games = GameService(self.db, self.registry, self.config, self.activity)
        self.mods = ModService(self.db, self.registry, self.paths, self.backups, self.activity)
        self.profiles = ProfileService(self.db, self.mods, self.activity)
        self.downloads = DownloadManager(
            self.db,
            self.paths,
            self.config,
            self.nexus,
            self.activity,
        )
        self.updates = UpdateService(
            self.db,
            self.registry,
            self.config,
            self.nexus,
            self.mods,
            self.downloads,
            self.activity,
        )
        self.llm = LlmClient(self.config, self.credentials)
        self.translation = TranslationEngine(
            self.db,
            self.registry,
            self.config,
            self.paths,
            self.llm,
            self.backups,
            self.activity,
            self.nexus,
        )
        self.diagnostics = DiagnosticEngine(
            self.db,
            self.registry,
            self.config,
            self.llm,
            self.mods,
            self.activity,
        )
        self.repairs = RepairService(
            self.db,
            self.paths,
            self.mods,
            self.backups,
            self.nexus,
            self.activity,
        )
        self.dashboard = DashboardService(
            self.db, self.registry, self.games, self.mods, self.activity
        )
        interrupted = self.downloads.reset_interrupted()
        if interrupted:
            self.activity.record(
                f"检测到 {interrupted} 个上次中断的下载，已暂停，可在下载中心继续。",
                kind="download",
                level="warning",
            )
        self._validate_active_game()

    def _validate_active_game(self) -> None:
        try:
            games = self.games.list_games()
            active = self.config.config.active_game_id
            if active is not None and all(game.id != active for game in games):
                self.config.update(active_game_id=games[0].id if games else None)
            elif active is None and games:
                self.config.update(active_game_id=games[0].id)
        except Exception:
            log.exception("校验当前游戏失败")

    @property
    def nexus_configured(self) -> bool:
        return bool(self.credentials.get(NEXUS_SECRET))

    @property
    def llm_configured(self) -> bool:
        return self.llm.is_configured()

    def close(self, *, wait_for_downloads: bool = False) -> None:
        try:
            self.downloads.shutdown(wait=wait_for_downloads)
        finally:
            self.nexus.close()
            self.db.dispose()
            log.info("ModPilot 已关闭")
