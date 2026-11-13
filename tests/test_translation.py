from __future__ import annotations

import json
from pathlib import Path

import pytest

from modpilot.config import LlmConfig
from modpilot.core.errors import LlmError, TranslationError
from modpilot.core.text_tokens import protect_tokens, restore_tokens, validate_placeholders
from modpilot.domain.enums import TranslationOrigin, TranslationStatus
from modpilot.services.translation.engine import TranslationEngine
from tests.conftest import TestEnv, make_zip


class FakeTranslationLlm:
    def __init__(self) -> None:
        self.calls = 0
        self.break_placeholders = False

    def is_configured(self) -> bool:
        return True

    def chat_json(self, messages, max_tokens=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        user = messages[-1]["content"]
        first = user.split("\n\n", 1)[0]
        source = json.loads(first)
        if self.break_placeholders:
            return {key: "没有占位符" for key in source}
        return {key: "译：" + value for key, value in source.items()}


def make_engine(env: TestEnv, fake: FakeTranslationLlm) -> TranslationEngine:
    env.config.update(
        llm=LlmConfig(base_url="https://example.com/v1", model="fake-model", requires_api_key=False)
    )
    return TranslationEngine(
        env.db,
        env.registry,
        env.config,
        env.paths,
        fake,
        env.backups,
        env.activity,
        env.nexus,
    )


def make_translatable(env: TestEnv, folder: str = "Translatable", unique_id: str = "Test.Translate") -> Path:
    return env.make_mod(
        folder,
        unique_id=unique_id,
        i18n={
            "greeting": "Hello {{name}}!",
            "count": "You have {0} apples.",
            "plain": "Good morning.",
        },
    )


def test_placeholder_guard_round_trip() -> None:
    source = "Hello {{name}}, you have {0} items in <color=red>red</color>.\nBye @"
    protected, tokens = protect_tokens(source)
    assert "{{name}}" not in protected
    restored, missing = restore_tokens(protected, tokens)
    assert restored == source
    assert missing == []
    assert validate_placeholders(source, restored) == []
    assert validate_placeholders(source, "Hello, you have items.")


def test_ai_translation_cache_and_manual_edit(env: TestEnv) -> None:
    mod = make_translatable(env)
    env.rescan()
    view = next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Translate")
    fake = FakeTranslationLlm()
    engine = make_engine(env, fake)
    overview = engine.overview(env.game_id)
    item = next(entry for entry in overview if entry.mod_id == view.id)
    assert item.total_units == 3
    assert item.translated_units == 0
    assert not item.applied

    units = engine.get_units(view.id)
    assert len(units) == 3
    assert all(unit.status is TranslationStatus.PENDING for unit in units)

    count = engine.translate_mod(view.id)
    assert count == 3
    assert fake.calls == 1
    target = mod / "i18n" / "zh.json"
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert set(data) == {"greeting", "count", "plain"}
    assert "{{name}}" in data["greeting"]
    assert "{0}" in data["count"]
    assert env.backups.list_for_game(env.game_id, mod_id=view.id)
    units = engine.get_units(view.id)
    assert all(unit.status is TranslationStatus.TRANSLATED for unit in units)

    # Identical source text in another mod should reuse the local cache and not
    # call the AI a second time.
    make_translatable(env, "Another Copy", "Test.Translate.Copy")
    env.rescan()
    copy_view = next(
        view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.Translate.Copy"
    )
    count = engine.translate_mod(copy_view.id)
    assert count == 3
    assert fake.calls == 1
    copy_target = env.mods_root / "Another Copy" / "i18n" / "zh.json"
    assert copy_target.is_file()

    # Manual edit + confirmation updates the file and human cache.
    unit = engine.get_units(view.id)[0]
    result = engine.save_unit(unit.id, "你好 {{name}}！", confirmed=True)
    assert result.translated_units >= 1
    updated = next(item for item in engine.get_units(view.id) if item.id == unit.id)
    assert updated.confirmed
    assert updated.origin is TranslationOrigin.HUMAN
    detailed = next(item for item in engine.get_units(view.id) if item.origin is TranslationOrigin.HUMAN)
    assert detailed.confirmed

    with pytest.raises(TranslationError):
        engine.save_unit(detailed.id, "缺少占位符", confirmed=True)

    export_path = engine.export_units(view.id, env.paths.exports_dir / "translation.json")
    exported = json.loads(Path(export_path).read_text(encoding="utf-8"))
    assert exported["units"]
    assert exported["language"] == "zh"

    # Removing the generated file must not touch the original i18n source.
    engine.remove_translation(view.id)
    assert not target.exists()
    assert (mod / "i18n" / "default.json").is_file()


def test_ai_placeholder_failure_does_not_write(env: TestEnv) -> None:
    mod = make_translatable(env, "Broken", "Test.BrokenTranslation")
    env.rescan()
    view = next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.BrokenTranslation")
    fake = FakeTranslationLlm()
    fake.break_placeholders = True
    engine = make_engine(env, fake)
    with pytest.raises(LlmError):
        engine.translate_mod(view.id)
    assert not (mod / "i18n" / "zh.json").exists()
    units = engine.get_units(view.id)
    assert any(unit.status is TranslationStatus.FAILED for unit in units)


def test_import_translation_pack(env: TestEnv) -> None:
    mod = make_translatable(env, "PackTarget", "Test.PackTarget")
    env.rescan()
    view = next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.PackTarget")
    fake = FakeTranslationLlm()
    engine = make_engine(env, fake)
    archive = make_zip(
        env.paths.staging_dir / "pack.zip",
        {
            "i18n/zh.json": json.dumps(
                {
                    "greeting": "你好 {{name}}！",
                    "count": "你有 {0} 个苹果。",
                    "plain": "早上好。",
                    "unknown": "忽略",
                },
                ensure_ascii=False,
            )
        },
    )
    result = engine.import_pack_archive(view.id, archive)
    assert result.translated_units == 3
    assert result.warnings
    target = mod / "i18n" / "zh.json"
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "unknown" not in data
    units = engine.get_units(view.id)
    assert any(unit.origin is TranslationOrigin.EXISTING for unit in units)


def test_import_translation_pack_rejects_bad_placeholders(env: TestEnv) -> None:
    make_translatable(env, "BadPack", "Test.BadPack")
    env.rescan()
    view = next(view for view in env.mods.list_mods(env.game_id) if view.unique_id == "Test.BadPack")
    engine = make_engine(env, FakeTranslationLlm())
    archive = make_zip(
        env.paths.staging_dir / "badpack.zip",
        {
            "i18n/zh.json": json.dumps(
                {"greeting": "你好！", "count": "你有苹果。", "plain": "早上好。"},
                ensure_ascii=False,
            )
        },
    )
    with pytest.raises(TranslationError, match="校验"):
        engine.import_pack_archive(view.id, archive)
