from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import func, select

from ..adapters.base import AdapterRegistry, GameAdapter
from ..config import ConfigStore
from ..data.db import Database
from ..data.models import Game, Mod
from ..data.repositories import DiagnosticRepository, GameRepository
from ..domain.enums import LogSeverity, UpdateStatus
from ..domain.models import (
    AdapterInfo,
    EnvironmentCheck,
    GameInstallation,
    GameSummary,
    GameValidation,
)
from .activity import ActivityService
from ..i18n import tr

log = logging.getLogger(__name__)


class GameService:
    def __init__(
        self,
        db: Database,
        registry: AdapterRegistry,
        config: ConfigStore,
        activity: ActivityService,
    ) -> None:
        self.db = db
        self.registry = registry
        self.config = config
        self.activity = activity

    def adapters(self) -> list[AdapterInfo]:
        return self.registry.infos()

    def adapter_for_game_id(self, game_id: int) -> tuple[Game, GameAdapter]:
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError(tr('services.game_service.no_game_has_been_added'))
            adapter = self.registry.try_get(game.adapter_key)
            if adapter is None:
                raise KeyError(tr('services.game_service.game_adapter_not_found_0', game.adapter_key))
            return game, adapter

    def detect_installations(self, adapter_key: str) -> list[GameInstallation]:
        adapter = self.registry.get(adapter_key)
        return adapter.detect_installations()

    def validate(self, adapter_key: str, path: str | Path) -> GameValidation:
        adapter = self.registry.get(adapter_key)
        return adapter.validate_installation(Path(path))

    def add_game(self, adapter_key: str, path: str | Path) -> GameSummary:
        adapter = self.registry.get(adapter_key)
        validation = adapter.validate_installation(Path(path))
        if not validation.valid:
            raise ValueError(validation.message)
        with self.db.session() as session:
            repo = GameRepository(session)
            existing = repo.get_by_adapter(adapter_key)
            if existing is not None:
                raise ValueError(
                    tr(
                        'services.game_service.0_has_already_been_added_1_remove_the_existing_entry_cef390',
                        adapter.display_name,
                        existing.install_path,
                    )
                )
            game = repo.add(
                adapter_key=adapter_key,
                name=adapter.name,
                install_path=str(validation.install_path),
                mods_path=str(validation.mods_path or adapter.resolve_mods_dir(validation.install_path)),
                nexus_domain=adapter.nexus_domain,
                game_version=validation.game_version,
                framework_version=validation.framework_version,
            )
            game_id = game.id
            if self.config.config.active_game_id is None:
                self.config.update(active_game_id=game_id)
            summary = self._summary(session, game)
        self.activity.record(
            tr('services.game_service.added_game_0', adapter.display_name),
            kind="game_add",
            game_id=game_id,
            details={"path": str(validation.install_path)},
        )
        return summary

    def remove_game(self, game_id: int) -> None:
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError(tr('services.game_service.no_game_has_been_added'))
            adapter = self.registry.try_get(game.adapter_key)
            name = adapter.display_name if adapter else game.name
            GameRepository(session).delete(game)
            if self.config.config.active_game_id == game_id:
                remaining = GameRepository(session).list_all()
                self.config.update(active_game_id=remaining[0].id if remaining else None)
        self.activity.record(
            tr('services.game_service.removed_game_management_record_game_files_were_not_d_e14c92', name),
            kind="game_remove",
            level="warning",
            details={"game_id": game_id},
        )

    def migrate_display_names(self) -> None:
        """Store language-neutral adapter message IDs in existing databases."""

        with self.db.session() as session:
            repo = GameRepository(session)
            for game in repo.list_all():
                adapter = self.registry.try_get(game.adapter_key)
                if adapter and adapter.name and game.name != adapter.name:
                    repo.update(game, name=adapter.name)

    def set_active(self, game_id: int) -> None:
        with self.db.session() as session:
            if GameRepository(session).get(game_id) is None:
                raise KeyError(tr('services.game_service.no_game_has_been_added'))
        self.config.update(active_game_id=game_id)

    def active_game(self) -> GameSummary | None:
        with self.db.session() as session:
            game_id = self.config.config.active_game_id
            game = GameRepository(session).get(game_id) if game_id else None
            if game is None:
                games = GameRepository(session).list_all()
                game = games[0] if games else None
                if game is not None:
                    self.config.update(active_game_id=game.id)
            return self._summary(session, game) if game else None

    def game_summary(self, game_id: int) -> GameSummary:
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError(tr('services.game_service.no_game_has_been_added'))
            return self._summary(session, game)

    def list_games(self) -> list[GameSummary]:
        with self.db.session() as session:
            return [self._summary(session, game) for game in GameRepository(session).list_all()]

    def refresh(self, game_id: int) -> GameSummary:
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError(tr('services.game_service.no_game_has_been_added'))
            adapter = self.registry.get(game.adapter_key)
            validation = adapter.validate_installation(Path(game.install_path))
            if not validation.valid:
                raise ValueError(validation.message)
            GameRepository(session).update(
                game,
                mods_path=str(validation.mods_path or adapter.resolve_mods_dir(validation.install_path)),
                game_version=validation.game_version,
                framework_version=validation.framework_version,
                nexus_domain=adapter.nexus_domain,
            )
            return self._summary(session, game)

    def environment(self, game_id: int) -> list[EnvironmentCheck]:
        game, adapter = self.adapter_for_game_id(game_id)
        checks = adapter.check_environment(Path(game.install_path))
        if not game.install_path:
            checks.append(
                EnvironmentCheck(
                    key="path",
                    label=tr('services.game_service.game_path'),
                    ok=False,
                    detail=tr('services.game_service.game_path_is_not_set'),
                    severity=LogSeverity.CRITICAL,
                )
            )
        return checks

    def _summary(self, session, game: Game | None) -> GameSummary:  # type: ignore[no-untyped-def]
        if game is None:
            raise KeyError(tr('services.game_service.no_game_has_been_added'))
        present = Mod.game_id == game.id, Mod.is_present.is_(True)
        mod_count = session.scalar(select(func.count(Mod.id)).where(*present)) or 0
        enabled_count = (
            session.scalar(
                select(func.count(Mod.id)).where(*present, Mod.enabled.is_(True))
            )
            or 0
        )
        update_count = (
            session.scalar(
                select(func.count(Mod.id)).where(
                    *present, Mod.update_status == UpdateStatus.AVAILABLE.value
                )
            )
            or 0
        )
        issue_count = DiagnosticRepository(session).count_issues(game.id)
        adapter = self.registry.try_get(game.adapter_key)
        try:
            framework_installed = (
                adapter.is_framework_installed(Path(game.install_path)) if adapter else False
            )
        except Exception:
            framework_installed = bool(game.framework_version)
        return GameSummary(
            id=game.id,
            adapter_key=game.adapter_key,
            name=adapter.display_name if adapter else game.name,
            install_path=game.install_path,
            mods_path=game.mods_path,
            nexus_domain=game.nexus_domain,
            game_version=game.game_version,
            framework_version=game.framework_version,
            framework_installed=framework_installed,
            mod_count=int(mod_count),
            enabled_count=int(enabled_count),
            update_count=int(update_count),
            issue_count=issue_count,
            translation_pending=0,
            last_scan_at=game.last_scan_at,
            created_at=game.created_at,
        )
