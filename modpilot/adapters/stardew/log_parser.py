from __future__ import annotations

import logging
import re
from pathlib import Path

from ...domain.enums import IssueCategory, LogSeverity
from ...domain.models import LogIssue, ParsedLog
from ...i18n import tr

log = logging.getLogger(__name__)

MAX_LOG_BYTES = 32 * 1024 * 1024

LINE_RE = re.compile(
    r"^\[(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<level>TRACE|DEBUG|INFO|WARN|ERROR|ALERT|CRITICAL)\s+(?P<source>[^\]]+)\]\s?(?P<message>.*)$"
)
HEADER_RE = re.compile(
    r"SMAPI\s+(?P<smapi>[\w.\-+]+)\s+with Stardew Valley\s+(?P<game>[\w.\-+]+)",
    re.IGNORECASE,
)
SMAPI_VERSION_RE = re.compile(r"SMAPI\s+(?P<smapi>\d[\w.\-+]*)", re.IGNORECASE)
LOADED_COUNT_RE = re.compile(r"Loaded\s+(\d+)\s+mods?:", re.IGNORECASE)
LOADED_MOD_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+(?P<version>\d[^\s]*)\s+by\s+(?P<author>.+?)(?:\s+\|\s+.*)?$"
)
SKIPPED_RE = re.compile(r"^\s*-\s+(?P<text>.+?)\s*$")
SKIPPED_DETAIL_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<version>\d[^\s]*)\s+because\s+(?P<reason>.+)$",
    re.IGNORECASE,
)
SKIPPED_NO_VERSION_RE = re.compile(r"^(?P<name>.+?)\s+because\s+(?P<reason>.+)$", re.IGNORECASE)

_WARNING_KEYWORDS = (
    "fail",
    "error",
    "not found",
    "missing",
    "conflict",
    "unable",
    "could not",
    "couldn't",
    "requires",
    "incompatible",
    "duplicate",
    "crash",
    "exception",
    "skipped",
)

_IGNORED_SMAPI_MESSAGES = {
    "skipped mods",
    "these mods could not be added to your game.",
    "these mods could not be added to your game",
    "loading mods...",
    "loading mod metadata...",
    "mods go here:",
}


def _level(value: str) -> LogSeverity:
    return {
        "TRACE": LogSeverity.INFO,
        "DEBUG": LogSeverity.INFO,
        "INFO": LogSeverity.INFO,
        "WARN": LogSeverity.WARNING,
        "ERROR": LogSeverity.ERROR,
        "ALERT": LogSeverity.CRITICAL,
        "CRITICAL": LogSeverity.CRITICAL,
    }[value]


def _extract_missing_dependencies(reason: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r"(?:requir(?:es|ed)\s+mods?\s+(?:which|that)\s+(?:aren't|are not|isn't|is not)\s+installed)\s*\((?P<deps>[^)]+)\)",
        r"(?:requir(?:es|ed)\s+mods?)\s*\((?P<deps>[^)]+)\)",
        r"(?:needs|requires)\s+(?:the\s+)?(?P<deps>[^.,;]+?)(?:\s+which\s+isn't\s+installed|\s*,|\.|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, reason, re.IGNORECASE)
        if not match:
            continue
        raw = match.group("deps")
        for part in raw.split(","):
            item = part.strip()
            if not item:
                continue
            # "Content Patcher: https://..." -> keep the human-readable name,
            # but preserve unique IDs and URLs for diagnostics.
            if ": http" in item:
                candidates.append(item)
                continue
            candidates.append(item)
        if candidates:
            break
    return list(dict.fromkeys(candidates))


def _categorize_reason(reason: str) -> tuple[IssueCategory, list[str], str]:
    lower = reason.lower()
    missing = _extract_missing_dependencies(reason)
    if "not compatible with your game version" in lower:
        return IssueCategory.COMPATIBILITY, [], tr('adapters.stardew.log_parser.this_mod_is_not_compatible_with_the_current_game_ver_d55a9b')
    if "requires api version" in lower or "requires a newer version of smapi" in lower:
        return IssueCategory.VERSION_MISMATCH, [], tr('adapters.stardew.log_parser.this_mod_requires_a_newer_version_of_smapi')
    if missing:
        return IssueCategory.MISSING_DEPENDENCY, missing, tr('adapters.stardew.log_parser.this_mod_is_missing_required_dependencies')
    if "duplicate" in lower:
        return IssueCategory.DUPLICATE, [], tr('adapters.stardew.log_parser.detected_duplicate_installation_or_duplicate_uniquei_2e831c')
    if "manifest" in lower and ("invalid" in lower or "parsing" in lower or "none of them" in lower):
        return IssueCategory.CONFIG, [], tr('adapters.stardew.log_parser.the_mod_s_manifest_json_is_invalid_reinstallation_is_d6a64b')
    if "none of them are manifest.json" in lower or "no manifest.json" in lower or "empty folder" in lower:
        return IssueCategory.OTHER, [], tr('adapters.stardew.log_parser.the_mod_folder_structure_is_incomplete_or_not_a_vali_4083f3')
    if "game version" in lower or "minimum api" in lower:
        return IssueCategory.VERSION_MISMATCH, [], tr('adapters.stardew.log_parser.version_requirement_not_met')
    return IssueCategory.LOG_ERROR, [], tr('adapters.stardew.log_parser.mod_failed_to_load_see_the_original_log')


def _categorize_error(message: str) -> tuple[IssueCategory, list[str]]:
    lower = message.lower()
    if "content pack" in lower and ("error" in lower or "failed" in lower):
        return IssueCategory.FILE_CONFLICT, re.findall(r"content pack '([^']+)'", message)
    if "requires" in lower and ("not installed" in lower or "missing" in lower):
        return IssueCategory.MISSING_DEPENDENCY, _extract_missing_dependencies(message)
    if "version" in lower and ("incompatible" in lower or "requires" in lower):
        return IssueCategory.VERSION_MISMATCH, []
    if "conflict" in lower or "already registered" in lower or "duplicate" in lower:
        return IssueCategory.COMPATIBILITY, []
    if "not found" in lower or "could not find" in lower or "file not found" in lower:
        return IssueCategory.FILE_CONFLICT, []
    if "config" in lower or "json" in lower and ("parse" in lower or "invalid" in lower):
        return IssueCategory.CONFIG, []
    return IssueCategory.LOG_ERROR, []


class SmapiLogParser:
    log_type = "smapi"

    def parse(self, log_path: Path) -> ParsedLog:
        path = Path(log_path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            return ParsedLog(
                path=path,
                log_type=self.log_type,
                parse_warnings=[tr('adapters.stardew.log_parser.failed_to_read_log_0', exc)],
            )
        try:
            if size > MAX_LOG_BYTES:
                with path.open("rb") as handle:
                    handle.seek(-MAX_LOG_BYTES, 2)
                    raw_bytes = handle.read()
                truncated = True
            else:
                raw_bytes = path.read_bytes()
                truncated = False
        except OSError as exc:
            return ParsedLog(
                path=path,
                log_type=self.log_type,
                parse_warnings=[tr('adapters.stardew.log_parser.failed_to_read_log_0', exc)],
            )
        text = raw_bytes.decode("utf-8", errors="replace")
        lines = text.splitlines()
        parse_warnings: list[str] = []
        if truncated:
            parse_warnings.append(tr('adapters.stardew.log_parser.the_log_file_is_too_large_only_the_last_32_mb_will_b_ff07de'))

        result = ParsedLog(
            path=path,
            log_type=self.log_type,
            line_count=len(lines),
            truncated=truncated,
        )
        loaded_section = False
        skipped_section = False
        pending_issue: LogIssue | None = None
        seen_issues: set[tuple[str, str]] = set()
        loaded_count: int | None = None

        for line_number, line in enumerate(lines, start=1):
            match = LINE_RE.match(line)
            if not match:
                if pending_issue is not None and line.strip():
                    if line.startswith(("   ", "\t", "at ")) or "Exception" in line:
                        pending_issue.stack_trace += ("\n" if pending_issue.stack_trace else "") + line
                        if pending_issue.raw:
                            pending_issue.raw += "\n" + line
                continue

            level = _level(match.group("level"))
            source = match.group("source").strip()
            message = match.group("message").strip()
            lower_message = message.lower()

            header = HEADER_RE.search(message)
            if header:
                result.framework_version = result.framework_version or header.group("smapi")
                result.game_version = result.game_version or header.group("game")
            elif result.framework_version is None and source.upper() == "SMAPI":
                smapi_match = SMAPI_VERSION_RE.search(message)
                if smapi_match:
                    result.framework_version = smapi_match.group("smapi")

            loaded_match = LOADED_COUNT_RE.search(message)
            if loaded_match:
                loaded_count = int(loaded_match.group(1))
                loaded_section = True
                skipped_section = False
                pending_issue = None
                continue

            if lower_message.startswith("skipped mods"):
                skipped_section = True
                loaded_section = False
                pending_issue = None
                continue

            if loaded_section:
                if not message:
                    continue
                mod_match = LOADED_MOD_RE.match(message)
                if mod_match and " by " in message:
                    result.loaded_mods.append(
                        {
                            "name": mod_match.group("name").strip(),
                            "version": mod_match.group("version").strip(),
                            "author": mod_match.group("author").strip(),
                            "line": line_number,
                        }
                    )
                    continue
                if message.startswith("Found ") or message.startswith("Mods go here") or len(message) < 200 and not line.startswith("    "):
                    loaded_section = False

            if skipped_section:
                skipped_match = SKIPPED_RE.match(message)
                if skipped_match:
                    self._parse_skipped(skipped_match.group("text"), line, line_number, result, seen_issues)
                    continue
                if message and not lower_message.startswith("these mods"):
                    # Keep the section active across blank separator lines, but
                    # stop at an unrelated normal log line.
                    if level == LogSeverity.INFO and not message.strip("- "):
                        skipped_section = False
                    elif "mods could not be added" in lower_message:
                        continue
                    elif message.strip("- "):
                        skipped_section = False

            if source.upper() == "SMAPI" and lower_message.rstrip(":") in _IGNORED_SMAPI_MESSAGES:
                continue
            if set(message) <= {"-"}:
                continue
            if level == LogSeverity.INFO:
                continue
            if level == LogSeverity.WARNING and not any(key in lower_message for key in _WARNING_KEYWORDS):
                continue

            # Start a new issue. Stack trace lines following it are attached.
            category, related_mods = _categorize_error(message)
            mods = list(related_mods)
            if source.upper() != "SMAPI":
                mods.insert(0, source)
            signature = (source.lower(), message[:400].lower())
            if signature in seen_issues:
                pending_issue = None
                continue
            seen_issues.add(signature)
            pending_issue = LogIssue(
                severity=level,
                category=category,
                message=message or tr('adapters.stardew.log_parser.0_produced_an_unexplained_error', source),
                raw=line,
                line_number=line_number,
                mods=list(dict.fromkeys(mods)),
                confidence=0.85 if level.rank >= 2 else 0.55,
            )
            result.issues.append(pending_issue)

        if loaded_count is not None and len(result.loaded_mods) < loaded_count:
            parse_warnings.append(
                tr('adapters.stardew.log_parser.the_log_claims_0_mods_were_loaded_but_only_1_were_pa_dc8a0d', loaded_count, len(result.loaded_mods))
            )
        if not result.framework_version:
            parse_warnings.append(tr('adapters.stardew.log_parser.could_not_identify_the_smapi_version_in_the_log_head_43c2bf'))
        result.issues.sort(key=lambda item: (-item.severity.rank, item.line_number))
        return result

    def _parse_skipped(
        self,
        text: str,
        raw_line: str,
        line_number: int,
        result: ParsedLog,
        seen: set[tuple[str, str]],
    ) -> None:
        detail = SKIPPED_DETAIL_RE.match(text) or SKIPPED_NO_VERSION_RE.match(text)
        if detail:
            name = detail.group("name").strip()
            version = detail.groupdict().get("version") or ""
            reason = detail.group("reason").strip()
        else:
            name = text.strip()
            version = ""
            reason = text.strip()
        category, missing, recommendation = _categorize_reason(reason)
        skipped_entry = {
            "name": name,
            "version": version,
            "reason": reason,
            "recommendation": recommendation,
            "missing_dependencies": missing,
            "line": line_number,
            "raw": raw_line,
        }
        result.skipped_mods.append(skipped_entry)

        signature = (name.lower(), reason[:300].lower())
        if signature in seen:
            return
        seen.add(signature)
        issue = LogIssue(
            severity=LogSeverity.ERROR,
            category=category,
            message=tr('adapters.stardew.log_parser.0_could_not_be_loaded_1', name, reason),
            raw=raw_line,
            line_number=line_number,
            mods=[name] if name else [],
            missing_dependencies=missing,
            confidence=0.95,
        )
        result.issues.append(issue)


def find_smapi_logs(game_path: Path) -> list[Path]:
    candidates: list[Path] = []
    log_dir = None
    try:
        from .paths import logs_directory

        log_dir = logs_directory()
    except Exception:
        log_dir = None
    if log_dir and log_dir.is_dir():
        latest = log_dir / "SMAPI-latest.txt"
        if latest.is_file():
            candidates.append(latest)
        candidates.extend(sorted(log_dir.glob("SMAPI-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True))
    for local_dir in (game_path / "ErrorLogs", game_path / "Logs"):
        if local_dir.is_dir():
            candidates.extend(sorted(local_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True))

    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen or not resolved.is_file():
            continue
        seen.add(key)
        unique.append(resolved)
    return unique[:20]
