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
    QSpinBox,
    QVBoxLayout,
)

from ...config import LlmConfig
from ...core.credentials import LLM_SECRET, NEXUS_SECRET
from ...core.paths import atomic_write_text
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


class SettingsPage(BasePage):
    title = "设置"
    subtitle = "Nexus API、AI 模型、下载目录、更新计划与本地数据"

    def __init__(self, controller, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(controller, parent)
        wrapper, self.content = make_scroll_content(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrapper)

        # Nexus ---------------------------------------------------------
        nexus_card = Card()
        nexus_header = SectionHeader("Nexus Mods", "官方 API Key 仅用于元数据与官方下载链接")
        self.nexus_status = Badge("未配置", "danger")
        nexus_header.add_action(self.nexus_status)
        nexus_card.add(nexus_header)
        nexus_form = QFormLayout()
        nexus_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        nexus_form.setHorizontalSpacing(14)
        nexus_form.setVerticalSpacing(8)
        self.nexus_key = QLineEdit()
        self.nexus_key.setEchoMode(QLineEdit.Password)
        self.nexus_key.setPlaceholderText("粘贴 Nexus API Key")
        nexus_form.addRow("API Key", self.nexus_key)
        key_hint = QLabel(
            "API Key 保存在 Windows 凭据管理器中，不会写入配置文件、数据库或日志。"
        )
        key_hint.setObjectName("Muted")
        key_hint.setWordWrap(True)
        nexus_form.addRow("", key_hint)
        nexus_key_actions = QHBoxLayout()
        nexus_key_actions.addWidget(
            make_button("保存 API Key", icon_name="save", variant="primary", on_click=self.save_nexus_key)
        )
        self.test_nexus_button = make_button("测试连接", icon_name="check", on_click=self.test_nexus)
        nexus_key_actions.addWidget(self.test_nexus_button)
        nexus_key_actions.addWidget(
            make_button("清除密钥", icon_name="trash", variant="danger", on_click=self.clear_nexus_key)
        )
        nexus_key_actions.addWidget(
            make_button(
                "获取 API Key",
                icon_name="external",
                on_click=lambda: QDesktopServices.openUrl(
                    QUrl("https://www.nexusmods.com/users/myaccount?tab=api")
                ),
            )
        )
        nexus_key_actions.addStretch(1)
        nexus_form.addRow("", nexus_key_actions)
        self.nexus_info = QLabel("")
        self.nexus_info.setObjectName("Muted")
        self.nexus_info.setWordWrap(True)
        nexus_form.addRow("状态", self.nexus_info)
        nexus_card.add_layout(nexus_form)
        self.content.addWidget(nexus_card)

        # AI ------------------------------------------------------------
        ai_card = Card()
        ai_header = SectionHeader("AI 模型", "OpenAI 兼容接口，自定义地址、模型、参数与请求头")
        self.ai_status = Badge("未配置", "warning")
        ai_header.add_action(self.ai_status)
        ai_card.add(ai_header)
        ai_form = QFormLayout()
        ai_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        ai_form.setHorizontalSpacing(14)
        ai_form.setVerticalSpacing(8)
        self.ai_base_url = QLineEdit()
        self.ai_base_url.setPlaceholderText("https://api.openai.com/v1")
        ai_form.addRow("Base URL", self.ai_base_url)
        self.ai_model = QLineEdit()
        self.ai_model.setPlaceholderText("例如 gpt-4o-mini、deepseek-chat、qwen-plus")
        ai_form.addRow("模型名称", self.ai_model)
        self.ai_key = QLineEdit()
        self.ai_key.setEchoMode(QLineEdit.Password)
        self.ai_key.setPlaceholderText("输入 API Key")
        ai_form.addRow("API Key", self.ai_key)
        keys_row = QHBoxLayout()
        self.ai_requires_key = QCheckBox("接口需要 API Key")
        self.ai_requires_key.toggled.connect(self._toggle_ai_key)
        keys_row.addWidget(self.ai_requires_key)
        keys_row.addWidget(
            make_button("保存 AI 配置", icon_name="save", variant="primary", on_click=self.save_ai)
        )
        self.test_ai_button = make_button("测试连接", icon_name="check", on_click=self.test_ai)
        keys_row.addWidget(self.test_ai_button)
        keys_row.addWidget(
            make_button("清除密钥", icon_name="trash", variant="danger", on_click=self.clear_ai_key)
        )
        keys_row.addStretch(1)
        ai_form.addRow("", keys_row)
        params_row = QHBoxLayout()
        self.ai_temperature = QDoubleSpinBox()
        self.ai_temperature.setRange(0.0, 2.0)
        self.ai_temperature.setSingleStep(0.1)
        self.ai_temperature.setDecimals(2)
        params_row.addWidget(QLabel("温度"))
        params_row.addWidget(self.ai_temperature)
        self.ai_max_tokens = QSpinBox()
        self.ai_max_tokens.setRange(64, 131072)
        self.ai_max_tokens.setSingleStep(256)
        params_row.addWidget(QLabel("最大 Tokens"))
        params_row.addWidget(self.ai_max_tokens)
        self.ai_timeout = QSpinBox()
        self.ai_timeout.setRange(5, 600)
        self.ai_timeout.setSuffix(" 秒")
        params_row.addWidget(QLabel("超时"))
        params_row.addWidget(self.ai_timeout)
        self.ai_json_mode = QCheckBox("请求 JSON 模式")
        params_row.addWidget(self.ai_json_mode)
        self.ai_verify_ssl = QCheckBox("校验 TLS 证书")
        params_row.addWidget(self.ai_verify_ssl)
        params_row.addStretch(1)
        ai_form.addRow("参数", params_row)
        self.ai_headers = QPlainTextEdit()
        self.ai_headers.setPlaceholderText(
            '额外请求头 JSON，例如 {"X-Provider": "value"}；不要在这里填写密钥'
        )
        self.ai_headers.setMaximumHeight(64)
        ai_form.addRow("额外请求头", self.ai_headers)
        self.ai_body = QPlainTextEdit()
        self.ai_body.setPlaceholderText('额外请求体 JSON，例如 {"top_p": 0.9}')
        self.ai_body.setMaximumHeight(64)
        ai_form.addRow("额外参数", self.ai_body)
        ai_hint = QLabel(
            "AI 只在你主动执行翻译、诊断或测试时调用。日志与 Mod 文本会发送到该 API；"
            "API Key 同样使用 Windows 凭据管理器保存。"
        )
        ai_hint.setObjectName("Muted")
        ai_hint.setWordWrap(True)
        ai_form.addRow("", ai_hint)
        self.ai_info = QLabel("")
        self.ai_info.setObjectName("Muted")
        self.ai_info.setWordWrap(True)
        ai_form.addRow("状态", self.ai_info)
        ai_card.add_layout(ai_form)
        self.content.addWidget(ai_card)

        # Directories ---------------------------------------------------
        dirs_card = Card()
        dirs_card.add(SectionHeader("目录", "下载、备份与导出位置"))
        dirs_form = QFormLayout()
        dirs_form.setHorizontalSpacing(14)
        dirs_form.setVerticalSpacing(8)
        download_row = QHBoxLayout()
        self.download_dir = QLineEdit()
        self.download_dir.setPlaceholderText(str(self.services.paths.downloads_dir))
        download_row.addWidget(self.download_dir, 1)
        download_row.addWidget(make_button("浏览…", icon_name="folder", on_click=self.browse_download_dir))
        download_row.addWidget(make_button("保存目录", icon_name="save", on_click=self.save_download_dir))
        download_row.addWidget(
            make_button("打开", icon_name="open", on_click=lambda: self._open_configured_downloads())
        )
        dirs_form.addRow("下载目录", download_row)
        paths_row = QHBoxLayout()
        paths_row.addWidget(
            make_button("数据目录", icon_name="folder", on_click=lambda: open_in_explorer(str(self.services.paths.base)))
        )
        paths_row.addWidget(
            make_button("日志目录", icon_name="folder", on_click=lambda: open_in_explorer(str(self.services.paths.logs_dir)))
        )
        paths_row.addWidget(
            make_button("备份目录", icon_name="backup", on_click=lambda: open_in_explorer(str(self.services.paths.backups_dir)))
        )
        paths_row.addWidget(
            make_button("导出目录", icon_name="download", on_click=lambda: open_in_explorer(str(self.services.paths.exports_dir)))
        )
        paths_row.addStretch(1)
        dirs_form.addRow("本地数据", paths_row)
        dirs_card.add_layout(dirs_form)
        self.content.addWidget(dirs_card)

        # Updates / appearance -----------------------------------------
        behavior_card = Card()
        behavior_card.add(SectionHeader("更新与外观", "全局更新策略与检查计划"))
        behavior_form = QFormLayout()
        behavior_form.setHorizontalSpacing(14)
        behavior_form.setVerticalSpacing(8)
        policy_row = QHBoxLayout()
        self.update_policy = QComboBox()
        for label, value in (
            ("仅手动检查", "manual"),
            ("更新提醒", "notify"),
            ("确认后下载", "confirm_download"),
            ("自动下载", "auto_download"),
            ("自动下载并安装", "auto_install"),
            ("忽略更新", "ignore"),
        ):
            self.update_policy.addItem(label, value)
        policy_row.addWidget(self.update_policy)
        self.check_on_startup = QCheckBox("启动后自动检查更新")
        policy_row.addWidget(self.check_on_startup)
        self.check_interval = QSpinBox()
        self.check_interval.setRange(0, 720)
        self.check_interval.setSuffix(" 小时")
        self.check_interval.setSpecialValueText("关闭定时检查")
        policy_row.addWidget(QLabel("检查间隔"))
        policy_row.addWidget(self.check_interval)
        policy_row.addStretch(1)
        behavior_form.addRow("更新", policy_row)
        save_behavior = make_button("保存更新设置", icon_name="save", on_click=self.save_behavior)
        theme_row = QHBoxLayout()
        theme_row.addWidget(save_behavior)
        theme_row.addSpacing(20)
        theme_row.addWidget(QLabel("主题"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch(1)
        behavior_form.addRow("外观", theme_row)
        behavior_card.add_layout(behavior_form)
        self.content.addWidget(behavior_card)

        # Data ----------------------------------------------------------
        data_card = Card()
        data_card.add(SectionHeader("本地数据与安全", "所有数据默认保存在本机，不需要账号或自建云服务"))
        data_form = QFormLayout()
        data_form.setHorizontalSpacing(14)
        data_form.setVerticalSpacing(8)
        self.credentials_label = QLabel("")
        self.credentials_label.setObjectName("Muted")
        self.credentials_label.setWordWrap(True)
        data_form.addRow("凭据存储", self.credentials_label)
        data_row = QHBoxLayout()
        data_row.addWidget(
            make_button("导出配置（不含密钥）", icon_name="download", on_click=self.export_config)
        )
        data_row.addWidget(make_button("备份数据库", icon_name="backup", on_click=self.backup_database))
        data_row.addWidget(
            make_button("清理翻译缓存", icon_name="trash", variant="danger", on_click=self.clear_translation_cache)
        )
        data_row.addWidget(
            make_button("打开日志文件", icon_name="external", on_click=lambda: open_in_explorer(str(self.services.log_path)))
        )
        data_row.addStretch(1)
        data_form.addRow("维护", data_row)
        data_card.add_layout(data_form)
        self.content.addWidget(data_card)

        about_card = Card()
        about_card.add(SectionHeader("关于 ModPilot", "轻量、本地优先、多游戏适配"))
        about = QLabel(
            f"版本 {VERSION}\n"
            f"数据目录：{self.services.paths.base}\n"
            f"数据库：{self.services.paths.db_path}\n\n"
            "核心管理逻辑与游戏适配器分离；所有 Mod 文件修改都会尽量创建可恢复备份。"
        )
        about.setObjectName("Muted")
        about.setWordWrap(True)
        about.setTextInteractionFlags(Qt.TextSelectableByMouse)
        about_card.add(about)
        self.content.addWidget(about_card)
        self.content.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        config = self.services.config.config
        self.nexus_key.clear()
        self.nexus_key.setPlaceholderText(
            "已保存，输入新 Key 可替换" if self.services.credentials.has(NEXUS_SECRET) else "粘贴 Nexus API Key"
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
            "已保存，输入新 Key 可替换" if self.services.credentials.has(LLM_SECRET) else "输入 API Key"
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
        self.credentials_label.setText(
            f"{self.services.credentials.backend_name}。敏感密钥单独保存，不会出现在配置导出或日志中。"
        )
        try:
            rate = self.services.nexus.rate_limit
            if rate.hourly_remaining is not None:
                self.nexus_info.setText(
                    f"API 额度：本小时剩余 {rate.hourly_remaining}，本日剩余 {rate.daily_remaining if rate.daily_remaining is not None else '未知'}。"
                )
        except Exception:
            pass

    def _update_nexus_status(self) -> None:
        configured = self.services.credentials.has(NEXUS_SECRET)
        self.nexus_status.set_status("已配置" if configured else "未配置", "success" if configured else "danger")

    def _update_ai_status(self) -> None:
        configured = self.services.llm_configured
        self.ai_status.set_status("已就绪" if configured else "未配置", "success" if configured else "warning")
        if configured:
            self.ai_info.setText(
                f"当前模型：{self.services.config.config.llm.model}。修改参数后请保存。"
            )
        else:
            self.ai_info.setText("填写 Base URL、模型名称与 API Key 后即可使用 AI 翻译与深度诊断。")

    def _toggle_ai_key(self, checked: bool) -> None:
        self.ai_key.setEnabled(checked)

    # ------------------------------------------------------------------
    # Nexus
    # ------------------------------------------------------------------

    def save_nexus_key(self) -> bool:
        value = self.nexus_key.text().strip()
        if not value:
            self.toast("请输入 API Key；留空不会修改已保存的密钥。", "warning")
            return False
        try:
            self.services.credentials.set(NEXUS_SECRET, value)
        except Exception as exc:
            self.error(str(exc))
            return False
        self.nexus_key.clear()
        self.nexus_key.setPlaceholderText("已保存，输入新 Key 可替换")
        self._update_nexus_status()
        self.toast("Nexus API Key 已保存。", "success")
        return True

    def clear_nexus_key(self) -> None:
        if not self.confirm("清除 Nexus API Key", "清除后需要重新输入才能使用 Nexus API。", danger=True):
            return
        try:
            self.services.credentials.delete(NEXUS_SECRET)
        except Exception as exc:
            self.error(str(exc))
            return
        self._update_nexus_status()
        self.nexus_key.setPlaceholderText("粘贴 Nexus API Key")
        self.toast("Nexus API Key 已清除。", "success")

    def test_nexus(self) -> None:
        if self.nexus_key.text().strip() and not self.save_nexus_key():
            return
        if not self.services.nexus_configured:
            self.error("请先保存 Nexus API Key。")
            return
        self.test_nexus_button.setEnabled(False)
        set_busy(self.test_nexus_button, True, "测试中…")

        def done(data) -> None:  # type: ignore[no-untyped-def]
            set_busy(self.test_nexus_button, False)
            premium = "Premium" if data.get("is_premium") else "非 Premium（API 直接下载受限）"
            self.nexus_info.setText(
                f"连接成功：{data.get('name') or 'Nexus 用户'} · {premium}。"
            )
            self.toast("Nexus API 连接正常。", "success")

        def failed(message: str) -> None:
            set_busy(self.test_nexus_button, False)
            self.nexus_info.setText(f"连接失败：{message}")
            self.error(message)

        self.tasks.run("测试 Nexus API", self.services.nexus.validate_key, on_done=done, on_error=failed)

    # ------------------------------------------------------------------
    # AI
    # ------------------------------------------------------------------

    def save_ai(self, *, silent: bool = False) -> bool:
        try:
            headers = json.loads(self.ai_headers.toPlainText().strip() or "{}")
            body = json.loads(self.ai_body.toPlainText().strip() or "{}")
            if not isinstance(headers, dict):
                raise ValueError("额外请求头必须是 JSON 对象。")
            if not isinstance(body, dict):
                raise ValueError("额外参数必须是 JSON 对象。")
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
            self.error(f"AI 配置无效：{exc}")
            return False
        key = self.ai_key.text().strip()
        if key:
            try:
                self.services.credentials.set(LLM_SECRET, key)
            except Exception as exc:
                self.error(str(exc))
                return False
            self.ai_key.clear()
            self.ai_key.setPlaceholderText("已保存，输入新 Key 可替换")
        try:
            self.services.config.update(llm=llm)
        except Exception as exc:
            self.error(f"保存 AI 配置失败：{exc}")
            return False
        self._update_ai_status()
        if not silent:
            self.toast("AI 配置已保存。", "success")
        return True

    def clear_ai_key(self) -> None:
        if not self.confirm("清除 AI API Key", "清除后需要重新输入才能进行 AI 翻译和深度诊断。", danger=True):
            return
        try:
            self.services.credentials.delete(LLM_SECRET)
        except Exception as exc:
            self.error(str(exc))
            return
        self.ai_key.setPlaceholderText("输入 API Key")
        self._update_ai_status()
        self.toast("AI API Key 已清除。", "success")

    def test_ai(self) -> None:
        if not self.save_ai(silent=True):
            return
        if not self.services.llm_configured:
            self.error("请填写模型名称并保存 API Key。")
            return
        set_busy(self.test_ai_button, True, "测试中…")

        def done(message: str) -> None:
            set_busy(self.test_ai_button, False)
            self.ai_info.setText(message)
            self.toast("AI API 连接正常。", "success")

        def failed(message: str) -> None:
            set_busy(self.test_ai_button, False)
            self.ai_info.setText(f"连接失败：{message}")
            self.error(message)

        self.tasks.run("测试 AI API", self.services.llm.test_connection, on_done=done, on_error=failed)

    # ------------------------------------------------------------------
    # Other sections
    # ------------------------------------------------------------------

    def browse_download_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择下载目录", self.download_dir.text() or str(self.services.paths.downloads_dir)
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
        self.toast(f"下载目录已设置为：{target}", "success")

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
        self.toast("更新设置已保存。", "success")

    def _theme_changed(self) -> None:
        theme = str(self.theme_combo.currentData())
        if theme and theme != self.services.config.config.theme:
            self.controller.set_theme(theme)
            self.toast("主题已切换。", "success")

    def export_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出配置",
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
        self.toast(f"配置已导出：{path}", "success")

    def backup_database(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "备份数据库",
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
            self.error(f"备份数据库失败：{exc}")
            return
        self.toast(f"数据库已备份：{path}", "success")

    def clear_translation_cache(self) -> None:
        if not self.confirm(
            "清理翻译缓存",
            "删除本地共享翻译缓存？\n\n已保存到 Mod 的译文记录和生成的汉化文件不会删除。",
            danger=True,
        ):
            return
        self.tasks.run(
            "清理翻译缓存",
            self.services.translation.clear_cache,
            on_done=lambda count: self.toast(f"已清理 {count} 条翻译缓存。", "success"),
            on_error=self.error,
        )
