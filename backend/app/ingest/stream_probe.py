from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import cv2

logger = logging.getLogger("app.ingest.probe")


@dataclass(frozen=True)
class ProbeResult:
    active: bool
    error: str | None = None


_cache: dict[str, tuple[float, ProbeResult]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def probe_stream(stream_url: str, *, timeout_sec: float = 1.5, cache_ttl_sec: float = 2.0) -> ProbeResult:
    """
    Быстрая проверка: можно ли прочитать 1 кадр из RTMP.
    Это надёжнее, чем nginx /stat в окружениях, где stat не показывает publisher.
    """
    now = time.monotonic()
    cached = _cache.get(stream_url)
    if cached is not None and now < cached[0]:
        return cached[1]

    lock = _lock_for(stream_url)
    async with lock:
        # двойная проверка после ожидания lock
        now = time.monotonic()
        cached = _cache.get(stream_url)
        if cached is not None and now < cached[0]:
            return cached[1]

        def _probe() -> ProbeResult:
            cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)
            try:
                if not cap.isOpened():
                    return ProbeResult(active=False, error="open_failed")
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout_sec * 1000))
                ret, frame = cap.read()
                if not ret or frame is None:
                    return ProbeResult(active=False, error="read_failed")
                return ProbeResult(active=True)
            finally:
                cap.release()

        loop = asyncio.get_event_loop()
        try:
            result: ProbeResult = await asyncio.wait_for(loop.run_in_executor(None, _probe), timeout=timeout_sec + 0.5)
        except Exception as e:
            logger.debug("Probe failed for %s: %s", stream_url, e)
            result = ProbeResult(active=False, error=str(e))

        _cache[stream_url] = (time.monotonic() + cache_ttl_sec, result)
        return result

