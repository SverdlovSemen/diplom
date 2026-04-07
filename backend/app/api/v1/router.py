from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import loggers, measurements, processing

api_router = APIRouter()
api_router.include_router(loggers.router, prefix="/loggers", tags=["loggers"])
api_router.include_router(measurements.router, prefix="/measurements", tags=["measurements"])
api_router.include_router(processing.router, prefix="/processing", tags=["processing"])

