from tga.capabilities.registry import build_default_registry


def test_registry_exposes_enabled_runtime_capabilities():
    registry = build_default_registry()
    snapshot = registry.snapshot()
    names = {item["name"] for item in snapshot["capabilities"]}

    assert {
        "http.request",
        "tool.invoke",
        "workspace.python",
        "artifact.inspect",
    } <= names
    assert "challenge.submit_flag" not in names
    assert "challenge.get_state" not in names
    for item in snapshot["capabilities"]:
        assert item["input_schema"]
        assert item["budget_key"]
