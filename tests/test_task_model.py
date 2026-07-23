import json

import pytest

from tga.contracts import TGATask
from tga.evidence.store import EvidenceStore, utc_now


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


def test_legacy_persisted_task_is_read_with_new_mode_and_without_non_ctf_flag(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.db")
    payload = {
        "id": "legacy_db", "name": "legacy", "mode": "code_audit",
        "target": "./source", "goal": "audit", "flag_format": r"FLAG\{.*\}",
    }
    store.conn.execute(
        "INSERT INTO tasks(id, payload_json, created_at) VALUES (?, ?, ?)",
        (payload["id"], json.dumps(payload), utc_now()),
    )
    store.conn.commit()
    snapshot = store.task_snapshot(payload["id"])
    assert snapshot["task"]["mode"] == "vulnerability_research"
    assert snapshot["task"]["flag_format"] is None
    store.close()


def test_task_model_trims_target_and_scope() -> None:
    task = TGATask(
        id="task_trim", name="trim", mode="ctf",
        target="  https://challenge.example/path  ",
        scope=["  https://challenge.example  ", "https://challenge.example"],
        goal="solve",
    )

    assert task.target == "https://challenge.example/path"
    assert task.scope == ["https://challenge.example"]


def test_penetration_test_derives_compatibility_scope_from_target():
    task = TGATask(
        id="task_1",
        name="audit",
        mode="penetration_test",
        target="http://127.0.0.1:8080/path",
        goal="audit",
    )
    assert task.scope == ["http://127.0.0.1:8080"]


def test_ctf_derives_scope_and_tls_exception_is_exact_target_origin():
    derived = TGATask(
        id="task_1", name="ctf", mode="ctf", target="https://challenge.example",
        goal="solve",
    )
    assert derived.scope == ["https://challenge.example"]
    task = TGATask(
        id="task_2", name="ctf", mode="ctf", target="https://challenge.example/",
        scope=["challenge.example"], goal="solve",
        insecure_tls_origins=["https://challenge.example"],
    )
    assert task.insecure_tls_origins == ["https://challenge.example"]

    with pytest.raises(ValueError, match="exact HTTPS target origin"):
        TGATask(
            id="task_3", name="ctf", mode="ctf", target="https://challenge.example",
            scope=["challenge.example"], goal="solve",
            insecure_tls_origins=["https://other.example"],
        )

