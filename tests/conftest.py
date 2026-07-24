from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_browser_model_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_LLM_CONFIG_PATH", str(tmp_path / "user-config" / "llm-settings.json"))
