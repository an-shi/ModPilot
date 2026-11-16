"""Runtime localization with explicit message keys.

Every supported language ships a complete JSON catalog. Source code contains
message IDs (for example ``ui.pages.settings.settings``) instead of display
text, so the default language is only a setting, not a property of the code.
``tr()`` looks up the active catalog, falls back to the English catalog for a
missing key, and finally returns the message ID itself. Tests verify that both
catalogs stay complete, so untranslated text never silently appears.

Language changes are applied at startup. The UI is rebuilt on the next launch
so long-running tasks never see a half-translated widget tree.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "zh-CN"
SUPPORTED_LANGUAGES: dict[str, str] = {
    "zh-CN": "简体中文",
    "en-US": "English",
}
FALLBACK_LANGUAGE = "en-US"
_CATALOG_DIR = Path(__file__).parent / "locales"

_language = DEFAULT_LANGUAGE
_catalog: dict[str, str] = {}
_catalogs: dict[str, dict[str, str]] = {}


def current_language() -> str:
    return _language


def normalize_language(language: str | None) -> str:
    if language in SUPPORTED_LANGUAGES:
        return str(language)
    # Be forgiving with common locale spellings.
    if language:
        lowered = language.lower().replace("_", "-")
        if lowered.startswith("zh"):
            return "zh-CN"
        if lowered.startswith("en"):
            return "en-US"
    return DEFAULT_LANGUAGE


def _read_catalog(language: str) -> dict[str, str]:
    path = _CATALOG_DIR / f"{language.replace('-', '_')}.json"
    if not path.is_file():
        log.error("Translation catalog does not exist: %s", path)
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Failed to read translation catalog %s: %s", path, exc)
        return {}
    translations = raw.get("translations", raw)
    if not isinstance(translations, dict):
        log.error("Invalid translation catalog format: %s", path)
        return {}
    return {
        str(key): str(value)
        for key, value in translations.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _load_catalog(language: str) -> dict[str, str]:
    if language not in _catalogs:
        _catalogs[language] = _read_catalog(language)
    return _catalogs[language]


def set_language(language: str | None) -> str:
    """Set the active language and load its catalog.

    Returns the normalized language actually applied. If the requested catalog
    cannot be loaded, the default Chinese catalog is used so the application
    still starts with a usable UI.
    """

    global _language, _catalog
    normalized = normalize_language(language)
    catalog = _load_catalog(normalized)
    if not catalog and normalized != DEFAULT_LANGUAGE:
        log.warning("Language catalog %s is empty; falling back to %s", normalized, DEFAULT_LANGUAGE)
        normalized = DEFAULT_LANGUAGE
        catalog = _load_catalog(normalized)
    _language = normalized
    _catalog = catalog
    log.info("UI language: %s", normalized)
    return normalized


def tr(text: str, *args: Any, **kwargs: Any) -> str:
    """Translate a message ID and optionally apply Python formatting.

    Missing IDs fall back to the English catalog and finally to the message ID
    itself. They never expose another language implicitly.
    """

    template = _catalog.get(text)
    if template is None and _language != FALLBACK_LANGUAGE:
        template = _load_catalog(FALLBACK_LANGUAGE).get(text)
    if template is None:
        template = text
    if not args and not kwargs:
        return template
    try:
        return template.format(*args, **kwargs)
    except (KeyError, IndexError, ValueError):
        # A bad translation must not crash the UI.
        try:
            return text.format(*args, **kwargs)
        except (KeyError, IndexError, ValueError):
            return template


def tr_or(key: str, fallback: str) -> str:
    """Translate *key*, returning *fallback* when the key is unavailable.

    This is intended for display names that may come from adapters or data
    created by an older application version.
    """

    template = _catalog.get(key)
    if template is None:
        template = _load_catalog(FALLBACK_LANGUAGE).get(key)
    return template if template is not None else fallback


def available_languages() -> dict[str, str]:
    return dict(SUPPORTED_LANGUAGES)


# Load the default catalog immediately so module-level calls have real text.
set_language(DEFAULT_LANGUAGE)
