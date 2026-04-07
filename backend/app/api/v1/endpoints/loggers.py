from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.core.config import settings
from app.schemas.logger import LoggerCreate, LoggerOut, LoggerStatus, LoggerUpdate, LoggerWithStatus
from app.services.loggers import (
    LoggerConflictError,
    LoggerNotFoundError,
    create_logger,
    delete_logger,
    get_logger,
    list_loggers,
    update_logger,
)
from app.services.measurements import get_last_measurement_for_logger
from app.processing.pipeline import check_nginx_stat_active, get_ingest_state, get_nginx_active_streams

router = APIRouter()


@router.get("/", response_model=list[LoggerWithStatus])
async def api_list_loggers(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> list[LoggerWithStatus]:
    items = await list_loggers(session, offset=offset, limit=limit)
    # Один запрос к nginx /stat на весь список вместо N запросов
    nginx_active = await get_nginx_active_streams()
    out: list[LoggerWithStatus] = []
    now = datetime.now(timezone.utc)
    for item in items:
        ingest = await get_ingest_state(item.stream_key)
        # Окно для in-memory состояния: не менее 2 минут, чтобы перезапуск backend
        # не гасил статус между циклами обработки.
        active_window_sec = max(120, item.sample_interval_sec * 3 + 30)
        ingest_recent = (
            ingest.last_success_at is not None
            and (now - ingest.last_success_at).total_seconds() <= active_window_sec
        )
        # Nginx stat — источник истины: если publisher подключён прямо сейчас, всегда active.
        stream_active = item.stream_key in nginx_active or ingest_recent
        last = await get_last_measurement_for_logger(session, item.id)
        status_obj = LoggerStatus(
            stream_active=stream_active,
            ingest_last_attempt_at=ingest.last_attempt_at,
            ingest_last_success_at=ingest.last_success_at,
            ingest_last_error=ingest.last_error,
            last_measurement_at=last.captured_at if last else None,
            last_ok=last.ok if last else None,
            last_error=last.error if last else None,
        )
        base = LoggerOut.model_validate(item, from_attributes=True)
        out.append(LoggerWithStatus(**base.model_dump(), status=status_obj))
    return out


@router.post("/", response_model=LoggerWithStatus, status_code=status.HTTP_201_CREATED)
async def api_create_logger(
    payload: LoggerCreate,
    session: AsyncSession = Depends(get_db_session),
) -> LoggerWithStatus:
    try:
        item = await create_logger(session, payload)
    except LoggerConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    ingest = await get_ingest_state(item.stream_key)
    status_obj = LoggerStatus(
        stream_active=False,
        ingest_last_attempt_at=ingest.last_attempt_at,
        ingest_last_success_at=ingest.last_success_at,
        ingest_last_error=ingest.last_error,
    )
    base = LoggerOut.model_validate(item, from_attributes=True)
    return LoggerWithStatus(**base.model_dump(), status=status_obj)


@router.get("/{logger_id}", response_model=LoggerWithStatus)
async def api_get_logger(
    logger_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> LoggerWithStatus:
    try:
        item = await get_logger(session, logger_id)
    except LoggerNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logger not found") from e
    ingest = await get_ingest_state(item.stream_key)
    now = datetime.now(timezone.utc)
    active_window_sec = max(120, item.sample_interval_sec * 3 + 30)
    ingest_recent = (
        ingest.last_success_at is not None
        and (now - ingest.last_success_at).total_seconds() <= active_window_sec
    )
    stream_active = await check_nginx_stat_active(item.stream_key) or ingest_recent
    last = await get_last_measurement_for_logger(session, item.id)
    status_obj = LoggerStatus(
        stream_active=stream_active,
        ingest_last_attempt_at=ingest.last_attempt_at,
        ingest_last_success_at=ingest.last_success_at,
        ingest_last_error=ingest.last_error,
        last_measurement_at=last.captured_at if last else None,
        last_ok=last.ok if last else None,
        last_error=last.error if last else None,
    )
    base = LoggerOut.model_validate(item, from_attributes=True)
    return LoggerWithStatus(**base.model_dump(), status=status_obj)


@router.patch("/{logger_id}", response_model=LoggerWithStatus)
async def api_update_logger(
    logger_id: uuid.UUID,
    payload: LoggerUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> LoggerWithStatus:
    try:
        item = await update_logger(session, logger_id, payload)
    except LoggerNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logger not found") from e
    except LoggerConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    ingest = await get_ingest_state(item.stream_key)
    now = datetime.now(timezone.utc)
    active_window_sec = max(120, item.sample_interval_sec * 3 + 30)
    ingest_recent = (
        ingest.last_success_at is not None
        and (now - ingest.last_success_at).total_seconds() <= active_window_sec
    )
    stream_active = await check_nginx_stat_active(item.stream_key) or ingest_recent
    last = await get_last_measurement_for_logger(session, item.id)
    status_obj = LoggerStatus(
        stream_active=stream_active,
        ingest_last_attempt_at=ingest.last_attempt_at,
        ingest_last_success_at=ingest.last_success_at,
        ingest_last_error=ingest.last_error,
        last_measurement_at=last.captured_at if last else None,
        last_ok=last.ok if last else None,
        last_error=last.error if last else None,
    )
    base = LoggerOut.model_validate(item, from_attributes=True)
    return LoggerWithStatus(**base.model_dump(), status=status_obj)


@router.delete("/{logger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_logger(
    logger_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    try:
        await delete_logger(session, logger_id)
    except LoggerNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logger not found") from e

