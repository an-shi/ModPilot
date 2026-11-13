from __future__ import annotations

from datetime import datetime
from typing import Any

from ..data.db import Database
from ..data.repositories import ActivityRepository
from ..domain.models import ActivityView


class ActivityService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        message: str,
        *,
        kind: str = "general",
        level: str = "info",
        game_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.db.session() as session:
            ActivityRepository(session).add(
                message,
                kind=kind,
                level=level,
                game_id=game_id,
                details=details,
            )

    def recent(self, game_id: int | None = None, limit: int = 30) -> list[ActivityView]:
        with self.db.session() as session:
            rows = ActivityRepository(session).list_recent(game_id, limit)
            return [
                ActivityView(
                    id=row.id,
                    kind=row.kind,
                    level=row.level,
                    message=row.message,
                    details=row.details_json or {},
                    created_at=row.created_at,
                    game_id=row.game_id,
                )
                for row in rows
            ]


def format_time(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M")
