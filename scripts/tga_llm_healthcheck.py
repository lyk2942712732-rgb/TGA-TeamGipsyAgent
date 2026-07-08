from __future__ import annotations

import json

from tga.models.base import ModelMessage
from tga.models.bootstrap import build_model_client_from_env, model_config_status


def main() -> int:
    client = build_model_client_from_env()
    if client is None:
        print(json.dumps({"ok": False, "status": model_config_status(), "error": "LLM_NOT_CONFIGURED"}, ensure_ascii=False, indent=2))
        return 0
    response = client.chat([ModelMessage(role="user", content="请只回复 TGA_OK")], temperature=0)
    print(json.dumps({"ok": "TGA_OK" in response.content, "model": response.model, "content": response.content}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
