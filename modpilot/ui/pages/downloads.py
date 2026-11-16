from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIntValidator
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.paths import human_size
from ...domain.enums import DownloadKind, DownloadStatus
from ...domain.models import DownloadView, InstallPlan, NexusFile, NexusModInfo
from ..dialogs import InstallPreviewDialog
from ..widgets import (
    Card,
    EmptyState,
    SectionHeader,
    cell,
    clear_table,
    make_button,
    make_icon,
    make_scroll_content,
    open_in_explorer,
    setup_table,
)
from .base import BasePage
from ...i18n import tr

log = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class DownloadsPage(BasePage):
    title = tr('ui.pages.downloads.download_center')
    subtitle = tr('ui.pages.downloads.fetch_details_and_files_via_nexus_mod_id_and_manage_15bef5')

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        self._mod_info: NexusModInfo | None = None
        self._files: list[NexusFile] = []
        self._downloads: list[DownloadView] = []
        self._pending_plan: InstallPlan | None = None
        self._install_game_id: int | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self.body = QStackedWidget()
        outer.addWidget(self.body, 1)
        self.empty = EmptyState(
            tr('ui.pages.downloads.no_game_selected'),
            tr('ui.pages.downloads.add_a_game_to_use_the_nexus_download_center'),
            "download",
            make_button(tr('ui.pages.downloads.go_to_game_management'), icon_name="gamepad", variant="primary", on_click=lambda: self.controller.go_to("GamesPage")),
        )
        self.body.addWidget(self.empty)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.body.addWidget(content)

        self.api_banner = Card()
        banner_row = QHBoxLayout()
        banner_text = QLabel(tr('ui.pages.downloads.no_nexus_api_key_is_configured_yet_you_can_browse_de_6d31ad'))
        banner_text.setWordWrap(True)
        banner_icon = QLabel()
        from ..theme import theme_colors

        banner_icon.setPixmap(make_icon("shield", theme_colors(self.controller._theme)["warning"], 20).pixmap(20, 20))
        banner_row.addWidget(banner_icon, 0)
        banner_row.addWidget(banner_text, 1)
        banner_row.addWidget(
            make_button(tr('ui.pages.downloads.go_to_settings'), icon_name="settings", variant="primary", on_click=lambda: self.controller.go_to("SettingsPage"))
        )
        self.api_banner.add_layout(banner_row)
        layout.addWidget(self.api_banner)

        search_card = Card()
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        title = QLabel("Nexus Mod ID")
        title.setStyleSheet("font-weight: 650;")
        search_row.addWidget(title)
        self.mod_id_edit = QLineEdit()
        self.mod_id_edit.setPlaceholderText(tr('ui.pages.downloads.e_g_3753_stardew_valley_sve'))
        self.mod_id_edit.setValidator(QIntValidator(1, 2_147_483_647, self))
        self.mod_id_edit.returnPressed.connect(self.query_mod)
        search_row.addWidget(self.mod_id_edit, 1)
        self.query_button = make_button(
            tr('ui.pages.downloads.fetch_mod_details'), icon_name="search", variant="primary", on_click=self.query_mod
        )
        search_row.addWidget(self.query_button)
        search_row.addWidget(
            make_button(tr('ui.pages.downloads.open_nexus_page'), icon_name="external", on_click=self.open_nexus_page)
        )
        search_row.addWidget(
            make_button(tr('ui.pages.downloads.import_local_archive'), icon_name="folder", on_click=self.import_archive)
        )
        search_card.add_layout(search_row)
        self.query_hint = QLabel(tr('ui.pages.downloads.uses_only_the_official_nexus_api_download_permission_bce51e'))
        self.query_hint.setObjectName("Muted")
        search_card.add(self.query_hint)
        layout.addWidget(search_card)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        details_wrapper, details_layout = make_scroll_content(splitter)
        details_card = Card()
        self.details_title = QLabel(tr('ui.pages.downloads.look_up_a_mod'))
        self.details_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.details_title.setWordWrap(True)
        details_card.add(self.details_title)
        self.details_meta = QLabel(tr('ui.pages.downloads.enter_a_nexus_mod_id_to_get_the_author_version_depen_57780c'))
        self.details_meta.setObjectName("Muted")
        self.details_meta.setWordWrap(True)
        details_card.add(self.details_meta)
        self.details_description = QLabel("")
        self.details_description.setWordWrap(True)
        self.details_description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details_card.add(self.details_description)
        self.requirements_label = QLabel("")
        self.requirements_label.setWordWrap(True)
        self.requirements_label.setObjectName("Muted")
        details_card.add(self.requirements_label)
        files_header = SectionHeader(tr('ui.pages.downloads.downloadable_files'), tr('ui.pages.downloads.select_the_main_file_choose_optional_files_such_as_t_7b282c'))
        self.files_table = QTableWidget()
        setup_table(
            self.files_table,
            [tr('ui.pages.downloads.file_name'), tr('ui.pages.downloads.version'), tr('ui.pages.downloads.category'), tr('ui.pages.downloads.size'), tr('ui.pages.downloads.uploaded')],
            stretch_column=0,
            row_height=34,
        )
        self.files_table.setMinimumHeight(150)
        self.files_table.itemSelectionChanged.connect(self._file_selection_changed)
        details_card.add(files_header)
        details_card.add(self.files_table, 1)
        file_actions = QHBoxLayout()
        self.download_button = make_button(
            tr('ui.pages.downloads.download_selected_files'), icon_name="download", variant="primary", on_click=self.download_selected
        )
        self.download_button.setEnabled(False)
        file_actions.addWidget(self.download_button)
        file_actions.addWidget(
            make_button(
                tr('ui.pages.downloads.open_file_page'),
                icon_name="external",
                on_click=self.open_selected_file_page,
            )
        )
        file_actions.addStretch(1)
        self.file_hint = QLabel("")
        self.file_hint.setObjectName("Muted")
        file_actions.addWidget(self.file_hint)
        details_card.add_layout(file_actions)
        details_layout.addWidget(details_card)
        details_layout.addStretch(1)
        splitter.addWidget(details_wrapper)

        queue_card = Card()
        queue_header = SectionHeader(tr('ui.pages.downloads.download_queue'), tr('ui.pages.downloads.supports_pause_resume_cancel_and_retry_on_failure'))
        self.queue_refresh = make_button(
            tr('ui.pages.downloads.refresh'), icon_name="refresh", variant="tool", on_click=self.refresh_downloads
        )
        queue_header.add_action(self.queue_refresh)
        queue_card.add(queue_header)
        self.queue_table = QTableWidget()
        setup_table(
            self.queue_table,
            [tr('ui.pages.downloads.file'), tr('ui.pages.downloads.type'), tr('ui.pages.downloads.status'), tr('ui.pages.downloads.progress'), tr('ui.pages.downloads.downloaded'), tr('ui.pages.downloads.update_time')],
            stretch_column=0,
            row_height=36,
        )
        self.queue_table.itemSelectionChanged.connect(self._queue_selection_changed)
        queue_card.add(self.queue_table, 1)
        queue_actions = QHBoxLayout()
        queue_actions.setSpacing(6)
        self.pause_button = make_button(tr('ui.pages.downloads.pause'), icon_name="pause", on_click=self.toggle_pause)
        self.cancel_button = make_button(tr('ui.pages.downloads.cancel'), icon_name="x", variant="danger", on_click=self.cancel_download)
        self.retry_button = make_button(tr('ui.pages.downloads.retry_resume'), icon_name="refresh", on_click=self.retry_download)
        self.install_button = make_button(
            tr('ui.pages.downloads.install_downloaded'), icon_name="package", variant="primary", on_click=self.install_downloaded
        )
        self.open_download_button = make_button(
            tr('ui.pages.downloads.open_containing_folder'), icon_name="folder", on_click=self.open_download_folder
        )
        for button in (
            self.pause_button,
            self.retry_button,
            self.cancel_button,
            self.install_button,
            self.open_download_button,
        ):
            button.setEnabled(False)
            queue_actions.addWidget(button)
        queue_actions.addStretch(1)
        self.download_hint = QLabel("")
        self.download_hint.setObjectName("Muted")
        self.download_hint.setWordWrap(True)
        queue_actions.addWidget(self.download_hint)
        queue_card.add_layout(queue_actions)
        splitter.addWidget(queue_card)
        splitter.setSizes([430, 300])
        layout.addWidget(splitter, 1)

        self._queue_timer = QTimer(self)
        self._queue_timer.setInterval(3000)
        self._queue_timer.timeout.connect(self.refresh_downloads)

    def refresh(self) -> None:
        if self.game_id is None:
            self.body.setCurrentIndex(0)
            self.query_button.setEnabled(False)
            return
        self.body.setCurrentIndex(1)
        self.api_banner.setVisible(not self.services.nexus_configured)
        self.query_button.setEnabled(True)
        self._queue_timer.start()
        self.refresh_downloads()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        self._queue_timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._queue_timer.stop()
        super().hideEvent(event)

    def refresh_downloads(self) -> None:
        if self.game_id is None:
            return
        try:
            self._downloads = self.services.downloads.list_for_game(self.game_id)
        except Exception:
            log.exception(tr('ui.pages.downloads.failed_to_read_download_queue'))
            return
        selected_id = self._selected_download().id if self._selected_download() else None
        clear_table(self.queue_table)
        for item in self._downloads:
            row = self.queue_table.rowCount()
            self.queue_table.insertRow(row)
            self.queue_table.setItem(row, 0, cell(item.file_name, data=item.id, tooltip=item.archive_path or ""))
            kind_label = {"mod": "Mod", "update": tr('ui.pages.downloads.update'), "translation": tr('ui.pages.downloads.translation')}.get(item.kind.value, item.kind.value)
            self.queue_table.setItem(row, 1, cell(kind_label))
            self.queue_table.setItem(row, 2, cell(item.status.label, tooltip=item.error or ""))
            progress = f"{item.progress}%"
            if item.status is DownloadStatus.FAILED:
                progress = "—"
            self.queue_table.setItem(row, 3, cell(progress))
            self.queue_table.setItem(row, 4, cell(human_size(item.downloaded_bytes) + (" / " + human_size(item.total_bytes) if item.total_bytes else "")))
            self.queue_table.setItem(row, 5, cell(item.updated_at.strftime("%m-%d %H:%M")))
            if item.id == selected_id:
                self.queue_table.selectRow(row)
        self.download_hint.setText(
            tr('ui.pages.downloads.0_tasks_non_premium_accounts_can_download_from_the_n_78fa2c', len(self._downloads))
        )
        self._queue_selection_changed()

    def _selected_download(self) -> DownloadView | None:
        rows = self.queue_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.queue_table.item(rows[0].row(), 0)
        download_id = item.data(Qt.UserRole) if item else None
        return next((item for item in self._downloads if item.id == download_id), None)

    def _queue_selection_changed(self) -> None:
        item = self._selected_download()
        if item is None:
            for button in (
                self.pause_button,
                self.retry_button,
                self.cancel_button,
                self.install_button,
                self.open_download_button,
            ):
                button.setEnabled(False)
            return
        self.open_download_button.setEnabled(bool(item.archive_path))
        self.install_button.setEnabled(item.status is DownloadStatus.COMPLETED)
        self.cancel_button.setEnabled(item.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED))
        self.pause_button.setEnabled(item.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING))
        self.pause_button.setText(tr('ui.pages.downloads.pause'))
        self.retry_button.setEnabled(
            item.status in (DownloadStatus.PAUSED, DownloadStatus.FAILED, DownloadStatus.CANCELLED)
        )
        if item.status is DownloadStatus.PAUSED:
            self.retry_button.setText(tr('ui.pages.downloads.resume_download'))
        elif item.status is DownloadStatus.FAILED:
            self.retry_button.setText(tr('ui.pages.downloads.retry'))
        else:
            self.retry_button.setText(tr('ui.pages.downloads.resume_retry'))
        if item.error:
            self.download_hint.setText(item.error)

    # ------------------------------------------------------------------
    # Nexus query
    # ------------------------------------------------------------------

    def query_mod(self) -> None:
        game_id = self.require_game()
        if game_id is None or not self.current_game:
            return
        text = self.mod_id_edit.text().strip()
        if not text.isdigit():
            self.error(tr('ui.pages.downloads.please_enter_a_valid_nexus_mod_id_numeric'))
            return
        if not self.services.nexus_configured:
            self.error(tr('ui.pages.downloads.please_configure_the_nexus_api_key_in_settings_first'))
            return
        mod_id = int(text)
        domain = self.current_game.nexus_domain
        self.query_button.setEnabled(False)
        self.query_button.setText(tr('ui.pages.downloads.looking_up'))
        self.query_hint.setText(tr('ui.pages.downloads.requesting_nexus_api'))

        def work():
            info = self.services.nexus.get_mod(domain, mod_id)
            files = self.services.nexus.get_files(domain, mod_id)
            return info, files

        self.tasks.run(tr('ui.pages.downloads.fetch_nexus_mod_details'), work, on_done=self._query_done, on_error=self._query_failed)

    def _query_done(self, result) -> None:  # type: ignore[no-untyped-def]
        self.query_button.setEnabled(True)
        self.query_button.setText(tr('ui.pages.downloads.fetch_mod_details'))
        info, files = result
        self._mod_info = info
        self._files = files
        self.details_title.setText(info.name)
        self.details_meta.setText(
            f"Mod ID {info.mod_id} · {info.version or tr('ui.pages.downloads.unknown_version')} · "
            f"{info.author or tr('ui.pages.downloads.unknown_author')} · {info.category or tr('ui.pages.downloads.uncategorized')}"
        )
        description = _strip_html(info.description)
        self.details_description.setText(
            (description[:1800] + "…") if len(description) > 1800 else description
        )
        if info.requirements:
            self.requirements_label.setText(
                tr('ui.pages.downloads.nexus_dependencies') + "；".join(
                    f"{req.mod_name}" + (f"（{req.url}）" if req.url else "")
                    for req in info.requirements
                )
            )
        else:
            self.requirements_label.setText(tr('ui.pages.downloads.nexus_lists_no_additional_dependencies_still_check_t_008963'))
        clear_table(self.files_table)
        for file in files:
            row = self.files_table.rowCount()
            self.files_table.insertRow(row)
            name = file.name or file.file_name
            self.files_table.setItem(row, 0, cell(name, data=file.file_id, tooltip=file.description or file.file_name))
            self.files_table.setItem(row, 1, cell(file.mod_version or file.version))
            self.files_table.setItem(row, 2, cell(file.category_name or file.category))
            self.files_table.setItem(row, 3, cell(human_size(file.size_bytes)))
            uploaded = file.uploaded_at.strftime("%Y-%m-%d") if file.uploaded_at else "—"
            self.files_table.setItem(row, 4, cell(uploaded))
        if files:
            latest = self.services.updates._pick_latest(files)
            for row in range(self.files_table.rowCount()):
                item = self.files_table.item(row, 0)
                if latest and item and item.data(Qt.UserRole) == latest.file_id:
                    self.files_table.selectRow(row)
                    break
            else:
                self.files_table.selectRow(0)
        self.query_hint.setText(
            tr(
                'ui.pages.downloads.retrieved_0_files_nexus_api_remaining_quota_1',
                len(files),
                self.services.nexus.rate_limit.hourly_remaining
                if self.services.nexus.rate_limit.hourly_remaining is not None
                else tr('ui.pages.downloads.unknown'),
            )
        )
        self._file_selection_changed()

    def _query_failed(self, message: str) -> None:
        self.query_button.setEnabled(True)
        self.query_button.setText(tr('ui.pages.downloads.fetch_mod_details'))
        self.query_hint.setText(tr('ui.pages.downloads.query_failed'))
        self.error(message)

    def _selected_file(self) -> NexusFile | None:
        rows = self.files_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.files_table.item(rows[0].row(), 0)
        file_id = item.data(Qt.UserRole) if item else None
        return next((file for file in self._files if file.file_id == file_id), None)

    def _file_selection_changed(self) -> None:
        file = self._selected_file()
        self.download_button.setEnabled(file is not None and self._mod_info is not None)
        if file:
            self.file_hint.setText(
                tr(
                    'ui.pages.downloads.selected_0_1',
                    file.file_name or file.name,
                    tr('ui.pages.downloads.main_file')
                    if file.is_primary
                    else file.category_name or tr('ui.pages.downloads.optional_files'),
                )
            )
        else:
            self.file_hint.setText("")

    def download_selected(self) -> None:
        file = self._selected_file()
        info = self._mod_info
        if not file or not info or not self.game_id:
            self.toast(tr('ui.pages.downloads.please_look_up_the_mod_and_select_a_file_first'), "warning")
            return
        try:
            self.services.downloads.enqueue(
                game_id=self.game_id,
                nexus_mod_id=info.mod_id,
                file_id=file.file_id,
                mod_name=info.name,
                file_name=file.file_name or file.name or f"{info.name}-{file.file_id}.zip",
                expected_md5=file.md5,
                kind=DownloadKind.MOD,
            )
        except Exception as exc:
            self.error(str(exc))
            return
        self.toast(tr('ui.pages.downloads.download_task_queued'), "success")
        self.refresh_downloads()
        self.controller.on_data_changed()

    def open_nexus_page(self) -> None:
        if not self.current_game:
            return
        mod_id = self.mod_id_edit.text().strip()
        if not mod_id.isdigit():
            self.toast(tr('ui.pages.downloads.please_enter_a_mod_id_first'), "warning")
            return
        QDesktopServices.openUrl(
            QUrl(f"https://www.nexusmods.com/{self.current_game.nexus_domain}/mods/{mod_id}")
        )

    def open_selected_file_page(self) -> None:
        if not self.current_game or not self._mod_info:
            return
        QDesktopServices.openUrl(
            QUrl(
                f"https://www.nexusmods.com/{self.current_game.nexus_domain}/mods/"
                f"{self._mod_info.mod_id}?tab=files"
            )
        )

    def on_download_event(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.isVisible():
            self.refresh_downloads()

    def toggle_pause(self) -> None:
        item = self._selected_download()
        if not item:
            return
        try:
            self.services.downloads.pause(item.id)
        except Exception as exc:
            self.error(str(exc))
        self.refresh_downloads()

    def retry_download(self) -> None:
        item = self._selected_download()
        if not item:
            return
        try:
            self.services.downloads.resume(item.id)
        except Exception as exc:
            self.error(str(exc))
        self.refresh_downloads()

    def cancel_download(self) -> None:
        item = self._selected_download()
        if not item:
            return
        if not self.confirm(tr('ui.pages.downloads.cancel_download'), tr('ui.pages.downloads.cancel_0_incomplete_parts_will_be_deleted', item.file_name)):
            return
        try:
            self.services.downloads.cancel(item.id)
        except Exception as exc:
            self.error(str(exc))
        self.refresh_downloads()

    def install_downloaded(self) -> None:
        item = self._selected_download()
        if not item or item.status is not DownloadStatus.COMPLETED or not item.archive_path:
            return
        if not self.game_id:
            return
        if item.kind is DownloadKind.UPDATE:
            if not self.confirm(
                tr('ui.pages.downloads.install_update'),
                tr('ui.pages.downloads.install_update_package_0_the_current_mod_will_be_bac_2b90a0', item.archive_path),
            ):
                return
            self._install_update(item)
        else:
            self._install_local(Path(item.archive_path), self.game_id)

    def _install_update(self, item: DownloadView) -> None:
        self.install_button.setEnabled(False)
        self.download_hint.setText(tr('ui.pages.downloads.installing_updates'))

        def done(_result) -> None:  # type: ignore[no-untyped-def]
            self.download_hint.setText(tr('ui.pages.downloads.update_installed'))
            self.toast(tr('ui.pages.downloads.update_installed'), "success")
            self.controller.on_data_changed()
            self.refresh_downloads()

        def failed(message: str) -> None:
            self.download_hint.setText(message)
            self.error(message)

        self.tasks.run(
            tr('ui.pages.downloads.install_update_package'),
            lambda: self.services.updates.install_update_from_download(item.id),
            on_done=done,
            on_error=failed,
        )

    def import_archive(self) -> None:
        game_id = self.require_game()
        if game_id is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr('ui.pages.downloads.select_local_mod_archive'),
            "",
            tr('ui.pages.downloads.mod_archives_zip_7z_tar_gz_all_files'),
        )
        if not path:
            return
        self._install_local(Path(path), game_id)

    def _install_local(self, archive: Path, game_id: int) -> None:
        self._install_game_id = game_id
        self.download_hint.setText(tr('ui.pages.downloads.analyzing_archive'))

        def previewed(plan) -> None:  # type: ignore[no-untyped-def]
            self._pending_plan = plan
            dialog = InstallPreviewDialog(plan, self)
            if dialog.exec() != dialog.Accepted:
                self.services.mods.discard_preview(plan)
                self._pending_plan = None
                self.download_hint.setText("")
                return
            self._execute_install(plan, game_id)

        self.tasks.run(
            tr('ui.pages.downloads.analyze_mod_archive'),
            lambda: self.services.mods.preview_install(game_id, archive),
            on_done=previewed,
            on_error=lambda message: self._install_failed(message),
        )

    def _execute_install(self, plan: InstallPlan, game_id: int) -> None:
        self.download_hint.setText(tr('ui.pages.downloads.installing'))

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self._pending_plan = None
            self.download_hint.setText("")
            self.toast(tr('ui.pages.downloads.installed_0_mods', len(result.installed_names) or len(plan.entries)), "success")
            self.controller.on_data_changed()
            self.refresh_downloads()

        self.tasks.run(
            tr('ui.pages.downloads.install_mod'),
            lambda: self.services.mods.install_plan(
                game_id,
                plan,
                allow_replace=True,
                allow_duplicate=True,
            ),
            on_done=done,
            on_error=lambda message: self._install_failed(message),
        )

    def _install_failed(self, message: str) -> None:
        if self._pending_plan is not None:
            self.services.mods.discard_preview(self._pending_plan)
        self._pending_plan = None
        self.download_hint.setText("")
        self.error(message)

    def open_download_folder(self) -> None:
        item = self._selected_download()
        if item and item.archive_path:
            open_in_explorer(str(Path(item.archive_path).parent))
