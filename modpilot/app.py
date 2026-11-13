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
from .ui.icons import render_pixmap
from .ui.main_window import MainWindow
from .ui.theme import apply_theme, theme_colors
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
            "ModPilot 启动失败",
            f"无法初始化本地数据：{exc}\n\n"
            "请确认数据目录可写，或使用 --data-dir 指定其他目录。",
        )
        return 1

    theme = services.config.config.theme
    app.setProperty("theme", theme)
    apply_theme(app, theme)
    app.setWindowIcon(QIcon(render_pixmap("sparkles", theme_colors(theme)["accent"], 64)))

    def exception_hook(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.getLogger("modpilot").error("未捕获异常：\n%s", text)
        QMessageBox.critical(
            None,
            "ModPilot 发生未处理错误",
            f"{exc}\n\n详细信息已写入日志：{services.log_path}",
        )

    sys.excepthook = exception_hook
    window = MainWindow(services)
    window.show()
    if services.config.load_error:
        QMessageBox.warning(window, "配置已重置", services.config.load_error)
    return app.exec()
