from __future__ import annotations

import ctypes
import logging
import os
import re
import sys
from pathlib import Path
from ...i18n import tr

log = logging.getLogger(__name__)


def _steam_roots_from_registry() -> list[Path]:
    if sys.platform != "win32":
        return []
    import winreg

    roots: list[Path] = []
    candidates = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, key_path, value_name in candidates:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
                if value:
                    roots.append(Path(str(value)))
        except OSError:
            continue
    return roots


def _steam_library_paths(steam_root: Path) -> list[Path]:
    paths = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf.is_file():
        return paths
    try:
        content = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return paths
    for match in re.finditer(r'"path"\s+"([^"]+)"', content):
        raw = match.group(1).replace("\\\\", "\\")
        try:
            paths.append(Path(raw))
        except (OSError, ValueError):
            continue
    return paths


def _looks_like_game(path: Path) -> bool:
    exe = path / "Stardew Valley.exe"
    exe_alt = path / "StardewValley.exe"
    return (exe.is_file() or exe_alt.is_file()) and (path / "Content").is_dir()


def candidate_installations() -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    for root in _steam_roots_from_registry():
        try:
            for library in _steam_library_paths(root):
                candidates.append((library / "steamapps" / "common" / "Stardew Valley", "Steam"))
        except OSError:
            continue

    env_paths = [
        (Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "steamapps" / "common" / "Stardew Valley", "Steam"),
        (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam" / "steamapps" / "common" / "Stardew Valley", "Steam"),
        (Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "GOG Galaxy" / "Games" / "Stardew Valley", "GOG"),
        (Path("C:/GOG Games/Stardew Valley"), "GOG"),
        (Path("D:/SteamLibrary/steamapps/common/Stardew Valley"), "Steam"),
        (Path("E:/SteamLibrary/steamapps/common/Stardew Valley"), "Steam"),
        (Path("D:/Games/Stardew Valley"), tr('adapters.stardew.paths.manual_install')),
    ]
    candidates.extend(env_paths)

    seen: set[str] = set()
    result: list[tuple[Path, str]] = []
    for path, source in candidates:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen or not _looks_like_game(resolved):
            continue
        seen.add(key)
        result.append((resolved, source))
    return result


def _file_version(path: Path) -> str | None:
    if sys.platform != "win32" or not path.is_file():
        return None

    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
        return None
    pointer = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not ctypes.windll.version.VerQueryValueW(
        buffer, r"\VarFileInfo\Translation", ctypes.byref(pointer), ctypes.byref(length)
    ):
        return None
    if not length.value:
        return None
    # Translation is an array of (language id, code page) pairs. Use the first.
    language_id = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ushort))[0]
    code_page = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ushort))[1]
    sub_block = f"\\StringFileInfo\\{language_id:04x}{code_page:04x}\\FileVersion"
    if not ctypes.windll.version.VerQueryValueW(
        buffer, sub_block, ctypes.byref(pointer), ctypes.byref(length)
    ):
        return None
    raw = ctypes.wstring_at(pointer, length.value).strip()
    match = re.match(r"^(\d+(?:\.\d+){1,3})", raw)
    return match.group(1) if match else raw or None


def game_version(install_path: Path) -> str | None:
    for name in ("Stardew Valley.exe", "StardewValley.exe", "Stardew Valley.dll"):
        version = _file_version(install_path / name)
        if version:
            return version
    return None


def framework_version(install_path: Path) -> str | None:
    for name in ("StardewModdingAPI.exe", "StardewModdingAPI.dll"):
        version = _file_version(install_path / name)
        if version:
            return version
    return None


def logs_directory() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "StardewValley" / "ErrorLogs"
    return None
