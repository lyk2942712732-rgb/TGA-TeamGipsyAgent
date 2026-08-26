import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

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


def test_skills_are_managed_as_packages_and_read_on_demand(tmp_path: Path):
    folder = tmp_path / "pwn"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# Pwn\n\nRead references/heap.md when needed.", encoding="utf-8")
    references = folder / "references"
    references.mkdir()
    (references / "heap.md").write_text("# Heap\n\nUse pwntools.", encoding="utf-8")
    catalog = SkillCatalog(tmp_path)
    assert [item.name for item in catalog.list()] == ["pwn"]
    assert catalog.list()[0].file_count == 2
    assert "pwntools" not in catalog.read("pwn")
    assert "pwntools" in catalog.read("pwn", "references/heap.md")
    assert [item.path for item in catalog.package("pwn").files] == ["SKILL.md", "references/heap.md"]
    catalog.create("web", {"SKILL.md": "# Web\n\nInspect inputs.", "sql.md": "# SQL\n\nTest injection."})
    assert "Inspect inputs" in catalog.read("web")
    assert "Test injection" in catalog.read("web", "sql.md")
    catalog.write_package("web", {"SKILL.md": "# Web\n\nUpdated."})
    assert [item.path for item in catalog.package("web").files] == ["SKILL.md"]
    catalog.delete("web")
    with pytest.raises(NotFoundError):
        catalog.read("web")
    with pytest.raises(NotFoundError):
        catalog.read("../pwn")


def test_skill_zip_import_uses_the_root_directory_and_preserves_markdown_layout(tmp_path: Path):
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("ctf-crypto/SKILL.md", "# CTF Crypto\n\nRead references/rsa.md when needed.")
        archive.writestr("ctf-crypto/references/rsa.md", "# RSA\n\nFactor small moduli.")
        archive.writestr("ctf-crypto/ignored.txt", "not part of the Skill package")

    package = SkillCatalog(tmp_path).import_archive("upload.zip", payload.getvalue())

    assert package.name == "ctf-crypto"
    assert [item.path for item in package.files] == ["SKILL.md", "references/rsa.md"]
    assert (tmp_path / "ctf-crypto" / "references" / "rsa.md").is_file()


def test_skill_zip_import_rejects_unsafe_or_incomplete_packages(tmp_path: Path):
    unsafe = BytesIO()
    with ZipFile(unsafe, "w") as archive:
        archive.writestr("../SKILL.md", "# Escape")
    with pytest.raises(ValueError, match="invalid Markdown file path"):
        SkillCatalog(tmp_path).import_archive("unsafe.zip", unsafe.getvalue())

    incomplete = BytesIO()
    with ZipFile(incomplete, "w") as archive:
        archive.writestr("notes.md", "# Notes")
    with pytest.raises(ValueError, match="must contain SKILL.md"):
        SkillCatalog(tmp_path).import_archive("incomplete.zip", incomplete.getvalue())


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
