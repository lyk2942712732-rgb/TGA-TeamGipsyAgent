from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import app
from tga.contracts import TGATask
from tga.runtime.prompt_settings import (
    load_agent_prompt_settings,
    save_agent_prompt_settings,
    snapshot_for_mode,
)
from tga.runtime.prompts import build_agent_system_prompt


def _task(*, snapshot: dict | None = None) -> TGATask:
    return TGATask(
        id="prompt_task",
        name="prompt task",
        mode="ctf",
        goal="solve",
        session_input={"prompt": "solve the challenge"},
        agent_prompt_snapshot=snapshot,
    )


def test_agent_prompt_api_persists_every_editable_prompt_field():
    client = TestClient(app)
    payload = client.get("/api/v2/settings/agent-prompts").json()
    payload["common_system_prompt"] = "Custom shared constraint."
    ctf = next(item for item in payload["modes"] if item["id"] == "ctf")
    ctf.update({
        "label": "Custom CTF",
        "methodology": ["inspect", "verify"],
        "completion_focus": "Provide verified evidence.",
        "observer_focus": "Reject unsupported claims.",
    })

    response = client.put("/api/v2/settings/agent-prompts", json=payload)

    assert response.status_code == 200
    assert client.get("/api/v2/settings/agent-prompts").json() == payload
    prompt = build_agent_system_prompt(_task(snapshot=snapshot_for_mode(load_agent_prompt_settings(), "ctf").model_dump(mode="json")))
    assert "Custom shared constraint." in prompt
    assert "Mode: Custom CTF (ctf). Methodology: inspect; verify." in prompt
    assert "Completion focus: Provide verified evidence." in prompt
    assert "Observer focus: Reject unsupported claims." in prompt


def test_agent_prompt_api_rejects_broken_structure():
    client = TestClient(app)
    payload = client.get("/api/v2/settings/agent-prompts").json()
    payload["modes"] = payload["modes"][:-1]

    response = client.put("/api/v2/settings/agent-prompts", json=payload)

    assert response.status_code == 422


def test_task_prompt_snapshot_is_stable_after_global_settings_change():
    settings = load_agent_prompt_settings()
    settings.common_system_prompt = "Original task prompt."
    save_agent_prompt_settings(settings)
    snapshot = snapshot_for_mode(settings, "ctf").model_dump(mode="json")
    task = _task(snapshot=snapshot)

    settings.common_system_prompt = "New global prompt."
    save_agent_prompt_settings(settings)

    assert "Original task prompt." in build_agent_system_prompt(task)
    assert "New global prompt." not in build_agent_system_prompt(task)
