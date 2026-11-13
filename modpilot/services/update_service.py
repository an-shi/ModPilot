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
                message="Mod 文件不存在，无法检查更新。",
            )
        if not view.nexus_mod_id:
            self.mods.apply_update_metadata(
                mod_id,
                latest_version=view.latest_version,
                latest_file_id=view.latest_file_id,
                latest_file_name=view.latest_file_name,
                status=UpdateStatus.UNKNOWN,
                error="该 Mod 未关联 Nexus Mod ID，无法检查更新。",
            )
            return UpdateCheckResult(
                mod_id=mod_id,
                name=view.name,
                installed_version=view.version,
                status=UpdateStatus.UNKNOWN,
                message="未关联 Nexus Mod ID。",
            )
        with self.db.session() as session:
            game = GameRepository(session).get(view.game_id)
            if game is None:
                raise NexusError("游戏记录不存在。")
            domain = game.nexus_domain
        if not domain:
            raise NexusError("游戏未配置 Nexus 游戏标识。")

        if progress:
            progress(10, f"正在检查 {view.name}…")
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
                progress(70, "正在比较版本…")

            status = UpdateStatus.UNKNOWN
            message = ""
            if versions_equal(latest_version, view.version):
                status = UpdateStatus.UP_TO_DATE
                message = "已是最新版本。"
            elif view.pinned_version and versions_equal(view.pinned_version, view.version):
                status = UpdateStatus.UP_TO_DATE
                message = f"版本已锁定在 {view.pinned_version}。"
            elif view.ignored_version and versions_equal(view.ignored_version, latest_version):
                status = UpdateStatus.IGNORED
                message = f"已忽略版本 {latest_version}。"
            elif is_newer(latest_version, view.version) or latest_version != view.version:
                status = UpdateStatus.AVAILABLE
                message = f"发现新版本 {latest_version}。"
            policy = self.effective_policy(view)
            if status is UpdateStatus.AVAILABLE and policy is UpdatePolicy.IGNORE:
                status = UpdateStatus.IGNORED
                message = f"已按更新策略忽略版本 {latest_version}。"
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
                        f"{view.name} 有可用更新：{view.version} → {latest_version}",
                        kind="update_available",
                        game_id=view.game_id,
                        details={"mod_id": mod_id, "latest_version": latest_version},
                    )
                elif policy is UpdatePolicy.CONFIRM_DOWNLOAD and previous_latest != latest_version:
                    self.activity.record(
                        f"{view.name} 有可用更新，等待你确认下载。",
                        kind="update_confirmation",
                        game_id=view.game_id,
                        details={"mod_id": mod_id, "latest_version": latest_version},
                    )
            if progress:
                progress(100, "检查完成。")
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
            log.warning("检查更新失败 %s：%s", view.name, message)
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
                progress(int(index / total * 100), f"检查更新 {index + 1}/{len(targets)}：{view.name}")
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
                log.warning("自动下载更新失败 %s：%s", result.name, exc)
                self.activity.record(
                    f"自动下载更新失败：{result.name} - {exc}",
                    kind="update_download",
                    level="error",
                    game_id=game_id,
                )
        if progress:
            progress(100, f"已检查 {len(results)} 个 Mod。")
        return results

    def download_update(self, mod_id: int, progress: ProgressCallback | None = None) -> int:
        view = self.mods.get_mod(mod_id)
        if view.pinned_version:
            raise ModOperationError(f"{view.name} 已锁定版本 {view.pinned_version}，请先解除锁定。")
        if not view.nexus_mod_id:
            raise ModOperationError("该 Mod 未关联 Nexus Mod ID。")
        if not view.latest_file_id:
            result = self.check_mod(mod_id)
            if not result.latest_file_id:
                raise ModOperationError(result.message or "没有可用的更新文件。")
            view = self.mods.get_mod(mod_id)
        with self.db.session() as session:
            game = GameRepository(session).get(view.game_id)
            if game is None:
                raise ModOperationError("游戏记录不存在。")
            domain = game.nexus_domain
        files = self.nexus.get_files(domain, view.nexus_mod_id)
        file = next((item for item in files if item.file_id == view.latest_file_id), None)
        if file is None:
            raise ModOperationError("Nexus 上找不到对应的更新文件。")
        if progress:
            progress(20, "正在创建下载任务…")
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
                message="更新文件下载中",
            )
        if progress:
            progress(100, "下载任务已加入队列。")
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
                            f"{entry.metadata.name} 需要 {dependency.unique_id}"
                            + (
                                f" ≥ {dependency.minimum_version}"
                                if dependency.minimum_version
                                else ""
                            )
                            + "，但当前未安装或已禁用。"
                        )
                    elif not version_satisfies(candidate_view.version, dependency.minimum_version):
                        missing.append(
                            f"{entry.metadata.name} 需要 {dependency.unique_id} ≥ {dependency.minimum_version}，"
                            f"当前版本 {candidate_view.version} 不满足。"
                        )
                else:
                    if not version_satisfies(candidate.metadata.version, dependency.minimum_version):
                        missing.append(
                            f"更新包中的 {dependency.unique_id} 版本 {candidate.metadata.version} "
                            f"低于要求的 {dependency.minimum_version}。"
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
            raise ModOperationError("没有可安装的更新版本信息。")
        if view.pinned_version:
            raise ModOperationError("该 Mod 已锁定版本，请先解除锁定。")
        if progress:
            progress(5, "正在校验更新包…")
        plan = self.mods.preview_install(view.game_id, Path(archive_path))
        try:
            missing = self._check_plan_dependencies(plan, view)
            if missing:
                raise ModOperationError(
                    "更新可能破坏依赖环境，已阻止自动安装：\n" + "\n".join(missing)
                )
            if progress:
                progress(15, "正在备份并安装更新…")
            result = self.mods.install_plan(
                view.game_id,
                plan,
                allow_replace=True,
                allow_duplicate=True,
                source="nexus",
                progress=lambda p, msg: progress(int(15 + p * 0.7), msg) if progress else None,
            )
            if progress:
                progress(90, "正在验证安装结果…")
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
                    f"安装后版本校验失败：期望 {expected}，实际 {installed_versions or '未知'}。"
                    + ("已自动回滚。" if backup_id else "")
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
                    message="更新安装完成",
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
                f"已更新 {view.name}：{view.version} → {expected}",
                kind="update_install",
                game_id=view.game_id,
                details={"backup_id": backup_id},
            )
            if progress:
                progress(100, "更新安装完成。")
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
                raise ModOperationError("下载任务不存在。")
            if row.status != DownloadStatus.COMPLETED.value:
                raise ModOperationError("下载尚未完成。")
            if row.kind != DownloadKind.UPDATE.value or not row.target_mod_id:
                raise ModOperationError("该下载不是 Mod 更新包。")
            archive = row.archive_path
            mod_id = row.target_mod_id
        if not archive:
            raise ModOperationError("下载文件记录缺失。")
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
                raise ModOperationError("没有可用的更新前备份。")
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
                message="已回滚到更新前版本",
                backup_id=backup_id,
                applied_at=datetime.now(UTC).replace(tzinfo=None),
            )
        self.activity.record(
            f"已回滚更新：{view.name} → {new_view.version}",
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
