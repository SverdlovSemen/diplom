from __future__ import annotations

import json
import math
import re
from typing import Any

import cv2
import numpy as np
import pytesseract

from app.cv.types import CVResult
from app.models.logger import GaugeType, Logger


def _parse_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _apply_roi(image: np.ndarray, roi_data: dict[str, Any]) -> np.ndarray:
    if not roi_data:
        return image
    x = int(roi_data.get("x", 0))
    y = int(roi_data.get("y", 0))
    w = int(roi_data.get("w", image.shape[1]))
    h = int(roi_data.get("h", image.shape[0]))
    x2 = max(x + w, x + 1)
    y2 = max(y + h, y + 1)
    return image[max(0, y) : min(image.shape[0], y2), max(0, x) : min(image.shape[1], x2)]


def _recognize_digital(image: np.ndarray) -> CVResult:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    # Для цифровых индикаторов OCR заметно стабильнее на увеличенном ROI.
    target_h = 160
    if h > 0 and h < target_h:
        scale = target_h / float(h)
        gray = cv2.resize(
            gray,
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            interpolation=cv2.INTER_CUBIC,
        )
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    variants: list[np.ndarray] = []
    _, th_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(th_otsu)
    variants.append(cv2.bitwise_not(th_otsu))

    th_adapt = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        3,
    )
    variants.append(th_adapt)
    variants.append(cv2.bitwise_not(th_adapt))

    # Тонкая морфология уменьшает шум (пыль/блики) и помогает Tesseract.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    variants.append(cv2.morphologyEx(th_otsu, cv2.MORPH_OPEN, kernel))
    variants.append(cv2.morphologyEx(cv2.bitwise_not(th_otsu), cv2.MORPH_OPEN, kernel))

    def _normalize_numeric_token(token: str) -> str | None:
        token = token.replace(",", ".").replace(" ", "")
        token = re.sub(r"[^0-9.\-]", "", token)
        if not token:
            return None
        if token.count("-") > 1:
            token = token.replace("-", "")
        if "-" in token and not token.startswith("-"):
            token = "-" + token.replace("-", "")
        if token.count(".") > 1:
            first = token.find(".")
            token = token[: first + 1] + token[first + 1 :].replace(".", "")
        if token in {"-", ".", "-."}:
            return None
        return token

    def _extract_numeric_token(text: str) -> str | None:
        m = re.search(r"-?\d+(?:[.,]\d+)?", text)
        if not m:
            return None
        return _normalize_numeric_token(m.group(0))

    psm_modes = (7, 8, 6, 13)
    raw_candidates: list[str] = []
    best: tuple[float, float, str, str] | None = None
    # tuple = (score, value, token, raw)

    for img in variants:
        for psm in psm_modes:
            config = (
                f"--oem 1 --psm {psm} "
                "-c tessedit_char_whitelist=0123456789.- "
                "-c classify_bln_numeric_mode=1"
            )
            raw = pytesseract.image_to_string(img, config=config).strip().replace(",", ".")
            raw_candidates.append(raw)
            token = _extract_numeric_token(raw)
            if token is None:
                continue
            try:
                value = float(token)
            except ValueError:
                continue

            score = 0.0
            # Предпочитаем токены с 2+ символами и десятичной частью (типичный счетчик).
            if len(token.replace("-", "")) >= 2:
                score += 1.2
            if "." in token:
                score += 0.8

            # Добавляем уверенность из image_to_data (если доступна).
            try:
                data = pytesseract.image_to_data(img, config=config, output_type=pytesseract.Output.DICT)
                conf_vals = [
                    float(c)
                    for c, t in zip(data.get("conf", []), data.get("text", []))
                    if str(c).strip() not in {"", "-1"} and str(t).strip()
                ]
                if conf_vals:
                    score += max(conf_vals) / 100.0
            except Exception:
                # image_to_data может падать на некоторых билдах tesseract; не роняем распознавание.
                pass

            if best is None or score > best[0]:
                best = (score, value, token, raw)

    if best is not None:
        return CVResult(value=best[1], ok=True, ocr_raw=best[3])

    joined = " | ".join(x for x in raw_candidates if x)
    return CVResult(value=None, ok=False, error=f"OCR failed: '{joined}'", ocr_raw=joined)


def _angle_from_center(center: tuple[float, float], point: tuple[float, float]) -> float:
    dx = point[0] - center[0]
    dy = center[1] - point[1]
    return math.degrees(math.atan2(dy, dx))


def _recognize_analog(image: np.ndarray, calibration_data: dict[str, Any]) -> CVResult:
    center_data = calibration_data.get("center")
    min_point_data = calibration_data.get("min_point")
    max_point_data = calibration_data.get("max_point")
    min_value = calibration_data.get("min_value")
    max_value = calibration_data.get("max_value")
    if not (center_data and min_point_data and max_point_data):
        return CVResult(value=None, ok=False, error="Calibration is required for analog gauge")
    if min_value is None or max_value is None:
        return CVResult(value=None, ok=False, error="Calibration min_value/max_value are required")

    center = (float(center_data["x"]), float(center_data["y"]))
    min_point = (float(min_point_data["x"]), float(min_point_data["y"]))
    max_point = (float(max_point_data["x"]), float(max_point_data["y"]))

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 80, 160)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=50, minLineLength=30, maxLineGap=12)
    if lines is None:
        return CVResult(value=None, ok=False, error="Needle detection failed")

    best_tip: tuple[float, float] | None = None
    best_dist = -1.0
    for item in lines:
        x1, y1, x2, y2 = item[0]
        p1 = (float(x1), float(y1))
        p2 = (float(x2), float(y2))
        d1 = (p1[0] - center[0]) ** 2 + (p1[1] - center[1]) ** 2
        d2 = (p2[0] - center[0]) ** 2 + (p2[1] - center[1]) ** 2
        near_center = min(d1, d2) < 45**2
        if not near_center:
            continue
        tip = p1 if d1 > d2 else p2
        dist = max(d1, d2)
        if dist > best_dist:
            best_tip = tip
            best_dist = dist

    if best_tip is None:
        return CVResult(value=None, ok=False, error="Needle near center not found")

    angle = _angle_from_center(center, best_tip)
    min_angle = _angle_from_center(center, min_point)
    max_angle = _angle_from_center(center, max_point)
    span = max_angle - min_angle
    if abs(span) < 1e-4:
        return CVResult(value=None, ok=False, error="Invalid calibration angle span")
    ratio = (angle - min_angle) / span
    ratio = min(1.0, max(0.0, ratio))
    value = float(min_value) + ratio * (float(max_value) - float(min_value))
    return CVResult(value=value, ok=True)


def recognize_from_image(image: np.ndarray, logger: Logger, *, roi_json_override: str | None = None) -> CVResult:
    roi_data = _parse_json(roi_json_override if roi_json_override is not None else logger.roi_json)
    calibration_data = _parse_json(logger.calibration_json)
    roi_image = _apply_roi(image, roi_data)
    if roi_image.size == 0:
        return CVResult(value=None, ok=False, error="ROI produced empty image")
    if logger.gauge_type == GaugeType.digital:
        return _recognize_digital(roi_image)
    return _recognize_analog(roi_image, calibration_data)

