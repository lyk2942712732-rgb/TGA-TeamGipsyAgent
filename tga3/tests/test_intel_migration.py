import json
from uuid import uuid4

from tga3.intel_migration import migrate_task_intels
from tga3.scout_intel import read_intels


def test_old_raw_and_merged_files_convert_with_distinct_ids_and_preserved_sources(tmp_path):
    task_id = uuid4()
    root, backup = tmp_path / "task", tmp_path / "backup"
    directory = root / "intels/scout"
    directory.mkdir(parents=True)
    actor = {"agent_id": "scout", "display_name": "Scout", "role": "scout"}
    originals = []
    for index in (1, 2):
        value = {"schema_version": 2, "task_id": str(task_id), "actor": actor, "kind": "intel",
                 "key": f"old-{index}", "sequence": index, "topic": "fixture",
                 "body": {"claim": f"fact-{index}"}, "source_action_ids": [f"scout-action-{index}"],
                 "evidence_paths": [f"result/{index}.txt"], "artifact_refs": [{"artifact_id": str(uuid4())}]}
        path = directory / f"{index}.json"
        path.write_text(json.dumps(value))
        originals.append(path.read_bytes())
    merged = directory / "merged"
    merged.mkdir()
    value.update(schema_version=3, key="old-final", sequence=1, merged_from_keys=["old-1", "old-2"],
                 source_action_ids=["scout-action-1", "scout-action-2"],
                 evidence_paths=["result/1.txt", "result/2.txt"])
    (merged / "1.json").write_text(json.dumps(value))
    assert migrate_task_intels(root, backup, task_id) == 3
    raw = read_intels(directory / "raw", task_id)
    final = read_intels(merged, task_id, merged=True)
    assert all(item.id.startswith("scout-raw-intel-") for item in raw.intels)
    assert final.intels[0].id.startswith("scout-merged-intel-")
    assert set(final.intels[0].merged_from_ids) == {item.id for item in raw.intels}
    assert len(list(directory.rglob("*.json"))) == 2
    assert (backup / "intels/scout/1.json").read_bytes() == originals[0]
    saved = json.loads((directory / "raw/scout-intels.json").read_text())
    assert "artifact_refs" not in json.dumps(saved) and "key" not in json.dumps(saved)
    assert migrate_task_intels(root, backup, task_id) == 0
