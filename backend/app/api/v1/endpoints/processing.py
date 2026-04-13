from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import uuid
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.cv.recognizer import (
    _calibration_to_roi_coords,
    _roi_origin,
    analog_debug_from_image,
    recognize_from_image,
)
from app.models.logger import GaugeType
from app.db.session import get_db_session
from app.processing.pipeline import (
    capture_frame_to_memory,
    check_nginx_stat_active,
    process_due_loggers,
    process_logger_once,
    record_ingest_success_now,
)
from app.schemas.measurement import MeasurementOut
from app.cv.config_readiness import logger_ready_for_automated_recognition
from app.services.loggers import get_logger, record_stream_gap, record_stream_success

router = APIRouter()
logger = logging.getLogger(__name__)

_STREAM_UNAVAILABLE_MSG = "Нет активного потока. Запустите трансляцию."


class TestRecognizeRequest(BaseModel):
    """Тело POST /test-recognize: опционально тот же JPEG, что на экране (snapshot), и/или ROI до сохранения в БД."""

    production_parity: bool = Field(
        default=False,
        description=(
            "Режим согласованности с боевым пайплайном: один кадр RTMP и recognize_from_image(image, logger) "
            "без подмены roi_json/calibration_json из тела запроса (как process_logger_once). "
            "Игнорирует frame_jpeg_base64 и поля подмены. Требует сохранённой валидной конфигурации в БД."
        ),
    )
    frame_jpeg_base64: str | None = Field(
        default=None,
        description="JPEG из последнего снимка в UI (base64). Если задан — распознавание по этому кадру, без повторного RTMP-захвата.",
    )
    roi_json: str | None = Field(
        default=None,
        description="Подмена roi_json для этого запроса (совпадает с рамкой в UI, в т.ч. до Save config).",
    )
    calibration_json: str | None = Field(
        default=None,
        description="Подмена calibration_json (center/min/max и шкала) — должна совпадать с UI; иначе берётся из БД.",
    )


def _decode_optional_jpeg_base64(data: str) -> bytes:
    s = data.strip()
    if "," in s and s.lower().startswith("data:"):
        s = s.split(",", 1)[1]
    try:
        raw = base64.b64decode(s, validate=False)
    except binascii.Error as e:
        raise ValueError(f"Invalid base64: {e}") from e
    _validate_jpeg_bytes(raw)
    return raw


def _validate_jpeg_bytes(data: bytes) -> None:
    if len(data) < 256:
        raise RuntimeError("Captured image too small or empty")
    if data[:2] != b"\xff\xd8":
        raise RuntimeError("Invalid JPEG from capture (not a real frame)")


def _is_stream_unavailable(exc: BaseException) -> bool:
    msg = str(exc).lower()
    needles = (
        "failed to open stream",
        "opencv failed to open stream",
        "opencv failed to read frame",
        "failed to read frame from stream",
        "rtmp capture failed",
        "empty captured frame",
        "input/output error",
        "ffmpeg capture timed out",
        "opencv capture timed out",
        "invalid jpeg",
        "too small or empty",
    )
    return isinstance(exc, TimeoutError) or any(n in msg for n in needles)


@router.post("/loggers/{logger_id}/capture", response_model=MeasurementOut)
async def api_capture_logger_once(
    logger_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> MeasurementOut:
    try:
        return await process_logger_once(session, logger_id)
    except Exception as e:
        await session.rollback()
        try:
            await record_stream_gap(session, logger_id, str(e))
        except Exception:
            logger.exception("record_stream_gap after capture failure")
        msg = str(e)
        if msg.startswith("configuration_incomplete:"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg) from e
        if _is_stream_unavailable(e):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_STREAM_UNAVAILABLE_MSG) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get("/loggers/{logger_id}/snapshot")
async def api_logger_snapshot(
    logger_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    try:
        target = await get_logger(session, logger_id)
        # Быстрая проверка nginx stat (3s timeout) перед тяжёлым ffmpeg/OpenCV захватом.
        # Без неё snapshot висит 36s на неактивном потоке, блокируя UI.
        stream_active = await check_nginx_stat_active(target.stream_key)
        if not stream_active:
            await record_stream_gap(session, target.id, "no_active_publisher")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_STREAM_UNAVAILABLE_MSG,
            )
        stream_url = f"{settings.rtmp_base_url.rstrip('/')}/{target.stream_key}"
        jpeg_bytes = await asyncio.wait_for(capture_frame_to_memory(stream_url), timeout=48.0)
        _validate_jpeg_bytes(jpeg_bytes)
        await record_ingest_success_now(target.stream_key)
        await record_stream_success(session, target.id)
        return Response(content=jpeg_bytes, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        if _is_stream_unavailable(e):
            try:
                await record_stream_gap(session, logger_id, str(e))
            except Exception:
                logger.exception("record_stream_gap after snapshot failure")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_STREAM_UNAVAILABLE_MSG) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/loggers/{logger_id}/test-recognize")
async def api_test_recognize(
    logger_id: uuid.UUID,
    body: TestRecognizeRequest = TestRecognizeRequest(),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        target = await get_logger(session, logger_id)
        frame_source: str

        if body.production_parity:
            ready, reason = logger_ready_for_automated_recognition(target)
            if not ready:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"configuration_incomplete:{reason}",
                )
            stream_active = await check_nginx_stat_active(target.stream_key)
            if not stream_active:
                await record_stream_gap(session, target.id, "no_active_publisher")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=_STREAM_UNAVAILABLE_MSG,
                )
            stream_url = f"{settings.rtmp_base_url.rstrip('/')}/{target.stream_key}"
            jpeg_bytes = await asyncio.wait_for(capture_frame_to_memory(stream_url), timeout=48.0)
            _validate_jpeg_bytes(jpeg_bytes)
            await record_ingest_success_now(target.stream_key)
            await record_stream_success(session, target.id)
            frame_source = "rtmp_production_parity"
        elif body.frame_jpeg_base64:
            try:
                jpeg_bytes = _decode_optional_jpeg_base64(body.frame_jpeg_base64)
            except (ValueError, RuntimeError) as e:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
            frame_source = "client_jpeg"
        else:
            stream_active = await check_nginx_stat_active(target.stream_key)
            if not stream_active:
                await record_stream_gap(session, target.id, "no_active_publisher")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=_STREAM_UNAVAILABLE_MSG,
                )
            stream_url = f"{settings.rtmp_base_url.rstrip('/')}/{target.stream_key}"
            jpeg_bytes = await asyncio.wait_for(capture_frame_to_memory(stream_url), timeout=48.0)
            _validate_jpeg_bytes(jpeg_bytes)
            await record_ingest_success_now(target.stream_key)
            await record_stream_success(session, target.id)
            frame_source = "rtmp_capture"

        nparr = np.frombuffer(jpeg_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("Failed to decode captured frame")

        cal_override = (
            body.calibration_json.strip()
            if body.calibration_json is not None and body.calibration_json.strip()
            else None
        )
        if body.production_parity:
            cv_result = recognize_from_image(image, target)
        else:
            cv_result = recognize_from_image(
                image,
                target,
                roi_json_override=body.roi_json,
                calibration_json_override=cal_override,
            )

        # Для интерактивной настройки ROI возвращаем вырезанный ROI всегда,
        # даже если распознавание не удалось.
        roi_b64: str | None = None
        from app.cv.recognizer import _apply_roi, _parse_json

        roi_for_preview = target.roi_json if body.production_parity else (body.roi_json if body.roi_json is not None else target.roi_json)
        roi_data = _parse_json(roi_for_preview)
        roi_image = _apply_roi(image, roi_data)
        if roi_image.size > 0:
            _, buf = cv2.imencode(".jpg", roi_image)
            roi_b64 = base64.b64encode(buf.tobytes()).decode("ascii")

        if cv_result.ocr_raw is not None:
            logger.info(
                "test-recognize digital OCR",
                extra={
                    "logger_id": str(logger_id),
                    "frame_source": frame_source,
                    "ocr_raw": cv_result.ocr_raw,
                    "ok": cv_result.ok,
                },
            )

        analog_debug: dict[str, Any] | None = None
        if target.gauge_type == GaugeType.analog:
            if body.production_parity:
                calibration_data = _parse_json(target.calibration_json)
            else:
                calibration_data = _parse_json(cal_override if cal_override is not None else target.calibration_json)
            rx, ry = _roi_origin(roi_data)
            cal_roi = _calibration_to_roi_coords(calibration_data, rx, ry)
            analog_debug = analog_debug_from_image(roi_image, cal_roi)

        return {
            "value": cv_result.value,
            "ok": cv_result.ok,
            "error": cv_result.error,
            "roi_image": roi_b64,
            "ocr_raw": cv_result.ocr_raw,
            "frame_source": frame_source,
            "analog_debug": analog_debug,
            "cv_warnings": cv_result.warnings,
        }
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        if _is_stream_unavailable(e):
            try:
                await record_stream_gap(session, logger_id, str(e))
            except Exception:
                logger.exception("record_stream_gap after test-recognize failure")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_STREAM_UNAVAILABLE_MSG) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/run-due")
async def api_run_due(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, int]:
    count = await process_due_loggers(session)
    return {"processed": count}

