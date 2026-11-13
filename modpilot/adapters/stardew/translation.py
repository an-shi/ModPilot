from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ...core.errors import TranslationError
from ...core.paths import atomic_write_text
from ...core.text_tokens import validate_placeholders
from ...domain.models import TranslationApplyResult, TranslationPlan, TranslationUnit

log = logging.getLogger(__name__)

SOURCE_CANDIDATES = ("default.json", "en.json")
TARGET_FILE = "zh.json"


def _i18n_dir(mod_dir: Path) -> Path:
    return mod_dir / "i18n"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TranslationError(f"{path.name} 不是有效的 UTF-8 文件：{exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TranslationError(f"{path.name} JSON 语法错误：{exc}") from exc
    if not isinstance(data, dict):
        raise TranslationError(f"{path.name} 的顶层必须是 JSON 对象。")
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
            log.warning("已有译文无法读取，将先备份再覆盖：%s", exc)
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
        errors.append("译文包含源文件中不存在的键：" + "、".join(unknown[:10]))
    if not translations:
        errors.append("没有可写入的译文。")
    source_map = {unit.key: unit.source_text for unit in plan.units}
    for key, value in translations.items():
        if key not in source_map:
            continue
        if not isinstance(value, str):
            errors.append(f"键 {key} 的译文不是字符串")
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
        raise TranslationError("译文结构校验失败：" + "；".join(errors[:8]))
    merged = dict(plan.existing_translations)
    merged.update({key: value for key, value in translations.items() if value.strip()})
    if not merged:
        raise TranslationError("合并后的译文为空，已取消写入。")

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
    log.info("已写入 SMAPI 汉化文件：%s（%d 条）", plan.target_file, len(translations))
    return TranslationApplyResult(
        target_file=plan.target_file,
        translated_units=len(translations),
    )


def read_translation_file(path: Path) -> dict[str, str]:
    data = _load_json(path)
    return {str(key): value for key, value in data.items() if isinstance(value, str)}
