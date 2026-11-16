from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ...core.paths import sanitize_filename
from ...domain.enums import LogSeverity
from ...domain.models import (
    EnvironmentCheck,
    GameInstallation,
    GameValidation,
    InstallEntry,
    InstallPlan,
    ModMetadata,
    ParsedLog,
    ScannedMod,
    TranslationApplyResult,
    TranslationPlan,
)
from ..base import AdapterCapabilities, GameAdapter, ProgressCallback
from . import translation as stardew_translation
from .log_parser import SmapiLogParser, find_smapi_logs
from .manifest import ManifestError, parse_manifest
from .paths import candidate_installations, framework_version, game_version
from ...i18n import tr

log = logging.getLogger(__name__)

MAX_SCAN_DEPTH = 7
_SKIP_DIRS = {".git", ".vs", ".idea", "__pycache__", "node_modules"}


class StardewValleyAdapter(GameAdapter):
    key = "stardew_valley"
    name = "adapters.stardew.adapter.stardew_valley"
    description = (
        "adapters.stardew.adapter."
        "smapi_mod_ecosystem_with_manifest_parsing_i18n_local_06a26c"
    )
    nexus_domain = "stardewvalley"
    steam_app_id = "413150"
    capabilities = AdapterCapabilities(
        scan=True,
        install=True,
        enable_disable=True,
        dependencies=True,
        log_parsing=True,
        environment_check=True,
        translation=True,
        conflict_rules=True,
        game_version=True,
    )

    def detect_installations(self) -> list[GameInstallation]:
        installations: list[GameInstallation] = []
        for path, source in candidate_installations():
            validation = self.validate_installation(path)
            installations.append(
                GameInstallation(
                    adapter_key=self.key,
                    path=path,
                    display_name="Stardew Valley",
                    source=source,
                    valid=validation.valid,
                    message=validation.message,
                    game_version=validation.game_version,
                )
            )
        return installations

    def _locate_executable(self, path: Path) -> Path | None:
        for name in ("Stardew Valley.exe", "StardewValley.exe"):
            candidate = path / name
            if candidate.is_file():
                return candidate
        return None

    def validate_installation(self, path: Path) -> GameValidation:
        install_path = Path(path).expanduser().resolve()
        if not install_path.is_dir():
            return GameValidation(
                valid=False,
                message=tr('adapters.stardew.adapter.directory_does_not_exist'),
                install_path=install_path,
            )
        exe = self._locate_executable(install_path)
        if exe is None:
            return GameValidation(
                valid=False,
                message=tr('adapters.stardew.adapter.stardew_valley_exe_not_found_please_select_the_game_a5bffc'),
                install_path=install_path,
            )
        if not (install_path / "Content").is_dir():
            return GameValidation(
                valid=False,
                message=tr('adapters.stardew.adapter.content_directory_not_found_this_does_not_look_like_e1b837'),
                install_path=install_path,
            )
        mods_path = self.resolve_mods_dir(install_path)
        return GameValidation(
            valid=True,
            message=tr('adapters.stardew.adapter.stardew_valley_game_directory_detected'),
            install_path=install_path,
            mods_path=mods_path,
            game_version=self.get_game_version(install_path),
            framework_version=self.get_framework_version(install_path),
        )

    def resolve_mods_dir(self, install_path: Path) -> Path:
        return Path(install_path).expanduser().resolve() / "Mods"

    def get_game_version(self, install_path: Path) -> str | None:
        return game_version(Path(install_path))

    def get_framework_version(self, install_path: Path) -> str | None:
        return framework_version(Path(install_path))

    def is_framework_installed(self, install_path: Path) -> bool:
        path = Path(install_path)
        return (path / "StardewModdingAPI.exe").is_file() or (
            path / "StardewModdingAPI.dll"
        ).is_file()

    def scan_mods(
        self,
        install_path: Path,
        progress: ProgressCallback | None = None,
    ) -> list[ScannedMod]:
        mods_root = self.resolve_mods_dir(install_path)
        if not mods_root.is_dir():
            return []
        discovered: list[ScannedMod] = []
        visited = 0

        for root, dirs, files in os.walk(mods_root, topdown=True, followlinks=False):
            root_path = Path(root)
            try:
                relative = root_path.relative_to(mods_root)
            except ValueError:
                continue
            depth = len(relative.parts)
            if depth > MAX_SCAN_DEPTH:
                dirs[:] = []
                continue

            filtered_dirs: list[str] = []
            for dir_name in dirs:
                if dir_name in _SKIP_DIRS:
                    continue
                full = root_path / dir_name
                if full.is_symlink():
                    continue
                filtered_dirs.append(dir_name)
            dirs[:] = filtered_dirs

            manifest_path = root_path / "manifest.json"
            if not manifest_path.is_file():
                continue

            rel_path = "." if str(relative) == "." else relative.as_posix()
            enabled = not any(part.startswith(".") for part in relative.parts)
            try:
                metadata = parse_manifest(manifest_path)
                size_bytes, file_count = self._measure_directory(root_path)
                discovered.append(
                    ScannedMod(
                        metadata=metadata,
                        directory=root_path,
                        relative_path=rel_path,
                        enabled=enabled,
                        size_bytes=size_bytes,
                        file_count=file_count,
                    )
                )
            except ManifestError as exc:
                fallback = self.metadata_fallback(
                    root_path,
                    error=str(exc),
                    enabled=enabled,
                    relative_path=rel_path,
                )
                discovered.append(fallback)
            visited += 1
            if progress and visited % 5 == 0:
                progress(min(90, 10 + visited * 2), tr('adapters.stardew.adapter.found_0_mods', len(discovered)))
            # Do not prune subdirectories: SMAPI can load nested manifests and we
            # want to expose broken/nested mods rather than silently hide them.

        discovered.sort(key=lambda item: item.metadata.name.casefold())
        if progress:
            progress(100, tr('adapters.stardew.adapter.scan_complete_found_0_mods_in_total', len(discovered)))
        return discovered

    @staticmethod
    def _measure_directory(path: Path, max_files: int = 30_000) -> tuple[int, int]:
        total = 0
        count = 0
        for root, _dirs, files in os.walk(path):
            for file_name in files:
                if count >= max_files:
                    return total, count
                file_path = Path(root) / file_name
                try:
                    total += file_path.stat().st_size
                except OSError:
                    continue
                count += 1
        return total, count

    def plan_install(
        self,
        archive_path: Path,
        staging_dir: Path,
        mods_root: Path,
    ) -> InstallPlan:
        staging = Path(staging_dir).resolve()
        manifest_paths = sorted(
            (path for path in staging.rglob("manifest.json") if path.is_file()),
            key=lambda path: len(path.parts),
        )
        manifest_dirs = [path.parent for path in manifest_paths]
        # Only install the outermost mod roots; everything below is part of the
        # same folder tree and will be discovered again by the scanner.
        top_dirs: list[Path] = []
        for directory in manifest_dirs:
            if any(directory != other and directory.is_relative_to(other) for other in manifest_dirs):
                continue
            top_dirs.append(directory)

        plan = InstallPlan(
            archive_path=Path(archive_path),
            staging_dir=staging,
            mods_root=Path(mods_root),
        )
        used_names: set[str] = set()
        for directory in top_dirs:
            try:
                metadata = parse_manifest(directory / "manifest.json")
            except ManifestError as exc:
                plan.warnings.append(f"{directory.name}: {exc}")
                continue
            if directory == staging:
                suggested = metadata.name or Path(archive_path).stem
            else:
                suggested = directory.name
            base_name = sanitize_filename(suggested, fallback="Mod", max_length=100)
            destination_name = base_name
            counter = 2
            while destination_name.casefold() in used_names:
                destination_name = f"{base_name} ({counter})"
                counter += 1
            used_names.add(destination_name.casefold())
            destination = plan.mods_root / destination_name
            metadata = metadata.model_copy(
                update={"manifest_path": destination / "manifest.json"}
            )
            warnings: list[str] = []
            if destination.exists():
                warnings.append(tr('adapters.stardew.adapter.the_target_folder_already_exists_it_will_be_backed_u_4c47bf'))
            plan.entries.append(
                InstallEntry(
                    source_dir=directory,
                    destination_name=destination_name,
                    metadata=metadata,
                    existing=destination.exists(),
                    warnings=warnings,
                )
            )

        if not plan.entries:
            raise ValueError(
                tr('adapters.stardew.adapter.no_valid_smapi_manifest_json_was_found_in_the_archiv_5cce71')
            )
        return plan

    def set_enabled(self, mods_root: Path, relative_path: str, enabled: bool) -> str:
        root = Path(mods_root).resolve()
        current = (root / relative_path).resolve()
        if not current.is_dir():
            raise FileNotFoundError(tr('adapters.stardew.adapter.mod_folder_does_not_exist_0', relative_path))
        try:
            rel = current.relative_to(root)
        except ValueError as exc:
            raise ValueError(tr('adapters.stardew.adapter.mod_path_is_not_inside_the_mods_directory')) from exc
        parts = list(rel.parts)
        if not parts:
            raise ValueError(tr('adapters.stardew.adapter.enable_disable_cannot_be_applied_to_the_mods_root_fo_0e41cd'))
        leaf = parts[-1]
        parent_disabled = any(part.startswith(".") for part in parts[:-1])
        is_disabled = leaf.startswith(".")

        if enabled and parent_disabled:
            raise ValueError(tr('adapters.stardew.adapter.this_mod_is_inside_a_disabled_parent_folder_enable_t_d478df'))
        if enabled and not is_disabled:
            return rel.as_posix()
        if not enabled and is_disabled:
            return rel.as_posix()

        new_leaf = leaf[1:] if enabled else f".{leaf}"
        if not new_leaf:
            raise ValueError(tr('adapters.stardew.adapter.failed_to_generate_a_valid_folder_name'))
        target = current.with_name(new_leaf)
        if target.exists():
            raise FileExistsError(tr('adapters.stardew.adapter.cannot_rename_target_already_exists_0', target.name))
        os.replace(current, target)
        new_rel = target.relative_to(root).as_posix()
        log.info(tr('adapters.stardew.adapter.toggle_mod_status_s_s'), rel.as_posix(), new_rel)
        return new_rel

    def check_environment(self, install_path: Path) -> list[EnvironmentCheck]:
        path = Path(install_path).resolve()
        checks: list[EnvironmentCheck] = []
        exe = self._locate_executable(path)
        checks.append(
            EnvironmentCheck(
                key="game_executable",
                label=tr('adapters.stardew.adapter.game_executable'),
                ok=exe is not None,
                detail=str(exe) if exe else tr('adapters.stardew.adapter.stardew_valley_exe_not_found'),
                severity=LogSeverity.CRITICAL,
            )
        )
        content = path / "Content"
        checks.append(
            EnvironmentCheck(
                key="content",
                label=tr('adapters.stardew.adapter.game_assets_directory'),
                ok=content.is_dir(),
                detail=str(content) if content.is_dir() else tr('adapters.stardew.adapter.content_directory_not_found'),
                severity=LogSeverity.CRITICAL,
            )
        )
        mods = self.resolve_mods_dir(path)
        checks.append(
            EnvironmentCheck(
                key="mods_dir",
                label=tr('adapters.stardew.adapter.mods_directory'),
                ok=mods.is_dir(),
                detail=str(mods) if mods.is_dir() else tr('adapters.stardew.adapter.not_created_yet_it_will_be_created_automatically_whe_2b833e'),
                severity=LogSeverity.WARNING,
            )
        )
        smapi = self.get_framework_version(path)
        smapi_exe = path / "StardewModdingAPI.exe"
        checks.append(
            EnvironmentCheck(
                key="smapi",
                label="SMAPI",
                ok=smapi_exe.is_file(),
                detail=(
                    (tr('adapters.stardew.adapter.smapi_0_is_installed', smapi) if smapi else tr('adapters.stardew.adapter.smapi_is_installed_version_could_not_be_read'))
                    if smapi_exe.is_file()
                    else tr('adapters.stardew.adapter.stardewmoddingapi_exe_was_not_detected_install_smapi_d63f78')
                ),
                severity=LogSeverity.WARNING,
            )
        )
        writable = False
        detail = tr('adapters.stardew.adapter.mods_directory_has_not_been_created_yet')
        if mods.is_dir():
            probe = mods / ".modpilot-write-test"
            try:
                probe.write_text("test", encoding="utf-8")
                probe.unlink()
                writable = True
                detail = tr('adapters.stardew.adapter.mods_directory_is_writable')
            except OSError as exc:
                detail = tr('adapters.stardew.adapter.mods_directory_is_not_writable_0', exc)
        checks.append(
            EnvironmentCheck(
                key="mods_writable",
                label=tr('adapters.stardew.adapter.mods_write_permission'),
                ok=writable,
                detail=detail,
                severity=LogSeverity.ERROR if mods.is_dir() else LogSeverity.WARNING,
            )
        )
        return checks

    def find_logs(self, install_path: Path) -> list[Path]:
        return find_smapi_logs(Path(install_path))

    def parse_log(self, log_path: Path) -> ParsedLog:
        return SmapiLogParser().parse(log_path)

    def is_translatable(self, metadata: ModMetadata, mod_dir: Path) -> bool:
        return stardew_translation.source_file_for(Path(mod_dir)) is not None

    def is_translation_applied(self, mod_dir: Path, language: str = "zh") -> bool:
        target_name = "zh.json" if language == "zh" else f"{language}.json"
        return (Path(mod_dir) / "i18n" / target_name).is_file()

    def build_translation_plan(self, mod_dir: Path, language: str = "zh") -> TranslationPlan | None:
        return stardew_translation.build_plan(Path(mod_dir), language)

    def validate_translation(
        self,
        plan: TranslationPlan,
        translations: dict[str, str],
    ) -> list[str]:
        return stardew_translation.validate(plan, translations)

    def write_translation(
        self,
        plan: TranslationPlan,
        translations: dict[str, str],
    ) -> TranslationApplyResult:
        language = str(plan.metadata.get("language", "zh"))
        return stardew_translation.write(plan, translations, language=language)

    def load_known_issue_rules(self) -> list[dict[str, object]]:
        rules_path = Path(__file__).parent / "data" / "known_issues.json"
        if not rules_path.is_file():
            return []
        try:
            data = json.loads(rules_path.read_text(encoding="utf-8"))
            rules = data.get("rules", [])
            return [rule for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else []
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(tr('adapters.stardew.adapter.failed_to_read_known_issue_rules_s'), exc)
            return []
