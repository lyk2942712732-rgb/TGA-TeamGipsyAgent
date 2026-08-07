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


def test_kali_build_matrix_declares_one_universal_image_for_all_profiles() -> None:
    values = _matrix_module().load_matrix()
    profiles = _sandbox_profiles()
    assert values == [{"image": "tga-kali-universal", "context": "universal"}]
    assert set(_kali_solver_bindings().values()) == set(profiles)
    assert {
        _repository_name(str(profile["image"])) for profile in profiles.values()
    } == {"tga-kali-universal"}


def test_universal_toolset_covers_every_profile_manifest() -> None:
    manifest_root = KALI_ROOT / "toolsets" / "generated"
    value = _matrix_module().load_matrix()[0]
    context = KALI_ROOT / value["context"]
    manifest = json.loads((context / "toolset.json").read_text(encoding="utf-8"))
    profiles = _sandbox_profiles()
    assert (context / "Dockerfile").is_file()
    assert manifest["schema_version"] == 2
    assert manifest["image_role"] == "universal"
    assert set(manifest["compatible_profiles"]) == set(profiles)
    for profile_id in profiles:
        generated = json.loads(
            (manifest_root / f"{profile_id}.json").read_text(encoding="utf-8")
        )
        assert set(generated["tools"]) <= set(manifest["tools"])


def test_all_profile_toolset_digests_match_universal_manifest() -> None:
    profiles = _sandbox_profiles()
    manifest_root = KALI_ROOT / "toolsets" / "generated"
    universal_raw = (KALI_ROOT / "universal" / "toolset.json").read_bytes()
    universal_digest = hashlib.sha256(universal_raw).hexdigest()
    assert {path.stem for path in manifest_root.glob("*.json")} == set(profiles)
    for profile_id, profile in profiles.items():
        assert universal_digest == profile["toolset_digest"]
        manifest = json.loads(
            (manifest_root / f"{profile_id}.json").read_text(encoding="utf-8")
        )
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
