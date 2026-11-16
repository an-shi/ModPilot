from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from ..core.paths import atomic_write_text
from ..data.db import Database
from ..data.models import Profile
from ..data.repositories import GameRepository, ModRepository, ProfileRepository
from ..domain.models import (
    ProfileApplyResult,
    ProfileEntryData,
    ProfileSnapshot,
    ProfileView,
)
from .activity import ActivityService
from .mod_service import ModService
from ..i18n import tr

log = logging.getLogger(__name__)


class ProfileService:
    def __init__(
        self,
        db: Database,
        mods: ModService,
        activity: ActivityService,
    ) -> None:
        self.db = db
        self.mods = mods
        self.activity = activity

    def list(self, game_id: int) -> list[ProfileView]:
        with self.db.session() as session:
            repo = ProfileRepository(session)
            return [self._view(session, profile) for profile in repo.list_for_game(game_id)]

    def get(self, profile_id: int) -> ProfileView:
        with self.db.session() as session:
            profile = ProfileRepository(session).get(profile_id)
            if profile is None:
                raise KeyError(tr('services.profile_service.profile_does_not_exist'))
            return self._view(session, profile)

    def _view(self, session, profile: Profile) -> ProfileView:  # type: ignore[no-untyped-def]
        total, enabled = ProfileRepository(session).count_entries(profile.id)
        return ProfileView(
            id=profile.id,
            game_id=profile.game_id,
            name=profile.name,
            description=profile.description,
            is_active=profile.is_active,
            mod_count=total,
            enabled_count=enabled,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    def create_from_current(
        self,
        game_id: int,
        name: str,
        description: str = "",
        *,
        overwrite: bool = False,
    ) -> ProfileView:
        name = name.strip()
        if not name:
            raise ValueError(tr('services.profile_service.profile_name_cannot_be_empty'))
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError(tr('services.profile_service.no_game_has_been_added'))
            mods = ModRepository(session).list_for_game(game_id, include_missing=False)
            repo = ProfileRepository(session)
            profile = repo.get_by_name(game_id, name)
            entries = [
                {
                    "unique_id": mod.unique_id,
                    "name": mod.name,
                    "version": mod.version,
                    "folder_name": mod.folder_name,
                    "relative_path": mod.relative_path,
                    "enabled": mod.enabled,
                }
                for mod in mods
            ]
            if profile and not overwrite:
                raise ValueError(tr('services.profile_service.profile_0_already_exists', name))
            if profile is None:
                profile = repo.create(game_id, name, description)
            else:
                profile.description = description
            repo.replace_entries(profile, entries)
            profile_id = profile.id
            repo.set_active(game_id, profile_id)
            view = self._view(session, profile)
        self.activity.record(
            tr('services.profile_service.created_profile_0_1_mods', name, len(entries)),
            kind="profile_create",
            game_id=game_id,
        )
        return view

    def get_snapshot(self, profile_id: int) -> ProfileSnapshot:
        with self.db.session() as session:
            repo = ProfileRepository(session)
            profile = repo.get(profile_id)
            if profile is None:
                raise KeyError(tr('services.profile_service.profile_does_not_exist'))
            game = GameRepository(session).get(profile.game_id)
            entries = repo.entries(profile_id)
            return ProfileSnapshot(
                game_key=game.adapter_key if game else "",
                game_name=tr(game.name) if game else "",
                profile_name=profile.name,
                description=profile.description,
                exported_at=datetime.now(UTC),
                entries=[
                    ProfileEntryData(
                        unique_id=entry.unique_id,
                        name=entry.name,
                        version=entry.version,
                        folder_name=entry.folder_name,
                        relative_path=entry.relative_path,
                        enabled=entry.enabled,
                    )
                    for entry in entries
                ],
            )

    def apply(self, profile_id: int, progress=None) -> ProfileApplyResult:  # type: ignore[no-untyped-def]
        with self.db.session() as session:
            repo = ProfileRepository(session)
            profile = repo.get(profile_id)
            if profile is None:
                raise KeyError(tr('services.profile_service.profile_does_not_exist'))
            game = GameRepository(session).get(profile.game_id)
            if game is None:
                raise KeyError(tr('services.profile_service.no_game_has_been_added'))
            entries = repo.entries(profile_id)
            current = {
                mod.unique_id: mod
                for mod in ModRepository(session).list_for_game(profile.game_id, include_missing=False)
            }
            game_id = profile.game_id
            profile_name = profile.name

        desired: dict[str, bool] = {entry.unique_id: entry.enabled for entry in entries}
        result = ProfileApplyResult()
        missing: list[str] = []
        version_warnings: list[str] = []
        for entry in entries:
            current_mod = current.get(entry.unique_id)
            if current_mod is None:
                missing.append(f"{entry.name} {entry.version}")
                continue
            if current_mod.version != entry.version:
                version_warnings.append(
                    tr('services.profile_service.0_profile_records_1_currently_installed_2', entry.name, entry.version, current_mod.version)
                )

        # Disable first, then enable. This avoids briefly having both old and new
        # profiles active when applying many toggles.
        def toggle_key(item: tuple[str, bool]) -> tuple[int, int]:
            unique_id, enabled = item
            depth = len(Path(current[unique_id].relative_path).parts)
            # Enable parents before children; disable children before parents.
            return (0 if enabled else 1, depth if enabled else -depth)

        toggle_order = sorted(
            (
                (unique_id, enabled)
                for unique_id, enabled in desired.items()
                if unique_id in current and current[unique_id].enabled != enabled
            ),
            key=toggle_key,
        )
        try:
            for index, (unique_id, enabled) in enumerate(toggle_order):
                mod = current[unique_id]
                if progress:
                    progress(int(index / max(1, len(toggle_order)) * 95), tr('services.profile_service.switching_0', mod.name))
                try:
                    self.mods.set_enabled(mod.id, enabled)
                    (result.enabled if enabled else result.disabled).append(mod.name)
                except Exception as exc:
                    result.warnings.append(tr('services.profile_service.failed_to_switch_0_1', mod.name, exc))
                # set_enabled renames folders and updates rows; refresh the current
                # map lazily only for nested paths, so IDs remain valid.
        finally:
            if progress:
                progress(100, tr('services.profile_service.profile_applied'))

        result.missing = missing
        result.warnings.extend(version_warnings)
        if missing:
            result.warnings.append(
                tr('services.profile_service.some_profile_entries_are_not_installed_and_were_skip_db355a')
            )
        with self.db.session() as session:
            repo = ProfileRepository(session)
            if not result.warnings or (result.enabled or result.disabled):
                repo.set_active(game_id, profile_id)
        self.activity.record(
            tr('services.profile_service.applied_profile_0', profile_name),
            kind="profile_apply",
            game_id=game_id,
            details={
                "enabled": result.enabled,
                "disabled": result.disabled,
                "missing": result.missing,
                "warnings": result.warnings,
            },
        )
        return result

    def export(self, profile_id: int, destination: Path) -> Path:
        snapshot = self.get_snapshot(profile_id)
        destination = Path(destination)
        if destination.suffix.lower() != ".json":
            destination = destination.with_suffix(".json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            destination,
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )
        return destination

    def import_file(self, game_id: int, path: Path, *, name: str | None = None) -> ProfileView:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(tr('services.profile_service.failed_to_read_profile_file_0', exc)) from exc
        try:
            snapshot = ProfileSnapshot.model_validate(raw)
        except Exception as exc:
            raise ValueError(tr('services.profile_service.invalid_profile_format_0', exc)) from exc
        unique_ids = [entry.unique_id.strip() for entry in snapshot.entries]
        if any(not unique_id for unique_id in unique_ids):
            raise ValueError(tr('services.profile_service.profile_contains_an_empty_uniqueid'))
        if len(unique_ids) != len(set(unique_ids)):
            raise ValueError(tr('services.profile_service.profile_contains_duplicate_uniqueids'))
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError(tr('services.profile_service.no_game_has_been_added'))
            if snapshot.game_key and snapshot.game_key != game.adapter_key:
                raise ValueError(
                    tr(
                        'services.profile_service.the_profile_belongs_to_0_and_cannot_be_imported_into_c033fe',
                        snapshot.game_name or snapshot.game_key,
                        tr(game.name),
                    )
                )
            base_name = (
                name or snapshot.profile_name or tr('services.profile_service.imported_profile_0', snapshot.game_name)
            ).strip() or tr('services.profile_service.import_profile')
            repo = ProfileRepository(session)
            candidate = base_name
            counter = 2
            while repo.get_by_name(game_id, candidate):
                candidate = f"{base_name} ({counter})"
                counter += 1
            profile = repo.create(
                game_id,
                candidate,
                description=tr('services.profile_service.import_from_json_profile'),
            )
            repo.replace_entries(
                profile,
                [
                    {
                        "unique_id": entry.unique_id,
                        "name": entry.name,
                        "version": entry.version,
                        "folder_name": entry.folder_name,
                        "relative_path": entry.relative_path,
                        "enabled": entry.enabled,
                    }
                    for entry in snapshot.entries
                ],
            )
            view = self._view(session, profile)
        self.activity.record(
            tr('services.profile_service.imported_profile_0_1_mods', view.name, view.mod_count),
            kind="profile_import",
            game_id=game_id,
        )
        return view

    def rename(self, profile_id: int, name: str, description: str | None = None) -> None:
        name = name.strip()
        if not name:
            raise ValueError(tr('services.profile_service.profile_name_cannot_be_empty'))
        with self.db.session() as session:
            repo = ProfileRepository(session)
            profile = repo.get(profile_id)
            if profile is None:
                raise KeyError(tr('services.profile_service.profile_does_not_exist'))
            existing = repo.get_by_name(profile.game_id, name)
            if existing and existing.id != profile_id:
                raise ValueError(tr('services.profile_service.a_profile_with_the_same_name_already_exists'))
            profile.name = name
            if description is not None:
                profile.description = description
            profile.updated_at = datetime.now(UTC).replace(tzinfo=None)

    def delete(self, profile_id: int) -> None:
        with self.db.session() as session:
            repo = ProfileRepository(session)
            profile = repo.get(profile_id)
            if profile is None:
                return
            game_id = profile.game_id
            was_active = profile.is_active
            repo.delete(profile)
            if was_active:
                repo.set_active(game_id, None)
        self.activity.record(
            tr('services.profile_service.profile_deleted'),
            kind="profile_delete",
            level="warning",
            game_id=game_id,
        )

    def duplicate(self, profile_id: int, name: str) -> ProfileView:
        snapshot = self.get_snapshot(profile_id)
        if not name.strip():
            raise ValueError(tr('services.profile_service.profile_name_cannot_be_empty'))
        with self.db.session() as session:
            profile = ProfileRepository(session).get(profile_id)
            if profile is None:
                raise KeyError(tr('services.profile_service.profile_does_not_exist'))
            game_id = profile.game_id
            repo = ProfileRepository(session)
            if repo.get_by_name(game_id, name.strip()):
                raise ValueError(tr('services.profile_service.a_profile_with_the_same_name_already_exists'))
            new_profile = repo.create(game_id, name.strip(), profile.description)
            repo.replace_entries(
                new_profile,
                [
                    {
                        "unique_id": entry.unique_id,
                        "name": entry.name,
                        "version": entry.version,
                        "folder_name": entry.folder_name,
                        "relative_path": entry.relative_path,
                        "enabled": entry.enabled,
                    }
                    for entry in snapshot.entries
                ],
            )
            return self._view(session, new_profile)
