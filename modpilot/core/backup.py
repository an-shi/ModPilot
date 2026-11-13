from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import BackupError
from .paths import assert_within, is_within, sanitize_filename

log = logging.getLogger(__name__)

MANIFEST_NAME = ".modpilot/manifest.json"
MANIFEST_VERSION = 1


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    manifest: dict[str, Any]
    size_bytes: int
    sha256: str


class BackupManager:
    """ZIP based, self-describing backups of files/directories.

    Every archive contains a manifest with the source root, backup kind and the
    exact top-level items included. Restoring validates every path before it
    touches the filesystem and rolls back partially applied restores.
    """

    def __init__(self, backups_dir: Path) -> None:
        self.backups_dir = backups_dir
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        root: Path,
        items: list[Path],
        *,
        kind: str,
        label: str,
        note: str = "",
        destination_dir: Path | None = None,
        extra: dict[str, Any] | None = None,
    ) -> BackupInfo:
        root = root.resolve()
        if not root.exists():
            raise BackupError(f"备份源目录不存在：{root}")
        normalized_items: list[Path] = []
        for item in items:
            resolved = item.resolve()
            try:
                relative = resolved.relative_to(root)
            except ValueError as exc:
                raise BackupError(f"备份项不在源目录中：{resolved}") from exc
            if not relative.parts or relative.name in ("", "."):
                raise BackupError("不能直接备份整个根目录，请指定具体内容。")
            if not resolved.exists():
                raise BackupError(f"备份项不存在：{resolved}")
            normalized_items.append(relative)
        if not normalized_items:
            raise BackupError("没有可备份的内容。")

        destination = (destination_dir or self.backups_dir).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        digest = hashlib.sha1(
            ("|".join(str(p) for p in normalized_items) + utcnow_iso()).encode("utf-8")
        ).hexdigest()[:6]
        filename = f"{stamp}_{sanitize_filename(kind, fallback='backup')}_{sanitize_filename(label, fallback='item')}_{digest}.zip"
        archive_path = destination / filename

        manifest: dict[str, Any] = {
            "manifest_version": MANIFEST_VERSION,
            "created_at": utcnow_iso(),
            "kind": kind,
            "label": label,
            "note": note,
            "root": str(root),
            "items": [p.as_posix() for p in normalized_items],
            "extra": extra or {},
        }

        temp_path = archive_path.with_suffix(".zip.tmp")
        try:
            with zipfile.ZipFile(
                temp_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                archive.writestr(
                    MANIFEST_NAME,
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                for relative in normalized_items:
                    self._add_path(archive, root, relative)
            os.replace(temp_path, archive_path)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            archive_path.unlink(missing_ok=True)
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"创建备份失败：{exc}") from exc

        size = archive_path.stat().st_size
        sha = self._sha256(archive_path)
        log.info("已创建备份：%s（%d bytes）", archive_path, size)
        return BackupInfo(path=archive_path, manifest=manifest, size_bytes=size, sha256=sha)

    def _add_path(self, archive: zipfile.ZipFile, root: Path, relative: Path) -> None:
        source = root / relative
        if source.is_symlink():
            log.warning("备份跳过符号链接：%s", source)
            return
        if source.is_file():
            archive.write(source, relative.as_posix())
            return
        if source.is_dir():
            children = list(source.rglob("*"))
            if not children:
                archive.writestr(f"{relative.as_posix().rstrip('/')}/", b"")
            for child in children:
                if child.is_symlink():
                    log.warning("备份跳过符号链接：%s", child)
                    continue
                child_rel = child.relative_to(root).as_posix()
                if child.is_dir():
                    # Keep empty directories in the archive.
                    if not any(child.iterdir()):
                        archive.writestr(f"{child_rel.rstrip('/')}/", b"")
                elif child.is_file():
                    archive.write(child, child_rel)
            return
        raise BackupError(f"不支持的备份项：{source}")

    def file_sha256(self, path: Path) -> str:
        return self._sha256(Path(path))

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def read_manifest(self, archive_path: Path) -> dict[str, Any]:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                raw = archive.read(MANIFEST_NAME)
                manifest = json.loads(raw.decode("utf-8"))
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError, OSError) as exc:
            raise BackupError(f"无效的 ModPilot 备份文件：{exc}") from exc
        if manifest.get("manifest_version") != MANIFEST_VERSION:
            raise BackupError("备份格式版本不受支持。")
        if not isinstance(manifest.get("items"), list) or not manifest["items"]:
            raise BackupError("备份清单缺少内容。")
        for item in manifest["items"]:
            if not isinstance(item, str) or Path(item).is_absolute() or ".." in Path(item).parts:
                raise BackupError("备份清单包含不安全路径。")
        return manifest

    def inspect(self, archive_path: Path) -> BackupInfo:
        archive_path = Path(archive_path)
        manifest = self.read_manifest(archive_path)
        return BackupInfo(
            path=archive_path,
            manifest=manifest,
            size_bytes=archive_path.stat().st_size,
            sha256=self._sha256(archive_path),
        )

    def restore(self, archive_path: Path, *, root: Path | None = None) -> dict[str, Any]:
        archive_path = Path(archive_path)
        manifest = self.read_manifest(archive_path)
        target_root = (root or Path(manifest["root"])).resolve()
        target_root.mkdir(parents=True, exist_ok=True)

        staging = Path(tempfile.mkdtemp(prefix="modpilot-restore-", dir=str(self.backups_dir)))
        rollback = Path(tempfile.mkdtemp(prefix="modpilot-rollback-", dir=str(self.backups_dir)))
        processed: list[tuple[Path, Path | None]] = []
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for info in archive.infolist():
                    if info.filename == MANIFEST_NAME:
                        continue
                    relative = Path(info.filename.replace("\\", "/"))
                    if relative.is_absolute() or ".." in relative.parts:
                        raise BackupError(f"备份包含不安全路径：{info.filename}")
                    destination = (staging / relative).resolve()
                    if not is_within(destination, staging):
                        raise BackupError(f"备份路径越界：{info.filename}")
                    if info.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, destination.open("wb") as out:
                        shutil.copyfileobj(source, out)

            for item in manifest["items"]:
                relative = Path(item)
                target = (target_root / relative).resolve()
                assert_within(target, target_root, label="恢复目标")
                staged = (staging / relative).resolve()
                assert_within(staged, staging, label="备份内容")
                if not staged.exists() and not target.exists():
                    continue

                old_location: Path | None = None
                if target.exists() or target.is_symlink():
                    old_location = rollback / relative
                    old_location.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(old_location))

                target.parent.mkdir(parents=True, exist_ok=True)
                if staged.exists():
                    shutil.move(str(staged), str(target))
                processed.append((target, old_location))

            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(rollback, ignore_errors=True)
            log.info("已恢复备份：%s -> %s", archive_path, target_root)
            return manifest
        except Exception as exc:
            log.exception("恢复失败，正在回滚：%s", archive_path)
            for target, old_location in reversed(processed):
                try:
                    if target.exists() or target.is_symlink():
                        if target.is_dir() and not target.is_symlink():
                            shutil.rmtree(target, ignore_errors=True)
                        else:
                            target.unlink(missing_ok=True)
                    if old_location and old_location.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(old_location), str(target))
                except OSError:
                    log.exception("回滚备份项失败：%s", target)
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(rollback, ignore_errors=True)
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"恢复备份失败：{exc}") from exc

    def delete(self, archive_path: Path) -> None:
        path = Path(archive_path)
        if is_within(path, self.backups_dir):
            path.unlink(missing_ok=True)
        else:
            raise BackupError("拒绝删除备份目录之外的文件。")
