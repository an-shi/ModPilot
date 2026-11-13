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
                raise KeyError("游戏未添加。")
            adapter = self.registry.try_get(game.adapter_key)
            if adapter is None:
                raise KeyError(f"找不到游戏适配器：{game.adapter_key}")
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
                    f"{adapter.name} 已添加（{existing.install_path}）。请先移除现有记录，再添加其他安装位置。"
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
            f"已添加游戏：{adapter.name}",
            kind="game_add",
            game_id=game_id,
            details={"path": str(validation.install_path)},
        )
        return summary

    def remove_game(self, game_id: int) -> None:
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError("游戏未添加。")
            name = game.name
            GameRepository(session).delete(game)
            if self.config.config.active_game_id == game_id:
                remaining = GameRepository(session).list_all()
                self.config.update(active_game_id=remaining[0].id if remaining else None)
        self.activity.record(
            f"已移除游戏管理记录（游戏文件未删除）：{name}",
            kind="game_remove",
            level="warning",
            details={"game_id": game_id},
        )

    def set_active(self, game_id: int) -> None:
        with self.db.session() as session:
            if GameRepository(session).get(game_id) is None:
                raise KeyError("游戏未添加。")
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
                raise KeyError("游戏未添加。")
            return self._summary(session, game)

    def list_games(self) -> list[GameSummary]:
        with self.db.session() as session:
            return [self._summary(session, game) for game in GameRepository(session).list_all()]

    def refresh(self, game_id: int) -> GameSummary:
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError("游戏未添加。")
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
                    label="游戏路径",
                    ok=False,
                    detail="未设置游戏路径。",
                    severity=LogSeverity.CRITICAL,
                )
            )
        return checks

    def _summary(self, session, game: Game | None) -> GameSummary:  # type: ignore[no-untyped-def]
        if game is None:
            raise KeyError("游戏未添加。")
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
            name=game.name,
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
