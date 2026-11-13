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

log = logging.getLogger(__name__)

MAX_SCAN_DEPTH = 7
_SKIP_DIRS = {".git", ".vs", ".idea", "__pycache__", "node_modules"}


class StardewValleyAdapter(GameAdapter):
    key = "stardew_valley"
    name = "星露谷物语"
    description = "SMAPI Mod 生态，支持 manifest 解析、i18n 汉化与 SMAPI 日志诊断。"
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
                message="目录不存在。",
                install_path=install_path,
            )
        exe = self._locate_executable(install_path)
        if exe is None:
            return GameValidation(
                valid=False,
                message="未找到 Stardew Valley.exe，请选择游戏安装根目录。",
                install_path=install_path,
            )
        if not (install_path / "Content").is_dir():
            return GameValidation(
                valid=False,
                message="未找到 Content 目录，这不像是完整的游戏安装目录。",
                install_path=install_path,
            )
        mods_path = self.resolve_mods_dir(install_path)
        return GameValidation(
            valid=True,
            message="已识别 Stardew Valley 游戏目录。",
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
                progress(min(90, 10 + visited * 2), f"已发现 {len(discovered)} 个 Mod…")
            # Do not prune subdirectories: SMAPI can load nested manifests and we
            # want to expose broken/nested mods rather than silently hide them.

        discovered.sort(key=lambda item: item.metadata.name.casefold())
        if progress:
            progress(100, f"扫描完成，共发现 {len(discovered)} 个 Mod。")
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
                warnings.append("目标文件夹已存在，安装前会自动备份并替换。")
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
                "压缩包中没有找到有效的 SMAPI manifest.json，无法作为星露谷物语 Mod 安装。"
            )
        return plan

    def set_enabled(self, mods_root: Path, relative_path: str, enabled: bool) -> str:
        root = Path(mods_root).resolve()
        current = (root / relative_path).resolve()
        if not current.is_dir():
            raise FileNotFoundError(f"Mod 文件夹不存在：{relative_path}")
        try:
            rel = current.relative_to(root)
        except ValueError as exc:
            raise ValueError("Mod 路径不在 Mods 目录中。") from exc
        parts = list(rel.parts)
        if not parts:
            raise ValueError("不能对 Mods 根目录执行启用/禁用。")
        leaf = parts[-1]
        parent_disabled = any(part.startswith(".") for part in parts[:-1])
        is_disabled = leaf.startswith(".")

        if enabled and parent_disabled:
            raise ValueError("该 Mod 位于已禁用的父文件夹中，请先启用父文件夹。")
        if enabled and not is_disabled:
            return rel.as_posix()
        if not enabled and is_disabled:
            return rel.as_posix()

        new_leaf = leaf[1:] if enabled else f".{leaf}"
        if not new_leaf:
            raise ValueError("无法生成有效的文件夹名称。")
        target = current.with_name(new_leaf)
        if target.exists():
            raise FileExistsError(f"无法重命名：目标已存在 {target.name}")
        os.replace(current, target)
        new_rel = target.relative_to(root).as_posix()
        log.info("切换 Mod 状态：%s -> %s", rel.as_posix(), new_rel)
        return new_rel

    def check_environment(self, install_path: Path) -> list[EnvironmentCheck]:
        path = Path(install_path).resolve()
        checks: list[EnvironmentCheck] = []
        exe = self._locate_executable(path)
        checks.append(
            EnvironmentCheck(
                key="game_executable",
                label="游戏主程序",
                ok=exe is not None,
                detail=str(exe) if exe else "未找到 Stardew Valley.exe",
                severity=LogSeverity.CRITICAL,
            )
        )
        content = path / "Content"
        checks.append(
            EnvironmentCheck(
                key="content",
                label="游戏资源目录",
                ok=content.is_dir(),
                detail=str(content) if content.is_dir() else "未找到 Content 目录",
                severity=LogSeverity.CRITICAL,
            )
        )
        mods = self.resolve_mods_dir(path)
        checks.append(
            EnvironmentCheck(
                key="mods_dir",
                label="Mods 目录",
                ok=mods.is_dir(),
                detail=str(mods) if mods.is_dir() else "尚未创建，安装第一个 Mod 时会自动创建。",
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
                    (f"SMAPI {smapi} 已安装。" if smapi else "SMAPI 已安装（无法读取版本号）。")
                    if smapi_exe.is_file()
                    else "未检测到 StardewModdingAPI.exe。请先安装 SMAPI，否则 Mod 不会加载。"
                ),
                severity=LogSeverity.WARNING,
            )
        )
        writable = False
        detail = "Mods 目录尚未创建。"
        if mods.is_dir():
            probe = mods / ".modpilot-write-test"
            try:
                probe.write_text("test", encoding="utf-8")
                probe.unlink()
                writable = True
                detail = "Mods 目录可写。"
            except OSError as exc:
                detail = f"Mods 目录不可写：{exc}"
        checks.append(
            EnvironmentCheck(
                key="mods_writable",
                label="Mods 写入权限",
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
            log.warning("读取已知问题规则失败：%s", exc)
            return []
