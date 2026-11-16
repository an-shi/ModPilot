from __future__ import annotations

import argparse
import ctypes
import logging
import sys
import traceback
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory

from .app_services import AppServices
from .i18n import set_language, tr
from .version import APP_ID, APP_NAME, VERSION

log = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ModPilot mod manager")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override the local data directory (also available as MODPILOT_DATA_DIR).",
    )
    parser.add_argument("--debug", action="store_true", help="Enable verbose console logging.")
    return parser.parse_args(argv)


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.{VERSION}")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _set_windows_app_id()
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName(APP_NAME)
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)
    font = QFont("Microsoft YaHei UI")
    font.setStyleHint(QFont.SansSerif)
    font.setPointSize(9)
    app.setFont(font)

    try:
        services = AppServices(args.data_dir)
    except Exception as exc:
        QMessageBox.critical(
            None,
            tr('app.modpilot_failed_to_start'),
            tr('app.unable_to_initialize_local_data_0_make_sure_the_data_28a885', exc),
        )
        return 1

    # Load the UI only after the persisted language is active so class-level
    # labels and constructors use the correct locale.
    set_language(services.config.config.ui_language)
    from .ui.icons import render_pixmap
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme, theme_colors

    theme = services.config.config.theme
    app.setProperty("theme", theme)
    apply_theme(app, theme)
    app.setWindowIcon(QIcon(render_pixmap("sparkles", theme_colors(theme)["accent"], 64)))

    def exception_hook(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.getLogger("modpilot").error(tr('app.uncaught_exception_s'), text)
        QMessageBox.critical(
            None,
            tr('app.unhandled_error_in_modpilot'),
            tr('app.0_details_written_to_log_1', exc, services.log_path),
        )

    sys.excepthook = exception_hook
    window = MainWindow(services)
    window.show()
    if services.config.load_error:
        QMessageBox.warning(window, tr('app.configuration_reset'), services.config.load_error)
    return app.exec()
