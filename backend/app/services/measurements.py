from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.logger import Logger
from app.models.measurement import Measurement


def _measurement_where_clause(
    *,
    logger_id: uuid.UUID | None = None,
    captured_from: datetime | None = None,
    captured_to: datetime | None = None,
):
    conditions = []
    if logger_id is not None:
        conditions.append(Measurement.logger_id == logger_id)
    if captured_from is not None:
        conditions.append(Measurement.captured_at >= captured_from)
    if captured_to is not None:
        conditions.append(Measurement.captured_at <= captured_to)
    return and_(*conditions) if conditions else None


@dataclass(frozen=True, slots=True)
class MeasurementStatsRow:
    count: int
    value_count: int
    value_min: float | None
    value_max: float | None
    value_avg: float | None
    recognition_fail_count: int
    out_of_range_count: int
    cv_warnings_count: int


def _float_or_none(v: float | Decimal | None) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


async def aggregate_measurements(
    session: AsyncSession,
    *,
    logger_id: uuid.UUID | None = None,
    captured_from: datetime | None = None,
    captured_to: datetime | None = None,
) -> MeasurementStatsRow:
    where_clause = _measurement_where_clause(
        logger_id=logger_id,
        captured_from=captured_from,
        captured_to=captured_to,
    )

    cv_warnings_nonempty = and_(
        Measurement.cv_warnings_json.is_not(None),
        Measurement.cv_warnings_json != "",
        Measurement.cv_warnings_json != "[]",
    )

    stmt = (
        select(
            func.count().label("n_total"),
            func.count().filter(Measurement.value.is_not(None)).label("n_value"),
            func.min(Measurement.value).label("v_min"),
            func.max(Measurement.value).label("v_max"),
            func.avg(Measurement.value).label("v_avg"),
            func.count().filter(Measurement.ok.is_(False)).label("n_fail"),
            func.count().filter(Measurement.out_of_range.is_(True)).label("n_oof"),
            func.count().filter(cv_warnings_nonempty).label("n_cv_warn"),
        ).select_from(Measurement)
    )
    if where_clause is not None:
        stmt = stmt.where(where_clause)

    row = (await session.execute(stmt)).one()
    return MeasurementStatsRow(
        count=int(row.n_total or 0),
        value_count=int(row.n_value or 0),
        value_min=_float_or_none(row.v_min),
        value_max=_float_or_none(row.v_max),
        value_avg=_float_or_none(row.v_avg),
        recognition_fail_count=int(row.n_fail or 0),
        out_of_range_count=int(row.n_oof or 0),
        cv_warnings_count=int(row.n_cv_warn or 0),
    )


async def list_measurements(
    session: AsyncSession,
    *,
    logger_id: uuid.UUID | None = None,
    captured_from: datetime | None = None,
    captured_to: datetime | None = None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[Measurement], int]:
    where_clause = _measurement_where_clause(
        logger_id=logger_id,
        captured_from=captured_from,
        captured_to=captured_to,
    )

    count_stmt = select(func.count()).select_from(Measurement)
    if where_clause is not None:
        count_stmt = count_stmt.where(where_clause)
    total = int((await session.execute(count_stmt)).scalar_one())

    list_stmt = select(Measurement).order_by(desc(Measurement.captured_at)).offset(offset).limit(limit)
    if where_clause is not None:
        list_stmt = list_stmt.where(where_clause)
    result = await session.execute(list_stmt)
    return list(result.scalars().all()), total


async def get_last_measurement_for_logger(
    session: AsyncSession,
    logger_id: uuid.UUID,
) -> Measurement | None:
    result = await session.execute(
        select(Measurement).where(Measurement.logger_id == logger_id).order_by(desc(Measurement.captured_at)).limit(1)
    )
    return result.scalar_one_or_none()


async def list_measurements_for_export(
    session: AsyncSession,
    *,
    logger_id: uuid.UUID | None = None,
    captured_from: datetime | None = None,
    captured_to: datetime | None = None,
    max_rows: int = 100_000,
) -> list[tuple[Measurement, str]]:
    """Пары (измерение, имя логера) по возрастанию captured_at, для CSV."""
    where_clause = _measurement_where_clause(
        logger_id=logger_id,
        captured_from=captured_from,
        captured_to=captured_to,
    )
    stmt = (
        select(Measurement, Logger.name)
        .join(Logger, Measurement.logger_id == Logger.id)
        .order_by(Measurement.captured_at.asc())
        .limit(max_rows)
    )
    if where_clause is not None:
        stmt = stmt.where(where_clause)
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def list_out_of_range_alerts(
    session: AsyncSession,
    *,
    logger_id: uuid.UUID | None = None,
    captured_from: datetime | None = None,
    captured_to: datetime | None = None,
    limit: int = 100,
) -> list[tuple[Measurement, str]]:
    where_clause = _measurement_where_clause(
        logger_id=logger_id,
        captured_from=captured_from,
        captured_to=captured_to,
    )
    stmt = (
        select(Measurement, Logger.name)
        .join(Logger, Measurement.logger_id == Logger.id)
        .where(Measurement.out_of_range.is_(True))
        .order_by(desc(Measurement.captured_at))
        .limit(limit)
    )
    if where_clause is not None:
        stmt = stmt.where(where_clause)
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]

