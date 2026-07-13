"""Desktop launcher for the local TGA Runtime UI.

The desktop command deliberately keeps the existing FastAPI/React split.  It
builds the React bundle, serves it from the same local origin as the API, then
opens that origin in a native WebView window.  No remote service is exposed.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


class DesktopLaunchError(RuntimeError):
    """A user-actionable failure while preparing the local desktop app."""


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def launch_desktop(*, host: str = "127.0.0.1", port: int = 8123, build: bool = True) -> int:
    """Start TGA locally and block until its native window is closed."""
    root = project_root()
    web_dist = _prepare_frontend(root=root, host=host, port=port, build=build)
    _assert_port_available(host, port)
    app = _desktop_application(web_dist)
    server, thread = _start_server(app=app, host=host, port=port)
    origin = f"http://{host}:{port}"
    try:
        _wait_for_health(origin)
        try:
            import webview
        except ImportError as exc:
            raise DesktopLaunchError(
                "Desktop WebView is not installed. Run: python -m pip install -e ."
            ) from exc
        webview.create_window("TGA · Trusted Goal Agent", origin, min_size=(1100, 720))
        webview.start()
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def launch_web(*, host: str = "127.0.0.1", port: int = 5173, build: bool = True) -> int:
    """Serve the local Runtime UI and open it with the default browser.

    The command stays attached to the terminal so the user can stop the local
    server with Ctrl+C.  It deliberately uses a distinct default port from the
    desktop launcher and common API services.
    """
    root = project_root()
    web_dist = _prepare_frontend(root=root, host=host, port=port, build=build)
    _assert_port_available(host, port)
    app = _desktop_application(web_dist)
    server, thread = _start_server(app=app, host=host, port=port)
    origin = f"http://{host}:{port}"
    try:
        _wait_for_health(origin)
        if not webbrowser.open(origin, new=2):
            print(f"TGA web is ready at {origin}")
        thread.join()
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _prepare_frontend(*, root: Path, host: str, port: int, build: bool) -> Path:
    web_root = root / "apps" / "web"
    dist = web_root / "dist"
    if not web_root.is_dir():
        raise DesktopLaunchError(
            "TGA frontend sources are missing. Run `tga go` from an editable project installation."
        )
    # These values are needed by the Python API process as well as the Vite
    # build subprocess.  Keep the user's explicit environment configuration.
    os.environ.setdefault("TGA_RUN_ROOT", str(root / "runs"))
    hub = root / "mcp-security-hub"
    if hub.is_dir():
        os.environ.setdefault("TGA_MCP_SECURITY_HUB_ROOT", str(hub))
    if not build and (dist / "index.html").is_file():
        return dist
    if not build:
        raise DesktopLaunchError("Frontend bundle is missing; run `tga go` without --no-build first.")

    env = os.environ.copy()
    # Keep an explicitly configured cross-origin API base, but otherwise let
    # the frontend use the page origin.  In particular, embedding 0.0.0.0 in
    # a Vite bundle makes a server reachable but is not a browser-reachable
    # address.  Same-origin fallback works for localhost, public IPs, domains
    # and reverse proxies alike.
    try:
        # On Windows CreateProcess does not reliably resolve the `npm` shim
        # without its extension.  Prefer the actual npm.cmd command resolved
        # from PATH instead of passing a shell-specific bare command name.
        npm = shutil.which("npm.cmd") if os.name == "nt" else shutil.which("npm")
        npm = npm or shutil.which("npm")
        if not npm:
            raise FileNotFoundError("npm executable was not found")
        subprocess.run([npm, "run", "build"], cwd=web_root, env=env, check=True)
    except FileNotFoundError as exc:
        # A normal desktop launch should still work on machines where Node was
        # used to build the bundled UI but is not available on the user's CMD
        # PATH.  Only require npm when no usable bundle exists at all.
        if (dist / "index.html").is_file():
            return dist
        raise DesktopLaunchError(
            "Node.js/npm is unavailable and no built frontend bundle exists. "
            "Install Node.js, or run this command once from a development environment."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise DesktopLaunchError("Frontend build failed; resolve the npm errors above and retry.") from exc
    if not (dist / "index.html").is_file():
        raise DesktopLaunchError("Frontend build completed without apps/web/dist/index.html.")
    return dist


def _desktop_application(web_dist: Path):
    """Attach the built SPA after API routes so `/api/*` keeps API semantics."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from apps.api.main import app

    marker = "tga_desktop_static_root"
    if getattr(app.state, marker, None) == str(web_dist):
        return app
    if getattr(app.state, marker, None):
        raise DesktopLaunchError("A different TGA desktop bundle is already attached in this process.")

    assets = web_dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="tga-desktop-assets")

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def desktop_spa(frontend_path: str):
        if frontend_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        candidate = (web_dist / frontend_path).resolve()
        try:
            candidate.relative_to(web_dist.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid static path") from exc
        if frontend_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(web_dist / "index.html")

    setattr(app.state, marker, str(web_dist))
    return app


def _assert_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            raise DesktopLaunchError(f"{host}:{port} is already in use. Stop the existing service or choose --port.") from exc


def _start_server(*, app, host: str, port: int):
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="tga-desktop-api", daemon=True)
    thread.start()
    return server, thread


def _wait_for_health(origin: str, timeout_seconds: float = 15) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{origin}/api/health", timeout=0.8) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.15)
    raise DesktopLaunchError("TGA API did not become ready within 15 seconds.")
