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
from ..i18n import tr

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
                raise RepairError(tr('services.repair_service.diagnostic_record_does_not_exist'))
            game = GameRepository(session).get(run.game_id)
            if game is None:
                raise RepairError(tr('services.repair_service.game_record_does_not_exist'))
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
                                    title=tr('services.repair_service.disable_duplicate_copy_0', copy.name),
                                    description=tr('services.repair_service.keep_0_1_and_disable_2_only_folders_are_renamed_no_f_d627c8', keep.relative_path, keep.version, copy.relative_path),
                                    risk=RiskLevel.LOW,
                                    automatic=True,
                                    action="disable_mod",
                                    params={"mod_id": copy.id},
                                    findings=[issue.rule_id or "duplicate"],
                                )
                            )
                        else:
                            warnings.append(
                                tr('services.repair_service.0_has_a_duplicate_copy_1_that_is_already_disabled_yo_57dc69', copy.name, copy.relative_path)
                            )
            elif category == IssueCategory.ENVIRONMENT.value:
                if "SMAPI" in title or "smapi" in (issue.description or "").lower():
                    add_step(
                        RepairStep(
                            id="manual-smapi",
                            title=tr('services.repair_service.install_or_update_smapi'),
                            description=issue.recommendation or tr('services.repair_service.install_smapi_then_run_diagnostics_again'),
                            risk=RiskLevel.MEDIUM,
                            automatic=False,
                            action="manual",
                            params={
                                "url": "https://smapi.io/",
                                "instructions": tr('services.repair_service.download_the_smapi_installer_follow_the_installer_pr_b14321'),
                            },
                            findings=[issue.rule_id or "environment"],
                        )
                    )
                elif tr('services.repair_service.logs') in title:
                    warnings.append(tr('services.repair_service.smapi_log_not_found_cannot_generate_repair_steps_for_4982c9'))
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
                            title=tr('services.repair_service.verify_version_0', view.name),
                            description=(
                                (issue.recommendation or issue.description or tr('services.repair_service.update_to_a_compatible_version'))
                                + tr('services.repair_service.version_upgrades_cannot_be_decided_from_logs_alone_p_9f36b5')
                            ),
                            risk=RiskLevel.MEDIUM,
                            automatic=False,
                            action="manual",
                            params={
                                "url": f"https://www.nexusmods.com/stardewvalley/mods/{view.nexus_mod_id}"
                                if view.nexus_mod_id
                                else "",
                                "instructions": tr('services.repair_service.update_this_mod_in_the_update_center_or_check_the_co_28d06b'),
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
                            title=tr('services.repair_service.reinstall_or_repair_configuration_0', view.name),
                            description=tr('services.repair_service.a_manifest_or_config_file_issue_was_detected_automat_3aba8a'),
                            risk=RiskLevel.MEDIUM,
                            automatic=False,
                            action="manual",
                            params={"instructions": tr('services.repair_service.open_the_mod_folder_and_check_manifest_json_or_reins_34823e')},
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
                    title=tr('services.repair_service.cleaned_up_0_temporary_files', len(removable)),
                    description=tr('services.repair_service.deletes_incomplete_download_chunks_and_install_stagi_32ff02'),
                    risk=RiskLevel.LOW,
                    automatic=True,
                    action="clean_temp",
                    params={"count": len(removable)},
                    findings=["maintenance"],
                )
            )

        steps.sort(key=lambda step: (not step.automatic, step.risk.value))
        summary = tr('services.repair_service.generated_0_repair_steps_1_can_run_automatically_and_5f0336', len(steps), sum(1 for step in steps if step.automatic), sum(1 for step in steps if not step.automatic))
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
            progress(100, tr('services.repair_service.repair_plan_generated'))
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
                            title=tr('services.repair_service.enable_dependencies_0', installed.name),
                            description=tr('services.repair_service.0_is_installed_but_disabled', installed.name),
                            risk=RiskLevel.LOW,
                            automatic=True,
                            action="enable_mod",
                            params={"mod_id": installed.id},
                            findings=[issue.rule_id or "dependency"],
                        )
                    )
                else:
                    warnings.append(
                        tr('services.repair_service.0_is_installed_but_its_version_may_not_meet_the_requ_63bad1', dep_name)
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
                        title=tr('services.repair_service.install_missing_dependencies_0', dep_name),
                        description=tr('services.repair_service.nexus_mod_id_0_was_determined_from_a_link_in_the_log_3168f0', nexus_mod_id),
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
                        title=tr('services.repair_service.install_dependency_manually_0', dep_name),
                        description=tr('services.repair_service.the_log_lists_a_dependency_name_uniqueid_but_no_nexu_192281'),
                        risk=RiskLevel.MEDIUM,
                        automatic=False,
                        action="manual",
                        params={
                            "url": "https://www.nexusmods.com/stardewvalley/search/?gsearch="
                            + dep_name.split(".")[-1],
                            "instructions": tr('services.repair_service.search_for_this_dependency_on_nexus_verify_the_uniqu_f087e3'),
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
                raise RepairError(tr('services.repair_service.repair_plan_does_not_exist'))
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
            raise RepairError(tr('services.repair_service.no_repair_steps_were_selected'))
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
                    progress(int(index / total * 80), tr('services.repair_service.running_0', step.title))
                if not step.automatic:
                    step.executed = False
                    step.success = None
                    step.message = tr('services.repair_service.requires_manual_action_it_was_not_performed_automati_a6ecb5')
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
                progress(90, tr('services.repair_service.rescanning_and_verifying_repair_results'))
            self.mods.scan_game(view.game_id, record_activity=False)
            after = self._problem_counts(view.game_id)
            verification = tr('services.repair_service.before_repair_0_missing_dependencies_1_duplicate_ins_779e55', before['missing'], before['duplicates'], after['missing'], after['duplicates'])
            worse = after["missing"] > before["missing"] or after["duplicates"] > before["duplicates"]
            if worse:
                failures.append(tr('services.repair_service.validation_failed_the_number_of_issues_increased_aft_7b37ec'))
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
                        [tr('services.repair_service.failed_0', failure) for failure in failures]
                        if failures
                        else [tr('services.repair_service.all_selected_automatic_steps_completed_successfully')]
                    ),
                ]
            )
        except Exception as exc:
            log.exception(tr('services.repair_service.repair_plan_execution_error'))
            self._rollback(reverse_actions, installed_backups)
            status = RepairStatus.FAILED.value
            result_text = tr('services.repair_service.repair_failed_and_a_rollback_has_been_attempted_0', exc)
        with self.db.session() as session:
            repo = RepairRepository(session)
            row = repo.get(plan_id)
            if row is None:
                raise RepairError(tr('services.repair_service.repair_plan_does_not_exist'))
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
            progress(100, tr('services.repair_service.repair_process_completed'))
        self.activity.record(
            tr('services.repair_service.repair_plan_completed_0', status),
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
                tr('services.repair_service.0_mod_1', tr('services.repair_service.enable') if enabled else tr('services.repair_service.disable'), mod_id),
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
            return tr('services.repair_service.installed_nexus_mod_0', nexus_mod_id), None
        if step.action == "restore_backup":
            backup_id = int(params["backup_id"])
            self.mods.rollback(backup_id)
            return tr('services.repair_service.restored_backup_0', backup_id), None
        if step.action == "clean_temp":
            removed = self._clean_temp()
            return tr('services.repair_service.cleaned_up_0_temporary_files_directories', removed), None
        if step.action == "manual":
            return tr('services.repair_service.manual_steps_were_not_executed_automatically'), None
        raise RepairError(tr('services.repair_service.unsupported_repair_action_0', step.action))

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
                log.exception(tr('services.repair_service.failed_to_roll_back_mod_status_s'), params)
        for backup_id in reversed(backup_ids):
            try:
                self.mods.rollback(backup_id)
            except Exception:
                log.exception(tr('services.repair_service.failed_to_roll_back_repair_backup_s'), backup_id)

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
                raise RepairError(tr('services.repair_service.game_record_does_not_exist'))
            domain = game.nexus_domain
        if not domain:
            raise RepairError(tr('services.repair_service.the_game_has_no_nexus_game_id_configured'))
        if progress:
            progress(10, tr('services.repair_service.fetching_dependency_download_info'))
        latest = self.nexus.get_latest_file(domain, nexus_mod_id)
        if latest is None:
            raise RepairError(tr('services.repair_service.no_downloadable_main_file_on_nexus'))
        try:
            links = self.nexus.get_download_links(domain, nexus_mod_id, latest.file_id)
        except NexusPremiumRequired as exc:
            raise RepairError(
                tr('services.repair_service.this_dependency_requires_nexus_premium_to_be_downloa_0275eb')
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
                progress(25, tr('services.repair_service.downloading_dependency_0', latest.name))
            with httpx.Client(
                timeout=httpx.Timeout(120.0, connect=20.0),
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client, client.stream("GET", url) as response:
                if response.status_code in (401, 403):
                    raise RepairError(tr('services.repair_service.the_download_was_rejected_by_nexus_please_download_i_b089ce'))
                if response.status_code >= 400:
                    raise RepairError(tr('services.repair_service.download_failed_http_0', response.status_code))
                with archive.open("wb") as handle:
                    for chunk in response.iter_bytes(256 * 1024):
                        handle.write(chunk)
            if latest.md5:
                digest = hashlib.md5()
                with archive.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest().lower() != latest.md5.lower():
                    raise RepairError(tr('services.repair_service.md5_verification_failed_for_the_dependency_download'))
            if progress:
                progress(70, tr('services.repair_service.installing_dependencies'))
            result = self.mods.install_archive(
                game_id,
                archive,
                allow_replace=True,
                allow_duplicate=True,
                source="nexus",
                progress=(lambda p, msg: progress(70 + int(p * 0.25), msg)) if progress else None,
            )
            if progress:
                progress(97, tr('services.repair_service.verifying_dependencies'))
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
                log.warning(tr('services.repair_service.uniqueid_s_was_not_found_after_installing_the_depend_52d307'), unique_id)
            return result.backup_ids
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
