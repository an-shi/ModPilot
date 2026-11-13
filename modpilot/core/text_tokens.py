from __future__ import annotations

import re

# Tokens commonly used by game text/translation frameworks. They must survive
# translation byte-for-byte. Keeping this list game-agnostic is safe: adapters
# may add stricter validation on top.
_TOKEN_PATTERNS = [
    re.compile(r"\{\{[^{}\n]+\}\}"),
    re.compile(r"\{[A-Za-z0-9_:.,\-]+\}"),
    re.compile(r"%[sdifr]"),
    re.compile(r"\\[ntr]"),
    re.compile(r"\^"),
    re.compile(r"\$[A-Za-z0-9_]+"),
    re.compile(r"<[^<>\n]{1,80}>"),
    re.compile(r"@"),
    re.compile(r"\uE000|\uE001"),
]

SENTINEL_OPEN = "\uE000"
SENTINEL_CLOSE = "\uE001"


def extract_tokens(text: str) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for pattern in _TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), match.group(0)))
    # Patterns can overlap (for example {{name}} also contains {name}). Keep
    # the longest token at each position and discard tokens inside it.
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    accepted: list[str] = []
    last_end = -1
    for start, end, token in matches:
        if start < last_end:
            continue
        accepted.append(token)
        last_end = end
    return accepted


def protect_tokens(text: str) -> tuple[str, list[str]]:
    """Replace protected tokens with private-use markers.

    The sentinels are extremely unlikely to be translated or normalised by an
    LLM. They are restored before validation.
    """

    tokens = extract_tokens(text)
    if not tokens:
        return text, []
    protected = text
    # Replace longer tokens first to avoid overlapping substitutions.
    for index, token in sorted(enumerate(tokens), key=lambda item: len(item[1]), reverse=True):
        protected = protected.replace(token, f"{SENTINEL_OPEN}{index}{SENTINEL_CLOSE}", 1)
    return protected, tokens


def restore_tokens(protected: str, tokens: list[str]) -> tuple[str, list[int]]:
    missing: list[int] = []

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if 0 <= index < len(tokens):
            return tokens[index]
        return match.group(0)

    restored = re.sub(
        re.escape(SENTINEL_OPEN) + r"(\d+)" + re.escape(SENTINEL_CLOSE),
        replace,
        protected,
    )
    for index in range(len(tokens)):
        if f"{SENTINEL_OPEN}{index}{SENTINEL_CLOSE}" in protected:
            # This can only happen when the model duplicated a placeholder.
            continue
        missing.append(index)
    return restored, missing


def token_multiset(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for token in extract_tokens(text):
        result[token] = result.get(token, 0) + 1
    return result


def validate_placeholders(source: str, translation: str) -> list[str]:
    errors: list[str] = []
    if not translation.strip():
        return ["译文为空"]
    source_tokens = token_multiset(source)
    translated_tokens = token_multiset(translation)
    if source_tokens != translated_tokens:
        missing = [
            f"{token}（{count} 次）"
            for token, count in source_tokens.items()
            if translated_tokens.get(token, 0) < count
        ]
        added = [
            f"{token}（{count} 次）"
            for token, count in translated_tokens.items()
            if source_tokens.get(token, 0) < count
        ]
        if missing:
            errors.append("占位符缺失：" + "、".join(missing))
        if added:
            errors.append("占位符被新增或重复：" + "、".join(added))
    if "\x00" in translation:
        errors.append("译文包含空字符")
    if len(translation) > max(4000, len(source) * 20):
        errors.append("译文长度异常，可能不是有效翻译")
    return errors


def contains_markers(text: str) -> bool:
    return SENTINEL_OPEN in text or SENTINEL_CLOSE in text
