"""Task config loading."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from tga.contracts import TGATask


def load_task_config(path: str | Path) -> TGATask:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.setdefault("id", f"task_{uuid4().hex[:10]}")
    return TGATask.model_validate(data)

