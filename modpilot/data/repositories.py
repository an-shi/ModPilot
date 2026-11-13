from __future__ import annotations

from typing import Any

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

from .models import (
    Activity,
    AppSetting,
    Backup,
    DiagnosticIssue,
    DiagnosticRun,
    Download,
    Game,
    InstallRecord,
    Mod,
    ModDependency,
    Profile,
    ProfileEntry,
    RepairPlan,
    TranslationCache,
    TranslationPack,
    TranslationRecord,
    UpdateRecord,
    utcnow,
)


class GameRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[Game]:
        return list(self.session.scalars(select(Game).order_by(Game.created_at)))

    def get(self, game_id: int) -> Game | None:
        return self.session.get(Game, game_id)

    def get_by_adapter(self, adapter_key: str) -> Game | None:
        return self.session.scalar(select(Game).where(Game.adapter_key == adapter_key))

    def add(self, **fields: Any) -> Game:
        game = Game(**fields)
        self.session.add(game)
        self.session.flush()
        return game

    def delete(self, game: Game) -> None:
        self.session.delete(game)

    def update(self, game: Game, **fields: Any) -> Game:
        for key, value in fields.items():
            setattr(game, key, value)
        self.session.flush()
        return game


class ModRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_game(self, game_id: int, *, include_missing: bool = True) -> list[Mod]:
        statement = select(Mod).where(Mod.game_id == game_id)
        if not include_missing:
            statement = statement.where(Mod.is_present.is_(True))
        return list(self.session.scalars(statement.order_by(Mod.name.collate("NOCASE"))))

    def get(self, mod_id: int) -> Mod | None:
        return self.session.get(Mod, mod_id)

    def list_by_ids(self, mod_ids: list[int]) -> list[Mod]:
        if not mod_ids:
            return []
        return list(self.session.scalars(select(Mod).where(Mod.id.in_(mod_ids))))

    def get_by_path(self, game_id: int, relative_path: str) -> Mod | None:
        return self.session.scalar(
            select(Mod).where(Mod.game_id == game_id, Mod.relative_path == relative_path)
        )

    def find_by_unique(self, game_id: int, unique_id: str) -> list[Mod]:
        return list(
            self.session.scalars(
                select(Mod).where(Mod.game_id == game_id, Mod.unique_id == unique_id)
            )
        )

    def add(self, **fields: Any) -> Mod:
        mod = Mod(**fields)
        self.session.add(mod)
        self.session.flush()
        return mod

    def update(self, mod: Mod, **fields: Any) -> Mod:
        for key, value in fields.items():
            setattr(mod, key, value)
        mod.updated_at = utcnow()
        self.session.flush()
        return mod

    def delete(self, mod: Mod) -> None:
        self.session.delete(mod)

    def replace_dependencies(self, mod: Mod, dependencies: list[dict[str, Any]]) -> None:
        self.session.execute(delete(ModDependency).where(ModDependency.mod_id == mod.id))
        for dep in dependencies:
            self.session.add(
                ModDependency(
                    mod_id=mod.id,
                    unique_id=dep["unique_id"],
                    minimum_version=dep.get("minimum_version"),
                    required=bool(dep.get("required", True)),
                    kind=dep.get("kind", "required"),
                )
            )
        self.session.flush()

    def dependencies_for(self, mod_ids: list[int]) -> dict[int, list[ModDependency]]:
        if not mod_ids:
            return {}
        rows = self.session.scalars(
            select(ModDependency).where(ModDependency.mod_id.in_(mod_ids))
        )
        result: dict[int, list[ModDependency]] = {mod_id: [] for mod_id in mod_ids}
        for row in rows:
            result.setdefault(row.mod_id, []).append(row)
        return result

    def present_ids(self, game_id: int) -> set[int]:
        return set(
            self.session.scalars(
                select(Mod.id).where(Mod.game_id == game_id, Mod.is_present.is_(True))
            )
        )


class ProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_game(self, game_id: int) -> list[Profile]:
        return list(
            self.session.scalars(
                select(Profile).where(Profile.game_id == game_id).order_by(Profile.created_at)
            )
        )

    def get(self, profile_id: int) -> Profile | None:
        return self.session.get(Profile, profile_id)

    def get_by_name(self, game_id: int, name: str) -> Profile | None:
        return self.session.scalar(
            select(Profile).where(Profile.game_id == game_id, Profile.name == name)
        )

    def entries(self, profile_id: int) -> list[ProfileEntry]:
        return list(
            self.session.scalars(
                select(ProfileEntry)
                .where(ProfileEntry.profile_id == profile_id)
                .order_by(ProfileEntry.name.collate("NOCASE"))
            )
        )

    def create(self, game_id: int, name: str, description: str = "") -> Profile:
        profile = Profile(game_id=game_id, name=name, description=description)
        self.session.add(profile)
        self.session.flush()
        return profile

    def add_entries(self, profile: Profile, entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            self.session.add(ProfileEntry(profile_id=profile.id, **entry))
        profile.updated_at = utcnow()
        self.session.flush()

    def replace_entries(self, profile: Profile, entries: list[dict[str, Any]]) -> None:
        self.session.execute(delete(ProfileEntry).where(ProfileEntry.profile_id == profile.id))
        self.add_entries(profile, entries)

    def set_active(self, game_id: int, profile_id: int | None) -> None:
        self.session.execute(
            update(Profile).where(Profile.game_id == game_id).values(is_active=False)
        )
        if profile_id is not None:
            profile = self.get(profile_id)
            if profile and profile.game_id == game_id:
                profile.is_active = True
                profile.updated_at = utcnow()
        self.session.flush()

    def delete(self, profile: Profile) -> None:
        self.session.delete(profile)

    def count_entries(self, profile_id: int) -> tuple[int, int]:
        total = self.session.scalar(
            select(func.count(ProfileEntry.id)).where(ProfileEntry.profile_id == profile_id)
        ) or 0
        enabled = self.session.scalar(
            select(func.count(ProfileEntry.id)).where(
                ProfileEntry.profile_id == profile_id, ProfileEntry.enabled.is_(True)
            )
        ) or 0
        return int(total), int(enabled)


class BackupRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, **fields: Any) -> Backup:
        backup = Backup(**fields)
        self.session.add(backup)
        self.session.flush()
        return backup

    def get(self, backup_id: int) -> Backup | None:
        return self.session.get(Backup, backup_id)

    def list_for_game(self, game_id: int, *, mod_id: int | None = None) -> list[Backup]:
        statement = select(Backup).where(Backup.game_id == game_id)
        if mod_id is not None:
            statement = statement.where(Backup.mod_id == mod_id)
        return list(self.session.scalars(statement.order_by(Backup.created_at.desc())))

    def update(self, backup: Backup, **fields: Any) -> Backup:
        for key, value in fields.items():
            setattr(backup, key, value)
        self.session.flush()
        return backup


class DownloadRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, **fields: Any) -> Download:
        download = Download(**fields)
        self.session.add(download)
        self.session.flush()
        return download

    def get(self, download_id: int) -> Download | None:
        return self.session.get(Download, download_id)

    def list_for_game(self, game_id: int, limit: int = 100) -> list[Download]:
        return list(
            self.session.scalars(
                select(Download)
                .where(Download.game_id == game_id)
                .order_by(Download.created_at.desc())
                .limit(limit)
            )
        )

    def list_active(self) -> list[Download]:
        return list(
            self.session.scalars(
                select(Download).where(
                    Download.status.in_(["queued", "downloading", "paused"])
                )
            )
        )

    def update(self, download: Download, **fields: Any) -> Download:
        for key, value in fields.items():
            setattr(download, key, value)
        download.updated_at = utcnow()
        self.session.flush()
        return download

    def mark_interrupted(self) -> int:
        result = self.session.execute(
            update(Download)
            .where(Download.status.in_(["queued", "downloading"]))
            .values(
                status="paused",
                error="应用上次退出时下载被中断，可手动继续。",
                updated_at=utcnow(),
            )
        )
        return int(result.rowcount or 0)


class UpdateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, **fields: Any) -> UpdateRecord:
        record = UpdateRecord(**fields)
        self.session.add(record)
        self.session.flush()
        return record

    def list_for_mod(self, mod_id: int, limit: int = 50) -> list[UpdateRecord]:
        return list(
            self.session.scalars(
                select(UpdateRecord)
                .where(UpdateRecord.mod_id == mod_id)
                .order_by(UpdateRecord.created_at.desc())
                .limit(limit)
            )
        )

    def list_for_game(self, game_id: int, limit: int = 100) -> list[UpdateRecord]:
        return list(
            self.session.scalars(
                select(UpdateRecord)
                .where(UpdateRecord.game_id == game_id)
                .order_by(UpdateRecord.created_at.desc())
                .limit(limit)
            )
        )


class TranslationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def counts_by_mod(self, mod_ids: list[int], language: str = "zh") -> dict[int, tuple[int, int, int]]:
        if not mod_ids:
            return {}
        done_expr = case(
            (TranslationRecord.status.in_(["translated", "confirmed"]), 1),
            else_=0,
        )
        rows = self.session.execute(
            select(
                TranslationRecord.mod_id,
                func.count(TranslationRecord.id),
                func.sum(done_expr),
                func.sum(case((TranslationRecord.confirmed.is_(True), 1), else_=0)),
            )
            .where(
                TranslationRecord.mod_id.in_(mod_ids),
                TranslationRecord.language == language,
            )
            .group_by(TranslationRecord.mod_id)
        ).all()
        return {
            int(mod_id): (int(total), int(done or 0), int(confirmed or 0))
            for mod_id, total, done, confirmed in rows
        }

    def units_for_mod(self, mod_id: int, language: str = "zh") -> list[TranslationRecord]:
        return list(
            self.session.scalars(
                select(TranslationRecord)
                .where(
                    TranslationRecord.mod_id == mod_id,
                    TranslationRecord.language == language,
                )
                .order_by(TranslationRecord.id)
            )
        )

    def get_unit(self, unit_id: int) -> TranslationRecord | None:
        return self.session.get(TranslationRecord, unit_id)

    def find_unit(self, mod_id: int, language: str, source_file: str, key: str) -> TranslationRecord | None:
        # source_file is intentionally not part of the lookup: a mod can switch
        # from default.json to en.json and the existing record should be updated
        # rather than duplicated.
        return self.session.scalar(
            select(TranslationRecord).where(
                TranslationRecord.mod_id == mod_id,
                TranslationRecord.language == language,
                TranslationRecord.key == key,
            )
        )

    def upsert_unit(self, **fields: Any) -> TranslationRecord:
        unit = self.find_unit(
            fields["mod_id"], fields["language"], fields["source_file"], fields["key"]
        )
        if unit is None:
            unit = TranslationRecord(**fields)
            self.session.add(unit)
        else:
            for key, value in fields.items():
                setattr(unit, key, value)
            unit.updated_at = utcnow()
        self.session.flush()
        return unit

    def update_unit(self, unit: TranslationRecord, **fields: Any) -> TranslationRecord:
        for key, value in fields.items():
            setattr(unit, key, value)
        unit.updated_at = utcnow()
        self.session.flush()
        return unit

    def delete_units_except(self, mod_id: int, language: str, keys: set[str]) -> None:
        statement = delete(TranslationRecord).where(
            TranslationRecord.mod_id == mod_id,
            TranslationRecord.language == language,
        )
        if keys:
            statement = statement.where(TranslationRecord.key.notin_(keys))
        self.session.execute(statement)

    def cache_get(self, game_key: str, language: str, source_hash: str) -> TranslationCache | None:
        return self.session.scalar(
            select(TranslationCache).where(
                TranslationCache.game_key == game_key,
                TranslationCache.language == language,
                TranslationCache.source_hash == source_hash,
            )
        )

    def cache_put(
        self,
        *,
        game_key: str,
        language: str,
        source_hash: str,
        source_text: str,
        translated_text: str,
        origin: str,
        model: str,
        confirmed: bool = False,
    ) -> TranslationCache:
        entry = self.cache_get(game_key, language, source_hash)
        if entry is None:
            entry = TranslationCache(
                game_key=game_key,
                language=language,
                source_hash=source_hash,
                source_text=source_text,
                translated_text=translated_text,
                origin=origin,
                model=model,
                confirmed=confirmed,
                uses=1,
            )
            self.session.add(entry)
        else:
            entry.translated_text = translated_text
            entry.origin = origin
            entry.model = model
            entry.confirmed = confirmed
            entry.uses += 1
            entry.updated_at = utcnow()
        self.session.flush()
        return entry

    def cache_stats(self) -> tuple[int, int]:
        total = self.session.scalar(select(func.count(TranslationCache.id))) or 0
        confirmed = self.session.scalar(
            select(func.count(TranslationCache.id)).where(TranslationCache.confirmed.is_(True))
        ) or 0
        return int(total), int(confirmed)

    def clear_cache(self) -> int:
        result = self.session.execute(delete(TranslationCache))
        return int(result.rowcount or 0)

    def add_candidates(self, mod_id: int, candidates: list[dict[str, Any]]) -> None:
        self.session.execute(delete(TranslationPack).where(TranslationPack.mod_id == mod_id))
        for candidate in candidates:
            self.session.add(TranslationPack(mod_id=mod_id, **candidate))
        self.session.flush()

    def list_candidates(self, mod_id: int) -> list[TranslationPack]:
        return list(
            self.session.scalars(
                select(TranslationPack)
                .where(TranslationPack.mod_id == mod_id)
                .order_by(TranslationPack.score.desc())
            )
        )


class DiagnosticRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_run(
        self,
        *,
        game_id: int,
        log_path: str | None,
        trigger: str,
        summary: str,
        report_json: dict[str, Any],
        ai_used: bool,
        model: str,
        status: str = "completed",
        error: str | None = None,
    ) -> DiagnosticRun:
        run = DiagnosticRun(
            game_id=game_id,
            log_path=log_path,
            trigger=trigger,
            summary=summary,
            report_json=report_json,
            ai_used=ai_used,
            model=model,
            status=status,
            error=error,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def add_issue(self, run_id: int, **fields: Any) -> DiagnosticIssue:
        issue = DiagnosticIssue(run_id=run_id, **fields)
        self.session.add(issue)
        self.session.flush()
        return issue

    def get_run(self, run_id: int) -> DiagnosticRun | None:
        return self.session.get(DiagnosticRun, run_id)

    def latest_run(self, game_id: int) -> DiagnosticRun | None:
        return self.session.scalar(
            select(DiagnosticRun)
            .where(DiagnosticRun.game_id == game_id)
            .order_by(DiagnosticRun.created_at.desc())
            .limit(1)
        )

    def list_runs(self, game_id: int, limit: int = 20) -> list[DiagnosticRun]:
        return list(
            self.session.scalars(
                select(DiagnosticRun)
                .where(DiagnosticRun.game_id == game_id)
                .order_by(DiagnosticRun.created_at.desc())
                .limit(limit)
            )
        )

    def issues(self, run_id: int) -> list[DiagnosticIssue]:
        return list(
            self.session.scalars(
                select(DiagnosticIssue)
                .where(DiagnosticIssue.run_id == run_id)
                .order_by(DiagnosticIssue.id)
            )
        )

    def count_issues(self, game_id: int) -> int:
        latest = self.latest_run(game_id)
        if not latest:
            return 0
        return int(
            self.session.scalar(
                select(func.count(DiagnosticIssue.id)).where(
                    DiagnosticIssue.run_id == latest.id,
                    DiagnosticIssue.severity.in_(["warning", "error", "critical"]),
                )
            )
            or 0
        )


class RepairRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, **fields: Any) -> RepairPlan:
        plan = RepairPlan(**fields)
        self.session.add(plan)
        self.session.flush()
        return plan

    def get(self, plan_id: int) -> RepairPlan | None:
        return self.session.get(RepairPlan, plan_id)

    def latest_for_run(self, run_id: int) -> RepairPlan | None:
        return self.session.scalar(
            select(RepairPlan)
            .where(RepairPlan.diagnostic_run_id == run_id)
            .order_by(RepairPlan.created_at.desc())
            .limit(1)
        )

    def update(self, plan: RepairPlan, **fields: Any) -> RepairPlan:
        for key, value in fields.items():
            setattr(plan, key, value)
        self.session.flush()
        return plan


class ActivityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        message: str,
        *,
        kind: str = "general",
        level: str = "info",
        game_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> Activity:
        activity = Activity(
            message=message,
            kind=kind,
            level=level,
            game_id=game_id,
            details_json=details or {},
        )
        self.session.add(activity)
        self.session.flush()
        return activity

    def list_recent(self, game_id: int | None = None, limit: int = 20) -> list[Activity]:
        statement = select(Activity)
        if game_id is not None:
            statement = statement.where(
                (Activity.game_id == game_id) | (Activity.game_id.is_(None))
            )
        return list(
            self.session.scalars(statement.order_by(Activity.created_at.desc()).limit(limit))
        )


class InstallRecordRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, **fields: Any) -> InstallRecord:
        record = InstallRecord(**fields)
        self.session.add(record)
        self.session.flush()
        return record


class SettingsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, key: str, default: Any = None) -> Any:
        setting = self.session.get(AppSetting, key)
        return setting.value_json if setting is not None else default

    def set(self, key: str, value: Any) -> None:
        setting = self.session.get(AppSetting, key)
        if setting is None:
            setting = AppSetting(key=key, value_json=value)
            self.session.add(setting)
        else:
            setting.value_json = value
            setting.updated_at = utcnow()
        self.session.flush()
