from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ...config import ConfigStore
from ...core.credentials import LLM_SECRET, CredentialStore
from ...core.errors import LlmError
from ...version import USER_AGENT

log = logging.getLogger(__name__)


class LlmClient:
    """Minimal OpenAI-compatible chat completion client.

    Supports custom base URL, API key, model, headers, body parameters and SSL
    verification. It deliberately does not stream: translation batches need a
    complete JSON object and the UI shows deterministic batch progress.
    """

    def __init__(self, config: ConfigStore, credentials: CredentialStore) -> None:
        self.config = config
        self.credentials = credentials

    def _settings(self):  # type: ignore[no-untyped-def]
        settings = self.config.config.llm
        if not settings.model.strip():
            raise LlmError("尚未配置 AI 模型名称。请在设置中填写模型。")
        api_key = self.credentials.get(LLM_SECRET) if settings.requires_api_key else None
        if settings.requires_api_key and not api_key:
            raise LlmError("尚未配置 AI API Key。请在设置中填写。")
        return settings, api_key

    def is_configured(self) -> bool:
        settings = self.config.config.llm
        if not settings.model.strip():
            return False
        if settings.requires_api_key:
            return bool(self.credentials.get(LLM_SECRET))
        return True

    def _headers(self, api_key: str | None) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers.update(self.config.config.llm.extra_headers)
        return headers

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool | None = None,
    ) -> str:
        settings, api_key = self._settings()
        base_url = settings.base_url.rstrip("/")
        url = (
            base_url
            if base_url.endswith("/chat/completions")
            else f"{base_url}/chat/completions"
        )
        body: dict[str, Any] = {
            "model": settings.model,
            "messages": messages,
            "temperature": settings.temperature if temperature is None else temperature,
            "max_tokens": settings.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        use_json = settings.json_mode if json_mode is None else json_mode
        if use_json:
            body["response_format"] = {"type": "json_object"}
        for key, value in settings.extra_body.items():
            if key not in ("messages", "model", "stream"):
                body[key] = value
        try:
            with httpx.Client(
                timeout=httpx.Timeout(settings.timeout_seconds, connect=20.0),
                verify=settings.verify_ssl,
                follow_redirects=True,
            ) as client:
                response = client.post(url, headers=self._headers(api_key), json=body)
        except httpx.TimeoutException as exc:
            raise LlmError("AI 请求超时，请检查 API 地址、网络或模型服务状态。") from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"无法连接 AI API：{exc}") from exc

        if response.status_code == 401 or response.status_code == 403:
            raise LlmError("AI API 认证失败，请检查 API Key。")
        if response.status_code == 404:
            raise LlmError("AI API 地址或模型不存在（HTTP 404），请检查 Base URL 与模型名称。")
        if response.status_code == 429:
            raise LlmError("AI API 触发速率限制，请稍后重试。")
        if response.status_code >= 400:
            detail = response.text.strip()[:800]
            try:
                payload = response.json()
                detail = str(
                    payload.get("error", {}).get("message")
                    if isinstance(payload.get("error"), dict)
                    else payload.get("error") or payload.get("message") or detail
                )
            except Exception:
                pass
            raise LlmError(f"AI API 返回错误（HTTP {response.status_code}）：{detail}")

        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmError("AI API 返回格式异常，无法读取回答。") from exc
        if content is None:
            raise LlmError("AI 返回了空内容。")
        if isinstance(content, list):
            # Some OpenAI-compatible servers return content parts.
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        return str(content)

    def test_connection(self) -> str:
        settings, _ = self._settings()
        reply = self.chat(
            [
                {"role": "system", "content": "You are a connection test. Reply with exactly OK."},
                {"role": "user", "content": "OK"},
            ],
            temperature=0,
            max_tokens=16,
            json_mode=False,
        )
        return f"连接成功，模型 {settings.model} 已响应：{reply.strip()[:60]}"

    @staticmethod
    def parse_json_object(content: str) -> dict[str, Any]:
        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError as exc:
                raise LlmError(f"AI 返回的 JSON 无法解析：{exc}") from exc
        raise LlmError("AI 未返回有效的 JSON 对象。")

    def chat_json(self, messages: list[dict[str, str]], *, max_tokens: int | None = None) -> dict[str, Any]:
        content = self.chat(messages, max_tokens=max_tokens)
        return self.parse_json_object(content)
