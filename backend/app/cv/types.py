from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CVResult:
    value: float | None
    ok: bool
    error: str | None = None
    ocr_raw: str | None = None

