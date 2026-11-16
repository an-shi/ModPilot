from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path

from ..adapters.base import AdapterRegistry, ProgressCallback
from ..config import AppPaths
from ..core.archive import ensure_supported_archive, extract_archive
from ..core.errors import AdapterError, ArchiveError, ModOperationError
from ..core.paths import assert_within, is_within
from ..core.versions import version_satisfies, versions_equal
from ..data.db import Database
from ..data.models import Mod, utcnow
from ..data.repositories import (
    GameRepository,
    InstallRecordRepository,
    ModRepository,
    TranslationRepository,
)
from ..domain.enums import BackupKind, UpdatePolicy, UpdateStatus
from ..domain.models import (
    BulkResult,
    DependencyStatus,
    InstallPlan,
    InstallResult,
    ModMetadata,
    ModView,
    UninstallResult,
)
from .activity import ActivityService
from .backup_service import BackupService
from ..i18n import tr

log = logging.getLogger(__name__)


class ModService:
    def __init__(
        self,
        db: Database,
        registry: AdapterRegistry,
        paths: AppPaths,
        backups: BackupService,
        activity: ActivityService,
    ) -> None:
        self.db = db
        self.registry = registry
        self.paths = paths
        self.backups = backups
        self.activity = activity

    # ------------------------------------------------------------------
    # Preview / installation
    # ------------------------------------------------------------------

    def _game_and_adapter(self, game_id: int):
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError(tr('services.mod_service.no_game_has_been_added'))
            adapter = self.registry.get(game.adapter_key)
            return game, adapter

    def preview_install(self, game_id: int, archive_path: Path) -> InstallPlan:
        archive = Path(archive_path).expanduser().resolve()
        ensure_supported_archive(archive)
        game, adapter = self._game_and_adapter(game_id)
        mods_root = Path(game.mods_path)
        staging = Path(tempfile.mkdtemp(prefix="install-", dir=str(self.paths.staging_dir)))
        try:
            extract_archive(archive, staging)
            try:
                plan = adapter.plan_install(archive, staging, mods_root)
            except Exception as exc:
                if isinstance(exc, AdapterError):
                    raise
                raise AdapterError(str(exc)) from exc
            self._annotate_plan(game_id, plan)
            if not plan.entries:
                raise ArchiveError(tr('services.mod_service.no_installable_mods_were_recognized_in_the_archive'))
            return plan
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _annotate_plan(self, game_id: int, plan: InstallPlan) -> None:
        with self.db.session() as session:
            repo = ModRepository(session)
            present = repo.list_for_game(game_id, include_missing=False)
        by_unique: dict[str, list[Mod]] = defaultdict(list)
        for mod in present:
            by_unique[mod.unique_id].append(mod)

        used_destinations: set[str] = set()
        for entry in plan.entries:
            unique_id = entry.metadata.unique_id
            duplicates = by_unique.get(unique_id, [])
            if duplicates:
                existing = duplicates[0]
                rel_parts = Path(existing.relative_path).parts
                if len(rel_parts) == 1:
                    entry.destination_name = rel_parts[0]
                    entry.metadata = entry.metadata.model_copy(
                        update={
                            "manifest_path": Path(plan.mods_root)
                            / entry.destination_name
                            / "manifest.json"
                        }
                    )
                    entry.warnings.append(
                        tr('services.mod_service.detected_the_same_mod_already_installed_0_1_the_exis_f0eed1', existing.name, existing.version)
                    )
                else:
                    entry.warnings.append(
                        tr('services.mod_service.detected_a_nested_installation_with_the_same_uniquei_845c69', existing.relative_path)
                    )
                entry.duplicate_of = f"{existing.name} {existing.version} ({existing.relative_path})"
                entry.existing_mod_id = existing.id
                entry.existing_version = existing.version
            destination = Path(plan.mods_root) / entry.destination_name
            entry.existing = destination.exists()
            if entry.existing:
                entry.warnings.append(tr('services.mod_service.the_target_folder_already_exists_it_will_be_backed_u_2cc142'))

            if entry.destination_name.casefold() in used_destinations:
                entry.warnings.append(tr('services.mod_service.multiple_mods_in_the_same_archive_used_the_same_targ_ef0b64'))
                base = entry.destination_name
                counter = 2
                while f"{base} ({counter})".casefold() in used_destinations:
                    counter += 1
                entry.destination_name = f"{base} ({counter})"
            used_destinations.add(entry.destination_name.casefold())

        # Warn about required dependencies that are neither installed nor part
        # of this same archive. This is a warning, not a hard block: dependency
        # handling is explained in the UI and install remains reversible.
        available_ids = {mod.unique_id.casefold() for mod in present}
        available_ids.update(entry.metadata.unique_id.casefold() for entry in plan.entries)
        for entry in plan.entries:
            for dependency in entry.metadata.dependencies:
                if dependency.required and dependency.unique_id.casefold() not in available_ids:
                    entry.warnings.append(
                        tr('services.mod_service.missing_dependency_0', dependency.unique_id)
                        + (tr('services.mod_service.minimum_version_0', dependency.minimum_version) if dependency.minimum_version else "")
                    )

    def install_plan(
        self,
        game_id: int,
        plan: InstallPlan,
        *,
        allow_replace: bool = False,
        allow_duplicate: bool = False,
        source: str = "manual",
        progress: ProgressCallback | None = None,
    ) -> InstallResult:
        staging = Path(plan.staging_dir).resolve()
        if not is_within(staging, self.paths.staging_dir):
            raise ModOperationError(tr('services.mod_service.the_install_staging_directory_is_not_inside_the_modp_886309'))
        game, _adapter = self._game_and_adapter(game_id)
        mods_root = Path(game.mods_path).resolve()
        if Path(plan.mods_root).resolve() != mods_root:
            raise ModOperationError(tr('services.mod_service.the_mods_directory_in_the_install_plan_does_not_matc_4183a7'))
        mods_root.mkdir(parents=True, exist_ok=True)

        for entry in plan.entries:
            if entry.existing and not allow_replace:
                raise ModOperationError(
                    tr('services.mod_service.the_target_folder_already_exists_0_confirm_the_repla_1b1361', entry.destination_name)
                )
            if entry.duplicate_of and not allow_duplicate:
                raise ModOperationError(
                    tr('services.mod_service.a_mod_with_the_same_uniqueid_is_already_installed_0_cced99', entry.duplicate_of)
                )

        journal: list[tuple[Path, Path | None, int | None]] = []
        result = InstallResult()
        total = max(1, len(plan.entries))
        try:
            for index, entry in enumerate(plan.entries):
                if progress:
                    progress(int(index / total * 80), tr('services.mod_service.installing_0', entry.metadata.name))
                destination = (mods_root / entry.destination_name).resolve()
                assert_within(destination, mods_root, label=tr('services.mod_service.install_target'))
                source_dir = Path(entry.source_dir).resolve()
                assert_within(source_dir, staging, label=tr('services.mod_service.install_source'))

                backup_id: int | None = None
                if destination.exists():
                    backup = self.backups.create(
                        game_id=game_id,
                        root=mods_root,
                        items=[destination],
                        kind=BackupKind.UPDATE if entry.duplicate_of else BackupKind.INSTALL,
                        label=f"{tr('services.mod_service.update') if entry.duplicate_of else tr('services.mod_service.install')} {entry.metadata.name}",
                        note=tr('services.mod_service.automatic_backup_before_installing_0_1', entry.metadata.unique_id, entry.metadata.version),
                        mod_id=entry.existing_mod_id,
                        extra={"destination": entry.destination_name},
                    )
                    backup_id = backup.id
                    result.backup_ids.append(backup_id)

                temp_dir = mods_root / f".modpilot-new-{uuid.uuid4().hex}"
                old_dir: Path | None = None
                try:
                    shutil.copytree(source_dir, temp_dir, symlinks=False)
                    if destination.exists():
                        old_dir = mods_root / f".modpilot-old-{uuid.uuid4().hex}"
                        os.replace(destination, old_dir)
                    os.replace(temp_dir, destination)
                except Exception:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    if old_dir and old_dir.exists() and not destination.exists():
                        os.replace(old_dir, destination)
                    raise
                journal.append((destination, old_dir, backup_id))

            if progress:
                progress(85, tr('services.mod_service.scanning_installed_mods'))
            self.scan_game(game_id, record_activity=False)
            with self.db.session() as session:
                repo = ModRepository(session)
                records = InstallRecordRepository(session)
                for index, entry in enumerate(plan.entries):
                    destination = (mods_root / entry.destination_name).resolve()
                    rel = destination.relative_to(mods_root).as_posix()
                    matches = [
                        mod
                        for mod in repo.list_for_game(game_id, include_missing=False)
                        if mod.relative_path == rel or mod.relative_path.startswith(rel + "/")
                    ]
                    for mod in matches:
                        result.installed_mod_ids.append(mod.id)
                        result.installed_names.append(mod.name)
                        if source != "manual" and mod.source == "manual":
                            repo.update(mod, source=source)
                    records.add(
                        game_id=game_id,
                        mod_id=matches[0].id if matches else None,
                        action="update" if entry.duplicate_of else "install",
                        status="success",
                        backup_id=journal[index][2] if index < len(journal) else None,
                        details_json={
                            "name": entry.metadata.name,
                            "unique_id": entry.metadata.unique_id,
                            "version": entry.metadata.version,
                            "destination": entry.destination_name,
                            "warnings": entry.warnings,
                        },
                    )
            for destination, old_dir, _backup_id in journal:
                if old_dir and old_dir.exists():
                    shutil.rmtree(old_dir, ignore_errors=True)
            result.warnings = list(plan.warnings)
            for entry in plan.entries:
                result.warnings.extend(entry.warnings)
            if progress:
                progress(100, tr('services.mod_service.installation_complete'))
        except Exception as exc:
            log.exception(tr('services.mod_service.installation_failed_rolling_back_files'))
            for destination, old_dir, _backup_id in reversed(journal):
                try:
                    if destination.exists():
                        shutil.rmtree(destination, ignore_errors=True)
                    if old_dir and old_dir.exists():
                        os.replace(old_dir, destination)
                except OSError:
                    log.exception(tr('services.mod_service.failed_to_roll_back_installation_s'), destination)
            with self.db.session() as session:
                InstallRecordRepository(session).add(
                    game_id=game_id,
                    action="install",
                    status="failed",
                    details_json={"error": str(exc), "warnings": result.warnings},
                )
            raise ModOperationError(
                tr('services.mod_service.installation_failed_rollback_attempted_0', exc),
                detail=str(exc),
            ) from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        self.activity.record(
            tr('services.mod_service.installed_0_mods', len(plan.entries))
            + "、".join(entry.metadata.name for entry in plan.entries),
            kind="install",
            game_id=game_id,
            details={
                "backup_ids": result.backup_ids,
                "warnings": result.warnings,
                "mod_ids": result.installed_mod_ids,
            },
        )
        return result

    def install_archive(
        self,
        game_id: int,
        archive_path: Path,
        *,
        allow_replace: bool = False,
        allow_duplicate: bool = False,
        source: str = "manual",
        progress: ProgressCallback | None = None,
    ) -> InstallResult:
        plan = self.preview_install(game_id, archive_path)
        try:
            return self.install_plan(
                game_id,
                plan,
                allow_replace=allow_replace,
                allow_duplicate=allow_duplicate,
                source=source,
                progress=progress,
            )
        finally:
            self.discard_preview(plan)

    def discard_preview(self, plan: InstallPlan) -> None:
        staging = Path(plan.staging_dir).resolve()
        if is_within(staging, self.paths.staging_dir):
            shutil.rmtree(staging, ignore_errors=True)

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan_game(
        self,
        game_id: int,
        progress: ProgressCallback | None = None,
        *,
        record_activity: bool = True,
    ) -> int:
        game, adapter = self._game_and_adapter(game_id)
        install_path = Path(game.install_path)
        if progress:
            progress(5, tr('services.mod_service.scanning_mods_folder'))
        scanned = adapter.scan_mods(install_path, progress)
        seen_ids: set[int] = set()
        created = 0
        updated = 0

        with self.db.session() as session:
            repo = ModRepository(session)
            existing_all = repo.list_for_game(game_id, include_missing=True)
            by_path = {mod.relative_path: mod for mod in existing_all}
            for scanned_mod in scanned:
                rel = scanned_mod.relative_path
                if rel == ".":
                    log.warning(tr('services.mod_service.ignoring_manifest_json_located_directly_in_the_mods_f970dd'), scanned_mod.directory)
                    continue
                mod = by_path.get(rel)
                if mod is None:
                    candidates = [
                        item
                        for item in repo.find_by_unique(game_id, scanned_mod.metadata.unique_id)
                        if item.folder_name.lstrip(".").casefold()
                        == scanned_mod.directory.name.lstrip(".").casefold()
                    ]
                    if len(candidates) == 1:
                        mod = candidates[0]
                        rep = repo.update(mod, relative_path=rel, folder_name=scanned_mod.directory.name)
                        by_path[rel] = rep
                    else:
                        mod = repo.add(
                            game_id=game_id,
                            unique_id=scanned_mod.metadata.unique_id,
                            name=scanned_mod.metadata.name,
                            author=scanned_mod.metadata.author,
                            version=scanned_mod.metadata.version,
                            description=scanned_mod.metadata.description,
                            folder_name=scanned_mod.directory.name,
                            relative_path=rel,
                            enabled=scanned_mod.enabled,
                            is_present=True,
                            kind=scanned_mod.metadata.kind.value,
                            nexus_mod_id=scanned_mod.metadata.nexus_mod_id,
                            source="manual",
                            metadata_json={},
                            size_bytes=scanned_mod.size_bytes,
                        )
                        by_path[rel] = mod
                        created += 1
                else:
                    updated += 1

                metadata = scanned_mod.metadata
                metadata_json = {
                    "raw_manifest": metadata.extra.get("raw_manifest", {}),
                    "entry_dll": metadata.extra.get("entry_dll"),
                    "update_keys": metadata.update_keys,
                    "minimum_api_version": metadata.minimum_api_version,
                    "content_pack_for": (
                        metadata.content_pack_for.model_dump(mode="json")
                        if metadata.content_pack_for
                        else None
                    ),
                    "file_count": scanned_mod.file_count,
                    "scan_error": scanned_mod.error,
                }
                update_status = mod.update_status
                if mod.latest_version:
                    if mod.ignored_version and versions_equal(mod.ignored_version, mod.latest_version):
                        update_status = UpdateStatus.IGNORED.value
                    elif versions_equal(mod.version, mod.latest_version):
                        update_status = UpdateStatus.UP_TO_DATE.value
                    else:
                        update_status = UpdateStatus.AVAILABLE.value
                repo.update(
                    mod,
                    unique_id=metadata.unique_id,
                    name=metadata.name,
                    author=metadata.author,
                    version=metadata.version,
                    description=metadata.description,
                    folder_name=scanned_mod.directory.name,
                    relative_path=rel,
                    enabled=scanned_mod.enabled,
                    is_present=True,
                    kind=metadata.kind.value,
                    nexus_mod_id=metadata.nexus_mod_id or mod.nexus_mod_id,
                    metadata_json=metadata_json,
                    size_bytes=scanned_mod.size_bytes,
                    update_status=update_status,
                )
                repo.replace_dependencies(
                    mod,
                    [
                        {
                            "unique_id": dep.unique_id,
                            "minimum_version": dep.minimum_version,
                            "required": dep.required,
                            "kind": dep.kind,
                        }
                        for dep in metadata.dependencies
                    ],
                )
                seen_ids.add(mod.id)

            for mod in existing_all:
                if mod.is_present and mod.id not in seen_ids:
                    repo.update(mod, is_present=False, enabled=False)
            GameRepository(session).update(
                game,
                last_scan_at=utcnow(),
                game_version=adapter.get_game_version(install_path) or game.game_version,
                framework_version=adapter.get_framework_version(install_path) or game.framework_version,
                mods_path=str(adapter.resolve_mods_dir(install_path)),
            )

        if progress:
            progress(100, tr('services.mod_service.scan_complete_0_added_1_updated', created, updated))
        if record_activity:
            self.activity.record(
                tr('services.mod_service.scan_complete_0_mods_detected_1_added_2_updated', len(scanned), created, updated),
                kind="scan",
                game_id=game_id,
                details={"created": created, "updated": updated, "scanned": len(scanned)},
            )
        return len(scanned)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_mod(self, mod_id: int) -> ModView:
        views = self._views_for_ids([mod_id])
        if not views:
            raise KeyError(tr('services.mod_service.mod_does_not_exist'))
        return views[0]

    def list_mods(self, game_id: int) -> list[ModView]:
        with self.db.session() as session:
            mods = ModRepository(session).list_for_game(game_id, include_missing=True)
            return self._build_views(session, game_id, mods)

    def _views_for_ids(self, mod_ids: list[int]) -> list[ModView]:
        if not mod_ids:
            return []
        with self.db.session() as session:
            game_id = None
            mods = []
            for mod_id in mod_ids:
                mod = ModRepository(session).get(mod_id)
                if mod is None:
                    continue
                game_id = mod.game_id
                mods.append(mod)
            if game_id is None:
                return []
            all_mods = ModRepository(session).list_for_game(game_id, include_missing=True)
            views = self._build_views(session, game_id, all_mods)
            wanted = {mod.id for mod in mods}
            return [view for view in views if view.id in wanted]

    def _build_views(self, session, game_id: int, mods: list[Mod]) -> list[ModView]:  # type: ignore[no-untyped-def]
        game = GameRepository(session).get(game_id)
        if game is None:
            return []
        adapter = self.registry.try_get(game.adapter_key)
        present = [mod for mod in mods if mod.is_present]
        by_unique: dict[str, list[Mod]] = defaultdict(list)
        for mod in present:
            by_unique[mod.unique_id].append(mod)
        dependencies = ModRepository(session).dependencies_for([mod.id for mod in mods])
        translation_counts = TranslationRepository(session).counts_by_mod([mod.id for mod in present])
        duplicate_ids = {uid for uid, items in by_unique.items() if len(items) > 1}

        views: list[ModView] = []
        for mod in mods:
            dependency_views: list[DependencyStatus] = []
            missing: list[DependencyStatus] = []
            for dependency in dependencies.get(mod.id, []):
                candidates = by_unique.get(dependency.unique_id, [])
                enabled_candidates = [candidate for candidate in candidates if candidate.enabled]
                chosen = (enabled_candidates or candidates or [None])[0]
                version_ok = chosen is not None and version_satisfies(
                    chosen.version, dependency.minimum_version
                )
                satisfied = bool(
                    version_ok
                    and (chosen.enabled or not dependency.required)
                )
                status = DependencyStatus(
                    unique_id=dependency.unique_id,
                    minimum_version=dependency.minimum_version,
                    required=dependency.required,
                    installed_version=chosen.version if chosen else None,
                    satisfied=satisfied,
                    enabled=bool(chosen and chosen.enabled),
                )
                dependency_views.append(status)
                if dependency.required and not satisfied:
                    missing.append(status)

            translation_total, translation_done, _confirmed = translation_counts.get(
                mod.id, (0, 0, 0)
            )
            eligible = False
            applied = False
            if adapter and mod.is_present:
                mod_dir = Path(game.mods_path) / mod.relative_path
                if mod_dir.is_dir():
                    try:
                        eligible = adapter.is_translatable(self._metadata_for(mod), mod_dir)
                        applied = adapter.is_translation_applied(mod_dir)
                    except Exception:
                        eligible = False
            views.append(
                ModView(
                    id=mod.id,
                    game_id=mod.game_id,
                    unique_id=mod.unique_id,
                    name=mod.name,
                    author=mod.author,
                    version=mod.version,
                    description=mod.description,
                    folder_name=mod.folder_name,
                    relative_path=mod.relative_path,
                    enabled=mod.enabled,
                    is_present=mod.is_present,
                    kind=mod.kind,
                    nexus_mod_id=mod.nexus_mod_id,
                    dependencies=dependency_views,
                    missing_dependencies=missing,
                    duplicate=mod.unique_id in duplicate_ids and mod.is_present,
                    latest_version=mod.latest_version,
                    latest_file_id=mod.latest_file_id,
                    latest_file_name=mod.latest_file_name,
                    update_status=UpdateStatus(mod.update_status)
                    if mod.update_status in UpdateStatus._value2member_map_
                    else UpdateStatus.UNKNOWN,
                    update_policy=mod.update_policy,
                    ignored_version=mod.ignored_version,
                    pinned_version=mod.pinned_version,
                    update_error=mod.update_error,
                    translation_total=translation_total,
                    translation_done=translation_done,
                    translation_applied=applied,
                    translation_eligible=eligible,
                    scan_error=(mod.metadata_json or {}).get("scan_error"),
                    size_bytes=mod.size_bytes,
                    installed_at=mod.installed_at,
                    updated_at=mod.updated_at,
                )
            )
        return views

    @staticmethod
    def _metadata_for(mod: Mod) -> ModMetadata:
        return ModMetadata(
            unique_id=mod.unique_id,
            name=mod.name,
            author=mod.author,
            version=mod.version,
            description=mod.description,
            nexus_mod_id=mod.nexus_mod_id,
        )

    # ------------------------------------------------------------------
    # Enable / disable / policy
    # ------------------------------------------------------------------

    def set_enabled(self, mod_id: int, enabled: bool) -> str:
        with self.db.session() as session:
            repo = ModRepository(session)
            mod = repo.get(mod_id)
            if mod is None:
                raise ModOperationError(tr('services.mod_service.mod_does_not_exist'))
            game = GameRepository(session).get(mod.game_id)
            if game is None:
                raise ModOperationError(tr('services.mod_service.game_record_does_not_exist'))
            adapter = self.registry.get(game.adapter_key)
            if not mod.is_present:
                raise ModOperationError(tr('services.mod_service.the_mod_file_no_longer_exists_please_rescan_or_unins_b721cb'))
            old_rel = mod.relative_path
            old_name = mod.name
            try:
                new_rel = adapter.set_enabled(Path(game.mods_path), old_rel, enabled)
            except (OSError, ValueError) as exc:
                raise ModOperationError(tr('services.mod_service.failed_to_toggle_status_0', exc)) from exc
            new_prefix = new_rel
            old_prefix = old_rel
            affected = [mod]
            for other in repo.list_for_game(mod.game_id, include_missing=True):
                if other.id == mod.id:
                    continue
                if other.relative_path.startswith(old_prefix + "/"):
                    affected.append(other)
            # Update the toggled mod first, then descendants that were physically
            # moved along with the parent folder.
            repo.update(mod, relative_path=new_rel, enabled=enabled)
            for other in affected[1:]:
                suffix = other.relative_path[len(old_prefix) :].lstrip("/")
                child_rel = f"{new_prefix}/{suffix}" if suffix else new_prefix
                child_enabled = not any(part.startswith(".") for part in Path(child_rel).parts)
                repo.update(other, relative_path=child_rel, enabled=child_enabled)
            InstallRecordRepository(session).add(
                game_id=mod.game_id,
                mod_id=mod.id,
                action="enable" if enabled else "disable",
                status="success",
                details_json={"old_path": old_rel, "new_path": new_rel},
            )
        self.activity.record(
            tr('services.mod_service.0_1', tr('services.mod_service.enable') if enabled else tr('services.mod_service.disable'), old_name),
            kind="enable" if enabled else "disable",
            game_id=game.id,
        )
        return new_rel

    def bulk_set_enabled(self, mod_ids: list[int], enabled: bool) -> BulkResult:
        result = BulkResult()
        with self.db.session() as session:
            rows = {row.id: row for row in ModRepository(session).list_by_ids(mod_ids)}
        # Enabling parents before children and disabling children before parents
        # avoids transient invalid nesting states.
        ordered = sorted(
            mod_ids,
            key=lambda mod_id: len(Path(rows[mod_id].relative_path).parts) if mod_id in rows else 0,
            reverse=not enabled,
        )
        for mod_id in ordered:
            name = rows[mod_id].name if mod_id in rows else f"#{mod_id}"
            try:
                if mod_id not in rows:
                    result.failed.append((name, tr('services.mod_service.mod_record_does_not_exist')))
                    continue
                current = rows[mod_id]
                if not current.is_present:
                    result.skipped.append(current.name)
                    continue
                if current.enabled == enabled:
                    result.skipped.append(current.name)
                    continue
                self.set_enabled(mod_id, enabled)
                result.succeeded.append(current.name)
            except Exception as exc:
                result.failed.append((name, str(exc)))
        return result

    def set_update_policy(self, mod_id: int, policy: str) -> None:
        try:
            value = UpdatePolicy(policy)
        except ValueError as exc:
            raise ValueError(tr('services.mod_service.unknown_update_strategy_0', policy)) from exc
        with self.db.session() as session:
            repo = ModRepository(session)
            mod = repo.get(mod_id)
            if mod is None:
                raise ModOperationError(tr('services.mod_service.mod_does_not_exist'))
            repo.update(mod, update_policy=value.value)

    def set_pinned_version(self, mod_id: int, version: str | None) -> None:
        with self.db.session() as session:
            repo = ModRepository(session)
            mod = repo.get(mod_id)
            if mod is None:
                raise ModOperationError(tr('services.mod_service.mod_does_not_exist'))
            pinned = version.strip() if version else None
            repo.update(mod, pinned_version=pinned or None, update_status=mod.update_status)

    def ignore_version(self, mod_id: int, version: str | None = None) -> None:
        with self.db.session() as session:
            repo = ModRepository(session)
            mod = repo.get(mod_id)
            if mod is None:
                raise ModOperationError(tr('services.mod_service.mod_does_not_exist'))
            target = (version or mod.latest_version or "").strip() or None
            repo.update(
                mod,
                ignored_version=target,
                update_status=UpdateStatus.IGNORED.value if target else mod.update_status,
            )

    def clear_update_ignore(self, mod_id: int) -> None:
        with self.db.session() as session:
            repo = ModRepository(session)
            mod = repo.get(mod_id)
            if mod is None:
                raise ModOperationError(tr('services.mod_service.mod_does_not_exist'))
            repo.update(mod, ignored_version=None, update_status=UpdateStatus.UNKNOWN.value)

    def set_nexus_id(self, mod_id: int, nexus_mod_id: int | None) -> None:
        with self.db.session() as session:
            repo = ModRepository(session)
            mod = repo.get(mod_id)
            if mod is None:
                raise ModOperationError(tr('services.mod_service.mod_does_not_exist'))
            repo.update(mod, nexus_mod_id=nexus_mod_id)

    def forget_missing_mod(self, mod_id: int) -> None:
        with self.db.session() as session:
            repo = ModRepository(session)
            mod = repo.get(mod_id)
            if mod is None:
                return
            if mod.is_present:
                raise ModOperationError(tr('services.mod_service.only_records_for_missing_mods_can_be_removed'))
            repo.delete(mod)

    # ------------------------------------------------------------------
    # Uninstall / backups
    # ------------------------------------------------------------------

    def uninstall(self, mod_id: int, *, delete_files: bool = True) -> UninstallResult:
        with self.db.session() as session:
            repo = ModRepository(session)
            mod = repo.get(mod_id)
            if mod is None:
                raise ModOperationError(tr('services.mod_service.mod_does_not_exist'))
            game = GameRepository(session).get(mod.game_id)
            if game is None:
                raise ModOperationError(tr('services.mod_service.game_record_does_not_exist'))
            mods_root = Path(game.mods_path)
            mod_dir = (mods_root / mod.relative_path).resolve()
            if not is_within(mod_dir, mods_root):
                raise ModOperationError(tr('services.mod_service.mod_path_is_not_inside_the_mods_directory_deletion_r_3996d6'))
            backup_id: int | None = None
            if mod.is_present and mod_dir.exists():
                if not delete_files:
                    raise ModOperationError(tr('services.mod_service.to_remove_only_the_record_use_forget_record_do_not_u_5ac6a6'))
                backup = self.backups.create(
                    game_id=game.id,
                    root=mods_root,
                    items=[mod_dir],
                    kind=BackupKind.UNINSTALL,
                    label=tr('services.mod_service.uninstall_0_1', mod.name, mod.version),
                    note=tr('services.mod_service.automatic_backup_before_uninstalling_0', mod.unique_id),
                    mod_id=mod.id,
                )
                backup_id = backup.id
                shutil.rmtree(mod_dir)
            repo.update(mod, is_present=False, enabled=False)
            repo.replace_dependencies(mod, [])
            InstallRecordRepository(session).add(
                game_id=game.id,
                mod_id=mod.id,
                action="uninstall",
                status="success",
                backup_id=backup_id,
                details_json={"name": mod.name, "path": mod.relative_path},
            )
            name = mod.name
            game_id = game.id
        self.scan_game(game_id, record_activity=False)
        self.activity.record(
            tr('services.mod_service.uninstalled_0', name) + (tr('services.mod_service.backup_created') if backup_id else ""),
            kind="uninstall",
            level="warning",
            game_id=game_id,
            details={"backup_id": backup_id},
        )
        return UninstallResult(mod_name=name, backup_id=backup_id)

    def rollback(self, backup_id: int) -> None:
        backup = self.backups.restore(backup_id)
        self.scan_game(backup.game_id, record_activity=False)
        self.activity.record(
            tr('services.mod_service.rolled_back_to_backup_0', backup.label),
            kind="rollback",
            level="warning",
            game_id=backup.game_id,
            details={"backup_id": backup_id},
        )

    # ------------------------------------------------------------------
    # Updates helper
    # ------------------------------------------------------------------

    def apply_update_metadata(
        self,
        mod_id: int,
        *,
        latest_version: str | None,
        latest_file_id: int | None,
        latest_file_name: str | None,
        status: UpdateStatus,
        error: str | None = None,
    ) -> None:
        with self.db.session() as session:
            repo = ModRepository(session)
            mod = repo.get(mod_id)
            if mod is None:
                return
            repo.update(
                mod,
                latest_version=latest_version,
                latest_file_id=latest_file_id,
                latest_file_name=latest_file_name,
                latest_checked_at=utcnow(),
                update_status=status.value,
                update_error=error,
            )

    def get_mods_for_update(self, game_id: int) -> list[ModView]:
        return [view for view in self.list_mods(game_id) if view.is_present]
