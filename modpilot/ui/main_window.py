from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..app_services import AppServices
from ..version import APP_NAME, VERSION
from .icons import render_pixmap
from .pages.dashboard import DashboardPage
from .pages.diagnostics import DiagnosticsPage
from .pages.downloads import DownloadsPage
from .pages.games import GamesPage
from .pages.mods import ModsPage
from .pages.profiles import ProfilesPage
from .pages.settings import SettingsPage
from .pages.translation import TranslationPage
from .pages.updates import UpdatesPage
from .tasks import TaskManager
from .theme import apply_theme, theme_colors
from .widgets import refresh_button_icons, show_toast
from ..i18n import tr

log = logging.getLogger(__name__)


class NavButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(18, 18))
        self.badge = QLabel(self)
        self.badge.setObjectName("NavBadge")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.hide()

    def set_badge(self, text: str, tone: str = "accent") -> None:
        if not text or text == "0":
            self.badge.hide()
            return
        self.badge.setText(text)
        width = max(24, self.badge.fontMetrics().horizontalAdvance(text) + 14)
        self.badge.setFixedSize(width, 18)
        self.badge.show()
        self._position_badge()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._position_badge()

    def _position_badge(self) -> None:
        if self.badge.isHidden():
            return
        self.badge.move(
            self.width() - self.badge.width() - 10,
            (self.height() - self.badge.height()) // 2,
        )
        self.badge.raise_()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        QTimer.singleShot(0, self._position_badge)


class _DownloadBridge(QObject):
    event = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self, services: AppServices) -> None:
        super().__init__()
        self.services = services
        self.tasks = TaskManager(self)
        self.current_game = None
        self._page_index = 0
        self._check_running = False
        self._download_bridge = _DownloadBridge(self)
        self._download_bridge.event.connect(self._on_download_event)
        self.services.downloads.on_event = self._download_bridge.event.emit

        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.setMinimumSize(1120, 700)
        self.resize(1320, 860)
        self.setWindowIcon(QIcon(render_pixmap("sparkles", theme_colors(self._theme)["accent"], 64)))
        self._build_ui()
        self._connect_shortcuts()
        self.tasks.unhandled_error.connect(self._on_task_error)
        self.tasks.count_changed.connect(self._update_task_status)
        self.refresh_games()
        self._update_nav_icons()
        self._reload_scheduler()
        if self.services.config.config.check_updates_on_startup:
            QTimer.singleShot(12_000, lambda: self.run_update_check(silent=True))

    @property
    def _theme(self) -> str:
        return self.services.config.config.theme

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.root = QWidget()
        self.root.setObjectName("AppRoot")
        self.setCentralWidget(self.root)
        outer = QHBoxLayout(self.root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(246)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(14, 20, 14, 16)
        sidebar_layout.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(8, 0, 8, 0)
        brand_row.setSpacing(10)
        brand_icon = QLabel()
        brand_icon.setPixmap(render_pixmap("sparkles", theme_colors(self._theme)["accent"], 28))
        brand_row.addWidget(brand_icon)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand = QLabel(APP_NAME)
        brand.setObjectName("Brand")
        brand_sub = QLabel(tr('ui.main_window.ai_mod_management_hub'))
        brand_sub.setObjectName("BrandSub")
        brand_text.addWidget(brand)
        brand_text.addWidget(brand_sub)
        brand_row.addLayout(brand_text, 1)
        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addSpacing(20)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[NavButton] = []
        self.pages: list = []
        self.pages_stack = QStackedWidget()
        self.pages_stack.setObjectName("Pages")

        page_defs = [
            ("dashboard", tr('ui.main_window.overview'), "dashboard", DashboardPage),
            ("games", tr('ui.main_window.game_management'), "gamepad", GamesPage),
            ("mods", tr('ui.main_window.mod_management'), "mods", ModsPage),
            ("downloads", tr('ui.main_window.download_center'), "download", DownloadsPage),
            ("updates", tr('ui.main_window.update_center'), "update", UpdatesPage),
            ("translation", tr('ui.main_window.translation_center'), "translate", TranslationPage),
            ("diagnostics", tr('ui.main_window.smart_diagnosis'), "diagnose", DiagnosticsPage),
            ("profiles", tr('ui.main_window.profile'), "profiles", ProfilesPage),
            ("settings", tr('ui.main_window.settings'), "settings", SettingsPage),
        ]

        sidebar_layout.addWidget(self._nav_section(tr('ui.main_window.workspace')))
        for index, (key, label, icon_name, page_class) in enumerate(page_defs):
            if key == "settings":
                sidebar_layout.addWidget(self._nav_section(tr('ui.main_window.system')))
            page = page_class(self)
            self.pages.append(page)
            self.pages_stack.addWidget(page)
            button = NavButton(label)
            button.setProperty("nav_key", key)
            button.setProperty("icon_name", icon_name)
            button.clicked.connect(lambda _checked=False, i=index: self.switch_page(i))
            self.nav_group.addButton(button)
            self.nav_buttons.append(button)
            sidebar_layout.addWidget(button)
        sidebar_layout.addStretch(1)
        footer = QLabel(tr('ui.main_window.version_0_local_first', VERSION))
        footer.setObjectName("SidebarVersion")
        footer.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(footer)
        outer.addWidget(self.sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.header = QFrame()
        self.header.setObjectName("Header")
        self.header.setFixedHeight(86)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(24, 14, 24, 14)
        header_layout.setSpacing(12)
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        self.header_title = QLabel(tr('ui.main_window.overview'))
        self.header_title.setObjectName("PageTitle")
        self.header_subtitle = QLabel("")
        self.header_subtitle.setObjectName("PageSubtitle")
        self.header_subtitle.setWordWrap(True)
        title_block.addWidget(self.header_title)
        title_block.addWidget(self.header_subtitle)
        header_layout.addLayout(title_block, 1)

        self.game_combo = QComboBox()
        self.game_combo.setMinimumWidth(190)
        self.game_combo.setToolTip(tr('ui.main_window.current_game'))
        self.game_combo.currentIndexChanged.connect(self._on_game_selected)
        header_layout.addWidget(self.game_combo)

        self.search = QLineEdit()
        self.search.setObjectName("GlobalSearch")
        self.search.setPlaceholderText(tr('ui.main_window.search_current_game_mods'))
        self.search.setToolTip(tr('ui.main_window.press_enter_to_open_mod_management_and_filter_by_the_69363f'))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(
            QIcon(render_pixmap("search", theme_colors(self._theme)["muted"], 17)),
            QLineEdit.LeadingPosition,
        )
        self.search.returnPressed.connect(self._global_search)
        header_layout.addWidget(self.search)

        self.theme_button = QPushButton()
        self.theme_button.setProperty("variant", "tool")
        self.theme_button.setFixedSize(36, 36)
        self.theme_button.setToolTip(tr('ui.main_window.toggle_dark_light_theme'))
        self.theme_button.clicked.connect(self.toggle_theme)
        header_layout.addWidget(self.theme_button)

        self.header_progress = QProgressBar()
        self.header_progress.setRange(0, 0)
        self.header_progress.setFixedWidth(70)
        self.header_progress.hide()
        header_layout.addWidget(self.header_progress)

        content_layout.addWidget(self.header)
        page_wrap = QWidget()
        page_layout = QVBoxLayout(page_wrap)
        page_layout.setContentsMargins(22, 18, 22, 18)
        page_layout.addWidget(self.pages_stack)
        content_layout.addWidget(page_wrap, 1)
        outer.addWidget(content, 1)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.setStatusBar(status)
        self.status_game = QLabel(tr('ui.main_window.no_game_selected'))
        self.status_game.setObjectName("Muted")
        self.status_tasks = QLabel(tr('ui.main_window.ready'))
        self.status_tasks.setObjectName("Muted")
        self.status_version = QLabel(f"ModPilot {VERSION}")
        self.status_version.setObjectName("Muted")
        status.addWidget(self.status_game, 1)
        status.addPermanentWidget(self.status_tasks)
        status.addPermanentWidget(self.status_version)

        self._update_theme_button()
        self.switch_page(0, force=True)

    def _nav_section(self, text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setObjectName("NavSection")
        return label

    def _connect_shortcuts(self) -> None:
        for key, handler in (
            ("Ctrl+F", self._focus_search),
            ("Ctrl+R", self._refresh_current),
            ("F5", self._refresh_current),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(handler)

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _refresh_current(self) -> None:
        if 0 <= self._page_index < len(self.pages):
            self.pages[self._page_index].activate()

    # ------------------------------------------------------------------
    # Navigation / games
    # ------------------------------------------------------------------

    def switch_page(self, index: int, *, force: bool = False) -> None:
        if index < 0 or index >= len(self.pages):
            return
        self._page_index = index
        page = self.pages[index]
        self.pages_stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        self._update_nav_icons()
        self.header_title.setText(page.title)
        self.header_subtitle.setText(page.subtitle)
        page.activate()

    def refresh_games(self) -> None:
        try:
            games = self.services.games.list_games()
        except Exception as exc:
            log.exception(tr('ui.main_window.failed_to_read_game_list'))
            self._on_task_error(tr('ui.main_window.loading_games'), str(exc))
            games = []
        active_id = self.services.config.config.active_game_id
        self.game_combo.blockSignals(True)
        self.game_combo.clear()
        if not games:
            self.game_combo.addItem(tr('ui.main_window.no_games_added_yet'), None)
            self.game_combo.setEnabled(False)
            self.current_game = None
        else:
            self.game_combo.setEnabled(True)
            selected_index = 0
            for index, game in enumerate(games):
                self.game_combo.addItem(game.name, game.id)
                if game.id == active_id:
                    selected_index = index
            self.game_combo.setCurrentIndex(selected_index)
            self.current_game = next((g for g in games if g.id == active_id), games[0])
            if self.current_game and self.current_game.id != active_id:
                self.services.games.set_active(self.current_game.id)
        self.game_combo.blockSignals(False)
        self.status_game.setText(
            self.current_game.install_path if self.current_game else tr('ui.main_window.please_add_the_game_installation_directory_first')
        )
        self._update_nav_badges()
        for page in self.pages:
            page.set_game(self.current_game)
        if 0 <= self._page_index < len(self.pages) and self.pages[self._page_index].isVisible():
            self.pages[self._page_index].refresh()

    def _on_game_selected(self, index: int) -> None:
        game_id = self.game_combo.itemData(index)
        if game_id is None:
            return
        try:
            self.services.games.set_active(int(game_id))
        except Exception as exc:
            self.error_message(str(exc))
            return
        self.refresh_games()
        self.on_data_changed()

    def _update_nav_badges(self) -> None:
        if not self.current_game:
            for button in self.nav_buttons:
                button.set_badge("")
            return
        try:
            summary = self.services.games.game_summary(self.current_game.id)
        except Exception:
            return
        mapping = {
            "updates": str(summary.update_count) if summary.update_count else "",
            "diagnostics": str(summary.issue_count) if summary.issue_count else "",
            "mods": str(summary.mod_count) if summary.mod_count else "",
        }
        for button in self.nav_buttons:
            key = str(button.property("nav_key"))
            button.set_badge(mapping.get(key, ""))

    def on_data_changed(self) -> None:
        self.refresh_games()
        self._update_nav_badges()
        if 0 <= self._page_index < len(self.pages):
            page = self.pages[self._page_index]
            page.on_data_changed()

    def go_to(self, page_class_name: str) -> None:
        for index, page in enumerate(self.pages):
            if page.__class__.__name__ == page_class_name:
                self.switch_page(index)
                return

    def _global_search(self) -> None:
        text = self.search.text().strip()
        if not text:
            return
        mods_index = next(
            (index for index, page in enumerate(self.pages) if page.__class__.__name__ == "ModsPage"),
            2,
        )
        self.switch_page(mods_index)
        page = self.pages[mods_index]
        if hasattr(page, "apply_search"):
            page.apply_search(text)

    # ------------------------------------------------------------------
    # Theme / notifications
    # ------------------------------------------------------------------

    def toggle_theme(self) -> None:
        self.set_theme("light" if self._theme == "dark" else "dark")

    def set_theme(self, new_theme: str) -> None:
        if new_theme not in ("dark", "light"):
            return
        self.services.config.update(theme=new_theme)
        apply_theme(QApplication.instance(), new_theme)
        refresh_button_icons(self)
        for widget in self.findChildren(QWidget):
            refresh = getattr(widget, "refresh_theme", None)
            if callable(refresh):
                refresh()
        self._update_theme_button()
        self._update_nav_icons()
        self.setWindowIcon(
            QIcon(render_pixmap("sparkles", theme_colors(new_theme)["accent"], 64))
        )
        self.search.actions()[0].setIcon(
            QIcon(render_pixmap("search", theme_colors(new_theme)["muted"], 17))
        )

    def _update_theme_button(self) -> None:
        colors = theme_colors(self._theme)
        self.theme_button.setIcon(
            QIcon(render_pixmap("sun" if self._theme == "dark" else "moon", colors["muted"], 18))
        )

    def _update_nav_icons(self) -> None:
        colors = theme_colors(self._theme)
        for button in self.nav_buttons:
            name = str(button.property("icon_name"))
            color = colors["accent"] if button.isChecked() else colors["muted"]
            button.setIcon(QIcon(render_pixmap(name, color, 18)))

    def toast(self, message: str, tone: str = "accent") -> None:
        show_toast(self, message, tone)

    def error_message(self, message: str) -> None:
        QMessageBox.warning(self, tr('ui.main_window.operation_failed'), message)

    def _on_task_error(self, title: str, message: str) -> None:
        self.toast(tr('ui.main_window.0_failed_1', title, message), "danger")
        log.warning(tr('ui.main_window.task_failed_s_s'), title, message)

    # ------------------------------------------------------------------
    # Tasks / scheduler / downloads
    # ------------------------------------------------------------------

    def _update_task_status(self, count: int) -> None:
        self.header_progress.setVisible(count > 0)
        self.status_tasks.setText(tr('ui.main_window.background_task_0', count) if count else tr('ui.main_window.ready'))

    def run_update_check(self, *, silent: bool = False) -> None:
        if self._check_running or not self.current_game:
            if not silent:
                self.toast(tr('ui.main_window.please_add_a_game_first'), "warning")
            return
        if not self.services.nexus_configured:
            if not silent:
                self.toast(tr('ui.main_window.please_configure_the_nexus_api_key_in_settings_first'), "warning")
            return
        self._check_running = True
        game_id = self.current_game.id

        def done(results) -> None:  # type: ignore[no-untyped-def]
            self._check_running = False
            available = sum(1 for item in results if item.status.value == "available")
            errors = sum(1 for item in results if item.status.value == "error")
            if available:
                self.toast(tr('ui.main_window.check_complete_0_mods_have_updates_available', available), "success")
            elif not silent:
                self.toast(tr('ui.main_window.check_complete_no_updates_available'), "success")
            if errors and not silent:
                self.toast(tr('ui.main_window.0_mods_failed_the_check_see_the_update_center', errors), "warning")
            self.on_data_changed()

        def failed(message: str) -> None:
            self._check_running = False
            self.toast(tr('ui.main_window.failed_to_check_for_updates_0', message), "danger")

        self.tasks.run(
            tr('ui.main_window.check_updates'),
            lambda: self.services.updates.check_all(game_id),
            on_done=done,
            on_error=failed,
        )

    def _reload_scheduler(self) -> None:
        if not hasattr(self, "_scheduler"):
            self._scheduler = QTimer(self)
            self._scheduler.timeout.connect(lambda: self.run_update_check(silent=True))
        hours = self.services.config.config.update_check_interval_hours
        if hours <= 0:
            self._scheduler.stop()
        else:
            self._scheduler.start(min(2_000_000_000, hours * 3600 * 1000))

    def _on_download_event(self, event) -> None:  # type: ignore[no-untyped-def]
        for page in self.pages:
            if page.__class__.__name__ == "DownloadsPage" and hasattr(page, "on_download_event"):
                page.on_download_event(event)
        if event.status == "completed":
            self.toast(tr('ui.main_window.download_complete_0', event.archive_path or event.kind), "success")
            self.on_data_changed()
            if self.services.updates.should_auto_install(event.download_id):
                def installed(_result) -> None:  # type: ignore[no-untyped-def]
                    self.toast(tr('ui.main_window.the_update_package_was_installed_automatically'), "success")
                    self.on_data_changed()

                self.tasks.run(
                    tr('ui.main_window.auto_install_updates'),
                    lambda: self.services.updates.install_update_from_download(event.download_id),
                    on_done=installed,
                    on_error=lambda message: self.toast(tr('ui.main_window.auto_install_failed_0', message), "danger"),
                )
        elif event.status == "failed":
            self.toast(tr('ui.main_window.download_failed_0', event.error or tr('ui.main_window.unknown_error')), "danger")
            if self.current_game:
                self._update_nav_badges()

    def setup_scheduler(self) -> None:
        self._reload_scheduler()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        active_downloads = 0
        try:
            if self.current_game:
                active_downloads = sum(
                    1
                    for item in self.services.downloads.list_for_game(self.current_game.id)
                    if item.status.value in ("queued", "downloading", "paused")
                )
        except Exception:
            pass
        if self.tasks.active_count or active_downloads:
            box = QMessageBox(self)
            box.setWindowTitle(tr('ui.main_window.exit_modpilot'))
            box.setText(
                tr('ui.main_window.there_are_0_background_tasks_and_1_unfinished_downlo_6bd916', self.tasks.active_count, active_downloads)
            )
            box.setIcon(QMessageBox.Warning)
            yes = box.addButton(tr('ui.main_window.exit'), QMessageBox.AcceptRole)
            cancel = box.addButton(tr('ui.main_window.cancel'), QMessageBox.RejectRole)
            box.setDefaultButton(cancel)
            box.exec()
            if box.clickedButton() is not yes:
                event.ignore()
                return
        self.tasks.shutdown(1500)
        self.services.close(wait_for_downloads=False)
        event.accept()
