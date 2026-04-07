from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.logger import Logger
from app.schemas.logger import LoggerCreate, LoggerUpdate


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

