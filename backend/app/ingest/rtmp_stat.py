from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET

import httpx

from app.core.config import settings

logger = logging.getLogger("app.ingest.rtmp")

# Кэш нужен, чтобы не дергать nginx /stat на каждый запрос UI.
_cache_expires_at: float = 0.0
_cache_streams: set[str] = set()


async def get_active_stream_keys(*, cache_ttl_sec: float = 2.0) -> set[str]:
    global _cache_expires_at, _cache_streams
    now = time.monotonic()
    if now < _cache_expires_at:
        return set(_cache_streams)

    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(settings.rtmp_stat_url)
            resp.raise_for_status()
        root = ET.fromstring(resp.text)
        active: set[str] = set()
        for stream in root.findall(".//application[name='live']/live/stream"):
            name = stream.findtext("name")
            publishing = stream.findtext("publishing")
            if name and publishing == "1":
                active.add(name)
        _cache_streams = set(active)
        _cache_expires_at = now + cache_ttl_sec
        return active
    except Exception as e:
        logger.warning("Failed to fetch RTMP stat: %s", e)
        _cache_streams = set()
        _cache_expires_at = now + min(cache_ttl_sec, 1.0)
        return set()

