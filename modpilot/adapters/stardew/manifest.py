from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ...domain.enums import ModKind
from ...domain.models import ModDependencyInfo, ModMetadata
from ...i18n import tr

log = logging.getLogger(__name__)

NEXUS_UPDATE_KEY = re.compile(r"nexus\s*:\s*(\d+)", re.IGNORECASE)

# Common SMAPI frameworks. This only affects the UI label; dependency handling
# remains entirely metadata-driven.
_KNOWN_FRAMEWORK_IDS = {
    "pathoschild.contentpatcher",
    "pathoschild.automate",
    "spacechase0.jsonassets",
    "spacechase0.spacecore",
    "spacechase0.genericmodconfigmenu",
    "esca.farmtypemanager",
    "cherry.shoptileframework",
    "leclair.bettercrafting",
    "leclair.stardewcommon",
    "leclair.stardewui",
    "selph.idapi",
    "furyx639.letseat",
    "sophie.calcifer",
}


class ManifestError(ValueError):
    pass


def _ci_get(data: dict[str, Any], *names: str, default: Any = None) -> Any:
    lower_map = {str(key).lower(): value for key, value in data.items()}
    for name in names:
        if name in data:
            return data[name]
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return default


def _string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _parse_update_keys(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _parse_dependencies(raw: Any) -> list[ModDependencyInfo]:
    result: list[ModDependencyInfo] = []
    if not isinstance(raw, list):
        return result
    for item in raw:
        if not isinstance(item, dict):
            continue
        unique_id = _string(_ci_get(item, "UniqueID", "UniqueId", "ID"))
        if not unique_id:
            continue
        minimum = _ci_get(item, "MinimumVersion", "MinVersion")
        required = _ci_get(item, "IsRequired", "Required", default=True)
        result.append(
            ModDependencyInfo(
                unique_id=unique_id,
                minimum_version=_string(minimum) or None,
                required=bool(required),
                kind="required" if required else "optional",
            )
        )
    return result


def parse_manifest(manifest_path: Path) -> ModMetadata:
    try:
        raw_text = manifest_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ManifestError(tr('adapters.stardew.manifest.manifest_json_is_not_a_valid_utf_8_file_0', exc)) from exc
    except OSError as exc:
        raise ManifestError(tr('adapters.stardew.manifest.failed_to_read_manifest_json_0', exc)) from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ManifestError(tr('adapters.stardew.manifest.manifest_json_json_syntax_error_line_0_1', exc.lineno, exc.msg)) from exc
    if not isinstance(data, dict):
        raise ManifestError(tr('adapters.stardew.manifest.the_top_level_of_manifest_json_must_be_an_object'))

    unique_id = _string(_ci_get(data, "UniqueID", "UniqueId"))
    name = _string(_ci_get(data, "Name"))
    if not unique_id:
        raise ManifestError(tr('adapters.stardew.manifest.manifest_json_is_missing_uniqueid'))
    if not name:
        raise ManifestError(tr('adapters.stardew.manifest.manifest_json_is_missing_name'))
    if not re.match(r"^[A-Za-z0-9_.\-]+$", unique_id):
        raise ManifestError(tr('adapters.stardew.manifest.uniqueid_contains_characters_that_smapi_does_not_all_44f6dc', unique_id))

    version = _string(_ci_get(data, "Version"), "0.0.0") or "0.0.0"
    author = _string(_ci_get(data, "Author"))
    description = _string(_ci_get(data, "Description"))
    entry_dll = _ci_get(data, "EntryDll", "EntryDLL")
    update_keys = _parse_update_keys(_ci_get(data, "UpdateKeys", "UpdateKey"))
    nexus_mod_id = None
    for update_key in update_keys:
        match = NEXUS_UPDATE_KEY.search(update_key)
        if match:
            nexus_mod_id = int(match.group(1))
            break

    dependencies = _parse_dependencies(_ci_get(data, "Dependencies"))
    content_pack_for: ModDependencyInfo | None = None
    raw_content_pack = _ci_get(data, "ContentPackFor")
    if isinstance(raw_content_pack, dict):
        target_id = _string(_ci_get(raw_content_pack, "UniqueID", "UniqueId"))
        if target_id:
            content_pack_for = ModDependencyInfo(
                unique_id=target_id,
                minimum_version=_string(_ci_get(raw_content_pack, "MinimumVersion")) or None,
                required=True,
                kind="content_pack",
            )
            dependencies.append(content_pack_for)

    if content_pack_for:
        kind = ModKind.CONTENT_PACK
    elif entry_dll:
        kind = ModKind.CODE
    else:
        kind = ModKind.UNKNOWN
    if unique_id.lower() in _KNOWN_FRAMEWORK_IDS:
        kind = ModKind.FRAMEWORK

    return ModMetadata(
        unique_id=unique_id,
        name=name,
        author=author,
        version=version,
        description=description,
        nexus_mod_id=nexus_mod_id,
        dependencies=dependencies,
        update_keys=update_keys,
        minimum_api_version=_string(_ci_get(data, "MinimumApiVersion", "MinimumAPIVersion")) or None,
        content_pack_for=content_pack_for,
        kind=kind,
        manifest_path=manifest_path,
        extra={"raw_manifest": data, "entry_dll": entry_dll},
    )
