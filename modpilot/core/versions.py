from __future__ import annotations

import re

from packaging.version import InvalidVersion, Version

_NORMALIZE = re.compile(r"^[vV]\s*")
_LOOSE = re.compile(r"^(\d+(?:\.\d+)*)(.*)$")


def parse_version(value: str | None) -> Version | None:
    if not value:
        return None
    text = _NORMALIZE.sub("", value.strip())
    try:
        return Version(text)
    except InvalidVersion:
        match = _LOOSE.match(text)
        if not match:
            return None
        numeric, suffix = match.groups()
        try:
            return Version(f"{numeric}{suffix}")
        except InvalidVersion:
            return None


def compare_versions(left: str | None, right: str | None) -> int | None:
    """Return -1/0/1, or None when either value is not a comparable version."""

    parsed_left = parse_version(left)
    parsed_right = parse_version(right)
    if parsed_left is None or parsed_right is None:
        return None
    if parsed_left < parsed_right:
        return -1
    if parsed_left > parsed_right:
        return 1
    return 0


def is_newer(candidate: str | None, current: str | None) -> bool:
    result = compare_versions(candidate, current)
    return result == 1 if result is not None else False


def version_satisfies(installed: str | None, minimum: str | None) -> bool:
    if not minimum:
        return True
    if not installed:
        return False
    result = compare_versions(installed, minimum)
    return result is not None and result >= 0


def versions_equal(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return left == right
    result = compare_versions(left, right)
    return left.strip() == right.strip() if result is None else result == 0
