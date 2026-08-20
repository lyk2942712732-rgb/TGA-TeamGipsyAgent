import json
from pathlib import Path

import pytest

from tga3.domain import EntryKind, PublishRequest
from tga3.errors import NotFoundError
from tga3.protocol import RpcMessage
from tga3.skills import SkillCatalog


def test_blackboard_vocabulary_has_no_legacy_worker_messages():
    assert {kind.value for kind in EntryKind} == {
        "user_prompt",
        "user_file",
        "supervisor_advice",
        "finding",
        "qa",
        "final_candidate",
    }


def test_finding_requires_artifact_but_does_not_put_it_in_body():
    with pytest.raises(ValueError):
        PublishRequest(
            kind="finding",
            body={"claim": "unsupported"},
            idempotency_key="no-artifact",
        )


def test_json_rpc_shapes():
    request = RpcMessage.request("1", "session.pause")
    parsed = RpcMessage.model_validate_json(request.model_dump_json(exclude_none=True))
    assert parsed.method == "session.pause"
    assert RpcMessage.success("1", {"accepted": True}).result == {"accepted": True}


def test_skills_are_managed_and_read_only_by_name(tmp_path: Path):
    folder = tmp_path / "pwn"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# Pwn\n\nUse pwntools.", encoding="utf-8")
    catalog = SkillCatalog(tmp_path)
    assert [item.name for item in catalog.list()] == ["pwn"]
    assert "pwntools" in catalog.read("pwn")
    catalog.write("web", "# Web\n\nInspect inputs.")
    assert "Inspect inputs" in catalog.read("web")
    catalog.delete("web")
    with pytest.raises(NotFoundError):
        catalog.read("web")
    with pytest.raises(NotFoundError):
        catalog.read("../pwn")


def test_config_separates_secrets_from_agent_bindings():
    config = Path(__file__).parents[1] / "config"
    models = json.loads((config / "models.json").read_text(encoding="utf-8"))
    bindings = json.loads((config / "agents.json").read_text(encoding="utf-8"))
    assert all("api_keys" in provider for provider in models["providers"])
    assert "providers" not in bindings
    assert all(item["system_prompt"] for item in bindings["agents"].values())
    assert set(bindings["agents"]) == {
        "supervisor",
        "worker-openai",
        "worker-claude",
        "reporter",
    }
