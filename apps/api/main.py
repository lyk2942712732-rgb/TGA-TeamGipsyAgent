"""The only HTTP application. TGA2 deliberately contains no FastAPI code."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.routes import router
from tga2.bootstrap import get_container

RUN_ROOT = Path(os.getenv("TGA2_RUN_ROOT", "runs2")).resolve()


class SPAStaticFiles(StaticFiles):
    """Serve React Router locations through the built SPA entry document."""

    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or not self._is_spa_route(path, scope):
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and self._is_spa_route(path, scope):
            return await super().get_response("index.html", scope)
        return response

    @staticmethod
    def _is_spa_route(path: str, scope) -> bool:
        normalized = str(scope.get("path") or path).lstrip("/")
        return (
            scope.get("method") in {"GET", "HEAD"}
            and not normalized.startswith("api/")
            and not Path(normalized).suffix
        )


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
    app.mount("/", SPAStaticFiles(directory=web_root, html=True), name="web")
