"""`tga reset` is the way out of a wedged provision, not a way to lose work."""

from __future__ import annotations

from tga.deployment import lifecycle, service_manager, state as state_module


def _quiet(monkeypatch) -> None:
    """Keep the test off this machine's systemd and processes."""
    monkeypatch.setattr(service_manager, "manages_api", lambda: False)
    monkeypatch.setattr(service_manager, "unit_installed", lambda unit=None: False)


def test_reset_clears_the_recorded_steps(monkeypatch, tmp_path):
    """`up` resumes from these, which is exactly what leaves it wedged."""
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path))
    _quiet(monkeypatch)
    current = state_module.load()
    current.phase = "degraded"
    current.mark_completed("ensure_configuration")
    current.mark_completed("start_api")
    current.api_url = "http://127.0.0.1:8123"
    state_module.save(current)

    payload = lifecycle.reset()

    assert payload["ok"] and payload["status"] == "uninstalled"
    after = state_module.load()
    assert after.completed_steps == []
    assert after.phase == "uninstalled"
    assert after.api_url == ""


def test_reset_keeps_task_data(monkeypatch, tmp_path):
    """The run root holds a competition's evidence; nothing here removes it."""
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path))
    _quiet(monkeypatch)
    evidence = tmp_path / "task_1" / "artifact.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("do not lose me", encoding="utf-8")

    payload = lifecycle.reset()

    assert evidence.read_text(encoding="utf-8") == "do not lose me"
    assert payload["preserved_run_root"] == str(tmp_path)


def test_reset_stops_before_forgetting(monkeypatch, tmp_path):
    """Clearing the record while the API still runs would orphan it."""
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path))
    _quiet(monkeypatch)
    order: list[str] = []
    real_down = lifecycle.down

    def watched_down():
        order.append("down")
        return real_down()

    monkeypatch.setattr(lifecycle, "down", watched_down)
    real_save = state_module.save

    def watched_save(state):
        order.append("save")
        return real_save(state)

    monkeypatch.setattr(state_module, "save", watched_save)

    lifecycle.reset()

    assert order[0] == "down", f"expected the stop first, got {order}"


def test_reset_is_idempotent(monkeypatch, tmp_path):
    """Someone reaching for this twice is already having a bad day."""
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path))
    _quiet(monkeypatch)

    first = lifecycle.reset()
    second = lifecycle.reset()

    assert first["status"] == second["status"] == "uninstalled"
    assert state_module.load().completed_steps == []
