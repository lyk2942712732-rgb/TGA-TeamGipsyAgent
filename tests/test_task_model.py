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

