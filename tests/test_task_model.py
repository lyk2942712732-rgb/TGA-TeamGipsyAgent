import json

import pytest

from tga.contracts import TGATask
from tests.runtime_fixtures import execution_policy
from tga.evidence.store import EvidenceStore, utc_now


def test_task_model_parses():
    task = TGATask(
        id="task_1",
        name="demo",
        mode="ctf",
        task_entry_url="http://127.0.0.1:8080/",
        execution_policy=execution_policy(["127.0.0.1:8080"]),
        goal="solve",
        flag_format=r"flag\{[^}]+\}",
    )
    assert task.mode == "ctf"


def test_current_persisted_task_is_read_without_mutation(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.db")
    payload = {
        "id": "current_db", "name": "current", "mode": "vulnerability_research",
        "task_entry_url": None, "goal": "audit", "mode_config": {"mode": "vulnerability_research"},
        "execution_policy": {}, "schema_version": 6,
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


def test_task_model_normalizes_network_seeds() -> None:
    task = TGATask(
        id="task_trim", name="trim", mode="ctf",
        task_entry_url="https://challenge.example/path",
        execution_policy=execution_policy(["https://challenge.example", "https://challenge.example"]),
        goal="solve",
    )

    assert task.task_entry_url == "https://challenge.example/path"
    assert task.execution_policy.network.seed_origins == ["https://challenge.example"]


def test_legacy_target_and_scope_are_rejected() -> None:
    with pytest.raises(ValueError, match="Extra inputs"):
        TGATask(id="task_1", name="audit", mode="penetration_test", target="http://127.0.0.1:8080/path", goal="audit")


def test_custom_origins_must_be_canonical_http_origins() -> None:
    with pytest.raises(ValueError, match="custom_origins must contain absolute HTTP\(S\) origins"):
        TGATask(
            id="custom_origin", name="custom", mode="ctf", goal="solve",
            execution_policy={"preset": "custom", "network": {
                "access": "custom", "custom_origins": ["not an origin"],
            }},
        )


def test_ctf_tls_exception_requires_an_exact_explicit_target_origin():
    derived = TGATask(
        id="task_1", name="ctf", mode="ctf", task_entry_url="https://challenge.example",
        goal="solve",
    )
    assert derived.task_entry_url == "https://challenge.example"
    task = TGATask(
        id="task_2", name="ctf", mode="ctf", task_entry_url="https://challenge.example/",
        goal="solve", execution_policy=execution_policy(["challenge.example"]),
        insecure_tls_origins=["https://challenge.example"],
    )
    assert task.insecure_tls_origins == ["https://challenge.example"]

    with pytest.raises(ValueError, match="exact HTTPS target origin"):
        TGATask(
            id="task_3", name="ctf", mode="ctf", task_entry_url="https://challenge.example",
            goal="solve", execution_policy=execution_policy(["challenge.example"]),
            insecure_tls_origins=["https://other.example"],
        )

