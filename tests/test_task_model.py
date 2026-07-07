import pytest

from tga.contracts import TGATask


def test_task_model_parses():
    task = TGATask(
        id="task_1",
        name="demo",
        mode="ctf",
        target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"],
        goal="solve",
        flag_format=r"flag\{[^}]+\}",
    )
    assert task.mode == "ctf"


def test_web_audit_requires_scope():
    with pytest.raises(ValueError, match="web_audit requires non-empty scope"):
        TGATask(
            id="task_1",
            name="audit",
            mode="web_audit",
            target="http://127.0.0.1:8080",
            scope=[],
            goal="audit",
        )

