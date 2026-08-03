"""FastAPI entrypoint for TGA's independent UI."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from apps.api.routes import router as runtime_v2_router
from apps.api.routes.support import _runtime_scheduler
from tga.deployment.paths import run_root
from tga.models.bootstrap import model_config_status
from tga.runtime.host_handler_contract import validate_runtime_host_handlers
from tga.sandbox import KaliProfileNotReadyError, inspect_kali_runtime_readiness
from tga.sandbox.lifecycle import SandboxLifecycleService

@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_runtime_host_handlers()
    # Must be the same root the task runtime writes to; a divergence here
    # leaves sandbox reconciliation scanning an empty tree while real
    # containers leak.
    sandbox_lifecycle = SandboxLifecycleService(run_root())
    sandbox_lifecycle.start()
    status = model_config_status()
    _runtime_scheduler().recover(
        schedule_runnable=status.get("verification_status") == "verified"
    )
    try:
        yield
    finally:
        sandbox_lifecycle.close()


app = FastAPI(title="TGA API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runtime_v2_router, prefix="/api")


@app.exception_handler(KaliProfileNotReadyError)
def kali_profile_not_ready(
    _request: Request, exc: KaliProfileNotReadyError
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "kali_profile_not_ready",
            "profile_id": exc.profile_id,
            "reason": exc.reason,
            "message": str(exc),
        },
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    """Process health used by the desktop and browser launchers."""
    readiness = inspect_kali_runtime_readiness()
    return {
        "status": "ok",
        "process": "healthy",
        "service": "tga-runtime",
        "kali_runtime": readiness.overall,
    }
