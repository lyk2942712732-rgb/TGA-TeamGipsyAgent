from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _matrix_module():
    path = ROOT / "scripts" / "kali_build_matrix.py"
    spec = importlib.util.spec_from_file_location("kali_build_matrix", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kali_build_matrix_covers_four_distinct_pilots() -> None:
    values = _matrix_module().load_matrix()
    assert len(values) == 4
    assert {value["solver"] for value in values} == {
        "surface-mapper",
        "ctf-web-solver",
        "ctf-pwn-solver",
        "static-analysis-solver",
    }
    assert len({value["profile"] for value in values}) == 4
    assert all((ROOT / "containers" / "kali" / value["context"] / "Dockerfile").is_file() for value in values)


def test_all_profile_toolset_digests_match_committed_manifests() -> None:
    config = json.loads(
        (ROOT / "config" / "sandbox.json").read_text(encoding="utf-8")
    )
    manifest_root = ROOT / "containers" / "kali" / "toolsets" / "generated"
    local_profiles = {
        profile_id: profile
        for profile_id, profile in config["profiles"].items()
        if profile["provider"] != "remote_http"
    }
    assert len(local_profiles) == 22
    assert {path.stem for path in manifest_root.glob("*.json")} == set(local_profiles)
    for profile_id, profile in local_profiles.items():
        raw = (manifest_root / f"{profile_id}.json").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == profile["toolset_digest"]
        manifest = json.loads(raw)
        assert manifest["profile_id"] == profile_id
        assert set(profile["allowed_executables"]) <= set(manifest["tools"])


def test_four_pilot_manifests_match_generated_manifests() -> None:
    for value in _matrix_module().load_matrix():
        context_manifest = ROOT / "containers" / "kali" / value["context"] / "toolset.json"
        generated_manifest = ROOT / "containers" / "kali" / "toolsets" / "generated" / f"{value['profile']}.json"
        assert context_manifest.read_bytes() == generated_manifest.read_bytes()
