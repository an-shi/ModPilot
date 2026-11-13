from __future__ import annotations

import json
from pathlib import Path

import pytest

from modpilot.adapters.stardew.manifest import ManifestError, parse_manifest
from modpilot.domain.enums import ModKind


def write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_parse_manifest_with_nexus_and_dependencies(tmp_path: Path) -> None:
    path = write(
        tmp_path / "manifest.json",
        {
            "Name": "Expanded Farm",
            "Author": "Alice",
            "Version": "1.2.3",
            "Description": "Adds a farm",
            "UniqueID": "Alice.ExpandedFarm",
            "EntryDll": "ExpandedFarm.dll",
            "MinimumApiVersion": "4.0.0",
            "UpdateKeys": ["Nexus:12345", "GitHub:alice/farm"],
            "Dependencies": [
                {"UniqueID": "Pathoschild.ContentPatcher", "MinimumVersion": "2.0.0", "IsRequired": True},
                {"UniqueID": "Some.Optional", "IsRequired": False},
            ],
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher", "MinimumVersion": "2.0.0"},
        },
    )
    metadata = parse_manifest(path)
    assert metadata.unique_id == "Alice.ExpandedFarm"
    assert metadata.nexus_mod_id == 12345
    assert metadata.version == "1.2.3"
    assert metadata.extra["entry_dll"] == "ExpandedFarm.dll"
    assert metadata.kind is ModKind.CONTENT_PACK
    assert metadata.minimum_api_version == "4.0.0"
    assert len(metadata.dependencies) == 3
    assert metadata.dependencies[0].minimum_version == "2.0.0"
    assert metadata.dependencies[1].required is False
    assert metadata.content_pack_for is not None
    assert metadata.extra["entry_dll"] == "ExpandedFarm.dll"


def test_parse_manifest_utf8_bom_and_update_key_string(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        "\ufeff" + json.dumps(
            {
                "name": "BOM Mod",
                "author": "Bob",
                "version": "2",
                "uniqueid": "Bob.BOM",
                "updatekeys": "nexus:98765",
            }
        ),
        encoding="utf-8",
    )
    metadata = parse_manifest(path)
    assert metadata.unique_id == "Bob.BOM"
    assert metadata.nexus_mod_id == 98765
    assert metadata.kind is ModKind.UNKNOWN


def test_parse_manifest_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"Name": "Broken",', encoding="utf-8")
    with pytest.raises(ManifestError, match="JSON"):
        parse_manifest(path)


def test_parse_manifest_requires_unique_id(tmp_path: Path) -> None:
    path = write(tmp_path / "manifest.json", {"Name": "No ID", "Version": "1.0"})
    with pytest.raises(ManifestError, match="UniqueID"):
        parse_manifest(path)


def test_parse_manifest_rejects_invalid_unique_id_characters(tmp_path: Path) -> None:
    path = write(
        tmp_path / "manifest.json",
        {"Name": "Bad", "UniqueID": "Bad ID With Spaces", "Version": "1.0"},
    )
    with pytest.raises(ManifestError, match="UniqueID"):
        parse_manifest(path)
