from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.models.measurement import Measurement
from app.schemas.measurement import MeasurementAlertOut, MeasurementListOut, MeasurementOut, MeasurementStatsOut
from app.services.measurements import (
    aggregate_measurements,
    list_measurements,
    list_measurements_for_export,
    list_out_of_range_alerts,
)

router = APIRouter()


def _normalize_query_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_measurement_period(
    logger_id: uuid.UUID | None,
    from_: datetime | None,
    to: datetime | None,
) -> tuple[uuid.UUID | None, datetime | None, datetime | None]:
    captured_from = _normalize_query_datetime(from_) if from_ is not None else None
    captured_to = _normalize_query_datetime(to) if to is not None else None
    if captured_from is not None and captured_to is not None and captured_from > captured_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Parameter 'from' must be less than or equal to 'to'.",
        )
    return logger_id, captured_from, captured_to


def _media_url_or_path(public_base: str, image_path: str | None) -> str:
    if not image_path:
        return ""
    rel = f"/media/{image_path.lstrip('/')}"
    base = public_base.rstrip("/")
    if base:
        return f"{base}{rel}"
    return rel


def _fmt_optional_bool(v: bool | None) -> str:
    if v is None:
        return ""
    return "true" if v else "false"


def _export_csv_row(m: Measurement, logger_name: str, public_base: str) -> list[str]:
    val = "" if m.value is None else str(m.value)
    return [
        m.captured_at.isoformat(),
        str(m.logger_id),
        logger_name,
        val,
        m.unit,
        "true" if m.ok else "false",
        _fmt_optional_bool(m.out_of_range),
        _media_url_or_path(public_base, m.image_path),
        m.error or "",
    ]


@router.get(
    "/export.csv",
    summary="Экспорт измерений в CSV",
    description="Колонки: captured_at, logger_id, logger_name, value, unit, ok, out_of_range, image_url_or_path, error. "
    "Фильтры как у списка. APP_PUBLIC_BASE_URL — абсолютные ссылки на кадры; иначе относительный путь /media/….",
    response_class=StreamingResponse,
)
async def export_measurements_csv(
    logger_id: uuid.UUID | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from", description="Начало периода (captured_at), включительно (UTC)."),
    to: datetime | None = Query(default=None, description="Конец периода (captured_at), включительно (UTC)."),
    max_rows: int = Query(default=100_000, ge=1, le=500_000, description="Максимум строк в выгрузке."),
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    lid, captured_from, captured_to = _parse_measurement_period(logger_id, from_, to)
    rows = await list_measurements_for_export(
        session,
        logger_id=lid,
        captured_from=captured_from,
        captured_to=captured_to,
        max_rows=max_rows,
    )
    public_base = settings.public_base_url.strip()

    header = [
        "captured_at",
        "logger_id",
        "logger_name",
        "value",
        "unit",
        "ok",
        "out_of_range",
        "image_url_or_path",
        "error",
    ]

    def generate_chunks():
        buf = io.StringIO()
        writer = csv.writer(buf)
        yield "\ufeff".encode("utf-8")
        writer.writerow(header)
        yield buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate(0)
        for m, logger_name in rows:
            writer.writerow(_export_csv_row(m, logger_name, public_base))
            yield buf.getvalue().encode("utf-8")
            buf.seek(0)
            buf.truncate(0)

    headers = {"Content-Disposition": 'attachment; filename="measurements_export.csv"'}
    return StreamingResponse(
        generate_chunks(),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


@router.get(
    "/alerts",
    response_model=list[MeasurementAlertOut],
    summary="Список алертов по аномалиям",
    description="In-app алерты по измерениям с out_of_range=true (новые сверху).",
)
async def api_measurement_alerts(
    logger_id: uuid.UUID | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from", description="Начало периода (captured_at), включительно (UTC)."),
    to: datetime | None = Query(default=None, description="Конец периода (captured_at), включительно (UTC)."),
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> list[MeasurementAlertOut]:
    lid, captured_from, captured_to = _parse_measurement_period(logger_id, from_, to)
    rows = await list_out_of_range_alerts(
        session,
        logger_id=lid,
        captured_from=captured_from,
        captured_to=captured_to,
        limit=limit,
    )
    return [
        MeasurementAlertOut(
            measurement_id=m.id,
            logger_id=m.logger_id,
            logger_name=logger_name,
            captured_at=m.captured_at,
            value=m.value,
            unit=m.unit,
            error=m.error,
            image_path=m.image_path,
        )
        for m, logger_name in rows
    ]


@router.get(
    "/stats",
    response_model=MeasurementStatsOut,
    summary="Сводка по измерениям за период",
    description="Агрегаты за интервал [from, to] по логеру или по всем; min/max/avg по числовым значениям, неуспешное распознавание (ok=false), вне диапазона (out_of_range), предупреждения CV (cv_warnings_json).",
)
async def api_measurement_stats(
    logger_id: uuid.UUID | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from", description="Начало периода (captured_at), включительно (UTC)."),
    to: datetime | None = Query(default=None, description="Конец периода (captured_at), включительно (UTC)."),
    session: AsyncSession = Depends(get_db_session),
) -> MeasurementStatsOut:
    lid, captured_from, captured_to = _parse_measurement_period(logger_id, from_, to)
    row = await aggregate_measurements(
        session,
        logger_id=lid,
        captured_from=captured_from,
        captured_to=captured_to,
    )
    return MeasurementStatsOut(
        period_from=captured_from,
        period_to=captured_to,
        logger_id=lid,
        count=row.count,
        value_count=row.value_count,
        value_min=row.value_min,
        value_max=row.value_max,
        value_avg=row.value_avg,
        recognition_fail_count=row.recognition_fail_count,
        out_of_range_count=row.out_of_range_count,
        cv_warnings_count=row.cv_warnings_count,
    )


@router.get("/", response_model=MeasurementListOut)
async def api_list_measurements(
    logger_id: uuid.UUID | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from", description="Начало периода (captured_at), включительно (UTC)."),
    to: datetime | None = Query(default=None, description="Конец периода (captured_at), включительно (UTC)."),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> MeasurementListOut:
    lid, captured_from, captured_to = _parse_measurement_period(logger_id, from_, to)
    items, total = await list_measurements(
        session,
        logger_id=lid,
        captured_from=captured_from,
        captured_to=captured_to,
        offset=offset,
        limit=limit,
    )
    return MeasurementListOut(items=[MeasurementOut.model_validate(m) for m in items], total=total)

