from __future__ import annotations

import json

import httpx
import pytest

from modpilot.core.errors import NexusAuthError, NexusPremiumRequired
from modpilot.services.nexus.client import NexusClient

DOMAIN = "stardewvalley"
MOD_ID = 123


def make_handler(*, download_status: int = 200, validate_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/users/validate.json":
            if validate_status != 200:
                return httpx.Response(validate_status, json={"message": "bad key"})
            return httpx.Response(
                200,
                json={
                    "user_id": 1,
                    "name": "Tester",
                    "is_premium": True,
                    "is_supporter": True,
                },
                headers={
                    "x-rl-hourly-remaining": "99",
                    "x-rl-hourly-limit": "100",
                    "x-rl-daily-remaining": "999",
                    "x-rl-daily-limit": "1000",
                },
            )
        if path == f"/v1/games/{DOMAIN}.json":
            return httpx.Response(200, json={"id": 1303, "name": "Stardew Valley", "domain_name": DOMAIN})
        if path == f"/v1/games/{DOMAIN}/mods/{MOD_ID}.json":
            assert request.headers.get("apikey") == "test-key"
            return httpx.Response(
                200,
                json={
                    "mod_id": MOD_ID,
                    "domain_name": DOMAIN,
                    "name": "Test Mod",
                    "summary": "A summary",
                    "description": "<p>A description</p>",
                    "version": "2.0.0",
                    "author": "Alice",
                    "uploaded_by": "Alice",
                    "category_name": "Gameplay",
                    "picture_url": "https://example.com/pic.png",
                    "nexusmods_url": "https://www.nexusmods.com/stardewvalley/mods/123",
                    "updated_timestamp": 1700000000,
                    "created_timestamp": 1600000000,
                    "mod_downloads": 500,
                    "endorsement_count": 20,
                },
            )
        if path == f"/v1/games/{DOMAIN}/mods/{MOD_ID}/files.json":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "file_id": 10,
                            "name": "Old file",
                            "file_name": "old.zip",
                            "version": "1.0.0",
                            "category_name": "OLD_VERSION",
                            "size_kb": 10,
                            "uploaded_timestamp": 100,
                            "mod_version": "1.0.0",
                            "is_primary": False,
                            "md5": "oldmd5",
                        },
                        {
                            "file_id": 20,
                            "name": "Main file",
                            "file_name": "main.zip",
                            "version": "2.0.0",
                            "category_name": "MAIN",
                            "size_kb": 2048,
                            "uploaded_timestamp": 200,
                            "mod_version": "2.0.0",
                            "is_primary": True,
                            "md5": "md5value",
                        },
                    ]
                },
            )
        if path == f"/v1/games/{DOMAIN}/mods/{MOD_ID}/files/20/download_link.json":
            if download_status != 200:
                return httpx.Response(download_status, json={"message": "premium required"})
            return httpx.Response(
                200,
                json=[{"name": "CDN", "short_name": "Nexus CDN", "URI": "https://cdn.example.com/file.zip"}],
            )
        if path == "/v2/graphql":
            payload = json.loads(request.content.decode("utf-8"))
            query = payload["query"]
            if "ModRequirements" in query:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "mod": {
                                "modRequirements": {
                                    "nexusRequirements": {
                                        "nodes": [
                                            {
                                                "modId": 999,
                                                "modName": "Required Mod",
                                                "url": "https://www.nexusmods.com/stardewvalley/mods/999",
                                                "externalRequirement": False,
                                                "notes": "",
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    },
                )
            if "SearchMods" in query:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "mods": {
                                "totalCount": 1,
                                "nodes": [
                                    {
                                        "modId": 555,
                                        "name": "Test Mod 汉化",
                                        "summary": "Chinese translation",
                                        "author": "Translator",
                                        "pictureUrl": "",
                                        "updatedAt": "2026-01-01T00:00:00Z",
                                        "downloads": 10,
                                        "endorsements": 1,
                                    }
                                ],
                            }
                        }
                    },
                )
        return httpx.Response(404, json={"message": f"unhandled {path}"})

    return handler


def client(handler, key: str | None = "test-key") -> NexusClient:
    return NexusClient(lambda: key, transport=httpx.MockTransport(handler))


def test_validate_and_rate_limit() -> None:
    nexus = client(make_handler())
    data = nexus.validate_key()
    assert data["name"] == "Tester"
    assert nexus.rate_limit.hourly_remaining == 99
    assert nexus.rate_limit.daily_limit == 1000
    nexus.close()


def test_get_mod_with_requirements_and_files() -> None:
    nexus = client(make_handler())
    info = nexus.get_mod(DOMAIN, MOD_ID)
    assert info.name == "Test Mod"
    assert info.version == "2.0.0"
    assert info.requirements[0].mod_name == "Required Mod"
    files = nexus.get_files(DOMAIN, MOD_ID)
    assert len(files) == 2
    latest = nexus.get_latest_file(DOMAIN, MOD_ID)
    assert latest is not None
    assert latest.file_id == 20
    assert latest.md5 == "md5value"
    nexus.close()


def test_download_links_and_premium_error() -> None:
    nexus = client(make_handler())
    links = nexus.get_download_links(DOMAIN, MOD_ID, 20)
    assert links == ["https://cdn.example.com/file.zip"]
    nexus.close()

    nexus = client(make_handler(download_status=403))
    with pytest.raises(NexusPremiumRequired, match="Premium"):
        nexus.get_download_links(DOMAIN, MOD_ID, 20)
    nexus.close()


def test_invalid_key() -> None:
    nexus = client(make_handler(validate_status=401))
    with pytest.raises(NexusAuthError):
        nexus.validate_key()
    nexus.close()


def test_search_mods_payload() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/graphql":
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return make_handler()(request)
        return make_handler()(request)

    nexus = client(handler)
    results = nexus.search_mods(DOMAIN, "test")
    assert len(results) == 1
    assert results[0].mod_id == 555
    variables = captured["body"]["variables"]
    assert variables["filter"]["op"] == "AND"
    assert variables["filter"]["filter"][0]["gameDomainName"][0]["value"] == DOMAIN
    nexus.close()
