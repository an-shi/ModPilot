from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from modpilot.app_services import AppServices
from modpilot.core.credentials import InMemorySecretBackend
from modpilot.ui.main_window import MainWindow
from modpilot.ui.theme import apply_theme, build_stylesheet, resources_dir


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_theme_stylesheets_resolve_assets() -> None:
    for theme in ("dark", "light"):
        stylesheet = build_stylesheet(theme)
        assert "@" not in stylesheet
        assert "check.svg" in stylesheet
        assert "arrow_up.svg" in stylesheet
        assert "arrow_down.svg" in stylesheet
    for name in ("check.svg", "arrow_up.svg", "arrow_down.svg"):
        assert (resources_dir() / name).is_file()


def test_window_with_real_game_and_mod(qapp, tmp_path: Path) -> None:
    services = AppServices(tmp_path / "data", credential_backend=InMemorySecretBackend())
    install = tmp_path / "Stardew Valley"
    install.mkdir()
    (install / "Stardew Valley.exe").write_bytes(b"stub")
    (install / "StardewModdingAPI.exe").write_bytes(b"stub")
    (install / "Content").mkdir()
    (install / "Mods").mkdir()
    services.games.add_game("stardew_valley", install)
    mod = install / "Mods" / "Demo"
    mod.mkdir()
    (mod / "manifest.json").write_text(
        json.dumps(
            {
                "Name": "Demo Mod",
                "Author": "Tester",
                "Version": "1.0.0",
                "UniqueID": "Test.Demo",
                "Description": "Demo",
                "UpdateKeys": ["Nexus:123"],
                "EntryDll": "Demo.dll",
            }
        ),
        encoding="utf-8",
    )
    game = services.games.active_game()
    assert game is not None
    services.mods.scan_game(game.id)
    apply_theme(qapp, services.config.config.theme)
    window = MainWindow(services)
    window.show()
    qapp.processEvents()
    assert len(window.pages) == 9
    window.switch_page(2)  # Mods
    qapp.processEvents()
    mods_page = window.pages[2]
    assert mods_page.table.rowCount() == 1
    assert mods_page.table.item(0, 0).text() == "Demo Mod"
    window.switch_page(1)  # Games
    qapp.processEvents()
    assert window.pages[1].managed_layout.count() >= 1
    window.switch_page(8)  # Settings
    qapp.processEvents()
    assert window.pages[8].ai_base_url.text()
    window.close()
    qapp.processEvents()
