"""Deployment readiness boundary consumed by the launcher, not by end users."""

from fastapi import APIRouter

from tga.deployment import readiness


router = APIRouter(tags=["system"])


@router.get("/system/readiness")
def system_readiness() -> dict:
    """Capability-graded readiness.

    `tga up` waits on this rather than `/api/health`, because a listening
    socket does not prove that storage is writable or that tool execution is
    isolated.  Always returns 200: the payload, not the status code, carries
    the verdict.
    """
    return readiness.evaluate().to_dict()
