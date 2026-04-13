from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("/public")
async def get_public_config() -> dict[str, str]:
    return {
        "rtmp_base_url": settings.rtmp_base_url.rstrip("/"),
    }
