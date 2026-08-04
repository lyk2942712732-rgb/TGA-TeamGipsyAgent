"""Headless API + SPA server, the only process that actually serves TGA.

This is what systemd supervises on Linux and what the launcher starts on
Windows.  It deliberately owns no user interaction: it never opens a browser,
never starts a WebView, and never shells out to `npm`.  A production install
serves a bundle that was built at packaging time and located through
``TGA_WEB_DIST``.
"""

from __future__ import annotations

import signal
import threading
from pathlib import Path

from tga.deployment.errors import DeploymentError, ErrorCode
from tga.deployment.paths import web_dist


def build_application(web_root: Path | None = None):
    """Attach the SPA to the API app after routes so `/api/*` keeps priority."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from apps.api.main import app

    resolved = Path(web_root) if web_root else web_dist()

    marker = "tga_static_root"
    if getattr(app.state, marker, None) == str(resolved):
        return app
    if getattr(app.state, marker, None):
        raise DeploymentError(
            ErrorCode.WEB_BUNDLE_MISSING,
            "a different frontend bundle is already attached in this process",
        )

    assets = resolved / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="tga-assets")

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def spa(frontend_path: str):
        if frontend_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        candidate = (resolved / frontend_path).resolve()
        try:
            candidate.relative_to(resolved.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid static path") from exc
        if frontend_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(resolved / "index.html")

    setattr(app.state, marker, str(resolved))
    return app


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8123,
    web_root: Path | None = None,
    log_level: str = "info",
) -> int:
    """Run the server in the foreground until terminated."""
    import uvicorn

    application = build_application(web_root)
    config = uvicorn.Config(
        application, host=host, port=port, log_level=log_level, access_log=False
    )
    server = uvicorn.Server(config)
    _install_termination_handlers(server)
    server.run()
    return 0


def start_background(
    *, host: str, port: int, web_root: Path | None = None
) -> tuple[object, threading.Thread]:
    """Start the server on a worker thread, for in-process orchestration."""
    import uvicorn

    application = build_application(web_root)
    config = uvicorn.Config(
        application, host=host, port=port, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="tga-api", daemon=True)
    thread.start()
    return server, thread


def _install_termination_handlers(server) -> None:
    """Make SIGTERM a clean shutdown so systemd never has to SIGKILL."""

    def request_exit(_signum, _frame):
        server.should_exit = True

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        handler = getattr(signal, name, None)
        if handler is None:
            continue
        try:
            signal.signal(handler, request_exit)
        except (ValueError, OSError):
            # Not on the main thread, or unsupported on this platform.
            continue
