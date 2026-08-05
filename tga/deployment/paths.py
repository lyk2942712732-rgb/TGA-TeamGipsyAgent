"""Single source of truth for TGA's on-disk layout.

Tasks, evidence databases, artifacts and sandbox reconciliation must agree on
one run root.  When a module hardcodes ``"runs"`` while the process is
configured with ``TGA_RUN_ROOT=/var/lib/tga/runs``, tasks are written to one
tree while sandbox cleanup scans another, and orphaned containers survive.
Every caller therefore resolves through :func:`run_root`.
"""

from __future__ import annotations

import os
from pathlib import Path

from tga.deployment.errors import DeploymentError, ErrorCode


#: Development fallback, relative to the current working directory.  Packaged
#: installations always set ``TGA_RUN_ROOT`` explicitly.
DEFAULT_RUN_ROOT = "runs"

_WEB_DIST_ENV = "TGA_WEB_DIST"
_RUN_ROOT_ENV = "TGA_RUN_ROOT"


def project_root() -> Path:
    """The repository root of a development checkout."""
    return Path(__file__).resolve().parents[2]


def run_root(explicit: str | Path | None = None) -> Path:
    """Resolve the run root for this process.

    Resolution order is explicit argument, then ``TGA_RUN_ROOT``, then a
    working-directory-relative ``runs`` for development checkouts.  The result
    is always absolute so that a later ``chdir`` cannot silently repoint it.
    """
    candidate = explicit or os.environ.get(_RUN_ROOT_ENV) or DEFAULT_RUN_ROOT
    return Path(candidate).expanduser().resolve()


def ensure_run_root(explicit: str | Path | None = None) -> Path:
    """Resolve the run root and prove this process can write to it."""
    root = run_root(explicit)
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".tga-write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise DeploymentError(
            ErrorCode.RUN_ROOT_UNWRITABLE, f"{root}: {exc}"
        ) from exc
    return root


def web_dist(explicit: str | Path | None = None) -> Path:
    """Locate the built frontend bundle.

    Resolution order follows the deployment contract: an explicit command
    argument, then ``TGA_WEB_DIST``, then a bundle packaged next to the
    installed ``tga`` package, then the development ``apps/web/dist`` tree.
    Production installations never build the bundle at startup.
    """
    for candidate in _web_dist_candidates(explicit):
        if (candidate / "index.html").is_file():
            return candidate
    raise DeploymentError(
        ErrorCode.WEB_BUNDLE_MISSING,
        "checked: " + ", ".join(str(item) for item in _web_dist_candidates(explicit)),
    )


def _web_dist_candidates(explicit: str | Path | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())
    env_value = os.environ.get(_WEB_DIST_ENV)
    if env_value:
        candidates.append(Path(env_value).expanduser().resolve())
    # Bundle shipped inside the installed package.
    candidates.append(Path(__file__).resolve().parents[1] / "web")
    # Development checkout.
    candidates.append(project_root() / "apps" / "web" / "dist")
    return candidates


def state_dir() -> Path:
    """Directory holding deployment state, locks and pid files."""
    override = os.environ.get("TGA_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return run_root().parent / "state"


def log_dir() -> Path:
    """Directory holding component logs read back by `tga logs`."""
    override = os.environ.get("TGA_LOG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return run_root().parent / "log"
