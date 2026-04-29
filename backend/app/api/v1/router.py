from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps.auth import require_admin, require_viewer
from app.api.v1.endpoints import admin_role_requests, auth, config, loggers, measurements, processing

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(admin_role_requests.router, prefix="/admin-role-requests", tags=["admin-role-requests"])
api_router.include_router(config.router, prefix="/config", tags=["config"], dependencies=[Depends(require_admin)])
api_router.include_router(loggers.router, prefix="/loggers", tags=["loggers"])
api_router.include_router(
    measurements.router,
    prefix="/measurements",
    tags=["measurements"],
    dependencies=[Depends(require_viewer)],
)
api_router.include_router(processing.router, prefix="/processing", tags=["processing"], dependencies=[Depends(require_admin)])

