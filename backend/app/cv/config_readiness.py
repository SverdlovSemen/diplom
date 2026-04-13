"""Проверка готовности логера к автоматическому распознаванию (тот же контракт, что и process_logger_once)."""

from __future__ import annotations

from app.cv.recognizer import _parse_json
from app.models.logger import GaugeType, Logger


def logger_ready_for_automated_recognition(logger: Logger) -> tuple[bool, str | None]:
    """False + код причины, если фоновый пайплайн не должен тратить захват на заведомо невалидную конфигурацию."""
    raw_roi = (logger.roi_json or "").strip()
    if not raw_roi:
        return False, "roi_json_not_configured"
    roi_data = _parse_json(logger.roi_json)
    w = int(roi_data.get("w", 0))
    h = int(roi_data.get("h", 0))
    if w < 5 or h < 5:
        return False, "roi_invalid_or_too_small"

    if logger.gauge_type == GaugeType.digital:
        return True, None

    cal = _parse_json(logger.calibration_json)
    if not cal.get("center") or not cal.get("min_point") or not cal.get("max_point"):
        return False, "analog_calibration_incomplete"
    if cal.get("min_value") is None or cal.get("max_value") is None:
        return False, "analog_scale_values_missing"
    return True, None
