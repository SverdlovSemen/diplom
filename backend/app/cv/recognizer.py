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


def _roi_origin(roi_data: dict[str, Any]) -> tuple[int, int]:
    """Левый верхний угол ROI в координатах полного кадра (как в _apply_roi)."""
    if not roi_data:
        return 0, 0
    return int(roi_data.get("x", 0)), int(roi_data.get("y", 0))


def _calibration_to_roi_coords(calibration_data: dict[str, Any], roi_x: int, roi_y: int) -> dict[str, Any]:
    """Переводит center/min_point/max_point из координат полного кадра в локальные координаты roi_image."""
    if roi_x == 0 and roi_y == 0:
        return dict(calibration_data)
    out: dict[str, Any] = dict(calibration_data)
    for key in ("center", "min_point", "max_point"):
        pt = calibration_data.get(key)
        if isinstance(pt, dict) and "x" in pt and "y" in pt:
            out[key] = {"x": float(pt["x"]) - roi_x, "y": float(pt["y"]) - roi_y}
    return out


def _append_calibration_roi_warnings(out_warnings: list[str], calibration_data: dict[str, Any], roi_w: int, roi_h: int) -> None:
    """Если точка калибровки явно вне вырезанного ROI — помечаем (погрешность округления ±2 px)."""
    margin = 2.0
    for key in ("center", "min_point", "max_point"):
        pt = calibration_data.get(key)
        if not isinstance(pt, dict):
            continue
        try:
            px, py = float(pt["x"]), float(pt["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            px < -margin
            or py < -margin
            or px > roi_w + margin
            or py > roi_h + margin
        ):
            out_warnings.append(f"calibration_{key}_outside_roi")
            break


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


def _point_to_segment_distance(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    abx = bx - ax
    aby = by - ay
    apx = px - ax
    apy = py - ay
    denom = abx * abx + aby * aby
    if denom <= 1e-8:
        return math.hypot(px - ax, py - ay)
    t = (apx * abx + apy * aby) / denom
    t = max(0.0, min(1.0, t))
    cx = ax + t * abx
    cy = ay + t * aby
    return math.hypot(px - cx, py - cy)


def _angle_delta_deg(start: float, end: float) -> float:
    d = end - start
    while d <= -180.0:
        d += 360.0
    while d > 180.0:
        d -= 360.0
    return d


def _angle_diff_deg(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _complement_span_deg(span_short: float) -> float:
    """Вторая дуга между теми же min/max: кратчайшая span_short, дополнительная = 360-|short| с тем же знаком направления."""
    if span_short > 0:
        return span_short - 360.0
    if span_short < 0:
        return span_short + 360.0
    return 0.0


def _ratio_on_arc(min_angle: float, max_angle: float, tip_angle: float) -> tuple[float, float, str]:
    """Интерполяция угла стрелки в ratio [0, 1] между min и max по кругу.

    Между min и max есть две дуги: короткая (до 180°) и длинная (360° - |short|).
    Для каждой дуги вычисляем свою дельту и ratio.

    Ключевой момент: для длинной дуги нельзя использовать _angle_delta_deg (даёт
    кратчайший путь). Когда стрелка переходит через 180°-рубеж относительно min,
    кратчайшая дельта меняет знак — но реальная дуга продолжается в том же направлении.

    Правило:
    - Если sign(delta_short) совпадает с sign(span_long) — дельта вдоль длинной дуги
      равна delta_short (начало дуги, направление совпадает).
    - Иначе — противоположное: delta_long = complement(delta_short) = delta_short ± 360°.
    """
    span_short = _angle_delta_deg(min_angle, max_angle)
    if abs(span_short) < 1e-6:
        return 0.0, 0.0, "degenerate"

    span_long = _complement_span_deg(span_short)
    delta_short = _angle_delta_deg(min_angle, tip_angle)

    # Дельта вдоль длинной дуги: совпадает с delta_short по знаку с span_long
    # только на первой половине дуги; после перехода через ±180° знак delta_short
    # меняется — тогда берём дополнение (± 360°).
    if abs(span_long) < 1e-6:
        delta_long = delta_short
    elif span_long * delta_short >= 0:
        # delta_short идёт в том же направлении, что и длинная дуга
        delta_long = delta_short
    else:
        # delta_short идёт «против» длинной дуги — нужна противоположная дуга до tip
        delta_long = _complement_span_deg(delta_short)

    r_short = delta_short / span_short
    r_long = delta_long / span_long

    eps = 0.02
    short_ok = -eps <= r_short <= 1.0 + eps
    long_ok = -eps <= r_long <= 1.0 + eps

    if short_ok and not long_ok:
        return min(1.0, max(0.0, r_short)), span_short, "short_arc"
    if long_ok and not short_ok:
        return min(1.0, max(0.0, r_long)), span_long, "long_arc"
    if short_ok and long_ok:
        # Обе дуги допустимы (стрелка очень близко к min): предпочитаем длинную для
        # типичного манометра (шкала через верх), или короткую если short > long.
        if abs(span_long) > abs(span_short):
            return min(1.0, max(0.0, r_long)), span_long, "long_arc"
        return min(1.0, max(0.0, r_short)), span_short, "short_arc"

    # Ни одна дуга (угол совсем снаружи): проецируем на ту, к которой ближе.
    r = min(1.0, max(0.0, r_short))
    return r, span_short, "short_clamped"


def _estimate_tip_from_dark_pixels(
    image: np.ndarray,
    center: tuple[float, float],
    expected_len: float,
) -> tuple[float, float] | None:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Для тестовых/контрастных стрелок: берём только тёмные пиксели.
    _, dark = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
    h, w = gray.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx.astype(np.float32) - float(center[0])
    dy = float(center[1]) - yy.astype(np.float32)
    r = np.sqrt(dx * dx + dy * dy)
    annulus = (r >= max(18.0, expected_len * 0.2)) & (r <= expected_len * 1.15)
    mask = (dark > 0) & annulus
    if int(mask.sum()) < 30:
        return None

    sel_dx = dx[mask]
    sel_dy = dy[mask]
    sel_r = r[mask]
    angles = np.degrees(np.arctan2(sel_dy, sel_dx))
    bins = ((angles + 180.0) % 360.0).astype(np.int32)
    hist = np.bincount(bins, weights=sel_r, minlength=360)
    best_bin = int(np.argmax(hist))
    if hist[best_bin] <= 0:
        return None
    # Берём дальнюю точку в узком угловом окне.
    diff = np.abs(((bins - best_bin + 180) % 360) - 180)
    near = diff <= 4
    if int(np.count_nonzero(near)) == 0:
        return None
    idx = int(np.argmax(sel_r[near]))
    pts_x = xx[mask][near]
    pts_y = yy[mask][near]
    return float(pts_x[idx]), float(pts_y[idx])


def _radial_darkness_mean(
    gray: np.ndarray,
    center: tuple[float, float],
    r0: float,
    r1: float,
    angle_deg: float,
) -> float:
    """Средняя «тёмность» (255 − яркость) вдоль луча — стрелка даёт пик.

    Важно: угол angle_deg совпадает с соглашением _angle_from_center, где
    dy = center_y - point_y (ось Y математическая, «вверх» = +). Поэтому для
    перевода в пиксельные координаты OpenCV ось Y нужно ИНВЕРТИРОВАТЬ:
        py = center[1] - sin(angle)*r    (НЕ +sin, иначе сканирование идёт в зеркальном направлении)
    """
    rad = math.radians(angle_deg)
    dx_dir = math.cos(rad)
    dy_dir = -math.sin(rad)   # инверсия Y: в image coords y увеличивается вниз
    s = 0.0
    n = 0
    steps = max(10, int((r1 - r0) / 2.5))
    for i in range(steps + 1):
        t = i / max(steps, 1)
        r = r0 + t * (r1 - r0)
        px = int(round(center[0] + dx_dir * r))
        py = int(round(center[1] + dy_dir * r))
        if 0 <= px < gray.shape[1] and 0 <= py < gray.shape[0]:
            s += 255.0 - float(gray[py, px])
            n += 1
    return s / max(n, 1e-6)


def _best_angle_radial_darkness(
    gray: np.ndarray,
    center: tuple[float, float],
    r0: float,
    r1: float,
    min_angle: float,
    max_angle: float,
) -> tuple[float, float]:
    """Угол с максимальной тёмностью на луче; ослабляем засечки у min/max."""
    scores = np.zeros(360, dtype=np.float64)
    for a in range(360):
        scores[a] = _radial_darkness_mean(gray, center, r0, r1, float(a))
        # Засечки и метки у 0 и 100 создают ложные пики — подавляем шире (±20°).
        if _angle_diff_deg(float(a), min_angle) < 20.0 or _angle_diff_deg(float(a), max_angle) < 20.0:
            scores[a] *= 0.30
    smoothed = np.convolve(scores, np.ones(5, dtype=np.float64) / 5.0, mode="same")
    best_i = int(np.argmax(smoothed))
    peak = float(smoothed[best_i])
    # Уточнение по соседним углам (шаг 0.25°)
    best_f = float(best_i)
    for _ in range(2):
        left = _radial_darkness_mean(gray, center, r0, r1, best_f - 0.25)
        right = _radial_darkness_mean(gray, center, r0, r1, best_f + 0.25)
        if left > right and left > peak:
            best_f -= 0.25
            peak = left
        elif right > peak:
            best_f += 0.25
            peak = right
        else:
            break
    return best_f, peak


def _detect_analog_needle_tip(
    image: np.ndarray,
    center: tuple[float, float],
    min_point: tuple[float, float],
    max_point: tuple[float, float],
    expected_len: float,
    near_center_thr: float,
    min_tip_len: float,
    lines: Any,
) -> tuple[tuple[float, float], float, str, dict[str, Any]]:
    """Hough + согласование с радиальным пиком тёмности (устойчиво к засечкам у 0/100)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    min_angle = _angle_from_center(center, min_point)
    max_angle = _angle_from_center(center, max_point)
    r0 = max(12.0, expected_len * 0.18)
    r1 = max(r0 + 8.0, expected_len * 1.02)
    rad_angle, rad_peak = _best_angle_radial_darkness(gray, center, r0, r1, min_angle, max_angle)

    debug: dict[str, Any] = {
        "radial_peak_angle": round(float(rad_angle), 3),
        "radial_peak_strength": round(float(rad_peak), 4),
    }

    mismatch_penalty_per_deg = 1.2
    best_tip: tuple[float, float] | None = None
    best_score = -1e18
    line_count = 0

    if lines is not None:
        line_count = int(len(lines))
        for item in lines:
            x1, y1, x2, y2 = item[0]
            p1 = (float(x1), float(y1))
            p2 = (float(x2), float(y2))
            d1 = math.hypot(p1[0] - center[0], p1[1] - center[1])
            d2 = math.hypot(p2[0] - center[0], p2[1] - center[1])
            center_to_segment = _point_to_segment_distance(center, p1, p2)
            if center_to_segment > near_center_thr:
                continue
            tip = p1 if d1 > d2 else p2
            tip_len = max(d1, d2)
            if tip_len < min_tip_len:
                continue
            tip_ang = _angle_from_center(center, tip)
            mismatch = _angle_diff_deg(tip_ang, rad_angle)
            score = tip_len - center_to_segment * 0.8 - mismatch * mismatch_penalty_per_deg
            if score > best_score:
                best_score = score
                best_tip = tip

    ray_len = max(min_tip_len, expected_len * 0.88)
    # ray_tip в пиксельных координатах: Y инвертирован (см. _radial_darkness_mean).
    ray_tip = (
        center[0] + math.cos(math.radians(rad_angle)) * ray_len,
        center[1] - math.sin(math.radians(rad_angle)) * ray_len,
    )
    ray_base_score = rad_peak * 0.35

    if best_tip is None:
        debug["needle_method"] = "radial_only"
        return ray_tip, ray_base_score, "radial_only", debug

    tip_ang = _angle_from_center(center, best_tip)
    if _angle_diff_deg(tip_ang, rad_angle) > 22.0:
        debug["needle_method"] = "radial_override_far_hough"
        return ray_tip, ray_base_score, "radial_override_far_hough", debug

    debug["needle_method"] = "hough_radial_agree"
    return best_tip, float(best_score), "hough_radial_agree", debug


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

    expected_len = (
        math.hypot(min_point[0] - center[0], min_point[1] - center[1])
        + math.hypot(max_point[0] - center[0], max_point[1] - center[1])
    ) / 2.0
    near_center_thr = max(18.0, expected_len * 0.25)
    min_tip_len = max(24.0, expected_len * 0.35)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    lines: Any = None
    for low, high, hough_thr in ((60, 140, 40), (80, 160, 45), (100, 200, 50)):
        edges = cv2.Canny(gray, low, high)
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180,
            threshold=hough_thr,
            minLineLength=max(28, int(round(expected_len * 0.28))),
            maxLineGap=12,
        )
        if lines is not None:
            break

    best_tip, _score, needle_method, _ = _detect_analog_needle_tip(
        image, center, min_point, max_point, expected_len, near_center_thr, min_tip_len, lines
    )
    if needle_method == "radial_only":
        fb = _estimate_tip_from_dark_pixels(image, center, expected_len)
        if fb is not None and _angle_diff_deg(
            _angle_from_center(center, fb), _angle_from_center(center, best_tip)
        ) < 12.0:
            best_tip = fb
    if best_tip is None:
        return CVResult(value=None, ok=False, error="Needle near center not found")

    angle = _angle_from_center(center, best_tip)
    min_angle = _angle_from_center(center, min_point)
    max_angle = _angle_from_center(center, max_point)
    ratio, _span_used, arc_hint = _ratio_on_arc(min_angle, max_angle, angle)
    if arc_hint == "degenerate":
        return CVResult(value=None, ok=False, error="Invalid calibration angle span")
    value = float(min_value) + ratio * (float(max_value) - float(min_value))
    return CVResult(value=value, ok=True)


def analog_debug_from_image(image: np.ndarray, calibration_data: dict[str, Any]) -> dict[str, Any]:
    """Диагностика analog-распознавания для UI setup/test."""
    out: dict[str, Any] = {
        "tip_point": None,
        "angle": None,
        "min_angle": None,
        "max_angle": None,
        "ratio": None,
        "quality_score": None,
        "radial_peak_angle": None,
        "radial_peak_strength": None,
        "needle_method": None,
        "warnings": [],
    }

    center_data = calibration_data.get("center")
    min_point_data = calibration_data.get("min_point")
    max_point_data = calibration_data.get("max_point")
    if not (center_data and min_point_data and max_point_data):
        out["warnings"] = ["missing_center_or_minmax_points"]
        return out

    center = (float(center_data["x"]), float(center_data["y"]))
    min_point = (float(min_point_data["x"]), float(min_point_data["y"]))
    max_point = (float(max_point_data["x"]), float(max_point_data["y"]))
    roi_h, roi_w = image.shape[:2]
    _append_calibration_roi_warnings(out["warnings"], calibration_data, roi_w, roi_h)
    expected_len = (
        math.hypot(min_point[0] - center[0], min_point[1] - center[1])
        + math.hypot(max_point[0] - center[0], max_point[1] - center[1])
    ) / 2.0
    if expected_len < 8:
        out["warnings"] = ["expected_length_too_small"]
        return out
    near_center_thr = max(18.0, expected_len * 0.25)
    min_tip_len = max(24.0, expected_len * 0.35)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    lines: Any = None
    for low, high, hough_thr in ((60, 140, 40), (80, 160, 45), (100, 200, 50)):
        edges = cv2.Canny(gray, low, high)
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180,
            threshold=hough_thr,
            minLineLength=max(28, int(round(expected_len * 0.28))),
            maxLineGap=12,
        )
        if lines is not None:
            break

    line_count = int(len(lines)) if lines is not None else 0
    best_tip, best_score, needle_method, ndbg = _detect_analog_needle_tip(
        image, center, min_point, max_point, expected_len, near_center_thr, min_tip_len, lines
    )
    out["radial_peak_angle"] = ndbg.get("radial_peak_angle")
    out["radial_peak_strength"] = ndbg.get("radial_peak_strength")
    out["needle_method"] = needle_method
    out["quality_score"] = round(float(best_score), 3)

    if needle_method == "radial_only":
        fb = _estimate_tip_from_dark_pixels(image, center, expected_len)
        if fb is not None and _angle_diff_deg(
            _angle_from_center(center, fb), _angle_from_center(center, best_tip)
        ) < 12.0:
            best_tip = fb
            out["warnings"] = [*out["warnings"], "tip_refined_dark_agree"]
    if needle_method == "radial_override_far_hough":
        out["warnings"] = [*out["warnings"], "hough_disagreed_with_radial"]

    if best_tip is None:
        out["warnings"] = [*out["warnings"], "needle_not_found", f"hough_lines={line_count}"]
        return out

    angle = _angle_from_center(center, best_tip)
    min_angle = _angle_from_center(center, min_point)
    max_angle = _angle_from_center(center, max_point)
    ratio, span_used, arc_hint = _ratio_on_arc(min_angle, max_angle, angle)
    if arc_hint == "degenerate":
        out["warnings"] = [*out["warnings"], "invalid_calibration_angle_span"]
        return out
    out["tip_point"] = {"x": round(float(best_tip[0]), 2), "y": round(float(best_tip[1]), 2)}
    out["angle"] = round(float(angle), 3)
    out["min_angle"] = round(float(min_angle), 3)
    out["max_angle"] = round(float(max_angle), 3)
    out["ratio"] = round(float(ratio), 5)
    out["span_deg"] = round(float(span_used), 3)
    out["arc"] = arc_hint
    if arc_hint == "short_clamped":
        out["warnings"] = [*out["warnings"], "tip_outside_minmax_span"]
    return out


def recognize_from_image(
    image: np.ndarray,
    logger: Logger,
    *,
    roi_json_override: str | None = None,
    calibration_json_override: str | None = None,
) -> CVResult:
    roi_data = _parse_json(roi_json_override if roi_json_override is not None else logger.roi_json)
    cal_raw = (
        calibration_json_override.strip()
        if calibration_json_override is not None and calibration_json_override.strip()
        else None
    )
    calibration_data = _parse_json(cal_raw if cal_raw is not None else logger.calibration_json)
    roi_image = _apply_roi(image, roi_data)
    if roi_image.size == 0:
        return CVResult(value=None, ok=False, error="ROI produced empty image")
    if logger.gauge_type == GaugeType.digital:
        return _recognize_digital(roi_image)
    rx, ry = _roi_origin(roi_data)
    cal_roi = _calibration_to_roi_coords(calibration_data, rx, ry)
    return _recognize_analog(roi_image, cal_roi)

