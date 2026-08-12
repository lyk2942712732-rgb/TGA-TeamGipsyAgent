"""The only HTTP application. TGA2 deliberately contains no FastAPI code."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from apps.api.routes import router
from tga2.bootstrap import get_container

RUN_ROOT = Path(os.getenv("TGA2_RUN_ROOT", "runs2")).resolve()


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = get_container(RUN_ROOT)
    app.state.container = container
    await container.refresh_mcp_tools()
    yield


app = FastAPI(title="TGA API", version="2.0.0", lifespan=lifespan)
app.state.container = get_container(RUN_ROOT)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api/v2")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "process": "healthy", "service": "tga2-langgraph"}


web_root = Path(
    os.getenv("TGA2_WEB_ROOT", Path(__file__).parents[1] / "web" / "dist")
).resolve()
if web_root.is_dir():
    app.mount("/", StaticFiles(directory=web_root, html=True), name="web")
