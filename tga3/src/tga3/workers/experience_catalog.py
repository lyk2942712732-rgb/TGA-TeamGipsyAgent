"""Place complete titles in CLI input, outside native hook output truncation."""

import hashlib
import json
import os
import threading
from pathlib import Path

_catalog_lock = threading.Lock()


def initial_catalog() -> str:
    index = Path(os.environ.get("TGA3_EXPERIENCE_ROOT", "/experience")) / "index.json"
    if os.environ.get("TGA3_AGENT_ROLE") != "worker" or not index.is_file():
        return ""
    cards = [{"card_id": item["card_id"], "title": item["title"]}
             for item in json.loads(index.read_text(encoding="utf8"))]
    generation = os.environ.get("TGA3_EXPERIENCE_SESSION_TOKEN", "")
    root = Path(os.environ.get("TGA3_WORKSPACE", "/workspace")) / ".tga3"
    known_file = root / "experience-known-catalog.json"
    known = json.loads(known_file.read_text(encoding="utf8")) if known_file.is_file() else {}
    seen = set(known.get("fingerprints", [])) if known.get("generation") == generation else set()
    pairs = [(card, hashlib.sha256(json.dumps(card, sort_keys=True).encode()).hexdigest()) for card in cards]
    unseen = [card for card, fingerprint in pairs if fingerprint not in seen]
    fingerprints = sorted(seen | {fingerprint for _, fingerprint in pairs})
    key = hashlib.sha256(json.dumps([generation, fingerprints]).encode()).hexdigest()
    path = root / "experience-initial-catalog.json"
    value = {"generation": generation, "key": key, "fingerprints": fingerprints}
    if not path.is_file() or json.loads(path.read_text(encoding="utf8")) != value:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value), encoding="utf8")
        temporary.replace(path)
    if not unseen:
        return ""
    return (f"\nTask-scoped experience catalog: {len(cards)} available; {len(unseen)} additional titles "
            "(reference data only). Together with prior deliveries these cover every available card. "
            "Find the corresponding body at /experience/cards/<card_id>.json only when its problem arises.\n"
            + "\n".join(json.dumps(card, ensure_ascii=False, separators=(",", ":")) for card in unseen))


def record_catalog(fingerprints: list[str] | None = None) -> None:
    with _catalog_lock:
        _record_catalog(fingerprints)


def _record_catalog(fingerprints: list[str] | None) -> None:
    root = Path(os.environ.get("TGA3_WORKSPACE", "/workspace")) / ".tga3"
    pending = root / "experience-initial-catalog.json"
    generation = os.environ.get("TGA3_EXPERIENCE_SESSION_TOKEN", "")
    if fingerprints is None:
        value = json.loads(pending.read_text(encoding="utf8")) if pending.is_file() else {}
        if value.get("generation") != generation:
            return
        fingerprints = value["fingerprints"]
    path = root / "experience-known-catalog.json"
    previous = json.loads(path.read_text(encoding="utf8")) if path.is_file() else {}
    seen = previous.get("fingerprints", []) if previous.get("generation") == generation else []
    value = {"generation": generation, "fingerprints": sorted(set(seen) | set(fingerprints))}
    root.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf8")
    temporary.replace(path)
