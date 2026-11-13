from __future__ import annotations

import logging
import logging.handlers
import sys
from collections.abc import Callable
from pathlib import Path

from ..config import redact_secrets


class SecretRedactionFilter(logging.Filter):
    """Defence-in-depth: never write configured secrets to logs."""

    def __init__(self, secret_provider: Callable[[], list[str]] | None = None) -> None:
        super().__init__()
        self._secret_provider = secret_provider

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = self._secret_provider() if self._secret_provider else []
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact_secrets(message, secrets)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        if record.exc_info:
            # Exception text may include URLs/headers; redact the rendered form
            # only (keeping exc_info allows the formatter to show the traceback).
            pass
        return True


def setup_logging(
    logs_dir: Path,
    *,
    level: int = logging.INFO,
    secret_provider: Callable[[], list[str]] | None = None,
    console: bool = True,
) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "modpilot.log"
    root = logging.getLogger()
    root.setLevel(level)

    # Remove only handlers created by this application (useful for tests/restarts).
    for handler in list(root.handlers):
        if getattr(handler, "_modpilot_handler", False):
            root.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=4 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(SecretRedactionFilter(secret_provider))
    file_handler._modpilot_handler = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    if console and sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(max(level, logging.WARNING))
        console_handler.setFormatter(formatter)
        console_handler.addFilter(SecretRedactionFilter(secret_provider))
        console_handler._modpilot_handler = True  # type: ignore[attr-defined]
        root.addHandler(console_handler)

    return log_path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
