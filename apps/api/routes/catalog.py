"""Bounded read-only product catalogs."""

from fastapi import APIRouter, HTTPException, Query

from apps.api.routes.support import _run_root
from tga.application.projections.models import CatalogPage
from tga.application.queries.catalog import CatalogQueries

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/{kind}", response_model=CatalogPage)
def catalog(
    kind: str,
    query: str = Query(default="", max_length=255),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> CatalogPage:
    try:
        result = CatalogQueries(run_root=_run_root()).catalog(
            kind, query=query, offset=offset, limit=limit
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={
            "code": "CATALOG_KIND_NOT_FOUND",
            "message": "catalog kind is not available",
            "kind": kind,
        }) from exc
    return CatalogPage.model_validate(result)
