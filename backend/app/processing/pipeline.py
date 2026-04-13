from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
import xml.etree.ElementTree as ET
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass

import cv2
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.cv.recognizer import recognize_from_image
from app.cv.types import CVResult
from app.models.logger import Logger
from app.models.measurement import Measurement
from app.cv.config_readiness import logger_ready_for_automated_recognition
from app.services.loggers import (
    STREAM_GAP_THROTTLE_SEC,
    get_logger,
    list_loggers,
    record_stream_gap,
)

# Повторная запись «конфиг не готов» в БД не чаще, чем раз в минуту при неизменной ошибке.
CONFIG_INCOMPLETE_GAP_THROTTLE_SEC = 60.0
from app.services.measurements import get_last_measurement_for_logger

logger = logging.getLogger("app.processing")
_last_retention_cleanup_at: datetime | None = None
_RETENTION_CLEANUP_PERIOD_SEC = 300
_RETENTION_BATCH_SIZE = 500


def _out_of_range_for_logger(target: Logger, value: float | None) -> bool | None:
    """None — границы в логере не заданы или значения нет; True — вне [min,max]; False — внутри."""
    if value is None:
        return None
    lo, hi = target.min_value, target.max_value
    if lo is None or hi is None:
        return None
    v = float(value)
    a, b = (float(lo), float(hi)) if lo <= hi else (float(hi), float(lo))
    return not (a <= v <= b)


def _cv_warnings_json(cv_result: CVResult) -> str | None:
    if cv_result.warnings:
        return json.dumps(cv_result.warnings)
    return None


# Повторы и fallback нужны: live RTMP из nginx-rtmp часто даёт ffmpeg «Input/output error» на первом коннекте.
_FFMPEG_CAPTURE_RETRIES = 2
_FFMPEG_RETRY_DELAY_SEC = 0.5
# Иначе communicate() висит до rw_timeout ffmpeg × N попыток — UI «Загрузка снимка» на минуты.
_FFMPEG_SUBPROCESS_TIMEOUT_SEC = 14.0


@dataclass
class IngestState:
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None


_ingest_state: dict[str, IngestState] = {}
_ingest_lock = asyncio.Lock()


async def get_ingest_state(stream_key: str) -> IngestState:
    async with _ingest_lock:
        state = _ingest_state.get(stream_key)
        if state is None:
            state = IngestState()
            _ingest_state[stream_key] = state
        # возвращаем копию, чтобы читатель не зависел от мутаций
        return IngestState(
            last_attempt_at=state.last_attempt_at,
            last_success_at=state.last_success_at,
            last_error=state.last_error,
        )


async def _mark_ingest_attempt(stream_key: str, when: datetime) -> None:
    async with _ingest_lock:
        state = _ingest_state.get(stream_key)
        if state is None:
            state = IngestState()
            _ingest_state[stream_key] = state
        state.last_attempt_at = when


_nginx_stat_cache: tuple[float, set[str]] | None = None
_NGINX_STAT_CACHE_TTL_SEC = 5.0


async def get_nginx_active_streams() -> set[str]:
    """Возвращает множество stream_key активных publisher'ов из nginx RTMP /stat XML.

    Кэшируется на _NGINX_STAT_CACHE_TTL_SEC секунд — background task вызывает эту функцию
    каждую секунду, без кэша получался бы 1 HTTP-запрос/сек к nginx.
    При любой ошибке возвращает пустое множество (fail-open: статус будет по _ingest_state).
    """
    global _nginx_stat_cache
    import time

    now = time.monotonic()
    if _nginx_stat_cache is not None:
        cached_at, cached_streams = _nginx_stat_cache
        if now - cached_at < _NGINX_STAT_CACHE_TTL_SEC:
            return cached_streams
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(settings.rtmp_stat_url)
        if resp.status_code != 200:
            _nginx_stat_cache = (now, set())
            return set()
        root = ET.fromstring(resp.text)
        active: set[str] = set()
        for stream_el in root.findall(".//stream"):
            name_el = stream_el.find("name")
            # Тег <active/> присутствует только у активных publisher'ов (не у play-only клиентов)
            if name_el is not None and name_el.text and stream_el.find("active") is not None:
                active.add(name_el.text)
        _nginx_stat_cache = (now, active)
        return active
    except Exception:
        _nginx_stat_cache = (now, set())
        return set()


async def check_nginx_stat_active(stream_key: str) -> bool:
    """Проверяет nginx RTMP /stat для одного stream_key (используется в get/patch endpoint)."""
    return stream_key in await get_nginx_active_streams()


async def record_ingest_success_now(stream_key: str) -> None:
    """Вызов из snapshot API: UI «ingest active» совпадает с тем, что реально тянется с RTMP."""
    await _mark_ingest_success(stream_key, datetime.now(timezone.utc))


async def _mark_ingest_success(stream_key: str, when: datetime) -> None:
    async with _ingest_lock:
        state = _ingest_state.get(stream_key)
        if state is None:
            state = IngestState()
            _ingest_state[stream_key] = state
        state.last_success_at = when
        state.last_error = None


async def _mark_ingest_error(stream_key: str, when: datetime, error: str) -> None:
    async with _ingest_lock:
        state = _ingest_state.get(stream_key)
        if state is None:
            state = IngestState()
            _ingest_state[stream_key] = state
        state.last_attempt_at = when
        state.last_error = error[:500]


def _ffmpeg_capture_cmd(stream_url: str, out_file: Path) -> list[str]:
    # Минимальный набор: aggressive low_delay/nobuffer ломает play с nginx-rtmp (Input/output error).
    return [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-rw_timeout",
        "8000000",
        "-i",
        stream_url,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_file),
        "-loglevel",
        "error",
        "-nostdin",
    ]


async def _capture_frame_ffmpeg_once(stream_url: str, out_file: Path) -> str | None:
    """Один проход ffmpeg. Возвращает stderr при ошибке, иначе None."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = _ffmpeg_capture_cmd(stream_url, out_file)
    logger.info("ffmpeg capture: %s", " ".join(cmd))
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=_FFMPEG_SUBPROCESS_TIMEOUT_SEC)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=3.0)
        return f"ffmpeg capture timed out after {_FFMPEG_SUBPROCESS_TIMEOUT_SEC:.0f}s"
    if process.returncode != 0:
        err = stderr.decode("utf-8", errors="ignore").strip()
        logger.warning("ffmpeg capture failed (rc=%s): %s", process.returncode, err or "<empty stderr>")
        return err or "ffmpeg capture failed"
    return None


async def _capture_frame_opencv_file(stream_url: str, out_file: Path, timeout: float = 12.0) -> None:
    """Запасной путь: тот же механизм, что snapshot — OpenCV + FFmpeg внутри."""

    def _run() -> None:
        cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            raise RuntimeError(f"OpenCV failed to open stream: {stream_url}")
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout * 1000))
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            raise RuntimeError(f"OpenCV failed to read frame: {stream_url}")
        out_file.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(out_file), frame, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            raise RuntimeError("OpenCV imwrite failed")

    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=timeout + 8.0)
    except TimeoutError as e:
        raise RuntimeError(f"OpenCV capture timed out: {stream_url}") from e


async def capture_frame(stream_url: str, out_file: Path) -> None:
    last_err: str | None = None
    for attempt in range(1, _FFMPEG_CAPTURE_RETRIES + 1):
        last_err = await _capture_frame_ffmpeg_once(stream_url, out_file)
        if last_err is None:
            if attempt > 1:
                logger.info("ffmpeg capture succeeded on attempt %s", attempt)
            return
        if attempt < _FFMPEG_CAPTURE_RETRIES:
            await asyncio.sleep(_FFMPEG_RETRY_DELAY_SEC)

    logger.warning("ffmpeg capture exhausted retries; trying OpenCV fallback for %s", stream_url)
    try:
        await _capture_frame_opencv_file(stream_url, out_file)
    except Exception as e:
        raise RuntimeError(
            f"RTMP capture failed (ffmpeg after {_FFMPEG_CAPTURE_RETRIES} tries, then OpenCV): "
            f"{last_err!r}; OpenCV: {e}"
        ) from e
    logger.info("Frame captured via OpenCV fallback at %s", out_file)


async def capture_frame_to_memory(stream_url: str, timeout: float = 42.0) -> bytes:
    """Тот же путь захвата, что и в пайплайне (ffmpeg + повторы, затем OpenCV). Snapshot/Test rely on this."""
    fd, name = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    path = Path(name)
    try:
        await asyncio.wait_for(capture_frame(stream_url, path), timeout=timeout)
        data = path.read_bytes()
        if not data:
            raise RuntimeError("Empty captured frame")
        return data
    finally:
        path.unlink(missing_ok=True)


async def process_logger_once(session: AsyncSession, logger_id: uuid.UUID) -> Measurement:
    target = await get_logger(session, logger_id)
    ready, reason = logger_ready_for_automated_recognition(target)
    if not ready:
        raise RuntimeError(f"configuration_incomplete:{reason}")
    stream_url = f"{settings.rtmp_base_url.rstrip('/')}/{target.stream_key}"
    logger.info("Processing started", extra={"logger_id": str(target.id), "stream_key": target.stream_key})
    now = datetime.now(timezone.utc)
    await _mark_ingest_attempt(target.stream_key, now)
    date_dir = now.strftime("%Y/%m/%d")
    filename = f"{target.id}_{now.strftime('%H%M%S_%f')}.jpg"
    relative = Path("measurements") / date_dir / filename
    output_path = Path(settings.storage_dir) / relative
    try:
        await asyncio.wait_for(capture_frame(stream_url, output_path), timeout=55.0)
    except Exception as e:
        await _mark_ingest_error(target.stream_key, now, str(e))
        raise
    await _mark_ingest_success(target.stream_key, now)
    logger.info("RTMP stream received for %s", target.stream_key)
    logger.info("Frame captured at %s", output_path)

    image = cv2.imread(str(output_path))
    if image is None:
        raise RuntimeError("Failed to read captured frame")
    cv_result = recognize_from_image(image, target)

    measurement = Measurement(
        logger_id=target.id,
        value=cv_result.value,
        unit=target.unit,
        ok=cv_result.ok,
        error=cv_result.error,
        out_of_range=_out_of_range_for_logger(target, cv_result.value),
        cv_warnings_json=_cv_warnings_json(cv_result),
        image_path=str(relative).replace("\\", "/"),
        captured_at=now,
    )
    session.add(measurement)
    target.last_stream_seen_at = now
    target.last_ingest_error = None
    await session.commit()
    await session.refresh(measurement)
    logger.info(
        "Processing completed",
        extra={
            "logger_id": str(target.id),
            "ok": measurement.ok,
            "value": measurement.value,
            "out_of_range": measurement.out_of_range,
        },
    )
    return measurement


async def process_due_loggers(session: AsyncSession) -> int:
    global _last_retention_cleanup_at
    now = datetime.now(timezone.utc)
    if _last_retention_cleanup_at is None or (now - _last_retention_cleanup_at).total_seconds() >= _RETENTION_CLEANUP_PERIOD_SEC:
        await cleanup_expired_images(session, now=now)
        _last_retention_cleanup_at = now

    items = await list_loggers(session, offset=0, limit=1000)
    # Один запрос к nginx /stat для всех логгеров — пропускаем те, у кого нет publisher'а.
    # Без этого каждый неактивный логгер тратит 8s+8s+20s = ~36s на ffmpeg+OpenCV timeout.
    nginx_active = await get_nginx_active_streams()
    processed = 0
    for item in items:
        if not item.enabled:
            continue
        if not _logger_schedule_allows_capture(item, now):
            continue
        last = await get_last_measurement_for_logger(session, item.id)
        if last is not None and last.captured_at is not None:
            if datetime.now(timezone.utc) - last.captured_at < timedelta(seconds=item.sample_interval_sec):
                continue
        if item.stream_key not in nginx_active:
            logger.debug("Skipping %s: no active publisher in nginx stat", item.stream_key)
            await record_stream_gap(
                session,
                item.id,
                "no_active_publisher",
                throttle_sec=STREAM_GAP_THROTTLE_SEC,
                last_gap_at=item.last_stream_gap_at,
                last_recorded_error=item.last_ingest_error,
            )
            continue
        ready, reason = logger_ready_for_automated_recognition(item)
        if not ready:
            logger.debug("Skipping %s: configuration not ready (%s)", item.stream_key, reason)
            await record_stream_gap(
                session,
                item.id,
                f"configuration_incomplete:{reason}",
                throttle_sec=CONFIG_INCOMPLETE_GAP_THROTTLE_SEC,
                last_gap_at=item.last_stream_gap_at,
                last_recorded_error=item.last_ingest_error,
            )
            continue
        try:
            await process_logger_once(session, item.id)
            processed += 1
        except Exception as e:
            await record_stream_gap(
                session,
                item.id,
                str(e)[:500],
                throttle_sec=STREAM_GAP_THROTTLE_SEC,
                last_gap_at=item.last_stream_gap_at,
                last_recorded_error=item.last_ingest_error,
            )
            logger.exception("Processing failed: %s", e, extra={"logger_id": str(item.id)})
    return processed


def _logger_schedule_allows_capture(item: Logger, now: datetime) -> bool:
    if getattr(item, "capture_mode", "continuous") == "continuous":
        return True
    start = item.schedule_start_hour_utc
    end = item.schedule_end_hour_utc
    if start is None or end is None:
        return False
    h = now.astimezone(timezone.utc).hour
    if start == end:
        return True
    if start < end:
        return start <= h < end
    return h >= start or h < end


async def cleanup_expired_images(session: AsyncSession, *, now: datetime) -> int:
    stmt = (
        select(Measurement, Logger.image_retention_days)
        .join(Logger, Measurement.logger_id == Logger.id)
        .where(Measurement.image_path.is_not(None), Logger.image_retention_days.is_not(None))
        .order_by(Measurement.captured_at.asc())
        .limit(_RETENTION_BATCH_SIZE)
    )
    rows = (await session.execute(stmt)).all()
    removed = 0
    for measurement, retention_days in rows:
        if retention_days is None or retention_days <= 0:
            continue
        if measurement.captured_at is None:
            continue
        cutoff = measurement.captured_at + timedelta(days=int(retention_days))
        if now < cutoff:
            continue
        rel = measurement.image_path
        if rel:
            path = Path(settings.storage_dir) / rel
            path.unlink(missing_ok=True)
        measurement.image_path = None
        removed += 1
    if removed > 0:
        await session.commit()
        logger.info("Retention cleanup removed images: %s", removed)
    return removed

