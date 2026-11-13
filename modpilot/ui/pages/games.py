from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ..widgets import (
    Badge,
    Card,
    EmptyState,
    SectionHeader,
    clear_layout,
    make_button,
    make_icon,
    make_scroll_content,
    open_in_explorer,
)
from .base import BasePage

log = logging.getLogger(__name__)


class GamesPage(BasePage):
    title = "游戏管理"
    subtitle = "添加游戏安装目录；核心引擎只通过适配器访问游戏文件"

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        wrapper, self.content = make_scroll_content(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrapper)

        managed_header = SectionHeader("已管理的游戏", "每个适配器管理一套游戏安装")
        self.refresh_button = make_button(
            "刷新", icon_name="refresh", variant="tool", on_click=self.refresh
        )
        managed_header.add_action(self.refresh_button)
        self.managed_card = Card()
        self.managed_card.add(managed_header)
        self.managed_container = QWidget()
        self.managed_layout = QVBoxLayout(self.managed_container)
        self.managed_layout.setContentsMargins(0, 0, 0, 0)
        self.managed_layout.setSpacing(10)
        self.managed_card.add(self.managed_container)
        self.content.addWidget(self.managed_card)

        add_card = Card()
        add_header = SectionHeader("添加游戏", "选择适配器并指定游戏安装根目录")
        add_card.add(add_header)
        form = QHBoxLayout()
        form.setSpacing(10)
        self.adapter_combo = QComboBox()
        self.adapter_combo.setMinimumWidth(190)
        for info in self.services.games.adapters():
            self.adapter_combo.addItem(info.name, info.key)
        self.adapter_combo.currentIndexChanged.connect(self._adapter_changed)
        form.addWidget(self.adapter_combo)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("例如 D:\\SteamLibrary\\steamapps\\common\\Stardew Valley")
        form.addWidget(self.path_edit, 1)
        self.browse_button = make_button(
            "浏览…", icon_name="folder", on_click=self.browse_folder
        )
        form.addWidget(self.browse_button)
        self.detect_button = make_button(
            "自动检测", icon_name="search", on_click=self.detect_installations
        )
        form.addWidget(self.detect_button)
        self.add_button = make_button(
            "添加游戏", icon_name="plus", variant="primary", on_click=self.add_game
        )
        form.addWidget(self.add_button)
        add_card.add_layout(form)
        self.adapter_hint = QLabel("")
        self.adapter_hint.setObjectName("Muted")
        self.adapter_hint.setWordWrap(True)
        add_card.add(self.adapter_hint)
        self.capabilities_layout = QHBoxLayout()
        self.capabilities_layout.setSpacing(6)
        add_card.add_layout(self.capabilities_layout)
        self.content.addWidget(add_card)

        info_card = Card()
        info_header = SectionHeader("适配器说明", "新增游戏只需要实现统一适配器")
        info_card.add(info_header)
        self.adapter_details = QLabel()
        self.adapter_details.setWordWrap(True)
        self.adapter_details.setObjectName("Muted")
        info_card.add(self.adapter_details)
        self.content.addWidget(info_card)
        self.content.addStretch(1)
        self._adapter_changed()

    def refresh(self) -> None:
        clear_layout(self.managed_layout)
        games = self.services.games.list_games()
        if not games:
            self.managed_layout.addWidget(
                EmptyState(
                    "还没有添加游戏",
                    "添加后即可扫描 Mod、配置 Nexus 下载、汉化与日志诊断。",
                    "gamepad",
                )
            )
            return
        active_id = self.services.config.config.active_game_id
        for game in games:
            row = QFrame()
            row.setObjectName("CardFlat")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(12)
            icon_label = QLabel()
            icon_label.setPixmap(make_icon("gamepad", self._tone_color("accent"), 26).pixmap(26, 26))
            layout.addWidget(icon_label)
            text = QVBoxLayout()
            text.setSpacing(2)
            title_row = QHBoxLayout()
            title_row.setSpacing(8)
            name = QLabel(game.name)
            name.setStyleSheet("font-weight: 650; font-size: 14px;")
            title_row.addWidget(name)
            if game.id == active_id:
                title_row.addWidget(Badge("当前游戏", "accent"))
            if game.game_version:
                title_row.addWidget(Badge(f"游戏 {game.game_version}", "muted"))
            if game.framework_installed:
                label = f"SMAPI {game.framework_version}" if game.framework_version else "SMAPI 已安装"
                title_row.addWidget(Badge(label, "success"))
            else:
                title_row.addWidget(Badge("缺少 SMAPI", "warning"))
            title_row.addStretch(1)
            text.addLayout(title_row)
            path = QLabel(game.install_path)
            path.setObjectName("Muted")
            path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            text.addWidget(path)
            text.addWidget(
                QLabel(
                    f"{game.mod_count} 个 Mod · {game.enabled_count} 个启用 · {game.update_count} 个待更新"
                )
            )
            layout.addLayout(text, 1)
            switch = make_button(
                "切换",
                icon_name="check",
                variant="primary" if game.id != active_id else None,
                on_click=lambda gid=game.id: self.switch_game(gid),
            )
            switch.setEnabled(game.id != active_id)
            layout.addWidget(switch)
            layout.addWidget(
                make_button(
                    "打开目录",
                    icon_name="folder",
                    variant="ghost",
                    on_click=lambda path=game.install_path: open_in_explorer(path),
                )
            )
            layout.addWidget(
                make_button(
                    "移除",
                    icon_name="trash",
                    variant="danger",
                    on_click=lambda gid=game.id, name=game.name: self.remove_game(gid, name),
                )
            )
            self.managed_layout.addWidget(row)

    def _tone_color(self, tone: str) -> str:
        from ..theme import theme_colors

        return theme_colors(self.controller._theme).get(tone, "#5B9DFF")

    def _adapter_changed(self) -> None:
        key = self.adapter_combo.currentData()
        info = next((item for item in self.services.games.adapters() if item.key == key), None)
        clear_layout(self.capabilities_layout)
        if info is None:
            self.adapter_hint.setText("没有可用适配器。")
            self.adapter_details.setText("")
            return
        self.adapter_hint.setText(info.description)
        for capability in info.capabilities:
            self.capabilities_layout.addWidget(Badge(capability, "muted"))
        self.capabilities_layout.addStretch(1)
        self.adapter_details.setText(
            f"Nexus 游戏标识：{info.nexus_domain or '未配置'}\n"
            "新增游戏时，在 adapters 目录实现统一接口即可；核心管理引擎不会包含游戏专属逻辑。"
        )

    def browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择游戏安装根目录", self.path_edit.text())
        if path:
            self.path_edit.setText(path)

    def detect_installations(self) -> None:
        key = self.adapter_combo.currentData()
        if not key:
            return
        self.detect_button.setEnabled(False)
        self.detect_button.setText("检测中…")
        self.tasks.run(
            "检测游戏安装",
            lambda: self.services.games.detect_installations(str(key)),
            on_done=self._detected,
            on_error=self._detect_failed,
        )

    def _detected(self, installations) -> None:  # type: ignore[no-untyped-def]
        self.detect_button.setEnabled(True)
        self.detect_button.setText("自动检测")
        valid = [item for item in installations if item.valid]
        if not valid:
            self.info(
                "未自动找到可用的游戏安装。请点击“浏览…”手动选择游戏根目录（包含 Stardew Valley.exe）。",
                "未找到游戏",
            )
            return
        if len(valid) == 1:
            chosen = valid[0]
        else:
            labels = [f"{item.source} · {item.path}" for item in valid]
            label, ok = QInputDialog.getItem(self, "选择游戏安装", "检测到多个安装：", labels, 0, False)
            if not ok:
                return
            chosen = valid[labels.index(label)]
        self.path_edit.setText(str(chosen.path))
        self.toast(f"已检测到游戏安装：{chosen.path}", "success")

    def _detect_failed(self, message: str) -> None:
        self.detect_button.setEnabled(True)
        self.detect_button.setText("自动检测")
        self.toast(f"检测失败：{message}", "danger")

    def add_game(self) -> None:
        key = self.adapter_combo.currentData()
        path = self.path_edit.text().strip()
        if not key:
            self.error("没有可用的游戏适配器。")
            return
        if not path:
            self.error("请填写或选择游戏安装目录。")
            return
        self.add_button.setEnabled(False)
        self.add_button.setText("添加中…")

        def work():
            validation = self.services.games.validate(str(key), path)
            if not validation.valid:
                raise ValueError(validation.message)
            return self.services.games.add_game(str(key), validation.install_path)

        self.tasks.run(
            "添加游戏",
            work,
            on_done=self._added,
            on_error=self._add_failed,
        )

    def _added(self, summary) -> None:  # type: ignore[no-untyped-def]
        self.add_button.setEnabled(True)
        self.add_button.setText("添加游戏")
        self.path_edit.clear()
        self.toast(f"已添加 {summary.name}。", "success")
        self.controller.refresh_games()
        self.refresh()

    def _add_failed(self, message: str) -> None:
        self.add_button.setEnabled(True)
        self.add_button.setText("添加游戏")
        self.error(message)

    def switch_game(self, game_id: int) -> None:
        try:
            self.services.games.set_active(game_id)
        except Exception as exc:
            self.error(str(exc))
            return
        self.controller.refresh_games()
        self.controller.on_data_changed()
        self.toast("已切换当前游戏。", "success")

    def remove_game(self, game_id: int, name: str) -> None:
        if not self.confirm(
            "移除游戏",
            f"从 ModPilot 移除“{name}”的管理记录。\n\n"
            "不会删除游戏文件、Mods 目录或备份。之后可以重新添加。",
            danger=True,
        ):
            return
        try:
            self.services.games.remove_game(game_id)
        except Exception as exc:
            self.error(str(exc))
            return
        self.controller.refresh_games()
        self.refresh()
        self.toast("已移除游戏管理记录。", "success")
