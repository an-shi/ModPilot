from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from ...core.errors import (
    NexusAuthError,
    NexusError,
    NexusNotFoundError,
    NexusPremiumRequired,
    NexusRateLimitError,
)
from ...domain.models import NexusFile, NexusModInfo, NexusRequirement, NexusSearchResult
from ...version import (
    NEXUS_APPLICATION_NAME,
    NEXUS_APPLICATION_VERSION,
    NEXUS_PROTOCOL_VERSION,
    USER_AGENT,
)

log = logging.getLogger(__name__)

API_BASE = "https://api.nexusmods.com"
GRAPHQL_URL = f"{API_BASE}/v2/graphql"
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class RateLimitInfo:
    hourly_remaining: int | None = None
    hourly_limit: int | None = None
    daily_remaining: int | None = None
    daily_limit: int | None = None
    updated_at: datetime | None = None


class NexusClient:
    def __init__(
        self,
        api_key_provider: Callable[[], str | None],
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key_provider = api_key_provider
        self.timeout = timeout
        self.rate_limit = RateLimitInfo()
        self.last_user: dict[str, Any] | None = None
        self._game_id_cache: dict[str, int] = {}
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=15.0),
            follow_redirects=True,
            transport=transport,
            headers={
                "User-Agent": USER_AGENT,
                "Application-Name": NEXUS_APPLICATION_NAME,
                "Application-Version": NEXUS_APPLICATION_VERSION,
                "Protocol-Version": NEXUS_PROTOCOL_VERSION,
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def api_key(self) -> str | None:
        key = self._api_key_provider()
        return key.strip() if key else None

    def is_configured(self) -> bool:
        return bool(self.api_key())

    def _auth_headers(self) -> dict[str, str]:
        key = self.api_key()
        if not key:
            raise NexusAuthError("尚未配置 Nexus Mods API Key。请在设置中填写。")
        return {"apikey": key}

    def _update_rate_limit(self, response: httpx.Response) -> None:
        headers = response.headers

        def read(name: str) -> int | None:
            value = headers.get(name)
            try:
                return int(value) if value is not None else None
            except ValueError:
                return None

        hourly_remaining = read("x-rl-hourly-remaining")
        hourly_limit = read("x-rl-hourly-limit")
        daily_remaining = read("x-rl-daily-remaining")
        daily_limit = read("x-rl-daily-limit")
        if any(
            value is not None
            for value in (hourly_remaining, daily_remaining, hourly_limit, daily_limit)
        ):
            self.rate_limit = RateLimitInfo(
                hourly_remaining=hourly_remaining,
                hourly_limit=hourly_limit,
                daily_remaining=daily_remaining,
                daily_limit=daily_limit,
                updated_at=datetime.now(UTC),
            )

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> Any:
        headers = self._auth_headers() if authenticated else {}
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self._client.request(method, url, params=params, json=json, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = NexusError("连接 Nexus Mods 超时，请检查网络。")
                log.warning("Nexus 请求超时（第 %d 次）：%s", attempt + 1, url)
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = NexusError(f"无法连接 Nexus Mods：{exc}")
                log.warning("Nexus 网络错误（第 %d 次）：%s", attempt + 1, exc)
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last_error from exc

            self._update_rate_limit(response)
            if response.status_code in RETRY_STATUSES and attempt < 2:
                retry_after = response.headers.get("Retry-After")
                delay = 2.0 * (attempt + 1)
                if retry_after:
                    try:
                        delay = min(30.0, max(delay, float(retry_after)))
                    except ValueError:
                        pass
                log.warning("Nexus 返回 %s，%.1f 秒后重试", response.status_code, delay)
                time.sleep(delay)
                continue
            if response.status_code == 429:
                raise NexusRateLimitError(
                    "Nexus API 请求已达到速率限制，请稍后再试。",
                    detail=response.text[:500],
                )
            if response.status_code == 401:
                raise NexusAuthError("Nexus API Key 无效或已过期，请在设置中更新。")
            if response.status_code == 403:
                message = self._error_message(response)
                if "premium" in message.lower() or "/download_link" in url:
                    raise NexusPremiumRequired(
                        "Nexus 仅允许 Premium 会员通过 API 获取直接下载链接。"
                        "你仍可手动在 Nexus 网页下载后使用“导入压缩包”。",
                        detail=message,
                    )
                raise NexusError(f"Nexus 拒绝访问：{message}")
            if response.status_code == 404:
                raise NexusNotFoundError("Nexus 上未找到对应内容。")
            if response.status_code >= 400:
                raise NexusError(f"Nexus API 错误 ({response.status_code})：{self._error_message(response)}")

            try:
                return response.json()
            except ValueError as exc:
                raise NexusError("Nexus 返回了无法解析的响应。") from exc
        raise last_error or NexusError("Nexus 请求失败。")

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
            if isinstance(data, dict):
                for key in ("message", "error", "detail"):
                    if data.get(key):
                        return str(data[key])
        except ValueError:
            pass
        return response.text.strip()[:500] or f"HTTP {response.status_code}"

    def validate_key(self) -> dict[str, Any]:
        data = self._request("GET", f"{API_BASE}/v1/users/validate.json")
        if not isinstance(data, dict):
            raise NexusAuthError("Nexus 返回了意外的验证结果。")
        self.last_user = data
        return data

    def get_game(self, domain: str) -> dict[str, Any]:
        data = self._request("GET", f"{API_BASE}/v1/games/{domain}.json")
        if not isinstance(data, dict):
            raise NexusError("Nexus 游戏信息格式异常。")
        if data.get("id") is not None:
            try:
                self._game_id_cache[domain] = int(data["id"])
            except (TypeError, ValueError):
                pass
        return data

    def game_id(self, domain: str) -> int:
        if domain in self._game_id_cache:
            return self._game_id_cache[domain]
        data = self.get_game(domain)
        try:
            return int(data["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise NexusError("无法获取 Nexus 游戏 ID。") from exc

    def get_mod(
        self,
        domain: str,
        mod_id: int,
        *,
        include_requirements: bool = True,
    ) -> NexusModInfo:
        data = self._request("GET", f"{API_BASE}/v1/games/{domain}/mods/{mod_id}.json")
        if not isinstance(data, dict):
            raise NexusError("Nexus Mod 信息格式异常。")
        requirements: list[NexusRequirement] = []
        if include_requirements:
            try:
                requirements = self.get_requirements(domain, mod_id)
            except Exception as exc:
                log.info("读取 Nexus 依赖信息失败（不影响 Mod 详情）：%s", exc)
        return NexusModInfo(
            mod_id=int(data.get("mod_id") or mod_id),
            game_domain=str(data.get("domain_name") or domain),
            name=str(data.get("name") or f"Mod {mod_id}"),
            summary=str(data.get("summary") or ""),
            description=str(data.get("description") or ""),
            version=str(data.get("version") or ""),
            author=str(data.get("author") or ""),
            uploaded_by=str(data.get("uploaded_by") or ""),
            category=str(data.get("category_name") or data.get("category") or ""),
            picture_url=str(data.get("picture_url") or ""),
            nexus_url=str(data.get("nexusmods_url") or ""),
            updated_timestamp=int(data.get("updated_timestamp") or 0),
            created_timestamp=int(data.get("created_timestamp") or 0),
            downloads=int(data.get("mod_downloads") or 0),
            endorsements=int(data.get("endorsement_count") or 0),
            adult=bool(data.get("contains_adult_content")),
            requirements=requirements,
        )

    def get_requirements(self, domain: str, mod_id: int) -> list[NexusRequirement]:
        game_id = self.game_id(domain)
        query = """
        query ModRequirements($modId: ID!, $gameId: ID!) {
          mod(modId: $modId, gameId: $gameId) {
            modRequirements {
              nexusRequirements {
                nodes {
                  modId
                  modName
                  url
                  externalRequirement
                  notes
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"modId": str(mod_id), "gameId": str(game_id)})
        try:
            nodes = data["mod"]["modRequirements"]["nexusRequirements"]["nodes"]
        except (KeyError, TypeError):
            return []
        result: list[NexusRequirement] = []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            result.append(
                NexusRequirement(
                    mod_id=int(node["modId"]) if node.get("modId") is not None else None,
                    mod_name=str(node.get("modName") or ""),
                    url=str(node.get("url") or ""),
                    external=bool(node.get("externalRequirement")),
                    notes=str(node.get("notes") or ""),
                )
            )
        return result

    def get_files(self, domain: str, mod_id: int) -> list[NexusFile]:
        data = self._request("GET", f"{API_BASE}/v1/games/{domain}/mods/{mod_id}/files.json")
        if not isinstance(data, dict):
            raise NexusError("Nexus 文件列表格式异常。")
        files = data.get("files") or []
        result: list[NexusFile] = []
        for raw in files:
            if not isinstance(raw, dict):
                continue
            try:
                result.append(
                    NexusFile(
                        file_id=int(raw.get("file_id")),
                        name=str(raw.get("name") or ""),
                        version=str(raw.get("version") or ""),
                        category=str(raw.get("category_name") or ""),
                        category_name=str(raw.get("category_name") or ""),
                        description=str(raw.get("description") or ""),
                        size_kb=int(raw.get("size_kb") or 0),
                        uploaded_timestamp=int(raw.get("uploaded_timestamp") or 0),
                        mod_version=str(raw.get("mod_version") or ""),
                        is_primary=bool(raw.get("is_primary")),
                        file_name=str(raw.get("file_name") or ""),
                        md5=str(raw.get("md5") or ""),
                    )
                )
            except (TypeError, ValueError) as exc:
                log.debug("忽略异常的 Nexus 文件条目：%s", exc)
        result.sort(key=lambda item: item.uploaded_timestamp, reverse=True)
        return result

    def get_latest_file(self, domain: str, mod_id: int) -> NexusFile | None:
        files = self.get_files(domain, mod_id)
        main = [
            item
            for item in files
            if item.category_name.upper() == "MAIN" or item.category.upper() == "MAIN"
        ]
        candidates = main or [item for item in files if "OLD" not in item.category.upper()]
        if not candidates:
            return None
        primary = [item for item in candidates if item.is_primary]
        pool = primary or candidates

        def version_key(item: NexusFile) -> tuple[int, str]:
            return (item.uploaded_timestamp, item.mod_version or item.version)

        return max(pool, key=version_key)

    def get_download_links(self, domain: str, mod_id: int, file_id: int) -> list[str]:
        data = self._request(
            "GET",
            f"{API_BASE}/v1/games/{domain}/mods/{mod_id}/files/{file_id}/download_link.json",
        )
        if not isinstance(data, list):
            raise NexusError("Nexus 下载链接响应格式异常。")
        links: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            url = str(item.get("URI") or "").strip()
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.scheme != "https":
                raise NexusError("Nexus 返回了非 HTTPS 下载链接，已拒绝。")
            links.append(url)
        if not links:
            raise NexusError("Nexus 未返回可用下载链接。")
        return links

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        data = self._request(
            "POST",
            GRAPHQL_URL,
            json={"query": query, "variables": variables},
        )
        if not isinstance(data, dict):
            raise NexusError("Nexus GraphQL 响应格式异常。")
        if data.get("errors"):
            messages = "; ".join(
                str(error.get("message", error)) if isinstance(error, dict) else str(error)
                for error in data["errors"]
            )
            raise NexusError(f"Nexus 查询失败：{messages}")
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise NexusError("Nexus 查询返回数据为空。")
        return payload

    def search_mods(self, domain: str, query: str, *, count: int = 12) -> list[NexusSearchResult]:
        query = query.strip()
        if not query:
            return []
        gql = """
        query SearchMods($filter: ModsFilter, $sort: [ModsSort!], $count: Int) {
          mods(filter: $filter, sort: $sort, count: $count) {
            totalCount
            nodes {
              modId
              name
              summary
              author
              pictureUrl
              updatedAt
              downloads
              endorsements
            }
          }
        }
        """
        variables = {
            "filter": {
                "op": "AND",
                "filter": [
                    {"gameDomainName": [{"value": domain, "op": "EQUALS"}]},
                    {"nameStemmed": [{"value": query, "op": "WILDCARD"}]},
                ],
            },
            "sort": [{"relevance": {"direction": "DESC"}}],
            "count": count,
        }
        payload = self._graphql(gql, variables)
        try:
            nodes = payload["mods"]["nodes"] or []
        except (KeyError, TypeError):
            return []
        results: list[NexusSearchResult] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            try:
                results.append(
                    NexusSearchResult(
                        mod_id=int(node.get("modId")),
                        name=str(node.get("name") or ""),
                        author=str(node.get("author") or ""),
                        summary=str(node.get("summary") or ""),
                        picture_url=str(node.get("pictureUrl") or ""),
                        updated_at=str(node.get("updatedAt") or ""),
                        downloads=int(node.get("downloads") or 0),
                        endorsements=int(node.get("endorsements") or 0),
                    )
                )
            except (TypeError, ValueError):
                continue
        return results
