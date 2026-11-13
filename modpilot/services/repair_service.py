from __future__ import annotations

import hashlib
import logging
import re
import shutil
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..config import AppPaths
from ..core.errors import NexusPremiumRequired, RepairError
from ..core.paths import sanitize_filename
from ..data.db import Database
from ..data.repositories import (
    DiagnosticRepository,
    GameRepository,
    RepairRepository,
)
from ..domain.enums import (
    IssueCategory,
    LogSeverity,
    RepairStatus,
    RiskLevel,
)
from ..domain.models import (
    ModView,
    RepairPlanData,
    RepairPlanView,
    RepairStep,
)
from ..version import USER_AGENT
from .activity import ActivityService
from .backup_service import BackupService
from .mod_service import ModService
from .nexus.client import NexusClient

log = logging.getLogger(__name__)

NEXUS_LINK_RE = re.compile(
    r"(?P<label>[^:\n,]{2,80}):\s*https?://(?:www\.)?nexusmods\.com/[^/\s]+/mods/(?P<id>\d+)",
    re.IGNORECASE,
)


class RepairService:
    def __init__(
        self,
        db: Database,
        paths: AppPaths,
        mods: ModService,
        backups: BackupService,
        nexus: NexusClient,
        activity: ActivityService,
    ) -> None:
        self.db = db
        self.paths = paths
        self.mods = mods
        self.backups = backups
        self.nexus = nexus
        self.activity = activity

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def create_plan(self, run_id: int, progress=None) -> RepairPlanView:  # type: ignore[no-untyped-def]
        with self.db.session() as session:
            run = DiagnosticRepository(session).get_run(run_id)
            if run is None:
                raise RepairError("诊断记录不存在。")
            game = GameRepository(session).get(run.game_id)
            if game is None:
                raise RepairError("游戏记录不存在。")
            game_id = run.game_id
        views = self.mods.list_mods(game_id)
        known = {view.name.casefold(): view for view in views}
        with self.db.session() as session:
            issues = DiagnosticRepository(session).issues(run_id)
        steps: list[RepairStep] = []
        warnings: list[str] = []
        seen: set[tuple] = set()

        def add_step(step: RepairStep) -> None:
            key = (step.action, tuple(sorted((k, str(v)) for k, v in step.params.items())))
            if key in seen:
                return
            seen.add(key)
            steps.append(step)

        for issue in issues:
            finding_mods = issue.mods_json or []
            severity = issue.severity
            category = issue.category
            title = issue.title
            text = " ".join([title, issue.description or "", " ".join(issue.evidence_json or [])])
            if category == IssueCategory.MISSING_DEPENDENCY.value:
                self._plan_missing_dependency(
                    issue, views, known, text, game_id, add_step, warnings
                )
            elif category == IssueCategory.DUPLICATE.value:
                unique_ids = [
                    view.unique_id
                    for view in views
                    if view.name in finding_mods and view.is_present
                ]
                for unique_id in set(unique_ids):
                    copies = [
                        view
                        for view in views
                        if view.is_present and view.unique_id == unique_id
                    ]
                    if len(copies) < 2:
                        continue
                    keep = sorted(
                        copies,
                        key=lambda view: (
                            0 if view.enabled else 1,
                            view.version,
                            view.relative_path,
                        ),
                    )[0]
                    for copy in copies:
                        if copy.id == keep.id:
                            continue
                        if copy.enabled:
                            add_step(
                                RepairStep(
                                    id=f"disable-{copy.id}",
                                    title=f"禁用重复副本：{copy.name}",
                                    description=(
                                        f"保留 {keep.relative_path}（{keep.version}），禁用 {copy.relative_path}。"
                                        "仅重命名文件夹，不删除文件。"
                                    ),
                                    risk=RiskLevel.LOW,
                                    automatic=True,
                                    action="disable_mod",
                                    params={"mod_id": copy.id},
                                    findings=[issue.rule_id or "duplicate"],
                                )
                            )
                        else:
                            warnings.append(
                                f"{copy.name} 的重复副本 {copy.relative_path} 已处于禁用状态，可在 Mod 管理中卸载。"
                            )
            elif category == IssueCategory.ENVIRONMENT.value:
                if "SMAPI" in title or "smapi" in (issue.description or "").lower():
                    add_step(
                        RepairStep(
                            id="manual-smapi",
                            title="安装或更新 SMAPI",
                            description=issue.recommendation or "请安装 SMAPI 后重新诊断。",
                            risk=RiskLevel.MEDIUM,
                            automatic=False,
                            action="manual",
                            params={
                                "url": "https://smapi.io/",
                                "instructions": "下载 SMAPI 安装器，按安装器提示安装，然后重新扫描。",
                            },
                            findings=[issue.rule_id or "environment"],
                        )
                    )
                elif "日志" in title:
                    warnings.append("未找到 SMAPI 日志，无法针对运行错误生成修复步骤。")
            elif category in (
                IssueCategory.VERSION_MISMATCH.value,
                IssueCategory.COMPATIBILITY.value,
            ) and severity in (LogSeverity.ERROR.value, LogSeverity.CRITICAL.value):
                affected = [
                    known[name.casefold()]
                    for name in finding_mods
                    if name.casefold() in known
                ]
                for view in affected:
                    add_step(
                        RepairStep(
                            id=f"manual-version-{view.id}",
                            title=f"核对版本：{view.name}",
                            description=(
                                (issue.recommendation or issue.description or "更新到兼容版本。")
                                + " 版本升级不能仅凭日志自动决定，请确认对应游戏/SMAPI 版本。"
                            ),
                            risk=RiskLevel.MEDIUM,
                            automatic=False,
                            action="manual",
                            params={
                                "url": f"https://www.nexusmods.com/stardewvalley/mods/{view.nexus_mod_id}"
                                if view.nexus_mod_id
                                else "",
                                "instructions": "在更新中心更新该 Mod，或查看 Nexus 页面兼容说明。",
                            },
                            findings=[issue.rule_id or "version"],
                        )
                    )
            elif category == IssueCategory.CONFIG.value:
                affected = [
                    known[name.casefold()]
                    for name in finding_mods
                    if name.casefold() in known
                ]
                for view in affected:
                    add_step(
                        RepairStep(
                            id=f"manual-config-{view.id}",
                            title=f"重新安装或修复配置：{view.name}",
                            description=(
                                "检测到 manifest 或配置文件问题。自动修改配置文件风险较高，"
                                "建议先查看原始日志；必要时卸载并重新安装该 Mod。"
                            ),
                            risk=RiskLevel.MEDIUM,
                            automatic=False,
                            action="manual",
                            params={"instructions": "打开 Mod 文件夹检查 manifest.json，或重新安装。"},
                            findings=[issue.rule_id or "config"],
                        )
                    )

        # Remove stale partial downloads and abandoned staging directories. This
        # action only touches ModPilot's own cache/download folders.
        removable = list(self.paths.downloads_dir.rglob("*.part"))
        staging_cutoff = datetime.now(UTC) - timedelta(hours=24)
        for staging in self.paths.staging_dir.glob("install-*"):
            try:
                mtime = datetime.fromtimestamp(staging.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if mtime < staging_cutoff:
                removable.append(staging)
        if removable:
            add_step(
                RepairStep(
                    id="clean-temp",
                    title=f"清理 {len(removable)} 个临时文件",
                    description="删除未完成的下载分片和 24 小时前遗留的安装暂存目录。不会删除 Mod 或备份。",
                    risk=RiskLevel.LOW,
                    automatic=True,
                    action="clean_temp",
                    params={"count": len(removable)},
                    findings=["maintenance"],
                )
            )

        steps.sort(key=lambda step: (not step.automatic, step.risk.value))
        summary = (
            f"生成 {len(steps)} 个修复步骤，其中 "
            f"{sum(1 for step in steps if step.automatic)} 个可自动执行、"
            f"{sum(1 for step in steps if not step.automatic)} 个需要手动操作。"
        )
        plan_data = RepairPlanData(summary=summary, steps=steps, warnings=warnings)
        with self.db.session() as session:
            plan_row = RepairRepository(session).add(
                game_id=game_id,
                diagnostic_run_id=run_id,
                status=RepairStatus.PLANNED.value,
                plan_json=plan_data.model_dump(mode="json"),
            )
            plan_id = plan_row.id
        if progress:
            progress(100, "修复计划已生成。")
        return self.get_plan(plan_id)

    def _plan_missing_dependency(
        self,
        issue,
        views: list[ModView],
        known: dict[str, ModView],
        text: str,
        game_id: int,
        add_step,
        warnings: list[str],
    ) -> None:
        finding_mods = issue.mods_json or []
        raw_dependencies = [dep for dep in finding_mods[1:] if dep]
        if not raw_dependencies:
            return
        parent_name = finding_mods[0]
        for raw_dependency in raw_dependencies:
            dep_name, inline_nexus_id = self._normalize_dependency(str(raw_dependency))
            installed = next(
                (
                    view
                    for view in views
                    if view.is_present
                    and (
                        view.unique_id.casefold() == dep_name.casefold()
                        or view.name.casefold() == dep_name.casefold()
                    )
                ),
                None,
            )
            if installed is not None:
                if not installed.enabled:
                    add_step(
                        RepairStep(
                            id=f"enable-dep-{installed.id}",
                            title=f"启用依赖：{installed.name}",
                            description=f"{installed.name} 已安装但被禁用。",
                            risk=RiskLevel.LOW,
                            automatic=True,
                            action="enable_mod",
                            params={"mod_id": installed.id},
                            findings=[issue.rule_id or "dependency"],
                        )
                    )
                else:
                    warnings.append(
                        f"{dep_name} 已安装，但版本可能不满足要求，请到更新中心检查。"
                    )
                continue

            nexus_mod_id: int | None = inline_nexus_id
            if nexus_mod_id is None:
                for match in NEXUS_LINK_RE.finditer(text):
                    label = match.group("label").strip().casefold()
                    dep_tail = dep_name.split(".")[-1].casefold()
                    if label and (
                        label in dep_tail
                        or dep_tail in label
                        or label.replace(" ", "") in dep_name.casefold().replace(".", "")
                    ):
                        nexus_mod_id = int(match.group("id"))
                        break
            if nexus_mod_id:
                add_step(
                    RepairStep(
                        id=f"install-dep-{nexus_mod_id}",
                        title=f"安装缺失依赖：{dep_name}",
                        description=(
                            f"Nexus Mod ID {nexus_mod_id} 由日志中的链接确定。"
                            "下载与安装遵循 Nexus API；非 Premium 用户可能需要在网页手动下载。"
                        ),
                        risk=RiskLevel.MEDIUM,
                        automatic=True,
                        requires_network=True,
                        action="install_dependency",
                        params={
                            "game_id": game_id,
                            "nexus_mod_id": nexus_mod_id,
                            "unique_id": dep_name,
                            "mod_name": parent_name,
                        },
                        findings=[issue.rule_id or "dependency"],
                    )
                )
            else:
                add_step(
                    RepairStep(
                        id=f"manual-dep-{re.sub(r'[^a-zA-Z0-9]+', '-', dep_name)[:60]}",
                        title=f"手动安装依赖：{dep_name}",
                        description=(
                            "日志中给出了依赖名称/UniqueID，但没有可直接确认的 Nexus Mod ID。"
                            "请根据 Mod 页面依赖说明安装，避免安装同名但错误的 Mod。"
                        ),
                        risk=RiskLevel.MEDIUM,
                        automatic=False,
                        action="manual",
                        params={
                            "url": "https://www.nexusmods.com/stardewvalley/search/?gsearch="
                            + dep_name.split(".")[-1],
                            "instructions": "在 Nexus 搜索该依赖并核对 UniqueID，然后导入安装。",
                        },
                        findings=[issue.rule_id or "dependency"],
                    )
                )

    @staticmethod
    def _normalize_dependency(raw: str) -> tuple[str, int | None]:
        match = re.match(
            r"^(?P<label>.+?):\s*https?://(?:www\.)?nexusmods\.com/[^/\s]+/mods/(?P<id>\d+)",
            raw,
            re.IGNORECASE,
        )
        if match:
            return match.group("label").strip(), int(match.group("id"))
        return raw.strip(), None

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def get_plan(self, plan_id: int) -> RepairPlanView:
        with self.db.session() as session:
            row = RepairRepository(session).get(plan_id)
            if row is None:
                raise RepairError("修复计划不存在。")
            plan = RepairPlanData.model_validate(row.plan_json)
            return RepairPlanView(
                id=row.id,
                game_id=row.game_id,
                diagnostic_run_id=row.diagnostic_run_id,
                status=row.status,
                plan=plan,
                result=row.result,
                backup_id=row.backup_id,
                created_at=row.created_at,
                applied_at=row.applied_at,
            )

    def latest_for_run(self, run_id: int) -> RepairPlanView | None:
        with self.db.session() as session:
            row = RepairRepository(session).latest_for_run(run_id)
            if row is None:
                return None
            plan_id = row.id
        return self.get_plan(plan_id)

    def apply_plan(
        self,
        plan_id: int,
        selected_step_ids: list[str] | None = None,
        progress=None,  # type: ignore[no-untyped-def]
    ) -> RepairPlanView:
        view = self.get_plan(plan_id)
        selected = [
            step
            for step in view.plan.steps
            if selected_step_ids is None or step.id in selected_step_ids
        ]
        if not selected:
            raise RepairError("没有选择任何修复步骤。")
        before = self._problem_counts(view.game_id)
        reverse_actions: list[tuple[str, dict[str, Any]]] = []
        installed_backups: list[int] = []
        first_backup: int | None = None
        executed: list[RepairStep] = []
        failures: list[str] = []
        total = max(1, len(selected))
        try:
            for index, step in enumerate(selected):
                if progress:
                    progress(int(index / total * 80), f"执行：{step.title}")
                if not step.automatic:
                    step.executed = False
                    step.success = None
                    step.message = "需手动操作，未自动执行。"
                    executed.append(step)
                    continue
                try:
                    result_message, reverse = self._execute_step(step, progress)
                    step.executed = True
                    step.success = True
                    step.message = result_message
                    if reverse:
                        reverse_actions.append(reverse)
                    if step.action == "install_dependency":
                        installed_backups.extend(
                            int(item) for item in step.params.get("backup_ids", []) if item
                        )
                        if step.params.get("backup_ids"):
                            first_backup = first_backup or int(step.params["backup_ids"][0])
                except Exception as exc:
                    step.executed = True
                    step.success = False
                    step.message = str(exc)
                    failures.append(f"{step.title}：{exc}")
                    executed.append(step)
                    break
                executed.append(step)
            if progress:
                progress(90, "正在重新扫描并验证修复结果…")
            self.mods.scan_game(view.game_id, record_activity=False)
            after = self._problem_counts(view.game_id)
            verification = (
                f"修复前：{before['missing']} 个缺失依赖，{before['duplicates']} 组重复安装；"
                f"修复后：{after['missing']} 个缺失依赖，{after['duplicates']} 组重复安装。"
            )
            worse = after["missing"] > before["missing"] or after["duplicates"] > before["duplicates"]
            if worse:
                failures.append("验证失败：修复后问题数量增加，正在回滚。")
                self._rollback(reverse_actions, installed_backups)
                status = RepairStatus.ROLLED_BACK.value
            elif failures:
                status = RepairStatus.FAILED.value
            else:
                status = RepairStatus.APPLIED.value
            result_text = "\n".join(
                [
                    view.plan.summary,
                    verification,
                    *(
                        [f"失败：{failure}" for failure in failures]
                        if failures
                        else ["所有已选择的自动步骤执行成功。"]
                    ),
                ]
            )
        except Exception as exc:
            log.exception("修复计划执行异常")
            self._rollback(reverse_actions, installed_backups)
            status = RepairStatus.FAILED.value
            result_text = f"修复执行失败并已尝试回滚：{exc}"
        with self.db.session() as session:
            repo = RepairRepository(session)
            row = repo.get(plan_id)
            if row is None:
                raise RepairError("修复计划不存在。")
            updated = view.plan.model_copy(update={"steps": executed + [
                step for step in view.plan.steps if step.id not in {item.id for item in executed}
            ]})
            repo.update(
                row,
                status=status,
                plan_json=updated.model_dump(mode="json"),
                result=result_text,
                backup_id=first_backup,
                applied_at=datetime.now(UTC).replace(tzinfo=None),
            )
        if progress:
            progress(100, "修复流程完成。")
        self.activity.record(
            f"修复计划执行完成：{status}",
            kind="repair",
            level="warning" if status != RepairStatus.APPLIED.value else "info",
            game_id=view.game_id,
            details={"plan_id": plan_id, "result": result_text},
        )
        return self.get_plan(plan_id)

    def _problem_counts(self, game_id: int) -> dict[str, int]:
        views = self.mods.list_mods(game_id)
        missing = sum(
            1
            for view in views
            if view.is_present and view.enabled and view.missing_dependencies
        )
        groups: dict[str, int] = {}
        for view in views:
            if view.is_present:
                groups[view.unique_id] = groups.get(view.unique_id, 0) + 1
        duplicates = sum(1 for count in groups.values() if count > 1)
        return {"missing": missing, "duplicates": duplicates}

    def _execute_step(self, step: RepairStep, progress=None):  # type: ignore[no-untyped-def]
        params = step.params
        if step.action in ("enable_mod", "disable_mod"):
            mod_id = int(params["mod_id"])
            enabled = step.action == "enable_mod"
            self.mods.set_enabled(mod_id, enabled)
            return (
                f"已{'启用' if enabled else '禁用'} Mod #{mod_id}。",
                ("enable_mod" if not enabled else "disable_mod", {"mod_id": mod_id}),
            )
        if step.action == "install_dependency":
            nexus_mod_id = int(params["nexus_mod_id"])
            unique_id = str(params.get("unique_id") or "")
            backups = self._install_nexus_dependency(
                int(step.params.get("game_id") or 0),
                nexus_mod_id,
                unique_id,
                progress,
            )
            step.params["backup_ids"] = backups
            return f"已安装 Nexus Mod {nexus_mod_id}。", None
        if step.action == "restore_backup":
            backup_id = int(params["backup_id"])
            self.mods.rollback(backup_id)
            return f"已恢复备份 #{backup_id}。", None
        if step.action == "clean_temp":
            removed = self._clean_temp()
            return f"已清理 {removed} 个临时文件/目录。", None
        if step.action == "manual":
            return "手动步骤未自动执行。", None
        raise RepairError(f"不支持的修复动作：{step.action}")

    def _rollback(
        self,
        reverse_actions: list[tuple[str, dict[str, Any]]],
        backup_ids: list[int],
    ) -> None:
        for action, params in reversed(reverse_actions):
            try:
                if action == "enable_mod":
                    self.mods.set_enabled(int(params["mod_id"]), True)
                elif action == "disable_mod":
                    self.mods.set_enabled(int(params["mod_id"]), False)
            except Exception:
                log.exception("回滚 Mod 状态失败：%s", params)
        for backup_id in reversed(backup_ids):
            try:
                self.mods.rollback(backup_id)
            except Exception:
                log.exception("回滚修复备份失败：%s", backup_id)

    def _clean_temp(self) -> int:
        removed = 0
        part_cutoff = time.time() - 3600
        for part in list(self.paths.downloads_dir.rglob("*.part")):
            try:
                if part.stat().st_mtime > part_cutoff:
                    continue
                part.unlink()
                removed += 1
            except OSError:
                continue
        cutoff = time.time() - 24 * 3600
        for directory in list(self.paths.staging_dir.glob("install-*")):
            try:
                if directory.stat().st_mtime < cutoff:
                    shutil.rmtree(directory, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        return removed

    def _install_nexus_dependency(
        self,
        game_id: int,
        nexus_mod_id: int,
        unique_id: str,
        progress=None,  # type: ignore[no-untyped-def]
    ) -> list[int]:
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise RepairError("游戏记录不存在。")
            domain = game.nexus_domain
        if not domain:
            raise RepairError("游戏未配置 Nexus 游戏标识。")
        if progress:
            progress(10, "正在获取依赖下载信息…")
        latest = self.nexus.get_latest_file(domain, nexus_mod_id)
        if latest is None:
            raise RepairError("Nexus 上没有可下载的主文件。")
        try:
            links = self.nexus.get_download_links(domain, nexus_mod_id, latest.file_id)
        except NexusPremiumRequired as exc:
            raise RepairError(
                "该依赖需要 Nexus Premium 才能通过 API 直接下载。"
                "请在 Nexus 网页手动下载后，使用下载中心的“导入压缩包”，或安装完成后重新诊断。"
            ) from exc
        url = links[0]
        filename = sanitize_filename(
            latest.file_name or f"dependency-{nexus_mod_id}-{latest.file_id}.zip",
            fallback=f"dependency-{nexus_mod_id}.zip",
        )
        if "." not in filename:
            filename += ".zip"
        temp_dir = Path(tempfile.mkdtemp(prefix="repair-download-", dir=str(self.paths.staging_dir)))
        archive = temp_dir / filename
        try:
            if progress:
                progress(25, f"正在下载依赖 {latest.name}…")
            with httpx.Client(
                timeout=httpx.Timeout(120.0, connect=20.0),
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client, client.stream("GET", url) as response:
                if response.status_code in (401, 403):
                    raise RepairError("下载被 Nexus 拒绝，请手动下载。")
                if response.status_code >= 400:
                    raise RepairError(f"下载失败：HTTP {response.status_code}")
                with archive.open("wb") as handle:
                    for chunk in response.iter_bytes(256 * 1024):
                        handle.write(chunk)
            if latest.md5:
                digest = hashlib.md5()
                with archive.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest().lower() != latest.md5.lower():
                    raise RepairError("依赖下载文件 MD5 校验失败。")
            if progress:
                progress(70, "正在安装依赖…")
            result = self.mods.install_archive(
                game_id,
                archive,
                allow_replace=True,
                allow_duplicate=True,
                source="nexus",
                progress=(lambda p, msg: progress(70 + int(p * 0.25), msg)) if progress else None,
            )
            if progress:
                progress(97, "正在验证依赖…")
            views = self.mods.list_mods(game_id)
            matches = [
                view
                for view in views
                if view.is_present
                and view.enabled
                and (
                    view.unique_id.casefold() == unique_id.casefold()
                    or not unique_id
                )
            ]
            if unique_id and not matches:
                log.warning("依赖安装后未找到 UniqueID %s，可能压缩包内目录不同。", unique_id)
            return result.backup_ids
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
