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
                raise KeyError("配置档不存在。")
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
            raise ValueError("配置档名称不能为空。")
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError("游戏未添加。")
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
                raise ValueError(f"配置档“{name}”已存在。")
            if profile is None:
                profile = repo.create(game_id, name, description)
            else:
                profile.description = description
            repo.replace_entries(profile, entries)
            profile_id = profile.id
            repo.set_active(game_id, profile_id)
            view = self._view(session, profile)
        self.activity.record(
            f"已创建配置档：{name}（{len(entries)} 个 Mod）",
            kind="profile_create",
            game_id=game_id,
        )
        return view

    def get_snapshot(self, profile_id: int) -> ProfileSnapshot:
        with self.db.session() as session:
            repo = ProfileRepository(session)
            profile = repo.get(profile_id)
            if profile is None:
                raise KeyError("配置档不存在。")
            game = GameRepository(session).get(profile.game_id)
            entries = repo.entries(profile_id)
            return ProfileSnapshot(
                game_key=game.adapter_key if game else "",
                game_name=game.name if game else "",
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
                raise KeyError("配置档不存在。")
            game = GameRepository(session).get(profile.game_id)
            if game is None:
                raise KeyError("游戏未添加。")
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
                    f"{entry.name}：配置档记录 {entry.version}，当前安装 {current_mod.version}"
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
                    progress(int(index / max(1, len(toggle_order)) * 95), f"正在切换 {mod.name}…")
                try:
                    self.mods.set_enabled(mod.id, enabled)
                    (result.enabled if enabled else result.disabled).append(mod.name)
                except Exception as exc:
                    result.warnings.append(f"{mod.name} 切换失败：{exc}")
                # set_enabled renames folders and updates rows; refresh the current
                # map lazily only for nested paths, so IDs remain valid.
        finally:
            if progress:
                progress(100, "配置档应用完成。")

        result.missing = missing
        result.warnings.extend(version_warnings)
        if missing:
            result.warnings.append(
                "有配置档条目未安装，已跳过。导入配置档不会自动安装缺失 Mod。"
            )
        with self.db.session() as session:
            repo = ProfileRepository(session)
            if not result.warnings or (result.enabled or result.disabled):
                repo.set_active(game_id, profile_id)
        self.activity.record(
            f"已应用配置档：{profile_name}",
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
            raise ValueError(f"无法读取配置档文件：{exc}") from exc
        try:
            snapshot = ProfileSnapshot.model_validate(raw)
        except Exception as exc:
            raise ValueError(f"配置档格式无效：{exc}") from exc
        unique_ids = [entry.unique_id.strip() for entry in snapshot.entries]
        if any(not unique_id for unique_id in unique_ids):
            raise ValueError("配置档包含空的 UniqueID。")
        if len(unique_ids) != len(set(unique_ids)):
            raise ValueError("配置档包含重复的 UniqueID。")
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise KeyError("游戏未添加。")
            if snapshot.game_key and snapshot.game_key != game.adapter_key:
                raise ValueError(
                    f"配置档属于 {snapshot.game_name or snapshot.game_key}，不能导入到 {game.name}。"
                )
            base_name = (
                name or snapshot.profile_name or f"{snapshot.game_name} 导入档"
            ).strip() or "导入档"
            repo = ProfileRepository(session)
            candidate = base_name
            counter = 2
            while repo.get_by_name(game_id, candidate):
                candidate = f"{base_name} ({counter})"
                counter += 1
            profile = repo.create(
                game_id,
                candidate,
                description="从 JSON 配置档导入",
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
            f"已导入配置档：{view.name}（{view.mod_count} 个 Mod）",
            kind="profile_import",
            game_id=game_id,
        )
        return view

    def rename(self, profile_id: int, name: str, description: str | None = None) -> None:
        name = name.strip()
        if not name:
            raise ValueError("配置档名称不能为空。")
        with self.db.session() as session:
            repo = ProfileRepository(session)
            profile = repo.get(profile_id)
            if profile is None:
                raise KeyError("配置档不存在。")
            existing = repo.get_by_name(profile.game_id, name)
            if existing and existing.id != profile_id:
                raise ValueError("同名配置档已存在。")
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
            "已删除配置档",
            kind="profile_delete",
            level="warning",
            game_id=game_id,
        )

    def duplicate(self, profile_id: int, name: str) -> ProfileView:
        snapshot = self.get_snapshot(profile_id)
        if not name.strip():
            raise ValueError("配置档名称不能为空。")
        with self.db.session() as session:
            profile = ProfileRepository(session).get(profile_id)
            if profile is None:
                raise KeyError("配置档不存在。")
            game_id = profile.game_id
            repo = ProfileRepository(session)
            if repo.get_by_name(game_id, name.strip()):
                raise ValueError("同名配置档已存在。")
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
