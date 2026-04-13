from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.logger import Logger
from app.schemas.logger import LoggerBulkMonitoringUpdate, LoggerCreate, LoggerUpdate

# Не чаще одной записи «нет publisher» на логер (снижает шум в БД при давно неактивном потоке).
STREAM_GAP_THROTTLE_SEC = 30.0


class LoggerConflictError(Exception):
    pass


class LoggerNotFoundError(Exception):
    pass


async def list_loggers(session: AsyncSession, *, offset: int = 0, limit: int = 100) -> list[Logger]:
    result = await session.execute(select(Logger).offset(offset).limit(limit).order_by(Logger.created_at.desc()))
    return list(result.scalars().all())


async def get_logger(session: AsyncSession, logger_id: uuid.UUID) -> Logger:
    result = await session.execute(select(Logger).where(Logger.id == logger_id))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise LoggerNotFoundError()
    return obj


async def create_logger(session: AsyncSession, payload: LoggerCreate) -> Logger:
    obj = Logger(**payload.model_dump())
    session.add(obj)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise LoggerConflictError("stream_key must be unique") from e
    await session.refresh(obj)
    return obj


async def update_logger(session: AsyncSession, logger_id: uuid.UUID, payload: LoggerUpdate) -> Logger:
    obj = await get_logger(session, logger_id)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise LoggerConflictError("update violates constraints") from e
    await session.refresh(obj)
    return obj


async def delete_logger(session: AsyncSession, logger_id: uuid.UUID) -> None:
    obj = await get_logger(session, logger_id)
    await session.delete(obj)
    await session.commit()


async def bulk_update_monitoring(session: AsyncSession, payload: LoggerBulkMonitoringUpdate) -> int:
    data = payload.model_dump(exclude_unset=True)
    apply_to_disabled = bool(data.pop("apply_to_disabled", True))
    if not data:
        return 0
    stmt = select(Logger)
    if not apply_to_disabled:
        stmt = stmt.where(Logger.enabled.is_(True))
    items = list((await session.execute(stmt)).scalars().all())
    for obj in items:
        for k, v in data.items():
            setattr(obj, k, v)
    await session.commit()
    return len(items)


def stream_unavailable_persisted(item: Logger) -> bool:
    """По данным БД поток считается недоступным, если зафиксированный разрыв новее последнего успешного кадра."""
    if item.last_stream_gap_at is None:
        return False
    if item.last_stream_seen_at is None:
        return True
    return item.last_stream_gap_at > item.last_stream_seen_at


async def record_stream_success(session: AsyncSession, logger_id: uuid.UUID) -> None:
    """Успешный кадр с потока (измерение, snapshot или ручной capture)."""
    obj = await get_logger(session, logger_id)
    now = datetime.now(timezone.utc)
    obj.last_stream_seen_at = now
    obj.last_ingest_error = None
    await session.commit()


async def record_stream_gap(
    session: AsyncSession,
    logger_id: uuid.UUID,
    error: str,
    *,
    throttle_sec: float | None = None,
    last_gap_at: datetime | None = None,
    last_recorded_error: str | None = None,
) -> bool:
    """Фиксирует отсутствие потока или ошибку захвата. Возвращает True, если строка в БД обновлена.

    При throttle: не пишем повторно, если та же ошибка и last_gap_at не старше throttle_sec.
    """
    err = (error or "")[:512]
    now = datetime.now(timezone.utc)
    if (
        throttle_sec is not None
        and throttle_sec > 0
        and last_gap_at is not None
        and (now - last_gap_at).total_seconds() < throttle_sec
        and last_recorded_error is not None
        and err == (last_recorded_error or "")[:512]
    ):
        return False

    obj = await get_logger(session, logger_id)
    obj.last_stream_gap_at = now
    obj.last_ingest_error = err or None
    await session.commit()
    return True

