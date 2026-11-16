from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ...core.errors import TranslationError
from ...core.paths import atomic_write_text
from ...core.text_tokens import validate_placeholders
from ...domain.models import TranslationApplyResult, TranslationPlan, TranslationUnit
from ...i18n import tr

log = logging.getLogger(__name__)

SOURCE_CANDIDATES = ("default.json", "en.json")
TARGET_FILE = "zh.json"


def _i18n_dir(mod_dir: Path) -> Path:
    return mod_dir / "i18n"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TranslationError(tr('adapters.stardew.translation.0_is_not_a_valid_utf_8_file_1', path.name, exc)) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TranslationError(tr('adapters.stardew.translation.0_json_syntax_error_1', path.name, exc)) from exc
    if not isinstance(data, dict):
        raise TranslationError(tr('adapters.stardew.translation.the_top_level_of_0_must_be_a_json_object', path.name))
    return data


def source_file_for(mod_dir: Path) -> Path | None:
    i18n = _i18n_dir(mod_dir)
    for name in SOURCE_CANDIDATES:
        candidate = i18n / name
        if candidate.is_file():
            return candidate
    return None


def build_plan(mod_dir: Path, language: str = "zh") -> TranslationPlan | None:
    source = source_file_for(mod_dir)
    if source is None:
        return None
    data = _load_json(source)
    units: list[TranslationUnit] = []
    non_string: list[str] = []
    for key, value in data.items():
        if isinstance(value, str):
            if value.strip():
                units.append(TranslationUnit(key=str(key), source_text=value))
        else:
            non_string.append(str(key))
    if not units:
        return None

    target = _i18n_dir(mod_dir) / (TARGET_FILE if language == "zh" else f"{language}.json")
    existing: dict[str, str] = {}
    if target.is_file():
        try:
            existing_data = _load_json(target)
            existing = {
                str(key): value
                for key, value in existing_data.items()
                if isinstance(value, str)
            }
        except TranslationError as exc:
            log.warning(tr('adapters.stardew.translation.the_existing_translation_cannot_be_read_it_will_be_b_8d0c51'), exc)
    return TranslationPlan(
        source_file=source,
        target_file=target,
        units=units,
        existing_translations=existing,
        metadata={
            "format": "smapi-i18n",
            "non_string_keys": non_string,
            "language": language,
        },
    )


def validate(plan: TranslationPlan, translations: dict[str, str]) -> list[str]:
    errors: list[str] = []
    valid_keys = {unit.key for unit in plan.units}
    unknown = sorted(set(translations) - valid_keys)
    if unknown:
        errors.append(tr('adapters.stardew.translation.translation_contains_keys_that_are_not_present_in_th_fb2149') + "、".join(unknown[:10]))
    if not translations:
        errors.append(tr('adapters.stardew.translation.there_are_no_translations_to_write'))
    source_map = {unit.key: unit.source_text for unit in plan.units}
    for key, value in translations.items():
        if key not in source_map:
            continue
        if not isinstance(value, str):
            errors.append(tr('adapters.stardew.translation.translation_for_key_0_is_not_a_string', key))
            continue
        for message in validate_placeholders(source_map[key], value):
            errors.append(f"{key}: {message}")
    return errors


def write(
    plan: TranslationPlan,
    translations: dict[str, str],
    *,
    language: str = "zh",
) -> TranslationApplyResult:
    errors = validate(plan, translations)
    if errors:
        raise TranslationError(tr('adapters.stardew.translation.translation_structure_validation_failed') + "；".join(errors[:8]))
    merged = dict(plan.existing_translations)
    merged.update({key: value for key, value in translations.items() if value.strip()})
    if not merged:
        raise TranslationError(tr('adapters.stardew.translation.the_merged_translation_is_empty_write_canceled'))

    # SMAPI i18n files are flat string maps. Keep unrelated existing keys intact
    # so a partial translation never destroys previously translated text.
    ordered = {unit.key: merged[unit.key] for unit in plan.units if unit.key in merged}
    for key, value in merged.items():
        ordered.setdefault(key, value)
    plan.target_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        plan.target_file,
        json.dumps(ordered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(tr('adapters.stardew.translation.wrote_smapi_translation_file_s_d_entries'), plan.target_file, len(translations))
    return TranslationApplyResult(
        target_file=plan.target_file,
        translated_units=len(translations),
    )


def read_translation_file(path: Path) -> dict[str, str]:
    data = _load_json(path)
    return {str(key): value for key, value in data.items() if isinstance(value, str)}
