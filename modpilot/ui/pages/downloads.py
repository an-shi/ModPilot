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

log = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class DownloadsPage(BasePage):
    title = "下载中心"
    subtitle = "通过 Nexus Mod ID 获取详情、文件并管理下载任务"

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
            "尚未选择游戏",
            "添加游戏后才能使用 Nexus 下载中心。",
            "download",
            make_button("前往游戏管理", icon_name="gamepad", variant="primary", on_click=lambda: self.controller.go_to("GamesPage")),
        )
        self.body.addWidget(self.empty)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.body.addWidget(content)

        self.api_banner = Card()
        banner_row = QHBoxLayout()
        banner_text = QLabel("尚未配置 Nexus API Key。可以浏览说明和导入本地压缩包，但无法通过 API 下载。")
        banner_text.setWordWrap(True)
        banner_icon = QLabel()
        from ..theme import theme_colors

        banner_icon.setPixmap(make_icon("shield", theme_colors(self.controller._theme)["warning"], 20).pixmap(20, 20))
        banner_row.addWidget(banner_icon, 0)
        banner_row.addWidget(banner_text, 1)
        banner_row.addWidget(
            make_button("前往设置", icon_name="settings", variant="primary", on_click=lambda: self.controller.go_to("SettingsPage"))
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
        self.mod_id_edit.setPlaceholderText("例如 3753（星露谷物语 SVE）")
        self.mod_id_edit.setValidator(QIntValidator(1, 2_147_483_647, self))
        self.mod_id_edit.returnPressed.connect(self.query_mod)
        search_row.addWidget(self.mod_id_edit, 1)
        self.query_button = make_button(
            "获取 Mod 详情", icon_name="search", variant="primary", on_click=self.query_mod
        )
        search_row.addWidget(self.query_button)
        search_row.addWidget(
            make_button("打开 Nexus 网页", icon_name="external", on_click=self.open_nexus_page)
        )
        search_row.addWidget(
            make_button("导入本地压缩包", icon_name="folder", on_click=self.import_archive)
        )
        search_card.add_layout(search_row)
        self.query_hint = QLabel("只使用 Nexus 官方 API；下载权限遵循 Nexus 账号与 API 规则。")
        self.query_hint.setObjectName("Muted")
        search_card.add(self.query_hint)
        layout.addWidget(search_card)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        details_wrapper, details_layout = make_scroll_content(splitter)
        details_card = Card()
        self.details_title = QLabel("查询一个 Mod")
        self.details_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.details_title.setWordWrap(True)
        details_card.add(self.details_title)
        self.details_meta = QLabel("输入 Nexus Mod ID 获取作者、版本、依赖与可下载文件。")
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
        files_header = SectionHeader("可下载文件", "选择主文件；汉化包等可选文件请按需选择")
        self.files_table = QTableWidget()
        setup_table(
            self.files_table,
            ["文件名", "版本", "类别", "大小", "上传时间"],
            stretch_column=0,
            row_height=34,
        )
        self.files_table.setMinimumHeight(150)
        self.files_table.itemSelectionChanged.connect(self._file_selection_changed)
        details_card.add(files_header)
        details_card.add(self.files_table, 1)
        file_actions = QHBoxLayout()
        self.download_button = make_button(
            "下载选中文件", icon_name="download", variant="primary", on_click=self.download_selected
        )
        self.download_button.setEnabled(False)
        file_actions.addWidget(self.download_button)
        file_actions.addWidget(
            make_button(
                "打开文件页面",
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
        queue_header = SectionHeader("下载队列", "支持暂停、继续、取消与失败重试")
        self.queue_refresh = make_button(
            "刷新", icon_name="refresh", variant="tool", on_click=self.refresh_downloads
        )
        queue_header.add_action(self.queue_refresh)
        queue_card.add(queue_header)
        self.queue_table = QTableWidget()
        setup_table(
            self.queue_table,
            ["文件", "类型", "状态", "进度", "已下载", "更新时间"],
            stretch_column=0,
            row_height=36,
        )
        self.queue_table.itemSelectionChanged.connect(self._queue_selection_changed)
        queue_card.add(self.queue_table, 1)
        queue_actions = QHBoxLayout()
        queue_actions.setSpacing(6)
        self.pause_button = make_button("暂停", icon_name="pause", on_click=self.toggle_pause)
        self.cancel_button = make_button("取消", icon_name="x", variant="danger", on_click=self.cancel_download)
        self.retry_button = make_button("重试/继续", icon_name="refresh", on_click=self.retry_download)
        self.install_button = make_button(
            "安装已下载", icon_name="package", variant="primary", on_click=self.install_downloaded
        )
        self.open_download_button = make_button(
            "打开所在文件夹", icon_name="folder", on_click=self.open_download_folder
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
            log.exception("读取下载队列失败")
            return
        selected_id = self._selected_download().id if self._selected_download() else None
        clear_table(self.queue_table)
        for item in self._downloads:
            row = self.queue_table.rowCount()
            self.queue_table.insertRow(row)
            self.queue_table.setItem(row, 0, cell(item.file_name, data=item.id, tooltip=item.archive_path or ""))
            kind_label = {"mod": "Mod", "update": "更新", "translation": "汉化"}.get(item.kind.value, item.kind.value)
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
            f"{len(self._downloads)} 个任务 · 非 Premium 账号可在 Nexus 网页下载后导入压缩包"
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
        self.pause_button.setText("暂停")
        self.retry_button.setEnabled(
            item.status in (DownloadStatus.PAUSED, DownloadStatus.FAILED, DownloadStatus.CANCELLED)
        )
        if item.status is DownloadStatus.PAUSED:
            self.retry_button.setText("继续下载")
        elif item.status is DownloadStatus.FAILED:
            self.retry_button.setText("重试")
        else:
            self.retry_button.setText("继续/重试")
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
            self.error("请输入正确的 Nexus Mod ID（数字）。")
            return
        if not self.services.nexus_configured:
            self.error("请先在设置中配置 Nexus API Key。")
            return
        mod_id = int(text)
        domain = self.current_game.nexus_domain
        self.query_button.setEnabled(False)
        self.query_button.setText("查询中…")
        self.query_hint.setText("正在请求 Nexus API…")

        def work():
            info = self.services.nexus.get_mod(domain, mod_id)
            files = self.services.nexus.get_files(domain, mod_id)
            return info, files

        self.tasks.run("获取 Nexus Mod 详情", work, on_done=self._query_done, on_error=self._query_failed)

    def _query_done(self, result) -> None:  # type: ignore[no-untyped-def]
        self.query_button.setEnabled(True)
        self.query_button.setText("获取 Mod 详情")
        info, files = result
        self._mod_info = info
        self._files = files
        self.details_title.setText(info.name)
        self.details_meta.setText(
            f"Mod ID {info.mod_id} · {info.version or '未知版本'} · {info.author or '未知作者'} · "
            f"{info.category or '未分类'}"
        )
        description = _strip_html(info.description)
        self.details_description.setText(
            (description[:1800] + "…") if len(description) > 1800 else description
        )
        if info.requirements:
            self.requirements_label.setText(
                "Nexus 依赖：" + "；".join(
                    f"{req.mod_name}" + (f"（{req.url}）" if req.url else "")
                    for req in info.requirements
                )
            )
        else:
            self.requirements_label.setText("Nexus 未列出额外依赖，仍请核对 Mod 页面说明。")
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
            f"已获取 {len(files)} 个文件。Nexus API 剩余额度："
            f"{self.services.nexus.rate_limit.hourly_remaining if self.services.nexus.rate_limit.hourly_remaining is not None else '未知'}"
        )
        self._file_selection_changed()

    def _query_failed(self, message: str) -> None:
        self.query_button.setEnabled(True)
        self.query_button.setText("获取 Mod 详情")
        self.query_hint.setText("查询失败。")
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
                f"选中：{file.file_name or file.name} · "
                f"{'主文件' if file.is_primary else file.category_name or '可选文件'}"
            )
        else:
            self.file_hint.setText("")

    def download_selected(self) -> None:
        file = self._selected_file()
        info = self._mod_info
        if not file or not info or not self.game_id:
            self.toast("请先查询 Mod 并选择文件。", "warning")
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
        self.toast("下载任务已加入队列。", "success")
        self.refresh_downloads()
        self.controller.on_data_changed()

    def open_nexus_page(self) -> None:
        if not self.current_game:
            return
        mod_id = self.mod_id_edit.text().strip()
        if not mod_id.isdigit():
            self.toast("请先输入 Mod ID。", "warning")
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
        if not self.confirm("取消下载", f"取消“{item.file_name}”？未完成的分片会被删除。"):
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
                "安装更新",
                f"安装更新包？\n{item.archive_path}\n\n安装前会备份当前 Mod，失败时可回滚。",
            ):
                return
            self._install_update(item)
        else:
            self._install_local(Path(item.archive_path), self.game_id)

    def _install_update(self, item: DownloadView) -> None:
        self.install_button.setEnabled(False)
        self.download_hint.setText("正在安装更新…")

        def done(_result) -> None:  # type: ignore[no-untyped-def]
            self.download_hint.setText("更新安装完成。")
            self.toast("更新安装完成。", "success")
            self.controller.on_data_changed()
            self.refresh_downloads()

        def failed(message: str) -> None:
            self.download_hint.setText(message)
            self.error(message)

        self.tasks.run(
            "安装更新包",
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
            "选择本地 Mod 压缩包",
            "",
            "Mod 压缩包 (*.zip *.7z *.tar.gz);;所有文件 (*.*)",
        )
        if not path:
            return
        self._install_local(Path(path), game_id)

    def _install_local(self, archive: Path, game_id: int) -> None:
        self._install_game_id = game_id
        self.download_hint.setText("正在分析压缩包…")

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
            "分析 Mod 压缩包",
            lambda: self.services.mods.preview_install(game_id, archive),
            on_done=previewed,
            on_error=lambda message: self._install_failed(message),
        )

    def _execute_install(self, plan: InstallPlan, game_id: int) -> None:
        self.download_hint.setText("正在安装…")

        def done(result) -> None:  # type: ignore[no-untyped-def]
            self._pending_plan = None
            self.download_hint.setText("")
            self.toast(f"已安装 {len(result.installed_names) or len(plan.entries)} 个 Mod。", "success")
            self.controller.on_data_changed()
            self.refresh_downloads()

        self.tasks.run(
            "安装 Mod",
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
