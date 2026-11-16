from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)

from ...config import LlmConfig
from ...core.credentials import LLM_SECRET, NEXUS_SECRET
from ...core.paths import atomic_write_text
from ...i18n import available_languages, tr
from ...version import VERSION
from ..widgets import (
    Badge,
    Card,
    SectionHeader,
    make_button,
    make_scroll_content,
    open_in_explorer,
    set_busy,
)
from .base import BasePage

log = logging.getLogger(__name__)


def _configure_form(form: QFormLayout) -> None:
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(14)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)


def _roomy_card(card: Card) -> Card:
    layout = card.layout()
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(16)
    return card


class SettingsPage(BasePage):
    title = tr('ui.pages.settings.settings')
    subtitle = tr('ui.pages.settings.nexus_api_ai_model_download_folder_update_schedule_a_835328')

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        wrapper, self.content = make_scroll_content(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrapper)

        # Nexus ---------------------------------------------------------
        nexus_card = _roomy_card(Card())
        nexus_header = SectionHeader("Nexus Mods", tr('ui.pages.settings.official_api_key_is_used_only_for_metadata_and_offic_18f74b'))
        self.nexus_status = Badge(tr('ui.pages.settings.not_configured'), "danger")
        nexus_header.add_action(self.nexus_status)
        nexus_card.add(nexus_header)
        nexus_form = QFormLayout()
        _configure_form(nexus_form)
        self.nexus_key = QLineEdit()
        self.nexus_key.setEchoMode(QLineEdit.Password)
        self.nexus_key.setPlaceholderText(tr('ui.pages.settings.paste_nexus_api_key'))
        nexus_form.addRow("API Key", self.nexus_key)
        key_hint = QLabel(
            tr('ui.pages.settings.the_api_key_is_stored_in_windows_credential_manager_36a449')
        )
        key_hint.setObjectName("Muted")
        key_hint.setWordWrap(True)
        key_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        nexus_form.addRow("", key_hint)
        nexus_key_actions = QHBoxLayout()
        nexus_key_actions.setSpacing(10)
        nexus_key_actions.setContentsMargins(0, 4, 0, 0)
        nexus_key_actions.addWidget(
            make_button(tr('ui.pages.settings.save_api_key'), icon_name="save", variant="primary", on_click=self.save_nexus_key)
        )
        self.test_nexus_button = make_button(tr('ui.pages.settings.test_connection'), icon_name="check", on_click=self.test_nexus)
        nexus_key_actions.addWidget(self.test_nexus_button)
        nexus_key_actions.addStretch(1)
        nexus_key_actions.addWidget(
            make_button(
                tr('ui.pages.settings.get_api_key'),
                icon_name="external",
                on_click=lambda: QDesktopServices.openUrl(
                    QUrl("https://www.nexusmods.com/users/myaccount?tab=api")
                ),
            )
        )
        nexus_key_actions.addWidget(
            make_button(tr('ui.pages.settings.clear_key'), icon_name="trash", variant="danger", on_click=self.clear_nexus_key)
        )
        nexus_form.addRow("", nexus_key_actions)
        self.nexus_info = QLabel("")
        self.nexus_info.setObjectName("Muted")
        self.nexus_info.setWordWrap(True)
        self.nexus_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        nexus_form.addRow(tr('ui.pages.settings.status'), self.nexus_info)
        nexus_card.add_layout(nexus_form)
        self.content.addWidget(nexus_card)

        # AI ------------------------------------------------------------
        ai_card = _roomy_card(Card())
        ai_header = SectionHeader(tr('ui.pages.settings.ai_model'), tr('ui.pages.settings.openai_compatible_api_with_custom_endpoint_model_par_c00b6a'))
        self.ai_status = Badge(tr('ui.pages.settings.not_configured'), "warning")
        ai_header.add_action(self.ai_status)
        ai_card.add(ai_header)
        ai_form = QFormLayout()
        _configure_form(ai_form)
        self.ai_base_url = QLineEdit()
        self.ai_base_url.setPlaceholderText("https://api.openai.com/v1")
        ai_form.addRow("Base URL", self.ai_base_url)
        self.ai_model = QLineEdit()
        self.ai_model.setPlaceholderText(tr('ui.pages.settings.e_g_gpt_4o_mini_deepseek_chat_qwen_plus'))
        ai_form.addRow(tr('ui.pages.settings.model_name'), self.ai_model)
        self.ai_key = QLineEdit()
        self.ai_key.setEchoMode(QLineEdit.Password)
        self.ai_key.setPlaceholderText(tr('ui.pages.settings.enter_api_key'))
        ai_form.addRow("API Key", self.ai_key)
        keys_row = QHBoxLayout()
        keys_row.setSpacing(10)
        keys_row.setContentsMargins(0, 4, 0, 0)
        self.ai_requires_key = QCheckBox(tr('ui.pages.settings.api_requires_an_api_key'))
        self.ai_requires_key.toggled.connect(self._toggle_ai_key)
        keys_row.addWidget(self.ai_requires_key)
        keys_row.addSpacing(8)
        keys_row.addWidget(
            make_button(tr('ui.pages.settings.save_ai_configuration'), icon_name="save", variant="primary", on_click=self.save_ai)
        )
        self.test_ai_button = make_button(tr('ui.pages.settings.test_connection'), icon_name="check", on_click=self.test_ai)
        keys_row.addWidget(self.test_ai_button)
        keys_row.addStretch(1)
        keys_row.addWidget(
            make_button(tr('ui.pages.settings.clear_key'), icon_name="trash", variant="danger", on_click=self.clear_ai_key)
        )
        ai_form.addRow("", keys_row)
        params_row = QHBoxLayout()
        params_row.setSpacing(10)
        self.ai_temperature = QDoubleSpinBox()
        self.ai_temperature.setRange(0.0, 2.0)
        self.ai_temperature.setSingleStep(0.1)
        self.ai_temperature.setDecimals(2)
        self.ai_temperature.setFixedWidth(96)
        params_row.addWidget(QLabel(tr('ui.pages.settings.temperature')))
        params_row.addWidget(self.ai_temperature)
        params_row.addSpacing(8)
        self.ai_max_tokens = QSpinBox()
        self.ai_max_tokens.setRange(64, 131072)
        self.ai_max_tokens.setSingleStep(256)
        self.ai_max_tokens.setFixedWidth(126)
        params_row.addWidget(QLabel(tr('ui.pages.settings.max_tokens')))
        params_row.addWidget(self.ai_max_tokens)
        params_row.addSpacing(8)
        self.ai_timeout = QSpinBox()
        self.ai_timeout.setRange(5, 600)
        self.ai_timeout.setSuffix(tr('ui.pages.settings.sec'))
        self.ai_timeout.setFixedWidth(112)
        params_row.addWidget(QLabel(tr('ui.pages.settings.timeout')))
        params_row.addWidget(self.ai_timeout)
        params_row.addStretch(1)
        ai_form.addRow(tr('ui.pages.settings.parameters'), params_row)
        options_row = QHBoxLayout()
        options_row.setSpacing(18)
        options_row.setContentsMargins(0, 2, 0, 0)
        self.ai_json_mode = QCheckBox(tr('ui.pages.settings.request_json_mode'))
        options_row.addWidget(self.ai_json_mode)
        self.ai_verify_ssl = QCheckBox(tr('ui.pages.settings.verify_tls_certificate'))
        options_row.addWidget(self.ai_verify_ssl)
        options_row.addStretch(1)
        ai_form.addRow("", options_row)
        self.ai_headers = QPlainTextEdit()
        self.ai_headers.setPlaceholderText(
            tr('ui.pages.settings.extra_header_json_e_g_x_provider_value_do_not_enter_1cff68')
        )
        self.ai_headers.setMaximumHeight(64)
        ai_form.addRow(tr('ui.pages.settings.extra_headers'), self.ai_headers)
        self.ai_body = QPlainTextEdit()
        self.ai_body.setPlaceholderText(tr('ui.pages.settings.extra_request_body_json_e_g_top_p_0_9'))
        self.ai_body.setMaximumHeight(64)
        ai_form.addRow(tr('ui.pages.settings.extra_parameters'), self.ai_body)
        ai_hint = QLabel(
            tr('ui.pages.settings.ai_is_only_called_when_you_actively_run_translation_c0377e')
        )
        ai_hint.setObjectName("Muted")
        ai_hint.setWordWrap(True)
        ai_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        ai_form.addRow("", ai_hint)
        self.ai_info = QLabel("")
        self.ai_info.setObjectName("Muted")
        self.ai_info.setWordWrap(True)
        self.ai_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        ai_form.addRow(tr('ui.pages.settings.status'), self.ai_info)
        ai_card.add_layout(ai_form)
        self.content.addWidget(ai_card)

        # Directories ---------------------------------------------------
        dirs_card = _roomy_card(Card())
        dirs_card.add(SectionHeader(tr('ui.pages.settings.directory'), tr('ui.pages.settings.download_backup_and_export_locations')))
        dirs_form = QFormLayout()
        _configure_form(dirs_form)
        download_row = QHBoxLayout()
        download_row.setSpacing(10)
        self.download_dir = QLineEdit()
        self.download_dir.setPlaceholderText(str(self.services.paths.downloads_dir))
        download_row.addWidget(self.download_dir, 1)
        download_row.addWidget(make_button(tr('ui.pages.settings.browse'), icon_name="folder", on_click=self.browse_download_dir))
        download_row.addWidget(make_button(tr('ui.pages.settings.save_directory'), icon_name="save", on_click=self.save_download_dir))
        download_row.addWidget(
            make_button(tr('ui.pages.settings.open'), icon_name="open", on_click=lambda: self._open_configured_downloads())
        )
        dirs_form.addRow(tr('ui.pages.settings.download_folder'), download_row)
        paths_row = QHBoxLayout()
        paths_row.setSpacing(10)
        paths_row.addWidget(
            make_button(tr('ui.pages.settings.data_directory'), icon_name="folder", on_click=lambda: open_in_explorer(str(self.services.paths.base)))
        )
        paths_row.addWidget(
            make_button(tr('ui.pages.settings.log_directory'), icon_name="folder", on_click=lambda: open_in_explorer(str(self.services.paths.logs_dir)))
        )
        paths_row.addWidget(
            make_button(tr('ui.pages.settings.backup_directory'), icon_name="backup", on_click=lambda: open_in_explorer(str(self.services.paths.backups_dir)))
        )
        paths_row.addWidget(
            make_button(tr('ui.pages.settings.export_folder'), icon_name="download", on_click=lambda: open_in_explorer(str(self.services.paths.exports_dir)))
        )
        paths_row.addStretch(1)
        dirs_form.addRow(tr('ui.pages.settings.local_data'), paths_row)
        dirs_card.add_layout(dirs_form)
        self.content.addWidget(dirs_card)

        # Updates / appearance -----------------------------------------
        behavior_card = _roomy_card(Card())
        behavior_card.add(SectionHeader(tr('ui.pages.settings.updates_appearance'), tr('ui.pages.settings.global_update_policy_and_check_schedule')))
        behavior_form = QFormLayout()
        _configure_form(behavior_form)
        policy_row = QHBoxLayout()
        policy_row.setSpacing(10)
        self.update_policy = QComboBox()
        self.update_policy.setMinimumWidth(190)
        for label, value in (
            (tr('ui.pages.settings.manual_check_only'), "manual"),
            (tr('ui.pages.settings.update_alerts'), "notify"),
            (tr('ui.pages.settings.download_after_confirmation'), "confirm_download"),
            (tr('ui.pages.settings.auto_download'), "auto_download"),
            (tr('ui.pages.settings.auto_download_and_install'), "auto_install"),
            (tr('ui.pages.settings.ignore_update'), "ignore"),
        ):
            self.update_policy.addItem(label, value)
        policy_row.addWidget(self.update_policy)
        self.check_on_startup = QCheckBox(tr('ui.pages.settings.check_for_updates_on_startup'))
        policy_row.addWidget(self.check_on_startup)
        policy_row.addStretch(1)
        behavior_form.addRow(tr('ui.pages.settings.update'), policy_row)
        interval_row = QHBoxLayout()
        interval_row.setSpacing(10)
        interval_row.setContentsMargins(0, 2, 0, 0)
        self.check_interval = QSpinBox()
        self.check_interval.setRange(0, 720)
        self.check_interval.setSuffix(tr('ui.pages.settings.hours'))
        self.check_interval.setSpecialValueText(tr('ui.pages.settings.turn_off_scheduled_checks'))
        self.check_interval.setMinimumWidth(150)
        interval_row.addWidget(QLabel(tr('ui.pages.settings.check_interval')))
        interval_row.addWidget(self.check_interval)
        interval_row.addSpacing(6)
        save_behavior = make_button(tr('ui.pages.settings.save_update_settings'), icon_name="save", on_click=self.save_behavior)
        interval_row.addWidget(save_behavior)
        interval_row.addStretch(1)
        behavior_form.addRow("", interval_row)
        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)
        theme_row.addWidget(QLabel(tr('ui.pages.settings.theme')))
        self.theme_combo = QComboBox()
        self.theme_combo.setMinimumWidth(140)
        self.theme_combo.addItem(tr('ui.pages.settings.dark'), "dark")
        self.theme_combo.addItem(tr('ui.pages.settings.light'), "light")
        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        theme_row.addWidget(self.theme_combo)
        theme_row.addSpacing(18)
        theme_row.addWidget(QLabel(tr('ui.pages.settings.ui_language')))
        self.language_combo = QComboBox()
        self.language_combo.setMinimumWidth(150)
        for code, label in available_languages().items():
            self.language_combo.addItem(label, code)
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        theme_row.addWidget(self.language_combo)
        theme_row.addStretch(1)
        behavior_form.addRow(tr('ui.pages.settings.appearance'), theme_row)
        behavior_card.add_layout(behavior_form)
        self.content.addWidget(behavior_card)

        # Data ----------------------------------------------------------
        data_card = _roomy_card(Card())
        data_card.add(SectionHeader(tr('ui.pages.settings.local_data_security'), tr('ui.pages.settings.all_data_is_stored_on_this_pc_by_default_no_account_1c9452')))
        data_form = QFormLayout()
        _configure_form(data_form)
        self.credentials_label = QLabel("")
        self.credentials_label.setObjectName("Muted")
        self.credentials_label.setWordWrap(True)
        self.credentials_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        data_form.addRow(tr('ui.pages.settings.credential_storage'), self.credentials_label)
        data_row = QHBoxLayout()
        data_row.setSpacing(10)
        data_row.addWidget(
            make_button(tr('ui.pages.settings.export_configuration_without_keys'), icon_name="download", on_click=self.export_config)
        )
        data_row.addWidget(make_button(tr('ui.pages.settings.back_up_database'), icon_name="backup", on_click=self.backup_database))
        data_row.addStretch(1)
        data_form.addRow(tr('ui.pages.settings.maintenance'), data_row)
        data_row2 = QHBoxLayout()
        data_row2.setSpacing(10)
        data_row2.setContentsMargins(0, 2, 0, 0)
        data_row2.addWidget(
            make_button(tr('ui.pages.settings.clear_translation_cache'), icon_name="trash", variant="danger", on_click=self.clear_translation_cache)
        )
        data_row2.addWidget(
            make_button(tr('ui.pages.settings.open_log_file'), icon_name="external", on_click=lambda: open_in_explorer(str(self.services.log_path)))
        )
        data_row2.addStretch(1)
        data_form.addRow("", data_row2)
        data_card.add_layout(data_form)
        self.content.addWidget(data_card)

        about_card = _roomy_card(Card())
        about_card.add(SectionHeader(tr('ui.pages.settings.about_modpilot'), tr('ui.pages.settings.lightweight_local_first_multi_game_support')))
        about = QLabel(
            tr('ui.pages.settings.version_0_data_directory_1_database_2_core_managemen_18ec1f', VERSION, self.services.paths.base, self.services.paths.db_path)
        )
        about.setObjectName("Muted")
        about.setWordWrap(True)
        about.setTextInteractionFlags(Qt.TextSelectableByMouse)
        about_card.add(about)
        self.content.addWidget(about_card)
        self.content.setSpacing(20)
        self.content.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        config = self.services.config.config
        self.nexus_key.clear()
        self.nexus_key.setPlaceholderText(
            tr('ui.pages.settings.saved_enter_a_new_key_to_replace_it') if self.services.credentials.has(NEXUS_SECRET) else tr('ui.pages.settings.paste_nexus_api_key')
        )
        self._update_nexus_status()
        self.ai_base_url.setText(config.llm.base_url)
        self.ai_model.setText(config.llm.model)
        self.ai_requires_key.setChecked(config.llm.requires_api_key)
        self.ai_temperature.setValue(config.llm.temperature)
        self.ai_max_tokens.setValue(config.llm.max_tokens)
        self.ai_timeout.setValue(config.llm.timeout_seconds)
        self.ai_json_mode.setChecked(config.llm.json_mode)
        self.ai_verify_ssl.setChecked(config.llm.verify_ssl)
        self.ai_headers.setPlainText(
            json.dumps(config.llm.extra_headers, ensure_ascii=False) if config.llm.extra_headers else ""
        )
        self.ai_body.setPlainText(
            json.dumps(config.llm.extra_body, ensure_ascii=False) if config.llm.extra_body else ""
        )
        self.ai_key.clear()
        self.ai_key.setPlaceholderText(
            tr('ui.pages.settings.saved_enter_a_new_key_to_replace_it') if self.services.credentials.has(LLM_SECRET) else tr('ui.pages.settings.enter_api_key')
        )
        self._toggle_ai_key(config.llm.requires_api_key)
        self._update_ai_status()
        self.download_dir.setText(config.download_dir or "")
        self.update_policy.blockSignals(True)
        index = self.update_policy.findData(config.update_policy)
        self.update_policy.setCurrentIndex(max(0, index))
        self.update_policy.blockSignals(False)
        self.check_on_startup.setChecked(config.check_updates_on_startup)
        self.check_interval.setValue(config.update_check_interval_hours)
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(config.theme)))
        self.theme_combo.blockSignals(False)
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(config.ui_language))
        )
        self.language_combo.blockSignals(False)
        self.credentials_label.setText(
            tr('ui.pages.settings.0_sensitive_keys_are_stored_separately_and_never_app_7e8628', self.services.credentials.backend_name)
        )
        try:
            rate = self.services.nexus.rate_limit
            if rate.hourly_remaining is not None:
                self.nexus_info.setText(
                    tr('ui.pages.settings.api_quota_0_left_this_hour_1_left_today', rate.hourly_remaining, rate.daily_remaining if rate.daily_remaining is not None else tr('ui.pages.settings.unknown'))
                )
        except Exception:
            pass

    def _update_nexus_status(self) -> None:
        configured = self.services.credentials.has(NEXUS_SECRET)
        self.nexus_status.set_status(tr('ui.pages.settings.configured') if configured else tr('ui.pages.settings.not_configured'), "success" if configured else "danger")

    def _update_ai_status(self) -> None:
        configured = self.services.llm_configured
        self.ai_status.set_status(tr('ui.pages.settings.ready') if configured else tr('ui.pages.settings.not_configured'), "success" if configured else "warning")
        if configured:
            self.ai_info.setText(
                tr('ui.pages.settings.current_model_0_save_after_changing_parameters', self.services.config.config.llm.model)
            )
        else:
            self.ai_info.setText(tr('ui.pages.settings.enter_the_base_url_model_name_and_api_key_to_use_ai_752992'))

    def _toggle_ai_key(self, checked: bool) -> None:
        self.ai_key.setEnabled(checked)

    # ------------------------------------------------------------------
    # Nexus
    # ------------------------------------------------------------------

    def save_nexus_key(self) -> bool:
        value = self.nexus_key.text().strip()
        if not value:
            self.toast(tr('ui.pages.settings.enter_an_api_key_leaving_it_blank_will_not_change_th_a3916e'), "warning")
            return False
        try:
            self.services.credentials.set(NEXUS_SECRET, value)
        except Exception as exc:
            self.error(str(exc))
            return False
        self.nexus_key.clear()
        self.nexus_key.setPlaceholderText(tr('ui.pages.settings.saved_enter_a_new_key_to_replace_it'))
        self._update_nexus_status()
        self.toast(tr('ui.pages.settings.nexus_api_key_saved'), "success")
        return True

    def clear_nexus_key(self) -> None:
        if not self.confirm(tr('ui.pages.settings.clear_nexus_api_key'), tr('ui.pages.settings.you_will_need_to_enter_it_again_to_use_the_nexus_api'), danger=True):
            return
        try:
            self.services.credentials.delete(NEXUS_SECRET)
        except Exception as exc:
            self.error(str(exc))
            return
        self._update_nexus_status()
        self.nexus_key.setPlaceholderText(tr('ui.pages.settings.paste_nexus_api_key'))
        self.toast(tr('ui.pages.settings.nexus_api_key_cleared'), "success")

    def test_nexus(self) -> None:
        if self.nexus_key.text().strip() and not self.save_nexus_key():
            return
        if not self.services.nexus_configured:
            self.error(tr('ui.pages.settings.please_save_the_nexus_api_key_first'))
            return
        self.test_nexus_button.setEnabled(False)
        set_busy(self.test_nexus_button, True, tr('ui.pages.settings.testing'))

        def done(data) -> None:  # type: ignore[no-untyped-def]
            set_busy(self.test_nexus_button, False)
            premium = "Premium" if data.get("is_premium") else tr('ui.pages.settings.non_premium_direct_api_downloads_limited')
            self.nexus_info.setText(
                tr('ui.pages.settings.connection_successful_0_1', data.get('name') or tr('ui.pages.settings.nexus_user'), premium)
            )
            self.toast(tr('ui.pages.settings.nexus_api_connection_ok'), "success")

        def failed(message: str) -> None:
            set_busy(self.test_nexus_button, False)
            self.nexus_info.setText(tr('ui.pages.settings.connection_failed_0', message))
            self.error(message)

        self.tasks.run(tr('ui.pages.settings.test_nexus_api'), self.services.nexus.validate_key, on_done=done, on_error=failed)

    # ------------------------------------------------------------------
    # AI
    # ------------------------------------------------------------------

    def save_ai(self, *, silent: bool = False) -> bool:
        try:
            headers = json.loads(self.ai_headers.toPlainText().strip() or "{}")
            body = json.loads(self.ai_body.toPlainText().strip() or "{}")
            if not isinstance(headers, dict):
                raise ValueError(tr('ui.pages.settings.extra_headers_must_be_a_json_object'))
            if not isinstance(body, dict):
                raise ValueError(tr('ui.pages.settings.extra_parameters_must_be_a_json_object'))
            llm = LlmConfig(
                base_url=self.ai_base_url.text().strip() or "https://api.openai.com/v1",
                model=self.ai_model.text().strip(),
                requires_api_key=self.ai_requires_key.isChecked(),
                temperature=self.ai_temperature.value(),
                max_tokens=self.ai_max_tokens.value(),
                timeout_seconds=self.ai_timeout.value(),
                json_mode=self.ai_json_mode.isChecked(),
                verify_ssl=self.ai_verify_ssl.isChecked(),
                extra_headers={str(k): str(v) for k, v in headers.items()},
                extra_body=body,
            )
        except Exception as exc:
            self.error(tr('ui.pages.settings.invalid_ai_configuration_0', exc))
            return False
        key = self.ai_key.text().strip()
        if key:
            try:
                self.services.credentials.set(LLM_SECRET, key)
            except Exception as exc:
                self.error(str(exc))
                return False
            self.ai_key.clear()
            self.ai_key.setPlaceholderText(tr('ui.pages.settings.saved_enter_a_new_key_to_replace_it'))
        try:
            self.services.config.update(llm=llm)
        except Exception as exc:
            self.error(tr('ui.pages.settings.failed_to_save_ai_configuration_0', exc))
            return False
        self._update_ai_status()
        if not silent:
            self.toast(tr('ui.pages.settings.ai_configuration_saved'), "success")
        return True

    def clear_ai_key(self) -> None:
        if not self.confirm(tr('ui.pages.settings.clear_ai_api_key'), tr('ui.pages.settings.you_will_need_to_enter_it_again_for_ai_translation_a_28ae08'), danger=True):
            return
        try:
            self.services.credentials.delete(LLM_SECRET)
        except Exception as exc:
            self.error(str(exc))
            return
        self.ai_key.setPlaceholderText(tr('ui.pages.settings.enter_api_key'))
        self._update_ai_status()
        self.toast(tr('ui.pages.settings.ai_api_key_cleared'), "success")

    def test_ai(self) -> None:
        if not self.save_ai(silent=True):
            return
        if not self.services.llm_configured:
            self.error(tr('ui.pages.settings.please_enter_a_model_name_and_save_the_api_key'))
            return
        set_busy(self.test_ai_button, True, tr('ui.pages.settings.testing'))

        def done(message: str) -> None:
            set_busy(self.test_ai_button, False)
            self.ai_info.setText(message)
            self.toast(tr('ui.pages.settings.ai_api_connection_ok'), "success")

        def failed(message: str) -> None:
            set_busy(self.test_ai_button, False)
            self.ai_info.setText(tr('ui.pages.settings.connection_failed_0', message))
            self.error(message)

        self.tasks.run(tr('ui.pages.settings.test_ai_api'), self.services.llm.test_connection, on_done=done, on_error=failed)

    # ------------------------------------------------------------------
    # Other sections
    # ------------------------------------------------------------------

    def browse_download_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, tr('ui.pages.settings.select_download_folder'), self.download_dir.text() or str(self.services.paths.downloads_dir)
        )
        if path:
            self.download_dir.setText(path)

    def save_download_dir(self) -> None:
        value = self.download_dir.text().strip()
        try:
            target = self.services.paths.resolve_downloads(value or None)
            self.services.config.update(download_dir=str(target) if value else None)
        except Exception as exc:
            self.error(str(exc))
            return
        self.download_dir.setText(str(target) if value else "")
        self.toast(tr('ui.pages.settings.download_folder_set_to_0', target), "success")

    def _open_configured_downloads(self) -> None:
        target = self.services.paths.resolve_downloads(self.services.config.config.download_dir)
        open_in_explorer(str(target))

    def save_behavior(self) -> None:
        try:
            self.services.config.update(
                update_policy=str(self.update_policy.currentData()),
                check_updates_on_startup=self.check_on_startup.isChecked(),
                update_check_interval_hours=self.check_interval.value(),
            )
        except Exception as exc:
            self.error(str(exc))
            return
        self.controller.setup_scheduler()
        self.toast(tr('ui.pages.settings.update_settings_saved'), "success")

    def _theme_changed(self) -> None:
        theme = str(self.theme_combo.currentData())
        if theme and theme != self.services.config.config.theme:
            self.controller.set_theme(theme)
            self.toast(tr('ui.pages.settings.theme_switched'), "success")

    def _language_changed(self) -> None:
        language = str(self.language_combo.currentData())
        if not language or language == self.services.config.config.ui_language:
            return
        self.services.config.update(ui_language=language)
        self.info(
            tr('ui.pages.settings.ui_language_switched_the_full_interface_will_be_appl_676d40'),
            tr('ui.pages.settings.ui_language'),
        )

    def export_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr('ui.pages.settings.export_configuration'),
            str(self.services.paths.exports_dir / "modpilot-config.json"),
            "JSON (*.json)",
        )
        if not path:
            return
        config_data = self.services.config.config.redacted_dict()
        llm_data = config_data.get("llm", {})
        if isinstance(llm_data, dict):
            # Custom headers/body are user-controlled and could contain secrets
            # from an older configuration. Never export their values verbatim.
            llm_data["extra_headers"] = {
                key: "[REDACTED]" for key in (llm_data.get("extra_headers") or {})
            }
            llm_data["extra_body"] = {}
        payload = {
            "schema_version": 1,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "note": "Secrets are stored separately in Windows Credential Manager and are not exported.",
            "config": config_data,
        }
        try:
            atomic_write_text(Path(path), json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception as exc:
            self.error(str(exc))
            return
        self.toast(tr('ui.pages.settings.configuration_exported_0', path), "success")

    def backup_database(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr('ui.pages.settings.back_up_database'),
            str(self.services.paths.exports_dir / f"modpilot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"),
            "SQLite (*.db)",
        )
        if not path:
            return
        try:
            source = sqlite3.connect(str(self.services.paths.db_path))
            destination = sqlite3.connect(path)
            with destination:
                source.backup(destination)
            destination.close()
            source.close()
        except Exception as exc:
            self.error(tr('ui.pages.settings.failed_to_back_up_database_0', exc))
            return
        self.toast(tr('ui.pages.settings.database_backed_up_0', path), "success")

    def clear_translation_cache(self) -> None:
        if not self.confirm(
            tr('ui.pages.settings.clear_translation_cache'),
            tr('ui.pages.settings.delete_local_shared_translation_cache_translation_re_355c14'),
            danger=True,
        ):
            return
        self.tasks.run(
            tr('ui.pages.settings.clear_translation_cache'),
            self.services.translation.clear_cache,
            on_done=lambda count: self.toast(tr('ui.pages.settings.cleared_0_cached_translations', count), "success"),
            on_error=self.error,
        )
