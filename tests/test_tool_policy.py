from tga.contracts import TGATask
from tga.tools.tool_policy import is_allowed


def test_active_tool_blocked_in_passive():
    task = TGATask(
        id="task_1",
        name="audit",
        mode="penetration_test",
        target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"],
        intensity="passive",
        allow_active_scan=False,
        goal="audit",
    )
    ok, reason = is_allowed(task=task, tool="nuclei", target="http://127.0.0.1:8080")
    assert not ok and reason == "ACTIVE_SCAN_NOT_ALLOWED"


def test_out_of_scope_tool_blocked():
    task = TGATask(
        id="task_1",
        name="audit",
        mode="penetration_test",
        target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"],
        intensity="active",
        allow_active_scan=True,
        goal="audit",
    )
    ok, reason = is_allowed(task=task, tool="nmap", target="http://127.0.0.1:9000")
    assert not ok and reason == "OUT_OF_SCOPE"

