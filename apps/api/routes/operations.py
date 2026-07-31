"""Operational Dashboard and global approval HTTP boundaries."""

from fastapi import APIRouter, Query

from apps.api.routes.support import _run_root
from tga.application.projections.models import DashboardResponse, GlobalApprovalPage
from tga.application.queries.operations import OperationalQueries


router = APIRouter(tags=["operations"])


def _queries() -> OperationalQueries:
    return OperationalQueries(run_root=_run_root())


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard() -> DashboardResponse:
    return _queries().dashboard()


@router.get("/approvals", response_model=GlobalApprovalPage)
def global_approvals(
    status: str | None = Query(default=None, pattern="^(pending|approved|rejected|expired)$"),
    task_id: str | None = Query(default=None, max_length=256),
    solver_id: str | None = Query(default=None, max_length=256),
    intent_id: str | None = Query(default=None, max_length=256),
    risk: str | None = Query(default=None, pattern="^(passive|active|destructive)$"),
    capability: str | None = Query(default=None, max_length=256),
    deadline: str | None = Query(default=None, pattern="^(overdue|24h|7d|none)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> GlobalApprovalPage:
    return _queries().approvals(
        status=status, task_id=task_id, solver_id=solver_id,
        intent_id=intent_id, risk=risk, capability=capability,
        deadline=deadline, offset=offset, limit=limit,
    )
