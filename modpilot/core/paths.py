from __future__ import annotations

import hashlib
import os
import re
import shutil
import unicodedata
from pathlib import Path

from .errors import ValidationError

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def assert_within(path: Path, root: Path, *, label: str = "路径") -> Path:
    resolved = path.expanduser().resolve()
    if not is_within(resolved, root):
        raise ValidationError(f"{label}超出允许目录：{resolved}")
    return resolved


def safe_child(root: Path, relative: str | Path) -> Path:
    """Join a relative path to *root*, rejecting traversal and absolute paths."""

    relative_path = Path(relative)
    if relative_path.is_absolute() or relative_path.drive:
        raise ValidationError(f"不允许使用绝对路径：{relative}")
    candidate = (root / relative_path).resolve()
    assert_within(candidate, root, label="目标")
    return candidate


def sanitize_filename(name: str, *, fallback: str = "mod", max_length: int = 120) -> str:
    normalized = unicodedata.normalize("NFKC", name).strip().strip(".")
    normalized = _INVALID_FILENAME.sub("_", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = normalized.rstrip(" .")
    if not normalized:
        normalized = fallback
    if normalized.upper() in _WINDOWS_RESERVED:
        normalized = f"_{normalized}"
    if len(normalized) > max_length:
        stem = normalized[:max_length].rstrip(" .")
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:6]
        normalized = f"{stem}~{digest}"
    return normalized


def unique_path(path: Path) -> Path:
    """Return a non-existing sibling path, appending (2), (3) ... if needed."""

    exists = path.exists
    if not exists(path):
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not exists(candidate):
            return candidate
    raise ValidationError(f"无法生成唯一路径：{path}")


def unique_directory(parent: Path, name: str) -> Path:
    target = parent / sanitize_filename(name)
    if not target.exists():
        return target
    for index in range(2, 1000):
        candidate = parent / f"{sanitize_filename(name)} ({index})"
        if not candidate.exists():
            return candidate
    raise ValidationError(f"无法生成唯一目录：{parent / name}")


def human_size(num_bytes: float | None) -> str:
    if not num_bytes or num_bytes < 0:
        return "—"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(content, encoding=encoding, newline="\n")
    os.replace(temp, path)


def copy_tree_atomic(source: Path, destination: Path) -> None:
    """Copy *source* to *destination* using a temporary sibling folder.

    The destination must not exist. If copying fails, the temporary folder is
    removed and no partial install is left behind.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".modpilot-copy-{os.getpid()}-{destination.name}")
    if temp.exists():
        shutil.rmtree(temp, ignore_errors=True)
    try:
        shutil.copytree(source, temp, symlinks=False)
        os.replace(temp, destination)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)
