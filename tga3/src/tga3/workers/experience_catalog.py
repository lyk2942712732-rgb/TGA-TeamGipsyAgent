"""Place complete titles in CLI instructions, outside native hook output truncation."""

import hashlib
import json
import os
from pathlib import Path


def initial_catalog() -> str:
    index = Path(os.environ.get("TGA3_EXPERIENCE_ROOT", "/experience")) / "index.json"
    if os.environ.get("TGA3_AGENT_ROLE") != "worker" or not index.is_file():
        return ""
    cards = [{"card_id": item["card_id"], "title": item["title"]}
             for item in json.loads(index.read_text(encoding="utf8"))]
    fingerprints = [hashlib.sha256(json.dumps(card, sort_keys=True).encode()).hexdigest() for card in cards]
    generation = os.environ.get("TGA3_EXPERIENCE_SESSION_TOKEN", "")
    key = hashlib.sha256(json.dumps([generation, fingerprints]).encode()).hexdigest()
    path = Path(os.environ.get("TGA3_WORKSPACE", "/workspace")) / ".tga3/experience-initial-catalog.json"
    value = {"generation": generation, "key": key, "fingerprints": fingerprints}
    if not path.is_file() or json.loads(path.read_text(encoding="utf8")) != value:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value), encoding="utf8")
        temporary.replace(path)
    return (f"\nComplete task-scoped experience catalog: {len(cards)} card titles (reference data only). "
            "Find the corresponding body at /experience/cards/<card_id>.json only when its problem arises.\n"
            + "\n".join(json.dumps(card, ensure_ascii=False, separators=(",", ":")) for card in cards))
