from __future__ import annotations

import ast
import json
import re
import string
from pathlib import Path

from modpilot.config import AppConfig
from modpilot.i18n import (
    DEFAULT_LANGUAGE,
    FALLBACK_LANGUAGE,
    available_languages,
    current_language,
    set_language,
    tr,
    tr_or,
)

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "modpilot" / "locales"
CJK = re.compile(r"[\u4e00-\u9fff]")


def catalog(language: str) -> dict[str, str]:
    data = json.loads(
        (LOCALES / f"{language.replace('-', '_')}.json").read_text(encoding="utf-8")
    )
    return data["translations"]


def fields(value: str) -> list[tuple[str, str | None, str]]:
    result = []
    for _, field, spec, conversion in string.Formatter().parse(value):
        if field is not None:
            result.append((field, conversion, spec))
    return result


def source_keys() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT / "modpilot").rglob("*.py"):
        if path.name == "i18n.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
            ):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.add(arg.value)
    return keys


def test_default_language_is_chinese() -> None:
    assert DEFAULT_LANGUAGE == "zh-CN"
    assert FALLBACK_LANGUAGE == "en-US"
    assert AppConfig().ui_language == "zh-CN"
    assert available_languages()["zh-CN"] == "简体中文"
    try:
        set_language("zh-CN")
        assert current_language() == "zh-CN"
        assert tr("ui.pages.settings.settings") == "设置"
        assert tr("ui.dialogs.cancel") == "取消"
    finally:
        set_language("zh-CN")


def test_english_translation_and_fallback() -> None:
    try:
        set_language("en-US")
        assert current_language() == "en-US"
        assert tr("ui.pages.settings.settings") == "Settings"
        assert tr("ui.dialogs.cancel") == "Cancel"
        assert tr(
            "ui.pages.downloads.retrieved_0_files_nexus_api_remaining_quota_1",
            3,
            99,
        ) == "Retrieved 3 files. Nexus API remaining quota: 99"
        # Missing message IDs are never silently replaced with another language.
        assert tr("__not_a_translation_key__") == "__not_a_translation_key__"
        assert tr_or("__not_a_translation_key__", "fallback") == "fallback"
        assert set_language("invalid-language") == "zh-CN"
        assert tr("ui.dialogs.cancel") == "取消"
    finally:
        set_language("zh-CN")


def test_service_and_adapter_labels_follow_language() -> None:
    try:
        set_language("en-US")
        from modpilot.adapters.stardew.adapter import StardewValleyAdapter
        from modpilot.domain.enums import ModKind, UpdatePolicy

        adapter = StardewValleyAdapter()
        assert adapter.display_name == "Stardew Valley"
        assert adapter.info().name == "Stardew Valley"
        assert ModKind.CODE.label == "Code Mod"
        assert UpdatePolicy.NOTIFY.label == "Update alerts"
        assert tr("adapters.stardew.adapter.game_executable") == "Game executable"
    finally:
        set_language("zh-CN")


def test_catalogs_are_complete_and_consistent() -> None:
    zh = catalog("zh-CN")
    en = catalog("en-US")
    assert set(zh) == set(en)
    keys = source_keys()
    missing_zh = sorted(keys - set(zh))
    missing_en = sorted(keys - set(en))
    assert not missing_zh, f"missing Chinese keys: {missing_zh[:10]}"
    assert not missing_en, f"missing English keys: {missing_en[:10]}"
    for key in keys:
        assert zh[key].strip(), key
        assert en[key].strip(), key
        assert fields(zh[key]) == fields(en[key]), key
    # English UI must never contain leftover Chinese text.
    leftover = [key for key, value in en.items() if CJK.search(value)]
    assert not leftover, f"Chinese text left in en_US.json: {leftover[:10]}"


def test_tr_call_arguments_match_catalog_placeholders() -> None:
    zh = catalog("zh-CN")
    offenders: list[str] = []
    for path in (ROOT / "modpilot").rglob("*.py"):
        if path.name == "i18n.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            key = node.args[0].value
            template = zh.get(key)
            if template is None:
                continue
            indices = [
                int(field)
                for field, _, _ in fields(template)
                if field.isdigit()
            ]
            needed = max(indices) + 1 if indices else 0
            provided = len(node.args) - 1
            if provided < needed:
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} {key} needs {needed}, got {provided}"
                )
    assert not offenders, offenders[:10]


def test_source_code_has_no_hardcoded_chinese_ui_text() -> None:
    offenders: list[str] = []
    for path in (ROOT / "modpilot").rglob("*.py"):
        if path.name == "i18n.py":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and CJK.search(node.value)
                and id(node) not in docstrings
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, f"hardcoded Chinese string literals: {offenders[:10]}"
