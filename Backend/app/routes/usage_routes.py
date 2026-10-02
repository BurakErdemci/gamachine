"""Authenticated, cache-first subscription usage reads."""
from fastapi import APIRouter, Header, Query

from auth_utils import _check_token


def create_usage_router(service):
    router = APIRouter()

    @router.get("/usage/limits")
    async def limits(
        refresh: bool = Query(default=False),
        wait: bool = Query(default=False),
        x_session_token: str = Header(alias="X-Session-Token", default=""),
    ):
        _check_token(x_session_token)
        snapshot = service.snapshot(force=refresh)
        if wait:
            await service.wait_for_refreshes(timeout_s=50)
            snapshot = service.snapshot(start=False)
        return snapshot

    return router
