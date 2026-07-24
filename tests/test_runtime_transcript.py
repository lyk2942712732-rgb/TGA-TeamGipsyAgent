from __future__ import annotations

import json

from tga.runtime.transcript import TranscriptStore


def test_transcript_redacts_complete_sensitive_values_and_case_insensitive_keys(tmp_path) -> None:
    transcript = TranscriptStore(tmp_path / "messages.json")
    transcript.save([{
        "role": "tool",
        "content": (
            "Authorization: Bearer provider-secret-token-12345\n"
            "Cookie: session=private-cookie-value\n"
            "api_key=private-api-key-value password=private-password"
        ),
        "Authorization": "Bearer nested-secret-token-98765",
        "metadata": {"X-API-Key": "private-header-key", "safe": "retained"},
    }])

    raw = (tmp_path / "messages.json").read_text(encoding="utf-8")
    persisted = json.loads(raw)

    for secret in (
        "provider-secret-token-12345",
        "private-cookie-value",
        "private-api-key-value",
        "private-password",
        "nested-secret-token-98765",
        "private-header-key",
    ):
        assert secret not in raw
    assert "Authorization" not in persisted[0]
    assert persisted[0]["metadata"] == {"safe": "retained"}
    assert "Authorization: [REDACTED]" in persisted[0]["content"]
