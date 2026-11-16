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
from ...i18n import tr

log = logging.getLogger(__name__)


class GamesPage(BasePage):
    title = tr('ui.pages.games.game_management')
    subtitle = tr('ui.pages.games.add_game_installation_folders_the_core_engine_access_34c53b')

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        wrapper, self.content = make_scroll_content(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrapper)

        managed_header = SectionHeader(tr('ui.pages.games.managed_games'), tr('ui.pages.games.each_adapter_manages_one_game_installation'))
        self.refresh_button = make_button(
            tr('ui.pages.games.refresh'), icon_name="refresh", variant="tool", on_click=self.refresh
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
        add_header = SectionHeader(tr('ui.pages.games.add_game'), tr('ui.pages.games.select_an_adapter_and_specify_the_game_installation_ddea2c'))
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
        self.path_edit.setPlaceholderText(tr('ui.pages.games.e_g_d_steamlibrary_steamapps_common_stardew_valley'))
        form.addWidget(self.path_edit, 1)
        self.browse_button = make_button(
            tr('ui.pages.games.browse'), icon_name="folder", on_click=self.browse_folder
        )
        form.addWidget(self.browse_button)
        self.detect_button = make_button(
            tr('ui.pages.games.auto_detect'), icon_name="search", on_click=self.detect_installations
        )
        form.addWidget(self.detect_button)
        self.add_button = make_button(
            tr('ui.pages.games.add_game'), icon_name="plus", variant="primary", on_click=self.add_game
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
        info_header = SectionHeader(tr('ui.pages.games.adapter_description'), tr('ui.pages.games.adding_a_game_only_requires_implementing_the_unified_4995a6'))
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
                    tr('ui.pages.games.no_games_added_yet'),
                    tr('ui.pages.games.once_added_you_can_scan_mods_set_up_nexus_downloads_2dc5b3'),
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
                title_row.addWidget(Badge(tr('ui.pages.games.current_game'), "accent"))
            if game.game_version:
                title_row.addWidget(Badge(tr('ui.pages.games.game_0', game.game_version), "muted"))
            if game.framework_installed:
                label = f"SMAPI {game.framework_version}" if game.framework_version else tr('ui.pages.games.smapi_installed')
                title_row.addWidget(Badge(label, "success"))
            else:
                title_row.addWidget(Badge(tr('ui.pages.games.missing_smapi'), "warning"))
            title_row.addStretch(1)
            text.addLayout(title_row)
            path = QLabel(game.install_path)
            path.setObjectName("Muted")
            path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            text.addWidget(path)
            text.addWidget(
                QLabel(
                    tr('ui.pages.games.0_mods_1_enabled_2_pending_updates', game.mod_count, game.enabled_count, game.update_count)
                )
            )
            layout.addLayout(text, 1)
            switch = make_button(
                tr('ui.pages.games.switch'),
                icon_name="check",
                variant="primary" if game.id != active_id else None,
                on_click=lambda gid=game.id: self.switch_game(gid),
            )
            switch.setEnabled(game.id != active_id)
            layout.addWidget(switch)
            layout.addWidget(
                make_button(
                    tr('ui.pages.games.open_folder'),
                    icon_name="folder",
                    variant="ghost",
                    on_click=lambda path=game.install_path: open_in_explorer(path),
                )
            )
            layout.addWidget(
                make_button(
                    tr('ui.pages.games.remove'),
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
            self.adapter_hint.setText(tr('ui.pages.games.no_adapters_available'))
            self.adapter_details.setText("")
            return
        self.adapter_hint.setText(info.description)
        for capability in info.capabilities:
            self.capabilities_layout.addWidget(Badge(capability, "muted"))
        self.capabilities_layout.addStretch(1)
        self.adapter_details.setText(
            tr(
                'ui.pages.games.nexus_game_id_0_when_adding_a_game_implement_the_uni_e24860',
                info.nexus_domain or tr('ui.pages.games.not_configured'),
            )
        )

    def browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr('ui.pages.games.select_game_installation_root_folder'), self.path_edit.text())
        if path:
            self.path_edit.setText(path)

    def detect_installations(self) -> None:
        key = self.adapter_combo.currentData()
        if not key:
            return
        self.detect_button.setEnabled(False)
        self.detect_button.setText(tr('ui.pages.games.detecting'))
        self.tasks.run(
            tr('ui.pages.games.detect_game_installations'),
            lambda: self.services.games.detect_installations(str(key)),
            on_done=self._detected,
            on_error=self._detect_failed,
        )

    def _detected(self, installations) -> None:  # type: ignore[no-untyped-def]
        self.detect_button.setEnabled(True)
        self.detect_button.setText(tr('ui.pages.games.auto_detect'))
        valid = [item for item in installations if item.valid]
        if not valid:
            self.info(
                tr('ui.pages.games.no_usable_game_installation_was_found_automatically_901ee5'),
                tr('ui.pages.games.no_game_found'),
            )
            return
        if len(valid) == 1:
            chosen = valid[0]
        else:
            labels = [f"{item.source} · {item.path}" for item in valid]
            label, ok = QInputDialog.getItem(self, tr('ui.pages.games.select_game_installation'), tr('ui.pages.games.multiple_installations_detected'), labels, 0, False)
            if not ok:
                return
            chosen = valid[labels.index(label)]
        self.path_edit.setText(str(chosen.path))
        self.toast(tr('ui.pages.games.game_installation_detected_0', chosen.path), "success")

    def _detect_failed(self, message: str) -> None:
        self.detect_button.setEnabled(True)
        self.detect_button.setText(tr('ui.pages.games.auto_detect'))
        self.toast(tr('ui.pages.games.detection_failed_0', message), "danger")

    def add_game(self) -> None:
        key = self.adapter_combo.currentData()
        path = self.path_edit.text().strip()
        if not key:
            self.error(tr('ui.pages.games.no_game_adapters_available'))
            return
        if not path:
            self.error(tr('ui.pages.games.please_enter_or_select_the_game_installation_directo_eef096'))
            return
        self.add_button.setEnabled(False)
        self.add_button.setText(tr('ui.pages.games.adding'))

        def work():
            validation = self.services.games.validate(str(key), path)
            if not validation.valid:
                raise ValueError(validation.message)
            return self.services.games.add_game(str(key), validation.install_path)

        self.tasks.run(
            tr('ui.pages.games.add_game'),
            work,
            on_done=self._added,
            on_error=self._add_failed,
        )

    def _added(self, summary) -> None:  # type: ignore[no-untyped-def]
        self.add_button.setEnabled(True)
        self.add_button.setText(tr('ui.pages.games.add_game'))
        self.path_edit.clear()
        self.toast(tr('ui.pages.games.added_0', summary.name), "success")
        self.controller.refresh_games()
        self.refresh()

    def _add_failed(self, message: str) -> None:
        self.add_button.setEnabled(True)
        self.add_button.setText(tr('ui.pages.games.add_game'))
        self.error(message)

    def switch_game(self, game_id: int) -> None:
        try:
            self.services.games.set_active(game_id)
        except Exception as exc:
            self.error(str(exc))
            return
        self.controller.refresh_games()
        self.controller.on_data_changed()
        self.toast(tr('ui.pages.games.current_game_switched'), "success")

    def remove_game(self, game_id: int, name: str) -> None:
        if not self.confirm(
            tr('ui.pages.games.remove_game'),
            tr('ui.pages.games.remove_the_management_record_for_0_from_modpilot_gam_911e57', name),
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
        self.toast(tr('ui.pages.games.game_management_record_removed'), "success")
