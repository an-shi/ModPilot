from __future__ import annotations

import logging
import os
import stat
import tarfile
import zipfile
from pathlib import Path

from .errors import ArchiveError
from .paths import is_within

log = logging.getLogger(__name__)

MAX_ARCHIVE_FILES = 100_000
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024 * 1024  # 32 GiB

SUPPORTED_EXTENSIONS = {".zip", ".7z"}
UNSUPPORTED_EXTENSIONS = {".rar": "RAR 压缩包暂不支持，请解压后重新打包为 ZIP 或 7Z。"}


def archive_extension(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".tar.gz"):
        return ".tar.gz"
    return path.suffix.lower()


def ensure_supported_archive(path: Path) -> str:
    ext = archive_extension(path)
    if ext in UNSUPPORTED_EXTENSIONS:
        raise ArchiveError(UNSUPPORTED_EXTENSIONS[ext])
    if ext not in SUPPORTED_EXTENSIONS | {".tar.gz"}:
        raise ArchiveError(f"不支持的压缩包格式：{ext or '未知'}。请使用 ZIP 或 7Z。")
    if not path.is_file():
        raise ArchiveError(f"压缩包不存在：{path}")
    return ext


def _validate_member_name(name: str) -> str:
    cleaned = name.replace("\\", "/").lstrip("/")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(part == ".." for part in parts) or ":" in parts[0]:
        raise ArchiveError(f"压缩包包含不安全的路径：{name}")
    return "/".join(parts)


def _zip_extract(archive_path: Path, destination: Path) -> list[Path]:
    extracted: list[Path] = []
    total = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_FILES:
                raise ArchiveError("压缩包文件数量异常，已停止解压。")
            for info in infos:
                normalized = _validate_member_name(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ArchiveError(f"压缩包包含符号链接，已拒绝：{info.filename}")
                total += info.file_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ArchiveError("压缩包解压后体积过大，已停止解压。")
                target = (destination / normalized).resolve()
                if not is_within(target, destination):
                    raise ArchiveError(f"压缩包路径越界：{info.filename}")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                extracted.append(target)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"ZIP 压缩包损坏：{exc}") from exc
    return extracted


def _seven_zip_extract(archive_path: Path, destination: Path) -> list[Path]:
    try:
        import py7zr
    except ImportError as exc:
        raise ArchiveError("读取 7Z 需要安装 py7zr。") from exc

    extracted: list[Path] = []
    try:
        with py7zr.SevenZipFile(archive_path, mode="r") as archive:
            entries = archive.list()
            if len(entries) > MAX_ARCHIVE_FILES:
                raise ArchiveError("压缩包文件数量异常，已停止解压。")
            total = 0
            for entry in entries:
                normalized = _validate_member_name(entry.filename)
                if getattr(entry, "is_symlink", False):
                    raise ArchiveError(f"压缩包包含符号链接，已拒绝：{entry.filename}")
                total += int(getattr(entry, "uncompressed", 0) or 0)
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ArchiveError("压缩包解压后体积过大，已停止解压。")
                (destination / normalized).resolve()
            archive.extractall(path=destination)
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError(f"7Z 解压失败：{exc}") from exc

    for root, _dirs, files in os.walk(destination):
        for file_name in files:
            file_path = Path(root) / file_name
            if file_path.is_symlink():
                raise ArchiveError(f"压缩包包含符号链接，已拒绝：{file_path.name}")
            extracted.append(file_path)
    return extracted


def _tar_extract(archive_path: Path, destination: Path) -> list[Path]:
    extracted: list[Path] = []
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_FILES:
                raise ArchiveError("压缩包文件数量异常，已停止解压。")
            total = 0
            for member in members:
                normalized = _validate_member_name(member.name)
                if member.issym() or member.islnk():
                    raise ArchiveError(f"压缩包包含链接，已拒绝：{member.name}")
                total += member.size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ArchiveError("压缩包解压后体积过大，已停止解压。")
                target = (destination / normalized).resolve()
                if not is_within(target, destination):
                    raise ArchiveError(f"压缩包路径越界：{member.name}")
                archive.extract(member, path=destination, filter="data")
                if member.isfile():
                    extracted.append(target)
    except ArchiveError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveError(f"TAR 解压失败：{exc}") from exc
    return extracted


def extract_archive(archive_path: Path, destination: Path) -> list[Path]:
    """Extract an archive safely into an empty directory."""

    ext = ensure_supported_archive(archive_path)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ArchiveError("解压目标目录必须为空。")
    if ext == ".zip":
        result = _zip_extract(archive_path, destination)
    elif ext == ".7z":
        result = _seven_zip_extract(archive_path, destination)
    else:
        result = _tar_extract(archive_path, destination)
    if not result and not any(destination.rglob("*")):
        raise ArchiveError("压缩包为空。")
    return result
