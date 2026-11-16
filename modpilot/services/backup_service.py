from __future__ import annotations

import logging
from pathlib import Path

from ..config import AppPaths
from ..core.backup import BackupManager
from ..core.errors import BackupError
from ..data.db import Database
from ..data.models import utcnow
from ..data.repositories import BackupRepository, GameRepository
from ..domain.enums import BackupKind
from ..domain.models import BackupView
from .activity import ActivityService
from ..i18n import tr

log = logging.getLogger(__name__)


class BackupService:
    def __init__(self, db: Database, paths: AppPaths, activity: ActivityService) -> None:
        self.db = db
        self.paths = paths
        self.manager = BackupManager(paths.backups_dir)
        self.activity = activity

    def create(
        self,
        *,
        game_id: int,
        root: Path,
        items: list[Path],
        kind: BackupKind | str,
        label: str,
        note: str = "",
        mod_id: int | None = None,
        extra: dict | None = None,
    ) -> BackupView:
        kind_value = str(kind)
        metadata_extra = dict(extra or {})
        metadata_extra["restore_root"] = str(Path(root).resolve())
        info = self.manager.create(
            Path(root),
            items,
            kind=kind_value,
            label=label,
            note=note,
            extra=metadata_extra,
        )
        with self.db.session() as session:
            row = BackupRepository(session).add(
                game_id=game_id,
                mod_id=mod_id,
                kind=kind_value,
                label=label,
                note=note,
                path=str(info.path),
                size_bytes=info.size_bytes,
                sha256=info.sha256,
                status="available",
                metadata_json=info.manifest,
            )
            backup_id = row.id
        self.activity.record(
            tr('services.backup_service.created_backup_0', label),
            kind="backup",
            game_id=game_id,
            details={"backup_id": backup_id, "path": str(info.path)},
        )
        return self.get(backup_id)

    def get(self, backup_id: int) -> BackupView:
        with self.db.session() as session:
            row = BackupRepository(session).get(backup_id)
            if row is None:
                raise BackupError(tr('services.backup_service.the_backup_record_does_not_exist'))
            return self._to_view(row)

    def list_for_game(self, game_id: int, mod_id: int | None = None) -> list[BackupView]:
        with self.db.session() as session:
            rows = BackupRepository(session).list_for_game(game_id, mod_id=mod_id)
            return [self._to_view(row) for row in rows]

    def restore(self, backup_id: int) -> BackupView:
        with self.db.session() as session:
            row = BackupRepository(session).get(backup_id)
            if row is None:
                raise BackupError(tr('services.backup_service.the_backup_record_does_not_exist'))
            game = GameRepository(session).get(row.game_id)
            if game is None:
                raise BackupError(tr('services.backup_service.the_game_associated_with_this_backup_no_longer_exist_4fd94b'))
            archive_path = Path(row.path)
            manifest = self.manager.read_manifest(archive_path)
            restore_root_raw = (row.metadata_json or {}).get("restore_root") or manifest.get("root")
            if not restore_root_raw:
                raise BackupError(tr('services.backup_service.the_backup_is_missing_restore_directory_information'))
            restore_root = Path(str(restore_root_raw)).resolve()
            install_root = Path(game.install_path).resolve()
            try:
                restore_root.relative_to(install_root)
            except ValueError as exc:
                raise BackupError(tr('services.backup_service.the_backup_s_restore_directory_is_not_inside_the_gam_23a6e6')) from exc
            if not archive_path.is_file():
                raise BackupError(tr('services.backup_service.the_backup_file_is_missing'))
            if row.sha256:
                actual = self.manager.file_sha256(archive_path)
                if actual.lower() != row.sha256.lower():
                    raise BackupError(
                        tr('services.backup_service.backup_file_integrity_check_failed_the_file_may_be_c_f48263')
                    )

            # Safety net for the restore operation itself.
            existing_items = []
            for item in manifest.get("items", []):
                candidate = (restore_root / str(item)).resolve()
                try:
                    candidate.relative_to(install_root)
                except ValueError as exc:
                    raise BackupError(tr('services.backup_service.the_backup_manifest_contains_paths_outside_the_game_c59906')) from exc
                if candidate.exists():
                    existing_items.append(candidate)
            safety_id: int | None = None
            if existing_items:
                safety = self.manager.create(
                    restore_root,
                    existing_items,
                    kind=BackupKind.MANUAL.value,
                    label=tr('services.backup_service.automatic_backup_before_restore_0', backup_id),
                    note=tr('services.backup_service.safety_backup_before_restore'),
                    extra={"restore_root": str(restore_root), "safety_for": backup_id},
                )
                safety_row = BackupRepository(session).add(
                    game_id=row.game_id,
                    mod_id=row.mod_id,
                    kind=BackupKind.MANUAL.value,
                    label=tr('services.backup_service.automatic_backup_before_restore_0', backup_id),
                    note=tr('services.backup_service.safety_backup_before_restore'),
                    path=str(safety.path),
                    size_bytes=safety.size_bytes,
                    sha256=safety.sha256,
                    status="available",
                    metadata_json=safety.manifest,
                )
                safety_id = safety_row.id

            self.manager.restore(archive_path, root=restore_root)
            BackupRepository(session).update(
                row,
                status="restored",
                restored_at=utcnow(),
            )
            session.flush()
            view = self._to_view(row)
        self.activity.record(
            tr('services.backup_service.restored_backup_0', view.label),
            kind="backup_restore",
            level="warning",
            game_id=view.game_id,
            details={"backup_id": backup_id, "safety_backup_id": safety_id},
        )
        return view

    def delete(self, backup_id: int, *, delete_file: bool = True) -> None:
        with self.db.session() as session:
            row = BackupRepository(session).get(backup_id)
            if row is None:
                raise BackupError(tr('services.backup_service.the_backup_record_does_not_exist'))
            if delete_file:
                try:
                    self.manager.delete(Path(row.path))
                except OSError as exc:
                    raise BackupError(tr('services.backup_service.failed_to_delete_backup_file_0', exc)) from exc
            session.delete(row)

    @staticmethod
    def _to_view(row) -> BackupView:  # type: ignore[no-untyped-def]
        try:
            kind = BackupKind(row.kind)
        except ValueError:
            kind = BackupKind.MANUAL
        return BackupView(
            id=row.id,
            game_id=row.game_id,
            mod_id=row.mod_id,
            kind=kind,
            label=row.label,
            note=row.note,
            path=row.path,
            size_bytes=row.size_bytes,
            status=row.status,
            created_at=row.created_at,
            restored_at=row.restored_at,
        )
