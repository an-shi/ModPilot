from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from ..adapters.base import AdapterRegistry, ProgressCallback
from ..config import ConfigStore
from ..core.errors import ModOperationError, NexusError
from ..core.versions import is_newer, version_satisfies, versions_equal
from ..data.db import Database
from ..data.repositories import (
    BackupRepository,
    DownloadRepository,
    GameRepository,
    ModRepository,
    UpdateRepository,
)
from ..domain.enums import BackupKind, DownloadKind, DownloadStatus, UpdatePolicy, UpdateStatus
from ..domain.models import (
    InstallPlan,
    ModView,
    NexusFile,
    UpdateCheckResult,
    UpdateRecordView,
)
from .activity import ActivityService
from .download_manager import DownloadManager
from .mod_service import ModService
from .nexus.client import NexusClient
from ..i18n import tr

log = logging.getLogger(__name__)


class UpdateService:
    def __init__(
        self,
        db: Database,
        registry: AdapterRegistry,
        config: ConfigStore,
        nexus: NexusClient,
        mods: ModService,
        downloads: DownloadManager,
        activity: ActivityService,
    ) -> None:
        self.db = db
        self.registry = registry
        self.config = config
        self.nexus = nexus
        self.mods = mods
        self.downloads = downloads
        self.activity = activity

    def effective_policy(self, view_or_policy: ModView | str | None) -> UpdatePolicy:
        if isinstance(view_or_policy, ModView):
            raw = view_or_policy.update_policy
        else:
            raw = view_or_policy
        if not raw or raw == "inherit":
            raw = self.config.config.update_policy
        try:
            return UpdatePolicy(raw)
        except ValueError:
            return UpdatePolicy.NOTIFY

    @staticmethod
    def _pick_latest(files: list[NexusFile]) -> NexusFile | None:
        main = [
            item
            for item in files
            if item.category_name.upper() == "MAIN" or item.category.upper() == "MAIN"
        ]
        candidates = main or [item for item in files if "OLD" not in item.category.upper()]
        if not candidates:
            return None
        primary = [item for item in candidates if item.is_primary]
        pool = primary or candidates
        return max(pool, key=lambda item: (item.uploaded_timestamp, item.mod_version or item.version))

    def check_mod(self, mod_id: int, progress: ProgressCallback | None = None) -> UpdateCheckResult:
        view = self.mods.get_mod(mod_id)
        if not view.is_present:
            return UpdateCheckResult(
                mod_id=mod_id,
                name=view.name,
                installed_version=view.version,
                status=UpdateStatus.ERROR,
                message=tr('services.update_service.mod_file_does_not_exist_cannot_check_for_updates'),
            )
        if not view.nexus_mod_id:
            self.mods.apply_update_metadata(
                mod_id,
                latest_version=view.latest_version,
                latest_file_id=view.latest_file_id,
                latest_file_name=view.latest_file_name,
                status=UpdateStatus.UNKNOWN,
                error=tr('services.update_service.this_mod_is_not_linked_to_a_nexus_mod_id_so_updates_ba26be'),
            )
            return UpdateCheckResult(
                mod_id=mod_id,
                name=view.name,
                installed_version=view.version,
                status=UpdateStatus.UNKNOWN,
                message=tr('services.update_service.no_nexus_mod_id_is_linked'),
            )
        with self.db.session() as session:
            game = GameRepository(session).get(view.game_id)
            if game is None:
                raise NexusError(tr('services.update_service.game_record_does_not_exist'))
            domain = game.nexus_domain
        if not domain:
            raise NexusError(tr('services.update_service.the_game_has_no_nexus_game_id_configured'))

        if progress:
            progress(10, tr('services.update_service.checking_0', view.name))
        previous_latest = view.latest_version
        try:
            info = self.nexus.get_mod(domain, view.nexus_mod_id, include_requirements=False)
            files = self.nexus.get_files(domain, view.nexus_mod_id)
            latest_file = self._pick_latest(files)
            latest_version = (
                info.version
                or (latest_file.mod_version if latest_file else "")
                or (latest_file.version if latest_file else "")
            ).strip()
            if not latest_version:
                latest_version = view.version
            if progress:
                progress(70, tr('services.update_service.comparing_versions'))

            status = UpdateStatus.UNKNOWN
            message = ""
            if versions_equal(latest_version, view.version):
                status = UpdateStatus.UP_TO_DATE
                message = tr('services.update_service.already_up_to_date')
            elif view.pinned_version and versions_equal(view.pinned_version, view.version):
                status = UpdateStatus.UP_TO_DATE
                message = tr('services.update_service.version_is_locked_to_0', view.pinned_version)
            elif view.ignored_version and versions_equal(view.ignored_version, latest_version):
                status = UpdateStatus.IGNORED
                message = tr('services.update_service.version_0_ignored', latest_version)
            elif is_newer(latest_version, view.version) or latest_version != view.version:
                status = UpdateStatus.AVAILABLE
                message = tr('services.update_service.new_version_0_found', latest_version)
            policy = self.effective_policy(view)
            if status is UpdateStatus.AVAILABLE and policy is UpdatePolicy.IGNORE:
                status = UpdateStatus.IGNORED
                message = tr('services.update_service.ignored_version_0_according_to_the_update_policy', latest_version)
            self.mods.apply_update_metadata(
                mod_id,
                latest_version=latest_version,
                latest_file_id=latest_file.file_id if latest_file else None,
                latest_file_name=latest_file.file_name if latest_file else None,
                status=status,
                error=None,
            )
            if status is UpdateStatus.AVAILABLE:
                policy = self.effective_policy(view)
                if policy is UpdatePolicy.NOTIFY and previous_latest != latest_version:
                    self.activity.record(
                        tr('services.update_service.update_available_for_0_1_2', view.name, view.version, latest_version),
                        kind="update_available",
                        game_id=view.game_id,
                        details={"mod_id": mod_id, "latest_version": latest_version},
                    )
                elif policy is UpdatePolicy.CONFIRM_DOWNLOAD and previous_latest != latest_version:
                    self.activity.record(
                        tr('services.update_service.an_update_is_available_for_0_waiting_for_your_confir_6299d6', view.name),
                        kind="update_confirmation",
                        game_id=view.game_id,
                        details={"mod_id": mod_id, "latest_version": latest_version},
                    )
            if progress:
                progress(100, tr('services.update_service.check_complete'))
            return UpdateCheckResult(
                mod_id=mod_id,
                name=view.name,
                installed_version=view.version,
                latest_version=latest_version,
                latest_file_id=latest_file.file_id if latest_file else None,
                latest_file_name=latest_file.file_name if latest_file else None,
                status=status,
                message=message,
            )
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            if isinstance(exc, NexusError) and "Premium" in message:
                pass
            self.mods.apply_update_metadata(
                mod_id,
                latest_version=view.latest_version,
                latest_file_id=view.latest_file_id,
                latest_file_name=view.latest_file_name,
                status=UpdateStatus.ERROR,
                error=message,
            )
            log.warning(tr('services.update_service.failed_to_check_for_updates_s_s'), view.name, message)
            return UpdateCheckResult(
                mod_id=mod_id,
                name=view.name,
                installed_version=view.version,
                status=UpdateStatus.ERROR,
                message=message,
            )

    def check_all(
        self,
        game_id: int,
        progress: ProgressCallback | None = None,
        *,
        include_manual: bool = True,
    ) -> list[UpdateCheckResult]:
        views = [view for view in self.mods.list_mods(game_id) if view.is_present]
        targets = []
        for view in views:
            policy = self.effective_policy(view)
            if policy is UpdatePolicy.IGNORE:
                continue
            if policy is UpdatePolicy.MANUAL and not include_manual:
                continue
            if not view.nexus_mod_id:
                continue
            targets.append(view)
        results: list[UpdateCheckResult] = []
        total = max(1, len(targets))
        for index, view in enumerate(targets):
            if progress:
                progress(int(index / total * 100), tr('services.update_service.checking_for_updates_0_1_2', index + 1, len(targets), view.name))
            results.append(self.check_mod(view.id))
            time.sleep(0.15)

        # Automatic download stage. Installation is deliberately handled after
        # the download finishes so it can use the same verified archive pipeline.
        for result in results:
            if result.status is not UpdateStatus.AVAILABLE or not result.latest_file_id:
                continue
            try:
                fresh = self.mods.get_mod(result.mod_id)
                policy = self.effective_policy(fresh)
                if policy in (UpdatePolicy.AUTO_DOWNLOAD, UpdatePolicy.AUTO_INSTALL):
                    if fresh.pinned_version:
                        continue
                    self.download_update(result.mod_id)
            except Exception as exc:
                log.warning(tr('services.update_service.failed_to_auto_download_update_s_s'), result.name, exc)
                self.activity.record(
                    tr('services.update_service.failed_to_auto_download_update_0_1', result.name, exc),
                    kind="update_download",
                    level="error",
                    game_id=game_id,
                )
        if progress:
            progress(100, tr('services.update_service.checked_0_mods', len(results)))
        return results

    def download_update(self, mod_id: int, progress: ProgressCallback | None = None) -> int:
        view = self.mods.get_mod(mod_id)
        if view.pinned_version:
            raise ModOperationError(tr('services.update_service.0_is_locked_to_version_1_please_unlock_it_first', view.name, view.pinned_version))
        if not view.nexus_mod_id:
            raise ModOperationError(tr('services.update_service.this_mod_is_not_linked_to_a_nexus_mod_id'))
        if not view.latest_file_id:
            result = self.check_mod(mod_id)
            if not result.latest_file_id:
                raise ModOperationError(result.message or tr('services.update_service.no_update_files_are_available'))
            view = self.mods.get_mod(mod_id)
        with self.db.session() as session:
            game = GameRepository(session).get(view.game_id)
            if game is None:
                raise ModOperationError(tr('services.update_service.game_record_does_not_exist'))
            domain = game.nexus_domain
        files = self.nexus.get_files(domain, view.nexus_mod_id)
        file = next((item for item in files if item.file_id == view.latest_file_id), None)
        if file is None:
            raise ModOperationError(tr('services.update_service.no_matching_update_file_found_on_nexus'))
        if progress:
            progress(20, tr('services.update_service.creating_download_task'))
        download = self.downloads.enqueue(
            game_id=view.game_id,
            nexus_mod_id=view.nexus_mod_id,
            file_id=file.file_id,
            mod_name=view.name,
            file_name=file.file_name or file.name or view.latest_file_name or f"{view.name}.zip",
            expected_md5=file.md5,
            kind=DownloadKind.UPDATE,
            target_mod_id=mod_id,
        )
        with self.db.session() as session:
            UpdateRepository(session).add(
                game_id=view.game_id,
                mod_id=mod_id,
                old_version=view.version,
                new_version=view.latest_version or file.mod_version or file.version,
                file_id=file.file_id,
                file_name=file.file_name,
                status="downloading",
                strategy=self.effective_policy(view).value,
                message=tr('services.update_service.downloading_update_file'),
            )
        if progress:
            progress(100, tr('services.update_service.download_task_queued'))
        return download.id

    def _check_plan_dependencies(self, plan: InstallPlan, mod_view: ModView) -> list[str]:
        current = {
            view.unique_id.casefold(): view
            for view in self.mods.list_mods(mod_view.game_id)
            if view.is_present and view.enabled
        }
        planned = {entry.metadata.unique_id.casefold(): entry for entry in plan.entries}
        missing: list[str] = []
        for entry in plan.entries:
            for dependency in entry.metadata.dependencies:
                if not dependency.required:
                    continue
                key = dependency.unique_id.casefold()
                candidate = planned.get(key)
                if candidate is None:
                    candidate_view = current.get(key)
                    if candidate_view is None:
                        missing.append(
                            tr('services.update_service.0_requires_1', entry.metadata.name, dependency.unique_id)
                            + (
                                f" ≥ {dependency.minimum_version}"
                                if dependency.minimum_version
                                else ""
                            )
                            + tr('services.update_service.but_it_is_not_installed_or_is_currently_disabled')
                        )
                    elif not version_satisfies(candidate_view.version, dependency.minimum_version):
                        missing.append(
                            tr('services.update_service.0_requires_1_2_but_the_current_version_3_does_not_sa_d956c8', entry.metadata.name, dependency.unique_id, dependency.minimum_version, candidate_view.version)
                        )
                else:
                    if not version_satisfies(candidate.metadata.version, dependency.minimum_version):
                        missing.append(
                            tr('services.update_service.0_version_1_in_the_update_package_is_lower_than_the_678f6e', dependency.unique_id, candidate.metadata.version, dependency.minimum_version)
                        )
        return missing

    def install_downloaded_update(
        self,
        mod_id: int,
        archive_path: Path,
        progress: ProgressCallback | None = None,
    ) -> int:
        view = self.mods.get_mod(mod_id)
        if not view.latest_version:
            raise ModOperationError(tr('services.update_service.no_installable_update_version_information'))
        if view.pinned_version:
            raise ModOperationError(tr('services.update_service.this_mod_s_version_is_locked_unlock_it_first'))
        if progress:
            progress(5, tr('services.update_service.validating_update_package'))
        plan = self.mods.preview_install(view.game_id, Path(archive_path))
        try:
            missing = self._check_plan_dependencies(plan, view)
            if missing:
                raise ModOperationError(
                    tr('services.update_service.the_update_may_break_the_dependency_environment_so_a_4b554b') + "\n".join(missing)
                )
            if progress:
                progress(15, tr('services.update_service.backing_up_and_installing_update'))
            result = self.mods.install_plan(
                view.game_id,
                plan,
                allow_replace=True,
                allow_duplicate=True,
                source="nexus",
                progress=lambda p, msg: progress(int(15 + p * 0.7), msg) if progress else None,
            )
            if progress:
                progress(90, tr('services.update_service.verifying_installation_result'))
            installed_versions: list[str] = []
            for entry in plan.entries:
                with self.db.session() as session:
                    rows = ModRepository(session).find_by_unique(
                        view.game_id, entry.metadata.unique_id
                    )
                    for row in rows:
                        if row.is_present:
                            installed_versions.append(row.version)
            expected = plan.entries[0].metadata.version if plan.entries else ""
            if expected and not any(versions_equal(version, expected) for version in installed_versions):
                backup_id = result.backup_ids[0] if result.backup_ids else None
                if backup_id:
                    self.mods.rollback(backup_id)
                raise ModOperationError(
                    tr('services.update_service.post_install_version_check_failed_expected_0_got_1', expected, installed_versions or tr('services.update_service.unknown'))
                    + (tr('services.update_service.automatically_rolled_back') if backup_id else "")
                )
            backup_id = result.backup_ids[0] if result.backup_ids else None
            with self.db.session() as session:
                UpdateRepository(session).add(
                    game_id=view.game_id,
                    mod_id=mod_id,
                    old_version=view.version,
                    new_version=expected or view.latest_version,
                    file_id=view.latest_file_id,
                    file_name=view.latest_file_name,
                    status="installed",
                    strategy=self.effective_policy(view).value,
                    message=tr('services.update_service.update_installed_91f47c'),
                    backup_id=backup_id,
                    applied_at=datetime.now(UTC).replace(tzinfo=None),
                )
            self.mods.apply_update_metadata(
                mod_id,
                latest_version=view.latest_version,
                latest_file_id=view.latest_file_id,
                latest_file_name=view.latest_file_name,
                status=UpdateStatus.UP_TO_DATE
                if versions_equal(view.latest_version, expected)
                else UpdateStatus.AVAILABLE,
                error=None,
            )
            self.activity.record(
                tr('services.update_service.updated_0_1_2', view.name, view.version, expected),
                kind="update_install",
                game_id=view.game_id,
                details={"backup_id": backup_id},
            )
            if progress:
                progress(100, tr('services.update_service.update_installed'))
            return backup_id or 0
        finally:
            self.mods.discard_preview(plan)

    def install_update_from_download(
        self,
        download_id: int,
        progress: ProgressCallback | None = None,
    ) -> int:
        with self.db.session() as session:
            row = DownloadRepository(session).get(download_id)
            if row is None:
                raise ModOperationError(tr('services.update_service.download_task_does_not_exist'))
            if row.status != DownloadStatus.COMPLETED.value:
                raise ModOperationError(tr('services.update_service.the_download_has_not_finished_yet'))
            if row.kind != DownloadKind.UPDATE.value or not row.target_mod_id:
                raise ModOperationError(tr('services.update_service.this_download_is_not_a_mod_update_package'))
            archive = row.archive_path
            mod_id = row.target_mod_id
        if not archive:
            raise ModOperationError(tr('services.update_service.download_file_record_is_missing'))
        return self.install_downloaded_update(mod_id, Path(archive), progress)

    def should_auto_install(self, download_id: int) -> bool:
        with self.db.session() as session:
            row = DownloadRepository(session).get(download_id)
            if (
                row is None
                or row.status != DownloadStatus.COMPLETED.value
                or row.kind != DownloadKind.UPDATE.value
                or not row.target_mod_id
            ):
                return False
            mod_id = row.target_mod_id
        try:
            view = self.mods.get_mod(mod_id)
        except Exception:
            return False
        return self.effective_policy(view) is UpdatePolicy.AUTO_INSTALL and not view.pinned_version

    def rollback_last_update(self, mod_id: int) -> None:
        view = self.mods.get_mod(mod_id)
        with self.db.session() as session:
            backups = BackupRepository(session).list_for_game(view.game_id, mod_id=mod_id)
            candidate = next(
                (
                    backup
                    for backup in backups
                    if backup.status == "available"
                    and backup.kind in (BackupKind.UPDATE.value, BackupKind.INSTALL.value)
                ),
                None,
            )
            if candidate is None:
                raise ModOperationError(tr('services.update_service.no_pre_update_backup_is_available'))
            backup_id = candidate.id
            old_version = view.version
        self.mods.rollback(backup_id)
        new_view = self.mods.get_mod(mod_id)
        with self.db.session() as session:
            UpdateRepository(session).add(
                game_id=view.game_id,
                mod_id=mod_id,
                old_version=old_version,
                new_version=new_view.version,
                status="rolled_back",
                strategy="manual",
                message=tr('services.update_service.rolled_back_to_the_version_before_the_update'),
                backup_id=backup_id,
                applied_at=datetime.now(UTC).replace(tzinfo=None),
            )
        self.activity.record(
            tr('services.update_service.rolled_back_update_0_1', view.name, new_view.version),
            kind="update_rollback",
            level="warning",
            game_id=view.game_id,
            details={"backup_id": backup_id},
        )

    def history(self, game_id: int) -> list[UpdateRecordView]:
        with self.db.session() as session:
            rows = UpdateRepository(session).list_for_game(game_id)
            mod_names = {
                row.id: row.name
                for row in ModRepository(session).list_for_game(game_id, include_missing=True)
            }
            return [
                UpdateRecordView(
                    id=row.id,
                    mod_id=row.mod_id,
                    mod_name=mod_names.get(row.mod_id, f"#{row.mod_id}"),
                    old_version=row.old_version,
                    new_version=row.new_version,
                    file_id=row.file_id,
                    file_name=row.file_name,
                    status=row.status,
                    strategy=row.strategy,
                    message=row.message,
                    backup_id=row.backup_id,
                    created_at=row.created_at,
                    applied_at=row.applied_at,
                )
                for row in rows
            ]
