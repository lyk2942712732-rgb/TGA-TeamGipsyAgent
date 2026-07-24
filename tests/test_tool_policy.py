from tga.contracts import TGATask
from tga.tools.tool_policy import is_allowed
from tests.runtime_fixtures import execution_policy


def test_active_tool_blocked_in_passive():
    task = TGATask(
        id="task_1",
        name="audit",
        mode="penetration_test",
        task_entry_url="http://127.0.0.1:8080/",
        execution_policy=execution_policy(["127.0.0.1:8080"], network_mode="observe"),
        goal="audit",
    )
    ok, reason = is_allowed(task=task, tool="nuclei", target="http://127.0.0.1:8080")
    assert not ok and reason == "NETWORK_INTERACTION_NOT_AUTHORIZED"


def test_out_of_scope_tool_blocked():
    task = TGATask(
        id="task_1",
        name="audit",
        mode="penetration_test",
        task_entry_url="http://127.0.0.1:8080/",
        execution_policy=execution_policy(["127.0.0.1:8080"]),
        goal="audit",
    )
    ok, reason = is_allowed(task=task, tool="nmap", target="http://127.0.0.1:9000")
    assert not ok and reason == "NETWORK_ORIGIN_NOT_IN_TASK_SOURCES"

