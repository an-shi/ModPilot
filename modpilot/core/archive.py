from __future__ import annotations

import logging
import os
import stat
import tarfile
import zipfile
from pathlib import Path

from .errors import ArchiveError
from .paths import is_within
from ..i18n import tr

log = logging.getLogger(__name__)

MAX_ARCHIVE_FILES = 100_000
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024 * 1024  # 32 GiB

SUPPORTED_EXTENSIONS = {".zip", ".7z"}
UNSUPPORTED_EXTENSIONS = {".rar": tr('core.archive.rar_archives_are_not_supported_yet_extract_and_repac_995ffa')}


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
        raise ArchiveError(tr('core.archive.unsupported_archive_format_0_please_use_zip_or_7z', ext or tr('core.archive.unknown')))
    if not path.is_file():
        raise ArchiveError(tr('core.archive.archive_not_found_0', path))
    return ext


def _validate_member_name(name: str) -> str:
    cleaned = name.replace("\\", "/").lstrip("/")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(part == ".." for part in parts) or ":" in parts[0]:
        raise ArchiveError(tr('core.archive.the_archive_contains_an_unsafe_path_0', name))
    return "/".join(parts)


def _zip_extract(archive_path: Path, destination: Path) -> list[Path]:
    extracted: list[Path] = []
    total = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_FILES:
                raise ArchiveError(tr('core.archive.the_archive_contains_an_abnormal_number_of_files_ext_e7dff0'))
            for info in infos:
                normalized = _validate_member_name(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ArchiveError(tr('core.archive.the_archive_contains_a_symbolic_link_rejected_0', info.filename))
                total += info.file_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ArchiveError(tr('core.archive.the_archive_would_expand_to_an_excessive_size_extrac_260c06'))
                target = (destination / normalized).resolve()
                if not is_within(target, destination):
                    raise ArchiveError(tr('core.archive.archive_path_escapes_the_target_directory_0', info.filename))
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                extracted.append(target)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(tr('core.archive.corrupted_zip_archive_0', exc)) from exc
    return extracted


def _seven_zip_extract(archive_path: Path, destination: Path) -> list[Path]:
    try:
        import py7zr
    except ImportError as exc:
        raise ArchiveError(tr('core.archive.reading_7z_requires_py7zr_to_be_installed')) from exc

    extracted: list[Path] = []
    try:
        with py7zr.SevenZipFile(archive_path, mode="r") as archive:
            entries = archive.list()
            if len(entries) > MAX_ARCHIVE_FILES:
                raise ArchiveError(tr('core.archive.the_archive_contains_an_abnormal_number_of_files_ext_e7dff0'))
            total = 0
            for entry in entries:
                normalized = _validate_member_name(entry.filename)
                if getattr(entry, "is_symlink", False):
                    raise ArchiveError(tr('core.archive.the_archive_contains_a_symbolic_link_rejected_0', entry.filename))
                total += int(getattr(entry, "uncompressed", 0) or 0)
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ArchiveError(tr('core.archive.the_archive_would_expand_to_an_excessive_size_extrac_260c06'))
                (destination / normalized).resolve()
            archive.extractall(path=destination)
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError(tr('core.archive.7z_extraction_failed_0', exc)) from exc

    for root, _dirs, files in os.walk(destination):
        for file_name in files:
            file_path = Path(root) / file_name
            if file_path.is_symlink():
                raise ArchiveError(tr('core.archive.the_archive_contains_a_symbolic_link_rejected_0', file_path.name))
            extracted.append(file_path)
    return extracted


def _tar_extract(archive_path: Path, destination: Path) -> list[Path]:
    extracted: list[Path] = []
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_FILES:
                raise ArchiveError(tr('core.archive.the_archive_contains_an_abnormal_number_of_files_ext_e7dff0'))
            total = 0
            for member in members:
                normalized = _validate_member_name(member.name)
                if member.issym() or member.islnk():
                    raise ArchiveError(tr('core.archive.the_archive_contains_a_link_rejected_0', member.name))
                total += member.size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ArchiveError(tr('core.archive.the_archive_would_expand_to_an_excessive_size_extrac_260c06'))
                target = (destination / normalized).resolve()
                if not is_within(target, destination):
                    raise ArchiveError(tr('core.archive.archive_path_escapes_the_target_directory_0', member.name))
                archive.extract(member, path=destination, filter="data")
                if member.isfile():
                    extracted.append(target)
    except ArchiveError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveError(tr('core.archive.failed_to_extract_tar_0', exc)) from exc
    return extracted


def extract_archive(archive_path: Path, destination: Path) -> list[Path]:
    """Extract an archive safely into an empty directory."""

    ext = ensure_supported_archive(archive_path)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ArchiveError(tr('core.archive.the_extraction_target_directory_must_be_empty'))
    if ext == ".zip":
        result = _zip_extract(archive_path, destination)
    elif ext == ".7z":
        result = _seven_zip_extract(archive_path, destination)
    else:
        result = _tar_extract(archive_path, destination)
    if not result and not any(destination.rglob("*")):
        raise ArchiveError(tr('core.archive.the_archive_is_empty'))
    return result
