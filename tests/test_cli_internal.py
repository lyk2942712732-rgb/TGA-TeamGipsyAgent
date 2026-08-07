"""The installed Linux shim must preserve user-facing launcher flags."""

from __future__ import annotations

from tga.cli import internal


def test_internal_up_accepts_public_for_the_linux_fallback_shim(monkeypatch, capsys):
    calls: list[dict] = []

    class Result:
        def to_dict(self):
            return {"ok": True, "status": "ready", "url": "http://0.0.0.0:8173"}

    monkeypatch.setattr(
        internal.lifecycle,
        "up",
        lambda **kwargs: (calls.append(kwargs), Result())[1],
    )

    assert internal.main(["up", "--public", "--port", "8173", "--json"]) == 0
    assert calls == [{
        "host": "0.0.0.0",
        "port": 8173,
        "open_browser": False,
        "timeout_seconds": 90.0,
        "pull_images": False,
    }]
    assert '"ok": true' in capsys.readouterr().out
