from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
KALI_ROOT = ROOT / "containers" / "kali"


def _matrix_module():
    path = ROOT / "scripts" / "kali_build_matrix.py"
    spec = importlib.util.spec_from_file_location("kali_build_matrix", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sandbox_profiles() -> dict[str, dict[str, object]]:
    config = json.loads((ROOT / "config" / "sandbox.json").read_text(encoding="utf-8"))
    return {
        profile_id: profile
        for profile_id, profile in config["profiles"].items()
        if profile["provider"] != "remote_http"
    }


def _kali_solver_bindings() -> dict[str, str]:
    bindings: dict[str, str] = {}
    definition_root = ROOT / "resources" / "solver_definitions" / "workers"
    for path in definition_root.glob("*.json"):
        definition = json.loads(path.read_text(encoding="utf-8"))
        kali = definition.get("kali")
        if kali:
            bindings[definition["id"]] = kali["profile_id"]
    return bindings


def _repository_name(image: str) -> str:
    repository = image.split("@", 1)[0]
    return repository.rsplit("/", 1)[-1]


def test_kali_build_matrix_covers_all_bound_solvers_and_local_profiles() -> None:
    values = _matrix_module().load_matrix()
    entries = {value["solver"]: value for value in values}
    bindings = _kali_solver_bindings()
    profiles = _sandbox_profiles()

    assert set(entries) == set(bindings)
    assert {value["profile"] for value in values} == set(profiles)
    for solver_id, profile_id in bindings.items():
        entry = entries[solver_id]
        profile = profiles[profile_id]
        assert entry["profile"] == profile_id
        assert entry["image"] == _repository_name(str(profile["image"]))


def test_matrix_contexts_and_toolsets_match_generated_manifests() -> None:
    manifest_root = KALI_ROOT / "toolsets" / "generated"
    for value in _matrix_module().load_matrix():
        context = KALI_ROOT / value["context"]
        context_manifest = context / "toolset.json"
        generated_manifest = manifest_root / f"{value['profile']}.json"
        assert (context / "Dockerfile").is_file()
        assert context_manifest.is_file()
        assert context_manifest.read_bytes() == generated_manifest.read_bytes()


def test_all_profile_toolset_digests_match_committed_manifests() -> None:
    profiles = _sandbox_profiles()
    manifest_root = KALI_ROOT / "toolsets" / "generated"
    assert {path.stem for path in manifest_root.glob("*.json")} == set(profiles)
    for profile_id, profile in profiles.items():
        raw = (manifest_root / f"{profile_id}.json").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == profile["toolset_digest"]
        manifest = json.loads(raw)
        assert manifest["profile_id"] == profile_id
        assert set(profile["allowed_executables"]) <= set(manifest["tools"])


def test_solver_dockerfiles_follow_static_image_contract() -> None:
    for value in _matrix_module().load_matrix():
        path = KALI_ROOT / value["context"] / "Dockerfile"
        dockerfile = path.read_text(encoding="utf-8")
        assert re.search(r"(?m)^ARG BASE_IMAGE=", dockerfile), path
        assert dockerfile.rstrip().endswith("USER 10001:10001"), path
        assert "COPY --chown=tga:tga toolset.json /opt/tga/toolset.json" in dockerfile, path
        assert not re.search(r"(?i)\bsudo\b", dockerfile), path
        assert not re.search(r"(?i)curl[^\n]*\|\s*(?:ba)?sh\b", dockerfile), path
        assert not re.search(r"(?i)wget[^\n]*\|\s*(?:ba)?sh\b", dockerfile), path
        assert not re.search(r"(?i):latest(?:\s|$)", dockerfile), path
        if "apt-get install" in dockerfile:
            assert "ARG DEBIAN_FRONTEND=noninteractive" in dockerfile, path
            assert "--no-install-recommends" in dockerfile, path
            assert "/var/lib/apt/lists/*" in dockerfile, path
