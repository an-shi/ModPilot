from __future__ import annotations

import logging

from ..adapters.base import AdapterRegistry
from ..data.db import Database
from ..data.repositories import GameRepository
from ..domain.models import DashboardSummary
from .activity import ActivityService
from .game_service import GameService
from .mod_service import ModService
from ..i18n import tr

log = logging.getLogger(__name__)


class DashboardService:
    def __init__(
        self,
        db: Database,
        registry: AdapterRegistry,
        games: GameService,
        mods: ModService,
        activity: ActivityService,
    ) -> None:
        self.db = db
        self.registry = registry
        self.games = games
        self.mods = mods
        self.activity = activity

    def summary(self) -> DashboardSummary:
        game = self.games.active_game()
        if game is None:
            return DashboardSummary(recent_activity=self.activity.recent(None, 10))
        views = self.mods.list_mods(game.id)
        with self.db.session() as session:
            row = GameRepository(session).get(game.id)
            adapter = self.registry.get(row.adapter_key) if row else None
        translation_pending = 0
        if adapter:

            for view in views:
                if not view.is_present or not view.translation_eligible:
                    continue
                if view.translation_applied:
                    continue
                translation_pending += 1
        environment = []
        try:
            environment = self.games.environment(game.id)
        except Exception as exc:
            log.warning(tr('services.dashboard_service.failed_to_read_game_environment_s'), exc)
        game = game.model_copy(update={"translation_pending": translation_pending})
        return DashboardSummary(
            game=game,
            mod_count=game.mod_count,
            enabled_count=game.enabled_count,
            update_count=game.update_count,
            issue_count=game.issue_count,
            translation_pending=translation_pending,
            environment=environment,
            recent_activity=self.activity.recent(game.id, 12),
        )
