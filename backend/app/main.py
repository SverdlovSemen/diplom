from __future__ import annotations

import logging
from contextlib import suppress
import asyncio
from pathlib import Path
import shutil

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import AsyncSessionLocal
from app.ingest.rtmp_stat import get_active_stream_keys
from app.models.user import UserRole
from app.processing.pipeline import process_due_loggers
from app.security.auth import decode_access_token
from app.services.bootstrap_users import seed_users_if_missing


configure_logging(settings.log_level)
logger = logging.getLogger("app")

app = FastAPI(
    title="Gauge Reader System",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")
Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.storage_dir), name="media")

_worker_task: asyncio.Task[None] | None = None


@app.middleware("http")
async def media_auth_middleware(request: Request, call_next):
    if not request.url.path.startswith("/media/"):
        return await call_next(request)
    token = request.query_params.get("access_token")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    try:
        payload = decode_access_token(token)
        role = payload.get("role")
        if role not in (UserRole.admin.value, UserRole.viewer.value):
            return JSONResponse(status_code=403, content={"detail": "Insufficient permissions"})
    except Exception:
        return JSONResponse(status_code=401, content={"detail": "Invalid token"})
    return await call_next(request)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, object]:
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    db_ok = False
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.exception("Database readiness check failed")

    # nginx stat — soft check: если недоступен, просто покажем false
    try:
        active = await get_active_stream_keys(cache_ttl_sec=0.0)
        rtmp_stat_ok = True
        active_count = len(active)
    except Exception:
        rtmp_stat_ok = False
        active_count = 0

    ok = ffmpeg_ok and db_ok and rtmp_stat_ok
    return {
        "ok": ok,
        "components": {
            "ffmpeg": ffmpeg_ok,
            "db": db_ok,
            "rtmp_stat": rtmp_stat_ok,
        },
        "active_streams": active_count,
    }

@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Starting app", extra={"env": settings.env})
    global _worker_task
    try:
        async with AsyncSessionLocal() as session:
            await seed_users_if_missing(session)
    except Exception:
        logger.exception("User bootstrap failed")

    async def _processing_worker() -> None:
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    await process_due_loggers(session)
            except Exception:
                logger.exception("Background processing loop failed")
            await asyncio.sleep(settings.processing_tick_sec)

    _worker_task = asyncio.create_task(_processing_worker())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    with suppress(asyncio.CancelledError):
        await _worker_task
    _worker_task = None

