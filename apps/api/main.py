"""FastAPI entrypoint for TGA's independent UI."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.routes import router as runtime_v2_router
from apps.api.routes.support import _runtime_scheduler
from tga.models.bootstrap import model_config_status

@asynccontextmanager
async def lifespan(_app: FastAPI):
    status = model_config_status()
    _runtime_scheduler().recover(
        schedule_runnable=status.get("verification_status") == "verified"
    )
    yield


app = FastAPI(title="TGA API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runtime_v2_router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Process health used by the desktop and browser launchers."""
    return {"status": "ok", "service": "tga-runtime"}
