from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ...adapters.base import AdapterRegistry
from ...config import AppPaths, ConfigStore
from ...core.archive import extract_archive
from ...core.errors import LlmError, TranslationError
from ...core.paths import atomic_write_text
from ...core.text_tokens import (
    contains_markers,
    protect_tokens,
    restore_tokens,
    validate_placeholders,
)
from ...data.db import Database
from ...data.models import Mod, TranslationRecord
from ...data.repositories import GameRepository, ModRepository, TranslationRepository
from ...domain.enums import TranslationOrigin, TranslationStatus
from ...domain.models import (
    TranslationApplyResult,
    TranslationCandidate,
    TranslationOverview,
    TranslationPackSearchResult,
    TranslationPlan,
    TranslationUnitView,
)
from ...version import USER_AGENT
from ..activity import ActivityService
from ..backup_service import BackupService
from ..nexus.client import NexusClient
from .llm import LlmClient

log = logging.getLogger(__name__)

BATCH_CHAR_LIMIT = 6000
GITHUB_API = "https://api.github.com/search/repositories"


@dataclass(frozen=True)
class _UnitSnapshot:
    id: int
    key: str
    source_text: str


class TranslationEngine:
    def __init__(
        self,
        db: Database,
        registry: AdapterRegistry,
        config: ConfigStore,
        paths: AppPaths,
        llm: LlmClient,
        backups: BackupService,
        activity: ActivityService,
        nexus: NexusClient,
    ) -> None:
        self.db = db
        self.registry = registry
        self.config = config
        self.paths = paths
        self.llm = llm
        self.backups = backups
        self.activity = activity
        self.nexus = nexus

    # ------------------------------------------------------------------
    # Context / overview
    # ------------------------------------------------------------------

    def _load_context(self, mod_id: int):
        with self.db.session() as session:
            mod = ModRepository(session).get(mod_id)
            if mod is None:
                raise TranslationError("Mod 不存在。")
            game = GameRepository(session).get(mod.game_id)
            if game is None:
                raise TranslationError("游戏记录不存在。")
            adapter = self.registry.get(game.adapter_key)
            mod_dir = (Path(game.mods_path) / mod.relative_path).resolve()
            if not mod_dir.is_dir():
                raise TranslationError("Mod 文件夹不存在，请重新扫描。")
            plan = adapter.build_translation_plan(mod_dir)
            if plan is None:
                raise TranslationError("该 Mod 没有可翻译的标准语言文件。")
            return mod, game, adapter, mod_dir, plan

    def overview(self, game_id: int, language: str = "zh") -> list[TranslationOverview]:
        with self.db.session() as session:
            game = GameRepository(session).get(game_id)
            if game is None:
                raise TranslationError("游戏未添加。")
            adapter = self.registry.get(game.adapter_key)
            mods = ModRepository(session).list_for_game(game_id, include_missing=False)
            counts = TranslationRepository(session).counts_by_mod(
                [mod.id for mod in mods], language
            )
        result: list[TranslationOverview] = []
        for mod in mods:
            mod_dir = Path(game.mods_path) / mod.relative_path
            if not mod_dir.is_dir():
                continue
            try:
                eligible = adapter.is_translatable(
                    self._metadata(mod), mod_dir
                )
            except Exception:
                eligible = False
            if not eligible:
                continue
            try:
                plan = adapter.build_translation_plan(mod_dir, language)
            except TranslationError as exc:
                result.append(
                    TranslationOverview(
                        mod_id=mod.id,
                        mod_name=mod.name,
                        version=mod.version,
                        source_file="",
                        target_file="",
                        total_units=0,
                        translated_units=0,
                        confirmed_units=0,
                        applied=False,
                        eligible=False,
                        message=str(exc),
                    )
                )
                continue
            if plan is None:
                continue
            existing_count = sum(
                1 for unit in plan.units if plan.existing_translations.get(unit.key, "").strip()
            )
            _total, db_done, db_confirmed = counts.get(mod.id, (0, 0, 0))
            applied = adapter.is_translation_applied(mod_dir, language)
            result.append(
                TranslationOverview(
                    mod_id=mod.id,
                    mod_name=mod.name,
                    version=mod.version,
                    source_file=plan.source_file.relative_to(mod_dir).as_posix(),
                    target_file=plan.target_file.relative_to(mod_dir).as_posix(),
                    total_units=len(plan.units),
                    translated_units=max(existing_count, db_done),
                    confirmed_units=db_confirmed,
                    applied=applied,
                    eligible=True,
                )
            )
        result.sort(key=lambda item: (-(item.total_units - item.translated_units), item.mod_name.casefold()))
        return result

    @staticmethod
    def _metadata(mod: Mod):  # type: ignore[no-untyped-def]
        from ...domain.models import ModMetadata

        return ModMetadata(
            unique_id=mod.unique_id,
            name=mod.name,
            author=mod.author,
            version=mod.version,
            description=mod.description,
            nexus_mod_id=mod.nexus_mod_id,
        )

    # ------------------------------------------------------------------
    # Unit synchronization
    # ------------------------------------------------------------------

    def _sync_units(
        self,
        mod: Mod,
        mod_dir: Path,
        plan: TranslationPlan,
        language: str,
    ) -> list[TranslationRecord]:
        source_rel = plan.source_file.relative_to(mod_dir).as_posix()
        target_rel = plan.target_file.relative_to(mod_dir).as_posix()
        with self.db.session() as session:
            repo = TranslationRepository(session)
            existing = {record.key: record for record in repo.units_for_mod(mod.id, language)}
            for unit in plan.units:
                record = existing.get(unit.key)
                target_value = plan.existing_translations.get(unit.key, "")
                if record is None:
                    status = TranslationStatus.TRANSLATED if target_value else TranslationStatus.PENDING
                    origin = TranslationOrigin.EXISTING if target_value else TranslationOrigin.NONE
                    repo.upsert_unit(
                        mod_id=mod.id,
                        language=language,
                        source_file=source_rel,
                        target_file=target_rel,
                        key=unit.key,
                        source_text=unit.source_text,
                        translated_text=target_value,
                        status=status.value,
                        origin=origin.value,
                        model="",
                        confirmed=False,
                        error=None,
                    )
                else:
                    fields: dict = {
                        "source_file": source_rel,
                        "target_file": target_rel,
                    }
                    if record.source_text != unit.source_text:
                        fields.update(
                            {
                                "source_text": unit.source_text,
                                "translated_text": target_value,
                                "status": (
                                    TranslationStatus.TRANSLATED.value
                                    if target_value
                                    else TranslationStatus.PENDING.value
                                ),
                                "origin": (
                                    TranslationOrigin.EXISTING.value
                                    if target_value
                                    else TranslationOrigin.NONE.value
                                ),
                                "confirmed": False,
                                "error": "源文本已变化，建议重新翻译。",
                            }
                        )
                    elif not record.translated_text and target_value:
                        fields.update(
                            {
                                "translated_text": target_value,
                                "status": TranslationStatus.TRANSLATED.value,
                                "origin": TranslationOrigin.EXISTING.value,
                            }
                        )
                    repo.update_unit(record, **fields)
            repo.delete_units_except(mod.id, language, {unit.key for unit in plan.units})
            session.flush()
            return list(repo.units_for_mod(mod.id, language))

    def get_units(self, mod_id: int, language: str = "zh") -> list[TranslationUnitView]:
        mod, _game, _adapter, mod_dir, plan = self._load_context(mod_id)
        records = self._sync_units(mod, mod_dir, plan, language)
        by_key = {record.key: record for record in records}
        result: list[TranslationUnitView] = []
        for unit in plan.units:
            record = by_key.get(unit.key)
            if record is None:
                continue
            result.append(
                TranslationUnitView(
                    id=record.id,
                    key=record.key,
                    source_text=record.source_text,
                    translated_text=record.translated_text,
                    status=TranslationStatus(record.status)
                    if record.status in TranslationStatus._value2member_map_
                    else TranslationStatus.PENDING,
                    origin=TranslationOrigin(record.origin)
                    if record.origin in TranslationOrigin._value2member_map_
                    else TranslationOrigin.NONE,
                    model=record.model,
                    confirmed=record.confirmed,
                    updated_at=record.updated_at,
                    error=record.error,
                )
            )
        return result

    # ------------------------------------------------------------------
    # AI translation
    # ------------------------------------------------------------------

    @staticmethod
    def _source_hash(game_key: str, language: str, source_text: str) -> str:
        return hashlib.sha256(f"{game_key}\0{language}\0{source_text}".encode()).hexdigest()

    def translate_mod(
        self,
        mod_id: int,
        *,
        keys: set[str] | None = None,
        force: bool = False,
        language: str = "zh",
        progress=None,  # type: ignore[no-untyped-def]
    ) -> int:
        mod, game, adapter, mod_dir, plan = self._load_context(mod_id)
        records = self._sync_units(mod, mod_dir, plan, language)
        selected = [
            _UnitSnapshot(id=record.id, key=record.key, source_text=record.source_text)
            for record in records
            if (keys is None or record.key in keys)
            and record.source_text.strip()
            and (
                force
                or (
                    not record.confirmed
                    and (
                        not record.translated_text.strip()
                        or (record.error or "").startswith("源文本已变化")
                    )
                )
            )
        ]
        if not selected:
            return 0

        translations: dict[str, str] = {}
        failures: list[str] = []
        pending: list[_UnitSnapshot] = []
        with self.db.session() as session:
            repo = TranslationRepository(session)
            for index, unit in enumerate(selected):
                source_hash = self._source_hash(game.adapter_key, language, unit.source_text)
                cache = repo.cache_get(game.adapter_key, language, source_hash)
                if cache and cache.translated_text.strip():
                    if not validate_placeholders(unit.source_text, cache.translated_text):
                        confirmed = bool(
                            cache.confirmed and cache.origin == TranslationOrigin.HUMAN.value
                        )
                        fresh = repo.get_unit(unit.id)
                        if fresh is not None:
                            repo.update_unit(
                                fresh,
                                translated_text=cache.translated_text,
                                status=(
                                    TranslationStatus.CONFIRMED.value
                                    if confirmed
                                    else TranslationStatus.TRANSLATED.value
                                ),
                                origin=(
                                    TranslationOrigin.HUMAN.value
                                    if confirmed
                                    else TranslationOrigin.CACHE.value
                                ),
                                model=cache.model,
                                confirmed=confirmed,
                                error=None,
                            )
                        translations[unit.key] = cache.translated_text
                        continue
                pending.append(unit)
                if progress:
                    progress(
                        int(index / max(1, len(selected)) * 15),
                        f"准备翻译 {index + 1}/{len(selected)}…",
                    )

        if pending:
            if not self.llm.is_configured():
                raise LlmError("尚未配置 AI 模型或 API Key。可在设置中启用 AI 翻译，或手动导入汉化包。")
            batches = self._make_batches(pending)
            for batch_index, batch in enumerate(batches):
                if progress:
                    progress(
                        15 + int(batch_index / max(1, len(batches)) * 65),
                        f"AI 翻译第 {batch_index + 1}/{len(batches)} 批（{len(batch)} 条）…",
                    )
                try:
                    results = self._translate_batch(batch, plan, adapter)
                    with self.db.session() as session:
                        repo = TranslationRepository(session)
                        for unit, translated, model in results:
                            source_hash = self._source_hash(game.adapter_key, language, unit.source_text)
                            existing_cache = repo.cache_get(game.adapter_key, language, source_hash)
                            if not (existing_cache and existing_cache.confirmed):
                                repo.cache_put(
                                    game_key=game.adapter_key,
                                    language=language,
                                    source_hash=source_hash,
                                    source_text=unit.source_text,
                                    translated_text=translated,
                                    origin=TranslationOrigin.AI.value,
                                    model=model,
                                    confirmed=False,
                                )
                            fresh = repo.get_unit(unit.id)
                            if fresh is not None:
                                repo.update_unit(
                                    fresh,
                                    translated_text=translated,
                                    status=TranslationStatus.TRANSLATED.value,
                                    origin=TranslationOrigin.AI.value,
                                    model=model,
                                    confirmed=False,
                                    error=None,
                                )
                            translations[unit.key] = translated
                except Exception as exc:
                    message = str(exc) or exc.__class__.__name__
                    failures.append(message)
                    log.warning("翻译批次失败：%s", message)
                    with self.db.session() as session:
                        repo = TranslationRepository(session)
                        for unit in batch:
                            fresh = repo.get_unit(unit.id)
                            if fresh is not None:
                                repo.update_unit(
                                    fresh,
                                    status=TranslationStatus.FAILED.value,
                                    error=message,
                                )
        if progress:
            progress(85, "正在校验并写入汉化文件…")
        if translations:
            translations = self._merge_with_existing(plan, translations)
            self._write_target(game, mod, adapter, mod_dir, plan, language, translations)
        if progress:
            progress(100, "翻译完成。")
        if failures and not translations:
            raise LlmError("AI 翻译失败：" + failures[0])
        self.activity.record(
            f"AI 翻译完成：{mod.name}（新增 {len(translations)} 条）",
            kind="translation",
            game_id=game.id,
            details={"mod_id": mod.id, "count": len(translations), "failures": failures},
        )
        return len(translations)

    def _make_batches(self, units: list[_UnitSnapshot]) -> list[list[_UnitSnapshot]]:
        batch_size = self.config.config.translation_batch_size
        batches: list[list[_UnitSnapshot]] = []
        current: list[_UnitSnapshot] = []
        chars = 0
        for unit in units:
            length = len(unit.source_text)
            if current and (len(current) >= batch_size or chars + length > BATCH_CHAR_LIMIT):
                batches.append(current)
                current = []
                chars = 0
            current.append(unit)
            chars += length
        if current:
            batches.append(current)
        return batches

    def _translate_batch(
        self,
        batch: list[_UnitSnapshot],
        plan: TranslationPlan,
        adapter,
    ) -> list[tuple[_UnitSnapshot, str, str]]:
        protected_map: dict[str, str] = {}
        token_map: dict[str, list[str]] = {}
        for unit in batch:
            protected, tokens = protect_tokens(unit.source_text)
            protected_map[unit.key] = protected
            token_map[unit.key] = tokens

        system = (
            "You are a professional game localization translator. Translate Stardew Valley "
            "mod text into Simplified Chinese. The input is a JSON object mapping stable keys "
            "to English source strings. Return ONLY a valid JSON object with exactly the same "
            "keys and no extra keys. Keep every placeholder token such as [[MP:0]] exactly as "
            "written and exactly once. Preserve newlines, punctuation intended for game markup, "
            "and the original tone. Never translate keys, identifiers, URLs, or tokens. Do not "
            "add explanations or markdown."
        )
        first_user = (
            json.dumps(protected_map, ensure_ascii=False)
            + "\n\nReturn the translated JSON object only."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": first_user},
        ]
        last_error: Exception | None = None
        results: list[tuple[_UnitSnapshot, str, str]] = []
        remaining = list(batch)
        for attempt in range(2):
            try:
                budget = max(512, sum(len(unit.source_text) for unit in remaining) * 3)
                payload = self.llm.chat_json(
                    messages,
                    max_tokens=min(
                        self.config.config.llm.max_tokens,
                        budget,
                    ),
                )
            except LlmError as exc:
                last_error = exc
                break
            if "translations" in payload and isinstance(payload["translations"], dict):
                payload = payload["translations"]
            retry: list[_UnitSnapshot] = []
            retry_errors: list[str] = []
            model = self.config.config.llm.model
            for unit in remaining:
                value = payload.get(unit.key)
                if not isinstance(value, str) or not value.strip():
                    retry.append(unit)
                    retry_errors.append(f"{unit.key}: 缺少译文")
                    continue
                restored, missing = restore_tokens(value, token_map.get(unit.key, []))
                if missing:
                    retry.append(unit)
                    retry_errors.append(f"{unit.key}: 占位符标记丢失")
                    continue
                if contains_markers(restored):
                    retry.append(unit)
                    retry_errors.append(f"{unit.key}: 译文残留占位符标记")
                    continue
                errors = validate_placeholders(unit.source_text, restored)
                if not errors:
                    errors = adapter.validate_translation(plan, {unit.key: restored})
                if errors:
                    retry.append(unit)
                    retry_errors.append(f"{unit.key}: {'；'.join(errors[:3])}")
                    continue
                results.append((unit, restored, model))
            if not retry:
                return results
            last_error = LlmError("部分条目校验失败：" + "；".join(retry_errors[:5]))
            if attempt == 0:
                messages = [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": (
                            "The previous response had problems: "
                            + "；".join(retry_errors[:10])
                            + "\nTranslate only these missing/invalid entries and return exactly the same keys:\n"
                            + json.dumps(
                                {unit.key: protected_map[unit.key] for unit in retry},
                                ensure_ascii=False,
                            )
                        ),
                    },
                ]
                remaining = retry
            else:
                break
        if results:
            return results
        raise LlmError(str(last_error) if last_error else "AI 未返回有效译文。")

    def _merge_with_existing(self, plan: TranslationPlan, translations: dict[str, str]) -> dict[str, str]:
        merged = {
            key: value
            for key, value in plan.existing_translations.items()
            if value.strip()
        }
        merged.update({key: value for key, value in translations.items() if value.strip()})
        return merged

    # ------------------------------------------------------------------
    # Writing / editing
    # ------------------------------------------------------------------

    def _write_target(
        self,
        game,
        mod: Mod,
        adapter,
        mod_dir: Path,
        plan: TranslationPlan,
        language: str,
        translations: dict[str, str],
    ) -> TranslationApplyResult:
        errors = adapter.validate_translation(plan, translations)
        if errors:
            raise TranslationError("译文校验失败：" + "；".join(errors[:8]))
        if not translations:
            raise TranslationError("没有可写入的译文。")
        backup_items = [plan.source_file.parent]
        backup = self.backups.create(
            game_id=game.id,
            root=mod_dir,
            items=backup_items,
            kind="translation",
            label=f"汉化 {mod.name}",
            note=f"写入 {plan.target_file.name} 前自动备份",
            mod_id=mod.id,
            extra={"target_file": plan.target_file.relative_to(mod_dir).as_posix()},
        )
        try:
            result = adapter.write_translation(plan, translations)
            result.backup_id = backup.id
        except Exception:
            log.exception("写入汉化失败，正在恢复备份")
            try:
                self.backups.manager.restore(backup.path, root=mod_dir)
            except Exception:
                log.exception("恢复汉化备份失败，请手动使用备份恢复")
            raise
        self.activity.record(
            f"已写入汉化：{mod.name}（{result.translated_units} 条）",
            kind="translation_write",
            game_id=game.id,
            details={"backup_id": backup.id, "target": str(result.target_file)},
        )
        return result

    def save_unit(
        self,
        unit_id: int,
        text: str,
        *,
        confirmed: bool = False,
        language: str = "zh",
    ) -> TranslationApplyResult:
        with self.db.session() as session:
            unit = TranslationRepository(session).get_unit(unit_id)
            if unit is None:
                raise TranslationError("译文条目不存在。")
            mod_id = unit.mod_id
            source_text = unit.source_text
            key = unit.key
        mod, game, adapter, mod_dir, plan = self._load_context(mod_id)
        text = text.strip("\n")
        if text.strip():
            errors = validate_placeholders(source_text, text)
            if not errors:
                errors = adapter.validate_translation(plan, {key: text})
            if errors:
                raise TranslationError("译文校验失败：" + "；".join(errors))
        records = self._sync_units(mod, mod_dir, plan, language)
        by_key = {record.key: record for record in records}
        record = by_key.get(key)
        if record is None:
            raise TranslationError("源文件中的该条目已不存在，请刷新。")
        translations = {
            other.key: other.translated_text
            for other in records
            if other.translated_text.strip()
        }
        with self.db.session() as session:
            repo = TranslationRepository(session)
            fresh = repo.get_unit(unit_id)
            if fresh is None:
                raise TranslationError("译文条目不存在。")
            status = TranslationStatus.CONFIRMED if confirmed else TranslationStatus.TRANSLATED
            repo.update_unit(
                fresh,
                translated_text=text,
                status=status.value if text.strip() else TranslationStatus.PENDING.value,
                origin=TranslationOrigin.HUMAN.value if text.strip() else TranslationOrigin.NONE.value,
                model="人工编辑",
                confirmed=bool(confirmed and text.strip()),
                error=None,
            )
            if confirmed and text.strip():
                source_hash = self._source_hash(game.adapter_key, language, source_text)
                repo.cache_put(
                    game_key=game.adapter_key,
                    language=language,
                    source_hash=source_hash,
                    source_text=source_text,
                    translated_text=text,
                    origin=TranslationOrigin.HUMAN.value,
                    model="人工确认",
                    confirmed=True,
                )
        if text.strip():
            translations[key] = text
        else:
            translations.pop(key, None)
        if not translations:
            raise TranslationError("没有可写入的译文。")
        return self._write_target(game, mod, adapter, mod_dir, plan, language, translations)

    def confirm_unit(self, unit_id: int, language: str = "zh") -> TranslationApplyResult:
        with self.db.session() as session:
            unit = TranslationRepository(session).get_unit(unit_id)
            if unit is None:
                raise TranslationError("译文条目不存在。")
            if not unit.translated_text.strip():
                raise TranslationError("该条目还没有译文。")
            text = unit.translated_text
        return self.save_unit(unit_id, text, confirmed=True, language=language)

    def apply_saved(self, mod_id: int, language: str = "zh") -> TranslationApplyResult:
        mod, game, adapter, mod_dir, plan = self._load_context(mod_id)
        records = self._sync_units(mod, mod_dir, plan, language)
        translations = {
            record.key: record.translated_text
            for record in records
            if record.translated_text.strip()
        }
        if not translations:
            raise TranslationError("没有已保存的译文。")
        return self._write_target(game, mod, adapter, mod_dir, plan, language, translations)

    def remove_translation(self, mod_id: int, language: str = "zh") -> Path:
        mod, game, _adapter, mod_dir, plan = self._load_context(mod_id)
        if not plan.target_file.exists():
            return plan.target_file
        backup = self.backups.create(
            game_id=game.id,
            root=mod_dir,
            items=[plan.target_file],
            kind="translation",
            label=f"移除汉化 {mod.name}",
            note="移除生成的目标语言文件前自动备份",
            mod_id=mod.id,
        )
        plan.target_file.unlink(missing_ok=True)
        self.activity.record(
            f"已移除汉化文件：{plan.target_file.name}（Mod 原始文件未修改）",
            kind="translation_remove",
            level="warning",
            game_id=game.id,
            details={"backup_id": backup.id},
        )
        return plan.target_file

    def export_units(self, mod_id: int, destination: Path, language: str = "zh") -> Path:
        mod, _game, _adapter, _mod_dir, _plan = self._load_context(mod_id)
        units = self.get_units(mod_id, language)
        payload = {
            "schema_version": 1,
            "mod": {"id": mod.id, "name": mod.name, "unique_id": mod.unique_id, "version": mod.version},
            "language": language,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "units": [unit.model_dump(mode="json") for unit in units],
        }
        destination = Path(destination)
        if destination.suffix.lower() != ".json":
            destination = destination.with_suffix(".json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(destination, json.dumps(payload, ensure_ascii=False, indent=2))
        return destination

    # ------------------------------------------------------------------
    # Translation pack search / import
    # ------------------------------------------------------------------

    def find_packs(self, mod_id: int, language: str = "zh") -> TranslationPackSearchResult:
        mod, game, _adapter, _mod_dir, _plan = self._load_context(mod_id)
        warnings: list[str] = []
        candidates: list[TranslationCandidate] = []
        name = mod.name
        term_list = [f"{name} translation", f"{name} chinese", f"{name} 汉化", mod.unique_id]
        with self.db.session() as session:
            domain = GameRepository(session).get(game.id).nexus_domain  # type: ignore[union-attr]

        if self.nexus.is_configured():
            seen_ids: set[int] = set()
            for term in term_list[:3]:
                try:
                    results = self.nexus.search_mods(domain, term, count=6)
                except Exception as exc:
                    warnings.append(f"Nexus 搜索失败：{exc}")
                    break
                for result in results:
                    if result.mod_id in seen_ids or result.mod_id == mod.nexus_mod_id:
                        continue
                    seen_ids.add(result.mod_id)
                    description = result.summary
                    try:
                        detail = self.nexus.get_mod(
                            domain, result.mod_id, include_requirements=False
                        )
                        description = detail.description or result.summary
                        title = detail.name or result.name
                        author = detail.author or result.author
                    except Exception:
                        title = result.name
                        author = result.author
                    text = f"{title}\n{description}".casefold()
                    score, reasons = self._score_candidate(
                        text,
                        name=name,
                        unique_id=mod.unique_id,
                        nexus_mod_id=mod.nexus_mod_id,
                        source="nexus",
                    )
                    if score < 25:
                        continue
                    candidates.append(
                        TranslationCandidate(
                            source="nexus",
                            title=title,
                            url=f"https://www.nexusmods.com/{domain}/mods/{result.mod_id}",
                            author=author,
                            version=result.updated_at,
                            updated=result.updated_at,
                            description=result.summary,
                            compatibility=self._compatibility_label(score),
                            score=score,
                            match_reasons=reasons,
                            needs_manual_download=True,
                        )
                    )
        else:
            warnings.append("未配置 Nexus API Key，已跳过 Nexus 汉化包搜索。")

        try:
            candidates.extend(
                self._search_github(name, mod.unique_id, mod.nexus_mod_id, warnings)
            )
        except Exception as exc:
            warnings.append(f"GitHub 搜索失败：{exc}")

        deduped: dict[str, TranslationCandidate] = {}
        for candidate in candidates:
            key = candidate.url.casefold() or candidate.title.casefold()
            if key not in deduped or candidate.score > deduped[key].score:
                deduped[key] = candidate
        final = sorted(deduped.values(), key=lambda item: item.score, reverse=True)
        with self.db.session() as session:
            TranslationRepository(session).add_candidates(
                mod.id,
                [
                    {
                        "source": item.source,
                        "title": item.title,
                        "url": item.url,
                        "author": item.author,
                        "version": item.version,
                        "compatibility": item.compatibility,
                        "score": item.score,
                        "status": "candidate",
                    }
                    for item in final
                ],
            )
        return TranslationPackSearchResult(candidates=final, warnings=warnings)

    def _search_github(
        self,
        name: str,
        unique_id: str,
        nexus_mod_id: int | None,
        warnings: list[str],
    ) -> list[TranslationCandidate]:
        queries = [f'"{name}" stardew valley translation', f'"{name}" 汉化']
        results: list[TranslationCandidate] = []
        seen: set[str] = set()
        with httpx.Client(
            timeout=httpx.Timeout(25.0, connect=15.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
            follow_redirects=True,
        ) as client:
            for query in queries:
                response = client.get(
                    GITHUB_API,
                    params={"q": query, "sort": "updated", "order": "desc", "per_page": 8},
                )
                if response.status_code == 403 or response.status_code == 429:
                    reset = response.headers.get("X-RateLimit-Reset", "")
                    warnings.append(
                        "GitHub 搜索触发速率限制，请稍后再试。"
                        + (f" 重置时间戳：{reset}" if reset else "")
                    )
                    break
                if response.status_code >= 400:
                    warnings.append(f"GitHub 搜索返回 HTTP {response.status_code}。")
                    break
                try:
                    payload = response.json()
                except ValueError:
                    warnings.append("GitHub 搜索返回了无法解析的数据。")
                    break
                for repo in payload.get("items", []):
                    if not isinstance(repo, dict):
                        continue
                    html_url = str(repo.get("html_url") or "")
                    if not html_url or html_url in seen:
                        continue
                    seen.add(html_url)
                    title = str(repo.get("full_name") or repo.get("name") or "")
                    description = str(repo.get("description") or "")
                    text = f"{title}\n{description}".casefold()
                    score, reasons = self._score_candidate(
                        text,
                        name=name,
                        unique_id=unique_id,
                        nexus_mod_id=nexus_mod_id,
                        source="github",
                    )
                    if score < 25:
                        continue
                    results.append(
                        TranslationCandidate(
                            source="github",
                            title=title,
                            url=html_url,
                            author=str((repo.get("owner") or {}).get("login") or ""),
                            updated=str(repo.get("updated_at") or ""),
                            description=description,
                            compatibility=self._compatibility_label(score),
                            score=score,
                            match_reasons=reasons,
                            needs_manual_download=True,
                        )
                    )
        return results

    @staticmethod
    def _score_candidate(
        text: str,
        *,
        name: str,
        unique_id: str,
        nexus_mod_id: int | None,
        source: str,
    ) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []
        normalized_name = name.casefold().strip()
        if normalized_name and normalized_name in text:
            score += 42
            reasons.append("标题或描述包含原 Mod 名称")
        else:
            words = [
                word
                for word in re.split(r"[^a-z0-9]+", normalized_name)
                if len(word) >= 4
            ]
            matched = [word for word in words if word in text]
            if matched:
                score += min(25, 8 * len(matched))
                reasons.append("匹配名称关键词：" + "、".join(matched[:4]))
        if unique_id.casefold() in text:
            score += 22
            reasons.append("包含原 Mod UniqueID")
        if nexus_mod_id and (
            f"mods/{nexus_mod_id}" in text or f"nexus:{nexus_mod_id}" in text
        ):
            score += 22
            reasons.append("包含原 Mod 的 Nexus ID")
        keyword_hits = [
            keyword
            for keyword in ("汉化", "中文", "chinese", "translation", "translate", "zh-cn", "简体")
            if keyword in text
        ]
        if keyword_hits:
            score += 18
            reasons.append("包含汉化关键词")
        if source == "github":
            score += 2
        if not reasons:
            reasons.append("仅搜索结果相关，未发现明确的版本兼容证据")
        return min(score, 100), reasons

    @staticmethod
    def _compatibility_label(score: int) -> str:
        if score >= 70:
            return "较可能匹配，但请在应用前核对汉化包版本"
        if score >= 45:
            return "可能相关，需要人工确认"
        return "低置信度，仅名称/关键词相关"

    def import_pack_archive(self, mod_id: int, archive_path: Path, language: str = "zh") -> TranslationApplyResult:
        mod, game, adapter, mod_dir, plan = self._load_context(mod_id)
        staging = Path(tempfile.mkdtemp(prefix="translation-pack-", dir=str(self.paths.staging_dir)))
        try:
            extract_archive(Path(archive_path), staging)
            candidates = sorted(
                (path for path in staging.rglob("*.json") if path.name.casefold() in ("zh.json", "zh-cn.json")),
                key=lambda path: (0 if path.parent.name == "i18n" else 1, len(path.parts)),
            )
            if not candidates:
                raise TranslationError("压缩包中没有找到 i18n/zh.json 汉化文件。")
            source = candidates[0]
            try:
                data = json.loads(source.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise TranslationError(f"汉化文件无法读取：{exc}") from exc
            if not isinstance(data, dict):
                raise TranslationError("汉化文件必须是 JSON 对象。")
            valid_keys = {unit.key for unit in plan.units}
            warnings: list[str] = []
            translations: dict[str, str] = {}
            for key, value in data.items():
                if str(key) not in valid_keys or not isinstance(value, str):
                    continue
                translations[str(key)] = value
            if not translations:
                raise TranslationError("汉化包中没有与当前 Mod 源文件匹配的键，可能版本不兼容。")
            unknown = len(data) - len(translations)
            if unknown > 0:
                warnings.append(f"已忽略 {unknown} 个源文件中不存在的键。")
            errors = adapter.validate_translation(plan, translations)
            if errors:
                raise TranslationError("汉化包校验失败：" + "；".join(errors[:8]))
            records = self._sync_units(mod, mod_dir, plan, language)
            by_key = {record.key: record for record in records}
            with self.db.session() as session:
                repo = TranslationRepository(session)
                for key, value in translations.items():
                    record = by_key.get(key)
                    if record is None:
                        continue
                    repo.update_unit(
                        record,
                        translated_text=value,
                        status=TranslationStatus.TRANSLATED.value,
                        origin=TranslationOrigin.EXISTING.value,
                        model="外部汉化包",
                        confirmed=False,
                        error=None,
                    )
            merged = self._merge_with_existing(plan, translations)
            result = self._write_target(game, mod, adapter, mod_dir, plan, language, merged)
            result.warnings.extend(warnings)
            result.warnings.append(
                "已通过键名与占位符校验，但 ModPilot 无法自动证明汉化包适用于当前 Mod 版本，请进入游戏验证。"
            )
            self.activity.record(
                f"已导入汉化包：{mod.name}（{len(translations)} 条）",
                kind="translation_pack_import",
                game_id=game.id,
                details={"archive": str(archive_path), "warnings": warnings},
            )
            return result
        finally:
            import shutil

            shutil.rmtree(staging, ignore_errors=True)

    def cache_stats(self) -> tuple[int, int]:
        with self.db.session() as session:
            return TranslationRepository(session).cache_stats()

    def clear_cache(self) -> int:
        with self.db.session() as session:
            removed = TranslationRepository(session).clear_cache()
        self.activity.record(
            f"已清理 {removed} 条翻译缓存",
            kind="translation_cache",
            level="warning",
        )
        return removed

    def cached_candidates(self, mod_id: int) -> list[TranslationCandidate]:
        with self.db.session() as session:
            rows = TranslationRepository(session).list_candidates(mod_id)
            return [
                TranslationCandidate(
                    source=row.source if row.source in ("nexus", "github", "local") else "nexus",
                    title=row.title,
                    url=row.url,
                    author=row.author,
                    version=row.version,
                    compatibility=row.compatibility,
                    score=row.score,
                    match_reasons=[],
                    needs_manual_download=True,
                )
                for row in rows
            ]
