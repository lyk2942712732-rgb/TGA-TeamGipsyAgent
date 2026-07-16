from tga.capabilities.registry import CapabilityRegistry


def test_registry_exposes_project_calibrated_capabilities(tmp_path):
    registry = CapabilityRegistry(project_root=tmp_path)
    snapshot = registry.snapshot()
    names = {item["name"] for item in snapshot["capabilities"]}

    assert {
        "http.request",
        "tool.invoke",
        "workspace.python",
        "workspace.binary",
        "artifact.inspect",
    } <= names
    assert "challenge.submit_flag" not in names
    assert "challenge.get_state" not in names
    for item in snapshot["capabilities"]:
        assert item["input_schema"]
        assert item["budget_key"]
        assert item["redacted_summary"]
