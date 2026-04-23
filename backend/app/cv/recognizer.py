from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import replace
from typing import Any

import cv2
import numpy as np
import pytesseract

from app.cv.types import CVResult
from app.models.logger import GaugeType, Logger


_LAST_SEGMENT_DEBUG: dict[str, Any] = {}


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
    img_h, img_w = image.shape[:2]
    # If ROI is expressed in another reference resolution (common for phone stream setup),
    # try to remap it to current frame size before cropping.
    if img_w > 0 and img_h > 0 and (
        x >= img_w
        or y >= img_h
        or x + w > int(img_w * 1.05)
        or y + h > int(img_h * 1.05)
    ):
        ref_sizes = [
            (1080, 1920),
            (1440, 1920),
            (720, 1280),
            (1170, 2532),
            (1080, 2340),
        ]
        remapped: tuple[int, int, int, int] | None = None
        for ref_w, ref_h in ref_sizes:
            if x + w <= int(ref_w * 1.05) and y + h <= int(ref_h * 1.05):
                sx = img_w / float(ref_w)
                sy = img_h / float(ref_h)
                remapped = (
                    int(round(x * sx)),
                    int(round(y * sy)),
                    int(round(w * sx)),
                    int(round(h * sy)),
                )
                break
        if remapped is not None:
            x, y, w, h = remapped
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


def _append_roi_geometry_warnings(
    out: list[str],
    roi_image: np.ndarray,
    roi_data: dict[str, Any],
    full_shape: tuple[int, ...],
) -> None:
    """Слабые проверки ROI (вариант 1 ТЗ п.12: зона задаётся оператором, без авто-детектора прибора)."""
    if roi_image.size == 0:
        return
    rh, rw = int(roi_image.shape[0]), int(roi_image.shape[1])
    fh, fw = int(full_shape[0]), int(full_shape[1])
    if rw < 5 or rh < 5:
        out.append("roi_crop_below_min_geometry")
    elif rw * rh < 2500:
        out.append("roi_area_below_recommended")
    if roi_data and fw > 0 and fh > 0 and rw * rh >= fw * fh * 0.98:
        out.append("roi_covers_almost_entire_frame")


def _merge_cv_warnings(result: CVResult, extra: list[str]) -> CVResult:
    if not extra:
        return result
    merged = [*extra, *(result.warnings or [])]
    return replace(result, warnings=merged)


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


_SEVEN_SEG_PATTERNS: dict[tuple[int, ...], str] = {
    (1, 1, 1, 0, 1, 1, 1): "0",
    (0, 0, 1, 0, 0, 1, 0): "1",
    (1, 0, 1, 1, 1, 0, 1): "2",
    (1, 0, 1, 1, 0, 1, 1): "3",
    (0, 1, 1, 1, 0, 1, 0): "4",
    (1, 1, 0, 1, 0, 1, 1): "5",
    (1, 1, 0, 1, 1, 1, 1): "6",
    (1, 0, 1, 0, 0, 1, 0): "7",
    (1, 1, 1, 1, 1, 1, 1): "8",
    (1, 1, 1, 1, 0, 1, 1): "9",
}


def _order_quad_points(pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    return np.array(
        [
            pts[int(np.argmin(s))],
            pts[int(np.argmin(diff))],
            pts[int(np.argmax(s))],
            pts[int(np.argmax(diff))],
        ],
        dtype=np.float32,
    )


def _warp_quad(image: np.ndarray, pts: np.ndarray) -> np.ndarray | None:
    rect = _order_quad_points(pts)
    (tl, tr, br, bl) = rect
    width_a = math.hypot(*(br - bl))
    width_b = math.hypot(*(tr - tl))
    height_a = math.hypot(*(tr - br))
    height_b = math.hypot(*(tl - bl))
    max_w = int(round(max(width_a, width_b)))
    max_h = int(round(max(height_a, height_b)))
    if max_w < 20 or max_h < 10:
        return None
    dst = np.array(
        [[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]],
        dtype=np.float32,
    )
    m = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, m, (max_w, max_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    if warped.shape[0] > warped.shape[1]:
        warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)
    return warped


def _detect_digital_segment_screen(image: np.ndarray) -> np.ndarray | None:
    if image.size == 0:
        return None
    h, w = image.shape[:2]
    if h < 40 or w < 80:
        return None
    top_h = max(30, int(h * 0.72))
    roi = image[:top_h, :]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bright_thr = int(np.percentile(gray, 50))
    low_sat = cv2.inRange(hsv, (0, 0, max(32, bright_thr - 25)), (180, 120, 255))
    greenish = cv2.inRange(hsv, (18, 4, max(25, bright_thr - 30)), (110, 190, 255))
    bright = cv2.inRange(gray, bright_thr, 255)
    mask = cv2.bitwise_and(cv2.bitwise_or(low_sat, greenish), bright)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    edges = cv2.Canny(gray, 45, 140)
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    edge_contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mask_contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [*mask_contours, *edge_contours]
    best_crop: np.ndarray | None = None
    best_score = -1e18
    img_area = float(max(1, h * w))
    for c in contours:
        area = float(cv2.contourArea(c))
        if area / img_area < 0.015:
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (rw, rh), _ang = rect
        if rw <= 0 or rh <= 0:
            continue
        long_side = max(rw, rh)
        short_side = min(rw, rh)
        if short_side < 18:
            continue
        aspect = long_side / max(short_side, 1.0)
        if aspect < 2.0 or aspect > 8.5:
            continue
        rect_area = float(rw * rh)
        fill = area / max(rect_area, 1.0)
        if fill < 0.18:
            continue
        box = cv2.boxPoints(rect)
        box[:, 1] = np.clip(box[:, 1], 0, top_h - 1)
        crop = _warp_quad(roi, box)
        if crop is None or crop.size == 0:
            continue
        cgray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        cgray = cv2.GaussianBlur(cgray, (3, 3), 0)
        _, dark = cv2.threshold(cgray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        dark_ratio = float(np.count_nonzero(dark)) / float(max(1, dark.size))
        contrast = float(np.percentile(cgray, 90) - np.percentile(cgray, 10))
        if dark_ratio < 0.01 or dark_ratio > 0.32:
            continue
        if contrast < 18.0:
            continue
        center_w = 1.0 - 0.25 * (abs(cx - w / 2.0) / max(1.0, w / 2.0) + abs(cy - h / 2.0) / max(1.0, h / 2.0))
        row_pack = _extract_digit_row_from_binary(dark)
        row_bonus = 1.25 if row_pack is not None else 0.85
        score = rect_area * max(0.35, fill) * center_w * row_bonus * (1.0 + min(0.7, aspect / 8.0))
        if score > best_score:
            best_score = score
            best_crop = crop
    return best_crop


def _detect_digital_segment_screen_edges(image: np.ndarray) -> np.ndarray | None:
    if image.size == 0:
        return None
    h, w = image.shape[:2]
    if h < 40 or w < 80:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gray = cv2.createCLAHE(clipLimit=2.8, tileGridSize=(8, 8)).apply(gray)
    edges = cv2.Canny(gray, 55, 170)
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_crop: np.ndarray | None = None
    best_score = -1e18
    img_area = float(max(1, h * w))
    for c in contours:
        area = float(cv2.contourArea(c))
        if area / img_area < 0.010:
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (rw, rh), _ = rect
        if rw <= 0 or rh <= 0:
            continue
        long_side = max(rw, rh)
        short_side = min(rw, rh)
        if short_side < 24:
            continue
        aspect = long_side / max(short_side, 1.0)
        if aspect < 1.8 or aspect > 8.0:
            continue
        box = cv2.boxPoints(rect)
        crop = _warp_quad(image, box)
        if crop is None or crop.size == 0:
            continue
        cgray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        cgray = cv2.GaussianBlur(cgray, (3, 3), 0)
        contrast = float(np.percentile(cgray, 92) - np.percentile(cgray, 8))
        _, dark = cv2.threshold(cgray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        dark_ratio = float(np.count_nonzero(dark)) / float(max(1, dark.size))
        if contrast < 12.0 or dark_ratio < 0.006 or dark_ratio > 0.46:
            continue
        center_w = 1.0 - 0.22 * (abs(cx - w / 2.0) / max(1.0, w / 2.0) + abs(cy - h / 2.0) / max(1.0, h / 2.0))
        score = area * max(0.5, center_w) * (1.0 + min(1.0, contrast / 80.0))
        if score > best_score:
            best_score = score
            best_crop = crop
    return best_crop


def _extract_digit_row_from_binary(bin_img: np.ndarray) -> tuple[np.ndarray, tuple[int, int]] | None:
    h, w = bin_img.shape[:2]
    if h < 20 or w < 40:
        return None
    row_counts = np.count_nonzero(bin_img, axis=1).astype(np.float32)
    if row_counts.max(initial=0.0) <= 0:
        return None
    win = max(7, h // 10)
    kernel = np.ones(win, dtype=np.float32) / float(win)
    smooth = np.convolve(row_counts, kernel, mode="same")
    peak_idx = int(np.argmax(smooth))
    peak_val = float(smooth[peak_idx])
    if peak_val <= 0:
        return None
    thr = max(3.0, peak_val * 0.62)
    y1 = peak_idx
    y2 = peak_idx
    while y1 > 0 and smooth[y1 - 1] >= thr:
        y1 -= 1
    while y2 < h - 1 and smooth[y2 + 1] >= thr:
        y2 += 1
    pad = max(3, h // 24)
    y1 = max(0, y1 - pad)
    y2 = min(h, y2 + pad + 1)
    row = bin_img[y1:y2, :]
    if row.size == 0 or row.shape[0] < max(10, h // 8):
        return None
    return row, (y1, y2)


def _group_digit_boxes(row_bin: np.ndarray) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, int, int, int]]] | None:
    h, w = row_bin.shape[:2]
    if h < 12 or w < 24:
        return None
    merge_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(1, w // 140), max(2, h // 8)))
    merged = cv2.morphologyEx(row_bin, cv2.MORPH_CLOSE, merge_kernel)
    merged = cv2.dilate(merged, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 2)))
    num_labels, _labels, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    boxes: list[tuple[int, int, int, int]] = []
    small_boxes: list[tuple[int, int, int, int]] = []
    for i in range(1, num_labels):
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        ww = int(stats[i, cv2.CC_STAT_WIDTH])
        hh = int(stats[i, cv2.CC_STAT_HEIGHT])
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < max(8, (h * w) // 700):
            continue
        if hh >= max(8, int(h * 0.28)) and ww >= max(3, int(w * 0.01)):
            boxes.append((x, y, ww, hh))
        elif hh >= max(2, int(h * 0.06)) and area >= max(4, (h * w) // 3600):
            small_boxes.append((x, y, ww, hh))
    if len(boxes) < 3:
        return None
    boxes.sort(key=lambda b: b[0])
    heights = np.array([b[3] for b in boxes], dtype=np.float32)
    median_h = float(np.median(heights))
    filtered = [b for b in boxes if 0.65 * median_h <= b[3] <= 1.40 * median_h]
    if len(filtered) < 3:
        filtered = boxes
    filtered.sort(key=lambda b: b[0])
    best_run: list[tuple[int, int, int, int]] = []
    for i in range(len(filtered)):
        run = [filtered[i]]
        prev = filtered[i]
        for j in range(i + 1, len(filtered)):
            cur = filtered[j]
            gap = cur[0] - (prev[0] + prev[2])
            vert_overlap = max(0, min(prev[1] + prev[3], cur[1] + cur[3]) - max(prev[1], cur[1]))
            if gap <= median_h * 1.35 and vert_overlap >= min(prev[3], cur[3]) * 0.25:
                run.append(cur)
                prev = cur
            else:
                break
        if len(run) > len(best_run) or (
            len(run) == len(best_run) and sum(x[2] for x in run) > sum(x[2] for x in best_run)
        ):
            best_run = run
    if len(best_run) < 3:
        return None
    return best_run, small_boxes


def _split_wide_projection_box(
    row_bin: np.ndarray,
    box: tuple[int, int, int, int],
    target_width: float,
) -> list[tuple[int, int, int, int]]:
    x, y, bw, bh = box
    if target_width <= 0 or bw <= int(target_width * 1.6):
        return [box]
    fragment = row_bin[y : y + bh, x : x + bw]
    if fragment.size == 0:
        return [box]
    cols = np.count_nonzero(fragment, axis=0).astype(np.float32)
    if cols.max(initial=0.0) <= 0:
        return [box]
    smooth = np.convolve(cols, np.ones(5, dtype=np.float32) / 5.0, mode="same")
    max_val = float(smooth.max())
    if max_val <= 0:
        return [box]

    estimated_parts = max(2, min(4, int(round(bw / max(1.0, target_width)))))
    cut_points: list[int] = []
    for part_idx in range(1, estimated_parts):
        expected = int(round(bw * part_idx / estimated_parts))
        radius = max(4, int(target_width * 0.35))
        left = max(2, expected - radius)
        right = min(bw - 2, expected + radius)
        if right <= left:
            continue
        local = smooth[left:right]
        if local.size == 0:
            continue
        cut_rel = int(np.argmin(local))
        cut_x = left + cut_rel
        if smooth[cut_x] <= max_val * 0.72:
            cut_points.append(cut_x)

    bounds = [0, *sorted(set(cut_points)), bw]
    if len(bounds) <= 2:
        return [box]

    parts: list[tuple[int, int, int, int]] = []
    for start, end in zip(bounds, bounds[1:]):
        if (end - start) < max(4, int(target_width * 0.35)):
            continue
        sub = fragment[:, start:end]
        rows = np.count_nonzero(sub, axis=1).astype(np.float32)
        if rows.max(initial=0.0) <= 0:
            continue
        row_thr = max(1.0, float(rows.max()) * 0.22)
        row_ids = np.where(rows >= row_thr)[0]
        if row_ids.size == 0:
            continue
        sy1 = int(row_ids[0])
        sy2 = int(row_ids[-1] + 1)
        if (sy2 - sy1) < max(8, int(fragment.shape[0] * 0.35)):
            continue
        parts.append((x + start, y + sy1, end - start, sy2 - sy1))
    return parts if len(parts) >= 2 else [box]


def _slot_digit_boxes(
    row_bin: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    if row_bin.size == 0 or not (4 <= len(boxes) <= 5):
        return boxes
    widths = np.array([b[2] for b in boxes], dtype=np.float32)
    median_w = float(np.median(widths))
    if median_w <= 0:
        return boxes
    if float(np.max(widths)) <= median_w * 1.45:
        return boxes

    x_left = min(b[0] for b in boxes)
    x_right = max(b[0] + b[2] for b in boxes)
    if x_right <= x_left:
        return boxes
    slot_count = len(boxes)
    slot_w = (x_right - x_left) / float(slot_count)
    if slot_w <= 0:
        return boxes

    slotted: list[tuple[int, int, int, int]] = []
    for idx in range(slot_count):
        sx1 = int(round(x_left + idx * slot_w))
        sx2 = int(round(x_left + (idx + 1) * slot_w))
        sx1 = max(0, min(sx1, row_bin.shape[1] - 1))
        sx2 = max(sx1 + 1, min(sx2, row_bin.shape[1]))
        fragment = row_bin[:, sx1:sx2]
        if fragment.size == 0:
            continue
        rows = np.count_nonzero(fragment, axis=1).astype(np.float32)
        cols = np.count_nonzero(fragment, axis=0).astype(np.float32)
        if rows.max(initial=0.0) <= 0 or cols.max(initial=0.0) <= 0:
            continue
        row_thr = max(1.0, float(rows.max()) * 0.20)
        col_thr = max(1.0, float(cols.max()) * 0.20)
        row_ids = np.where(rows >= row_thr)[0]
        col_ids = np.where(cols >= col_thr)[0]
        if row_ids.size == 0 or col_ids.size == 0:
            continue
        y1 = int(row_ids[0])
        y2 = int(row_ids[-1] + 1)
        x1 = sx1 + int(col_ids[0])
        x2 = sx1 + int(col_ids[-1] + 1)
        if (x2 - x1) < max(4, int(slot_w * 0.18)) or (y2 - y1) < max(8, int(row_bin.shape[0] * 0.40)):
            continue
        slotted.append((x1, y1, x2 - x1, y2 - y1))
    return slotted if len(slotted) >= 4 else boxes


def _digit_projection_profile(row_bin: np.ndarray) -> np.ndarray:
    h, _w = row_bin.shape[:2]
    if row_bin.size == 0:
        return np.zeros((0,), dtype=np.float32)
    yy1 = max(0, int(h * 0.10))
    yy2 = min(h, max(yy1 + 1, int(h * 0.90)))
    focus = row_bin[yy1:yy2, :]
    cols = np.count_nonzero(focus, axis=0).astype(np.float32)
    if cols.size == 0:
        return cols
    fill_ratio = cols / float(max(1, focus.shape[0]))
    cols = np.where(fill_ratio >= 0.92, 0.0, cols)
    smooth_win = max(5, min(11, row_bin.shape[1] // 30 if row_bin.shape[1] > 0 else 5))
    if smooth_win % 2 == 0:
        smooth_win += 1
    return np.convolve(cols, np.ones(smooth_win, dtype=np.float32) / float(smooth_win), mode="same")


def _boxes_look_pathological(boxes: list[tuple[int, int, int, int]], row_shape: tuple[int, int]) -> bool:
    if not boxes:
        return True
    if len(boxes) < 4 or len(boxes) > 6:
        return True
    widths = np.array([b[2] for b in boxes], dtype=np.float32)
    if widths.size == 0:
        return True
    median_w = float(np.median(widths))
    if median_w <= 0:
        return True
    row_h, row_w = row_shape
    return bool(
        float(np.max(widths)) > median_w * 1.55
        or float(np.max(widths)) > row_w * 0.26
        or median_w > row_h * 0.82
        or sum(1 for width in widths if width > median_w * 1.35) >= 2
        or sum(1 for _x, y, _bw, bh in boxes if y <= 1 and (y + bh) >= row_h - 1) >= max(2, len(boxes) - 1)
    )


def _refine_slot_boundaries(profile: np.ndarray, x_left: int, x_right: int, slot_count: int) -> list[int]:
    if profile.size == 0 or x_right <= x_left or slot_count < 2:
        return [x_left, x_right]
    slot_w = (x_right - x_left) / float(slot_count)
    cuts: list[int] = [x_left]
    for idx in range(1, slot_count):
        expected = int(round(x_left + slot_w * idx))
        radius = max(4, int(slot_w * 0.38))
        left = max(cuts[-1] + 3, expected - radius)
        right = min(x_right - 3, expected + radius)
        if right <= left:
            continue
        local = profile[left:right]
        if local.size == 0:
            continue
        cut_x = left + int(np.argmin(local))
        cuts.append(cut_x)
    cuts.append(x_right)
    refined: list[int] = [cuts[0]]
    min_gap = max(6, int(slot_w * 0.34))
    for cut in cuts[1:]:
        if cut - refined[-1] < min_gap:
            continue
        refined.append(cut)
    if refined[-1] != x_right:
        refined[-1] = x_right
    return refined if len(refined) == slot_count + 1 else [x_left, x_right]


def _choose_best_slot_run(cols: np.ndarray, slot_w: float) -> tuple[int, int] | None:
    if cols.size == 0 or float(cols.max(initial=0.0)) <= 0:
        return None
    smooth = np.convolve(cols, np.ones(5, dtype=np.float32) / 5.0, mode="same")
    thr = max(1.0, float(smooth.max()) * 0.22)
    active = np.where(smooth >= thr)[0]
    if active.size == 0:
        return None
    runs: list[tuple[int, int]] = []
    start = int(active[0])
    prev = int(active[0])
    for idx in active[1:]:
        cur = int(idx)
        if cur <= prev + 2:
            prev = cur
            continue
        runs.append((start, prev + 1))
        start = cur
        prev = cur
    runs.append((start, prev + 1))

    expected_center = cols.shape[0] / 2.0
    target_w = max(8.0, slot_w * 0.60)
    best_run: tuple[int, int] | None = None
    best_score = -1e18
    for x1, x2 in runs:
        width = x2 - x1
        if width < max(4, int(slot_w * 0.14)):
            continue
        center = (x1 + x2) / 2.0
        center_penalty = abs(center - expected_center) / max(1.0, slot_w)
        width_penalty = abs(width - target_w) / max(1.0, target_w)
        fill_score = float(np.mean(smooth[x1:x2])) / max(1.0, float(smooth.max()))
        score = fill_score * 3.5 - center_penalty * 1.8 - width_penalty * 1.3
        if width <= slot_w * 0.95:
            score += 0.6
        if score > best_score:
            best_score = score
            best_run = (x1, x2)
    return best_run


def _build_uniform_slot_boxes(row_bin: np.ndarray, slot_count: int) -> tuple[list[tuple[int, int, int, int]], float]:
    h, w = row_bin.shape[:2]
    if row_bin.size == 0 or slot_count < 4:
        return [], -1e18
    profile = _digit_projection_profile(row_bin)
    if profile.size == 0 or float(profile.max(initial=0.0)) <= 0:
        return [], -1e18
    thr = max(1.0, float(profile.max()) * 0.16)
    active = np.where(profile >= thr)[0]
    if active.size == 0:
        return [], -1e18
    x_left = int(active[0])
    x_right = int(active[-1] + 1)
    if x_right - x_left < max(24, int(w * 0.20)):
        return [], -1e18

    boundaries = _refine_slot_boundaries(profile, x_left, x_right, slot_count)
    if len(boundaries) != slot_count + 1:
        return [], -1e18

    slot_w = (x_right - x_left) / float(slot_count)
    if slot_w < max(10.0, h * 0.18) or slot_w > max(160.0, w * 0.45):
        return [], -1e18

    boxes: list[tuple[int, int, int, int]] = []
    occupancies: list[float] = []
    valley_scores: list[float] = []
    for idx in range(slot_count):
        raw_x1 = boundaries[idx]
        raw_x2 = boundaries[idx + 1]
        pad = max(1, int(slot_w * 0.06))
        sx1 = max(0, raw_x1 - pad)
        sx2 = min(w, raw_x2 + pad)
        fragment = row_bin[:, sx1:sx2]
        if fragment.size == 0:
            return [], -1e18
        rows = np.count_nonzero(fragment, axis=1).astype(np.float32)
        cols = np.count_nonzero(fragment, axis=0).astype(np.float32)
        if rows.max(initial=0.0) <= 0 or cols.max(initial=0.0) <= 0:
            return [], -1e18
        chosen_run = _choose_best_slot_run(cols, slot_w)
        if chosen_run is None:
            return [], -1e18
        run_x1, run_x2 = chosen_run
        fragment_run = fragment[:, run_x1:run_x2]
        if fragment_run.size == 0:
            return [], -1e18
        rows = np.count_nonzero(fragment_run, axis=1).astype(np.float32)
        cols = np.count_nonzero(fragment_run, axis=0).astype(np.float32)
        if rows.max(initial=0.0) <= 0 or cols.max(initial=0.0) <= 0:
            return [], -1e18
        row_thr = max(1.0, float(rows.max()) * 0.22)
        col_thr = max(1.0, float(cols.max()) * 0.22)
        row_ids = np.where(rows >= row_thr)[0]
        col_ids = np.where(cols >= col_thr)[0]
        if row_ids.size == 0 or col_ids.size == 0:
            return [], -1e18
        y1 = int(row_ids[0])
        y2 = int(row_ids[-1] + 1)
        x1 = sx1 + run_x1 + int(col_ids[0])
        x2 = sx1 + run_x1 + int(col_ids[-1] + 1)
        bw = x2 - x1
        bh = y2 - y1
        if bw < max(6, int(slot_w * 0.18)) or bh < max(12, int(h * 0.45)):
            return [], -1e18
        box = (x1, y1, bw, bh)
        boxes.append(box)
        occupancies.append(float(np.count_nonzero(row_bin[y1:y2, x1:x2])) / float(max(1, bw * bh)))
        if idx < slot_count - 1:
            boundary_x = boundaries[idx + 1]
            left_v = max(0, boundary_x - 1)
            right_v = min(profile.shape[0], boundary_x + 2)
            valley = float(np.mean(profile[left_v:right_v])) if right_v > left_v else float(profile[boundary_x])
            valley_scores.append(valley)

    widths = np.array([b[2] for b in boxes], dtype=np.float32)
    heights = np.array([b[3] for b in boxes], dtype=np.float32)
    width_median = float(np.median(widths))
    score = slot_count * 12.0
    score += max(0.0, 16.0 - float(np.std(widths)) * 0.9)
    score += max(0.0, 14.0 - float(np.std(heights)) * 0.7)
    score += min(8.0, float(np.mean(occupancies)) * 22.0)
    if slot_count == 5:
        score += 10.0
    elif slot_count == 4:
        score += 7.0
    if float(np.max(widths)) <= width_median * 1.20:
        score += 8.0
    if valley_scores:
        score += max(0.0, 12.0 - float(np.mean(valley_scores)) * 0.20)
    if width_median <= h * 0.78:
        score += 6.0
    return boxes, score


def _combined_decimal_scores(
    digit_boxes: list[tuple[int, int, int, int]],
    small_boxes: list[tuple[int, int, int, int]],
    row_bin: np.ndarray,
) -> dict[int, float]:
    scores = _find_decimal_scores(digit_boxes, small_boxes, row_bin.shape[:2])
    for idx, score in _find_decimal_scores_by_gap_geometry(row_bin, digit_boxes).items():
        scores[idx] = max(scores.get(idx, 0.0), score)
    return scores


def _score_digit_box_layout(
    row_bin: np.ndarray,
    digit_boxes: list[tuple[int, int, int, int]],
    small_boxes: list[tuple[int, int, int, int]],
) -> float:
    if not digit_boxes:
        return -1e18
    h, _w = row_bin.shape[:2]
    widths = np.array([b[2] for b in digit_boxes], dtype=np.float32)
    heights = np.array([b[3] for b in digit_boxes], dtype=np.float32)
    width_median = float(np.median(widths)) if widths.size else 0.0
    height_median = float(np.median(heights)) if heights.size else 0.0
    if width_median <= 0 or height_median <= 0:
        return -1e18

    decimal_scores = _combined_decimal_scores(digit_boxes, small_boxes, row_bin)
    score = 0.0
    score += max(0.0, 18.0 - float(np.std(widths)) * 1.0)
    score += max(0.0, 14.0 - float(np.std(heights)) * 0.8)
    if float(np.max(widths)) <= width_median * 1.22:
        score += 8.0
    score += max(0.0, 8.0 - abs(width_median - height_median * 0.72) * 0.25)
    if len(digit_boxes) == 5:
        score += 9.0
        score += decimal_scores.get(1, 0.0) * 16.0
        score += decimal_scores.get(2, 0.0) * 4.0
        score -= decimal_scores.get(0, 0.0) * 4.0
    elif len(digit_boxes) == 4:
        score += 7.0
        score += decimal_scores.get(0, 0.0) * 16.0
        score += decimal_scores.get(1, 0.0) * 4.0
        score -= decimal_scores.get(2, 0.0) * 4.0
    else:
        score -= abs(len(digit_boxes) - 5) * 6.0
    if not decimal_scores:
        if len(digit_boxes) == 5:
            score += 2.0
        elif len(digit_boxes) == 4:
            score += 1.0
    if height_median >= h * 0.55:
        score += 3.0
    return score


def _resegment_digit_boxes_uniform(
    row_bin: np.ndarray,
    current_boxes: list[tuple[int, int, int, int]],
    small_boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    best_boxes = current_boxes
    best_score = _score_digit_box_layout(row_bin, current_boxes, small_boxes)
    if _boxes_look_pathological(current_boxes, row_bin.shape[:2]):
        best_score -= 12.0
    for slot_count in (5, 4):
        boxes, score = _build_uniform_slot_boxes(row_bin, slot_count)
        if score > best_score and boxes:
            layout_score = _score_digit_box_layout(row_bin, boxes, small_boxes)
            total_score = score + layout_score
            if total_score <= best_score:
                continue
            best_score = total_score
            best_boxes = boxes
    return best_boxes


def _split_digit_boxes_by_projection(row_bin: np.ndarray) -> list[tuple[int, int, int, int]]:
    h, w = row_bin.shape[:2]
    if h < 12 or w < 40:
        return []
    cols = np.count_nonzero(row_bin, axis=0).astype(np.float32)
    if cols.max(initial=0.0) <= 0:
        return []
    smooth = np.convolve(cols, np.ones(5, dtype=np.float32) / 5.0, mode="same")
    thr = max(1.0, float(smooth.max()) * 0.20)
    active = np.where(smooth >= thr)[0]
    if active.size == 0:
        return []
    runs: list[tuple[int, int]] = []
    start = int(active[0])
    prev = int(active[0])
    for idx in active[1:]:
        cur = int(idx)
        if cur <= prev + 2:
            prev = cur
            continue
        runs.append((start, prev + 1))
        start = cur
        prev = cur
    runs.append((start, prev + 1))

    boxes: list[tuple[int, int, int, int]] = []
    for x1, x2 in runs:
        if (x2 - x1) < max(4, int(w * 0.015)):
            continue
        fragment = row_bin[:, x1:x2]
        rows = np.count_nonzero(fragment, axis=1).astype(np.float32)
        if rows.max(initial=0.0) <= 0:
            continue
        row_thr = max(1.0, float(rows.max()) * 0.25)
        row_ids = np.where(rows >= row_thr)[0]
        if row_ids.size == 0:
            continue
        y1 = int(row_ids[0])
        y2 = int(row_ids[-1] + 1)
        if (y2 - y1) < max(8, int(h * 0.35)):
            continue
        boxes.append((x1, y1, x2 - x1, y2 - y1))
    if not boxes:
        return []
    widths = np.array([b[2] for b in boxes], dtype=np.float32)
    base_width = float(np.median(widths))
    refined: list[tuple[int, int, int, int]] = []
    for box in boxes:
        refined.extend(_split_wide_projection_box(row_bin, box, base_width))
    refined = _slot_digit_boxes(row_bin, refined)
    refined.sort(key=lambda b: b[0])
    return refined


def _select_digit_cluster(
    boxes: list[tuple[int, int, int, int]],
    row_width: int,
    row_height: int | None = None,
) -> list[tuple[int, int, int, int]]:
    if not boxes:
        return []
    if len(boxes) <= 7:
        return boxes
    best: list[tuple[int, int, int, int]] = []
    best_score = -1e18
    max_box_h = float(max(b[3] for b in boxes))
    for size in (5, 6, 4, 7, 3):
        if size > len(boxes):
            continue
        for i in range(0, len(boxes) - size + 1):
            chunk = boxes[i : i + size]
            x1 = chunk[0][0]
            x2 = chunk[-1][0] + chunk[-1][2]
            width = x2 - x1
            if width <= 0:
                continue
            gaps = []
            for a, b in zip(chunk, chunk[1:]):
                gaps.append(b[0] - (a[0] + a[2]))
            gap_penalty = float(sum(max(0, g) for g in gaps))
            heights = np.array([b[3] for b in chunk], dtype=np.float32)
            h_std = float(np.std(heights)) if heights.size else 0.0
            median_h = float(np.median(heights)) if heights.size else 0.0
            median_cy = float(np.median([b[1] + b[3] / 2.0 for b in chunk]))
            center = (x1 + x2) / 2.0
            center_penalty = abs(center - row_width / 2.0) * 0.02
            height_ratio = median_h / float(max(1.0, max_box_h))
            if size == 5:
                count_bonus = 26.0
            elif size == 4:
                count_bonus = 22.0
            elif size == 6:
                count_bonus = 10.0
            elif size == 3:
                count_bonus = 5.0
            else:
                count_bonus = 0.0
            score = count_bonus + width * 0.08 - gap_penalty * 0.6 - h_std * 0.8 - center_penalty
            score += median_h * 1.35
            if x1 <= row_width * 0.45:
                score += 6.0
            if x2 >= row_width * 0.80:
                score -= 8.0 + (x2 - row_width * 0.80) * 0.08
            if row_height is not None and row_height > 0:
                if median_cy <= row_height * 0.50:
                    score += 10.0
                elif median_cy > row_height * 0.60:
                    score -= (median_cy - row_height * 0.60) * 2.2
            if height_ratio < 0.78:
                score -= (0.78 - height_ratio) * 55.0
            if score > best_score:
                best_score = score
                best = chunk
    return best or boxes[:7]


def _decode_seven_segment_digit(bin_digit: np.ndarray) -> tuple[str | None, float]:
    if bin_digit.size == 0:
        return None, 0.0
    ys, xs = np.where(bin_digit > 0)
    if xs.size > 0 and ys.size > 0:
        x1 = int(max(0, xs.min() - 1))
        x2 = int(min(bin_digit.shape[1], xs.max() + 2))
        y1 = int(max(0, ys.min() - 1))
        y2 = int(min(bin_digit.shape[0], ys.max() + 2))
        bin_digit = bin_digit[y1:y2, x1:x2]
    if bin_digit.size == 0:
        return None, 0.0
    pad = max(2, bin_digit.shape[1] // 14)
    digit = cv2.copyMakeBorder(bin_digit, 3, 3, pad, pad, cv2.BORDER_CONSTANT, value=0)
    digit = cv2.resize(digit, (64, 108), interpolation=cv2.INTER_NEAREST)
    digit = cv2.morphologyEx(digit, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    h, w = digit.shape[:2]

    segs = [
        digit[int(h * 0.05) : int(h * 0.16), int(w * 0.24) : int(w * 0.76)],
        digit[int(h * 0.14) : int(h * 0.43), int(w * 0.06) : int(w * 0.24)],
        digit[int(h * 0.14) : int(h * 0.43), int(w * 0.76) : int(w * 0.94)],
        digit[int(h * 0.44) : int(h * 0.56), int(w * 0.22) : int(w * 0.78)],
        digit[int(h * 0.57) : int(h * 0.86), int(w * 0.06) : int(w * 0.24)],
        digit[int(h * 0.57) : int(h * 0.86), int(w * 0.76) : int(w * 0.94)],
        digit[int(h * 0.84) : int(h * 0.96), int(w * 0.24) : int(w * 0.76)],
    ]
    occ_vals = [float(np.count_nonzero(seg)) / float(max(1, seg.size)) for seg in segs]
    dynamic_thr = max(0.16, min(0.34, float(np.mean(occ_vals)) * 0.95 + 0.08))
    on = tuple(1 if v >= dynamic_thr else 0 for v in occ_vals)

    best_digit: str | None = None
    best_score = -1e18
    for pattern, digit_char in _SEVEN_SEG_PATTERNS.items():
        score = 0.0
        for obs, exp, occ in zip(on, pattern, occ_vals):
            if obs == exp:
                score += 1.0 + (0.35 if exp == 1 else 0.0)
            else:
                score -= 0.8 + (0.30 if exp == 1 and occ < 0.18 else 0.0)
        if score > best_score:
            best_score = score
            best_digit = digit_char
    conf = 1.0 / (1.0 + math.exp(-best_score / 2.6))
    return best_digit, float(conf)


def _center_digit_crop_bounds(bin_digit: np.ndarray) -> tuple[int, int, int, int] | None:
    if bin_digit.size == 0:
        return None
    ys, xs = np.where(bin_digit > 0)
    if xs.size == 0 or ys.size == 0:
        return None
    x1 = int(max(0, xs.min() - 1))
    x2 = int(min(bin_digit.shape[1], xs.max() + 2))
    y1 = int(max(0, ys.min() - 1))
    y2 = int(min(bin_digit.shape[0], ys.max() + 2))
    work = bin_digit[y1:y2, x1:x2]
    if work.size == 0:
        return None

    cols = np.count_nonzero(work, axis=0).astype(np.float32)
    if cols.max(initial=0.0) <= 0:
        return None
    smooth = np.convolve(cols, np.ones(5, dtype=np.float32) / 5.0, mode="same")
    thr = max(1.0, float(smooth.max()) * 0.18)
    active = np.where(smooth >= thr)[0]
    if active.size == 0:
        return x1, y1, x2, y2

    runs: list[tuple[int, int]] = []
    start = int(active[0])
    prev = int(active[0])
    for idx in active[1:]:
        cur = int(idx)
        if cur <= prev + 2:
            prev = cur
            continue
        runs.append((start, prev + 1))
        start = cur
        prev = cur
    runs.append((start, prev + 1))

    total_w = float(max(1, work.shape[1]))
    center_x = total_w / 2.0
    best_run: tuple[int, int] | None = None
    best_score = -1e18
    for rx1, rx2 in runs:
        run_w = rx2 - rx1
        if run_w < max(4, int(total_w * 0.10)):
            continue
        run_center = (rx1 + rx2) / 2.0
        center_penalty = abs(run_center - center_x) / total_w
        width_ratio = run_w / total_w
        width_penalty = abs(width_ratio - 0.60)
        fill_score = float(np.mean(smooth[rx1:rx2])) / max(1.0, float(smooth.max()))
        score = fill_score * 3.2 - center_penalty * 2.0 - width_penalty * 1.1
        if rx1 <= work.shape[1] * 0.08:
            score -= 0.25
        if rx2 >= work.shape[1] * 0.92:
            score -= 0.25
        if score > best_score:
            best_score = score
            best_run = (rx1, rx2)

    if best_run is None:
        return x1, y1, x2, y2

    rx1, rx2 = best_run
    sub = work[:, rx1:rx2]
    rows = np.count_nonzero(sub, axis=1).astype(np.float32)
    if rows.max(initial=0.0) <= 0:
        return x1 + rx1, y1, x1 + rx2, y2
    row_thr = max(1.0, float(rows.max()) * 0.18)
    row_ids = np.where(rows >= row_thr)[0]
    if row_ids.size == 0:
        return x1 + rx1, y1, x1 + rx2, y2
    return x1 + rx1, y1 + int(row_ids[0]), x1 + rx2, y1 + int(row_ids[-1] + 1)


def _decode_seven_segment_digit_variants(bin_digit: np.ndarray, gray_digit: np.ndarray | None = None) -> tuple[str | None, float]:
    if getattr(_decode_seven_segment_digit, "__name__", "") != "_decode_seven_segment_digit":
        return _decode_seven_segment_digit(bin_digit)
    orig_shape = bin_digit.shape[:2]
    crop_bounds = _center_digit_crop_bounds(bin_digit)
    if crop_bounds is not None:
        x1, y1, x2, y2 = crop_bounds
        bin_digit = bin_digit[y1:y2, x1:x2]
        if gray_digit is not None and gray_digit.shape[:2] == orig_shape:
            gray_digit = gray_digit[y1:y2, x1:x2]
    variants: list[np.ndarray] = []
    if bin_digit.size > 0:
        variants.append(bin_digit)
        variants.append(cv2.morphologyEx(bin_digit, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))))
    if gray_digit is not None and gray_digit.size > 0:
        gray_digit = cv2.normalize(gray_digit, None, 0, 255, cv2.NORM_MINMAX)
        gray_digit = cv2.medianBlur(gray_digit, 3)
        _, otsu_inv = cv2.threshold(gray_digit, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        adapt_inv = cv2.adaptiveThreshold(
            gray_digit,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            21,
            3,
        )
        variants.extend([otsu_inv, adapt_inv])

    best_digit: str | None = None
    best_conf = 0.0
    digit_counts: Counter[str] = Counter()
    digit_best_conf: dict[str, float] = {}
    for variant in variants:
        digit_char, conf = _decode_seven_segment_digit(variant)
        if digit_char is None:
            continue
        digit_counts[digit_char] += 1
        digit_best_conf[digit_char] = max(digit_best_conf.get(digit_char, 0.0), conf)
        if conf > best_conf:
            best_digit = digit_char
            best_conf = conf
    if not digit_counts:
        return None, 0.0
    stable_digit = max(digit_counts, key=lambda key: (digit_counts[key], digit_best_conf.get(key, 0.0)))
    stable_conf = digit_best_conf.get(stable_digit, 0.0) + min(0.18, 0.06 * (digit_counts[stable_digit] - 1))
    return stable_digit, float(min(0.99, stable_conf))


def _find_decimal_scores(
    digit_boxes: list[tuple[int, int, int, int]],
    small_boxes: list[tuple[int, int, int, int]],
    row_shape: tuple[int, int],
) -> dict[int, float]:
    h, _w = row_shape
    scores: dict[int, float] = {}
    if len(digit_boxes) < 2:
        return scores
    for sb in small_boxes:
        sx, sy, sw, sh = sb
        area = sw * sh
        if sh > max(12, int(h * 0.38)) or area > max(120, (h * h) // 2):
            continue
        cy = sy + sh / 2.0
        if cy < h * 0.45:
            continue
        cx = sx + sw / 2.0
        for i in range(len(digit_boxes) - 1):
            x1, _y1, w1, h1 = digit_boxes[i]
            x2, _y2, _w2, _h2 = digit_boxes[i + 1]
            gap_left = x1 + w1 - max(2, int(w1 * 0.12))
            gap_right = x2 + max(2, int((x2 - (x1 + w1)) * 0.65))
            if gap_left <= cx <= gap_right and sh <= h1 * 0.35:
                gap_width = max(2.0, float(x2 - (x1 + w1)))
                gap_center = (x1 + w1 + x2) / 2.0
                center_score = 1.0 - min(1.0, abs(cx - gap_center) / gap_width)
                size_score = 1.0 - min(1.0, abs((sh / float(max(1, h1))) - 0.18) / 0.22)
                scores[i] = max(scores.get(i, 0.0), 0.65 + center_score * 0.20 + size_score * 0.15)
                break
    return scores


def _find_decimal_indices(
    digit_boxes: list[tuple[int, int, int, int]],
    small_boxes: list[tuple[int, int, int, int]],
    row_shape: tuple[int, int],
) -> set[int]:
    scores = _find_decimal_scores(digit_boxes, small_boxes, row_shape)
    if not scores:
        return set()
    best_idx, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score < 0.55:
        return set()
    return {int(best_idx)}


def _find_decimal_scores_by_gap_geometry(
    row_bin: np.ndarray,
    digit_boxes: list[tuple[int, int, int, int]],
) -> dict[int, float]:
    scores: dict[int, float] = {}
    if row_bin.size == 0 or len(digit_boxes) < 2:
        return scores
    h, w = row_bin.shape[:2]
    median_digit_w = float(np.median([b[2] for b in digit_boxes]))
    median_digit_h = float(np.median([b[3] for b in digit_boxes]))
    if median_digit_w <= 0 or median_digit_h <= 0:
        return scores

    search_y = max(0, int(h * 0.55))
    search = row_bin[search_y:, :]
    if search.size == 0:
        return scores

    num_labels, _labels, stats, _ = cv2.connectedComponentsWithStats(search, connectivity=8)
    best_scores: dict[int, float] = {}
    for label in range(1, num_labels):
        sx = int(stats[label, cv2.CC_STAT_LEFT])
        sy = int(stats[label, cv2.CC_STAT_TOP]) + search_y
        sw = int(stats[label, cv2.CC_STAT_WIDTH])
        sh = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = int(stats[label, cv2.CC_STAT_AREA])
        if sw <= 0 or sh <= 0:
            continue
        if not (median_digit_w * 0.08 <= sw <= median_digit_w * 0.30):
            continue
        if not (median_digit_h * 0.08 <= sh <= median_digit_h * 0.32):
            continue
        fill = area / float(max(1, sw * sh))
        if fill < 0.10 or fill > 0.95:
            continue
        cx = sx + sw / 2.0
        cy = sy + sh / 2.0
        if cy < h * 0.58:
            continue

        for idx in range(len(digit_boxes) - 1):
            x1, _y1, w1, h1 = digit_boxes[idx]
            x2, _y2, _w2, h2 = digit_boxes[idx + 1]
            gap_left = x1 + w1
            gap_right = x2
            gap_width = gap_right - gap_left
            if gap_width <= 1:
                continue
            gap_center = (gap_left + gap_right) / 2.0
            if not (gap_left - median_digit_w * 0.10 <= cx <= gap_right + median_digit_w * 0.10):
                continue
            height_ratio = sh / float(max(1.0, (h1 + h2) / 2.0))
            width_ratio = sw / float(max(1.0, median_digit_w))
            center_score = 1.0 - min(1.0, abs(cx - gap_center) / float(max(2.0, gap_width / 2.0)))
            width_score = 1.0 - min(1.0, abs(width_ratio - 0.15) / 0.15)
            height_score = 1.0 - min(1.0, abs(height_ratio - 0.18) / 0.18)
            score = center_score * 0.50 + width_score * 0.20 + height_score * 0.20 + min(fill, 1.0) * 0.10
            if score >= 0.55:
                best_scores[idx] = max(best_scores.get(idx, 0.0), score)
    scores.update(best_scores)
    return scores


def _collect_decimal_indices(
    digit_boxes: list[tuple[int, int, int, int]],
    small_boxes: list[tuple[int, int, int, int]],
    row_bin: np.ndarray,
) -> set[int]:
    combined_scores = _find_decimal_scores(digit_boxes, small_boxes, row_bin.shape[:2])
    for idx, score in _find_decimal_scores_by_gap_geometry(row_bin, digit_boxes).items():
        combined_scores[idx] = max(combined_scores.get(idx, 0.0), score)
    if not combined_scores:
        return set()
    best_idx, best_score = max(combined_scores.items(), key=lambda item: item[1])
    if best_score < 0.55:
        return set()
    return {int(best_idx)}


def _find_best_digit_run(
    bin_img: np.ndarray,
) -> tuple[tuple[int, int, int, int], list[tuple[int, int, int, int]], list[tuple[int, int, int, int]], float] | None:
    h, w = bin_img.shape[:2]
    if h < 30 or w < 60:
        return None
    num_labels, _labels, stats, _ = cv2.connectedComponentsWithStats(bin_img, connectivity=8)
    digit_like: list[tuple[int, int, int, int, int]] = []
    small_like: list[tuple[int, int, int, int]] = []
    for i in range(1, num_labels):
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        ww = int(stats[i, cv2.CC_STAT_WIDTH])
        hh = int(stats[i, cv2.CC_STAT_HEIGHT])
        area = int(stats[i, cv2.CC_STAT_AREA])
        if ww <= 0 or hh <= 0:
            continue
        fill = area / float(max(1, ww * hh))
        cy = y + hh / 2.0
        if hh >= max(10, int(h * 0.03)) and hh <= int(h * 0.45) and ww >= max(3, int(w * 0.003)) and ww <= int(w * 0.20):
            if 0.12 <= fill <= 0.88 and cy <= h * 0.82:
                digit_like.append((x, y, ww, hh, area))
        elif hh >= max(2, int(h * 0.008)) and hh <= int(h * 0.16) and area >= max(4, (h * w) // 6000):
            if cy <= h * 0.85:
                small_like.append((x, y, ww, hh))
    if len(digit_like) < 3:
        return None
    digit_like.sort(key=lambda b: b[0])
    run_candidates: list[
        tuple[tuple[int, int, int, int], list[tuple[int, int, int, int]], list[tuple[int, int, int, int]], float, float, float]
    ] = []
    for i in range(len(digit_like)):
        run = [digit_like[i]]
        prev = digit_like[i]
        for j in range(i + 1, len(digit_like)):
            cur = digit_like[j]
            gap = cur[0] - (prev[0] + prev[2])
            cy_delta = abs((cur[1] + cur[3] / 2.0) - (prev[1] + prev[3] / 2.0))
            med_h = float(np.median([b[3] for b in run]))
            if gap <= med_h * 1.05 and cy_delta <= med_h * 0.42 and 0.55 * med_h <= cur[3] <= 1.55 * med_h:
                run.append(cur)
                prev = cur
            elif gap > med_h * 1.4:
                break
        if len(run) < 3:
            continue
        med_w = float(np.median([b[2] for b in run]))
        med_h = float(np.median([b[3] for b in run]))
        run = [b for b in run if 0.35 * med_w <= b[2] <= 2.4 * med_w and 0.60 * med_h <= b[3] <= 1.45 * med_h]
        if len(run) < 3:
            continue
        xs = [b[0] for b in run]
        ys = [b[1] for b in run]
        x2s = [b[0] + b[2] for b in run]
        y2s = [b[1] + b[3] for b in run]
        x1 = min(xs)
        y1 = min(ys)
        x2 = max(x2s)
        y2 = max(y2s)
        run_h = y2 - y1
        run_w = x2 - x1
        if run_w < max(28, int(w * 0.06)):
            continue
        row_rect = (
            max(0, x1 - max(4, run_w // 30)),
            max(0, y1 - max(4, run_h // 6)),
            min(w, x2 + max(4, run_w // 18)),
            min(h, y2 + max(5, run_h // 5)),
        )
        rx1, ry1, rx2, ry2 = row_rect
        smalls = [
            s
            for s in small_like
            if rx1 - 3 <= s[0] <= rx2 + 3 and ry1 <= s[1] + s[3] / 2.0 <= ry2 + run_h * 0.35
        ]
        count = len(run)
        mean_y = float(np.mean([b[1] + b[3] / 2.0 for b in run]))
        if mean_y > h * 0.60 or ry1 > h * 0.66:
            continue
        height_std = float(np.std([b[3] for b in run]))
        area_sum = float(sum(b[4] for b in run))
        if count == 5:
            count_score = 26.0
        elif count == 4:
            count_score = 22.0
        elif count == 6:
            count_score = 8.0
        else:
            count_score = 0.0
        score = count_score
        score += min(25.0, area_sum / max(1.0, (h * w) / 250.0))
        score += max(0.0, 20.0 - height_std * 1.4)
        if mean_y <= h * 0.58:
            score += 10.0
        elif mean_y > h * 0.60:
            score -= (mean_y - h * 0.60) * 0.35
        score += med_h * 1.1
        score += min(10.0, run_w / max(1.0, w) * 25.0)
        score += min(8.0, len(smalls) * 1.5)
        boxes = [(b[0], b[1], b[2], b[3]) for b in run]
        run_candidates.append((row_rect, boxes, smalls, score, med_h, mean_y))
    if not run_candidates:
        return None

    max_median_h = max(candidate[4] for candidate in run_candidates)
    best: tuple[tuple[int, int, int, int], list[tuple[int, int, int, int]], list[tuple[int, int, int, int]], float] | None = None
    best_score = -1e18
    for row_rect, boxes, smalls, score, median_h, mean_y in run_candidates:
        height_ratio = median_h / float(max(1.0, max_median_h))
        if height_ratio < 0.80:
            score -= (0.80 - height_ratio) * 75.0
        if mean_y > h * 0.60:
            score -= (mean_y - h * 0.60) * 0.55
        if score > best_score:
            best_score = score
            best = (row_rect, boxes, smalls, score)
    return best


def _normalize_segment_token(token: str) -> str | None:
    cleaned = token.replace(",", ".").replace(" ", "")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned:
        return None
    if cleaned.count(".") > 1:
        first = cleaned.find(".")
        cleaned = cleaned[: first + 1] + cleaned[first + 1 :].replace(".", "")
    if cleaned.startswith("."):
        cleaned = f"0{cleaned}"
    if cleaned.endswith("."):
        cleaned = cleaned[:-1]
    if not cleaned or not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        return None
    return cleaned


def _extract_segment_tokens(raw: str) -> list[str]:
    matched = re.findall(r"\d+(?:[.,]\d+)?|[.,]\d+", raw)
    normalized: list[str] = []
    for match in matched:
        token = _normalize_segment_token(match)
        if token is None:
            continue
        normalized.append(token)

    keep_short = not any(len(token.replace(".", "")) >= 3 for token in normalized)
    out: list[str] = []
    seen: set[str] = set()
    for token in normalized:
        if not keep_short and len(token.replace(".", "")) <= 2:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _is_serial_like_segment_token(token: str) -> bool:
    if "." in token:
        return False
    digits = token.replace(".", "")
    if not digits.isdigit() or not (6 <= len(digits) <= 8):
        return False
    zero_count = digits.count("0")
    return zero_count >= max(3, len(digits) // 2)


def _score_segment_token(
    token: str,
    *,
    seg_conf: float = 0.0,
    source: str = "segment",
    has_decimal_mark: bool = False,
    decimal_indices: set[int] | None = None,
    position_ratio: float | None = None,
) -> float:
    digits = token.replace(".", "")
    int_part, _, frac = token.partition(".")
    decimal_indices = decimal_indices or set()
    score = seg_conf * 2.4
    if source == "segment":
        score += 0.9
    elif source == "ocr":
        score += 0.2
    if "." in token:
        score += 1.3
        if len(frac) == 3:
            score += 1.6
        elif 1 <= len(frac) <= 4:
            score += 0.8
        else:
            score -= 0.8
        if decimal_indices:
            if (len(int_part) - 1) in decimal_indices:
                score += 2.8
            else:
                score -= 1.2
        elif 1 <= len(int_part) <= 2 and len(frac) == 3:
            score += 1.2
    elif has_decimal_mark:
        score -= 1.4
    if len(int_part) <= 2:
        score += 0.9
    elif len(int_part) > 4:
        score -= 1.3
    if 3 <= len(digits) <= 6:
        score += 1.0
    elif len(digits) > 7:
        score -= 4.0
    if "." not in token and len(digits) >= 4:
        score -= 1.7
    if token.startswith("0"):
        score += 0.2
    if _is_serial_like_segment_token(token):
        score -= 2.8
    if position_ratio is not None and position_ratio >= 0.66:
        score -= 1.5 + max(0.0, position_ratio - 0.66) * 3.5
    return score


def _expand_segment_decimal_candidates(token: str) -> list[str]:
    if "." in token or not token.isdigit() or not (4 <= len(token) <= 6):
        return [token]
    candidates: list[str] = [token]
    seen = {token}
    for frac_len in (3, 2, 1):
        if len(token) <= frac_len:
            continue
        candidate = _normalize_segment_token(f"{token[:-frac_len]}.{token[-frac_len:]}")
        if candidate is None or candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return candidates


def _recognize_digital_segment(image: np.ndarray) -> CVResult:
    global _LAST_SEGMENT_DEBUG
    warnings: list[str] = []
    _LAST_SEGMENT_DEBUG = {
        "token_counter": {},
        "selected_token": None,
        "decimal_after": [],
        "seg_token": None,
        "seg_conf": 0.0,
        "expected_digit_count": 0,
        "row_shape": None,
        "screen_shape": None,
        "digit_boxes": [],
        "top_tokens": [],
    }
    if image.size == 0:
        return CVResult(value=None, ok=False, error="OCR segment failed: empty image", warnings=["segment_screen_not_found"])

    work = image
    h, w = work.shape[:2]
    aspect_wh = w / float(max(1, h))
    already_tight_digit_roi = (aspect_wh >= 2.2 and h <= 280) or (aspect_wh >= 1.8 and (h * w) <= 180000)
    target_h = 260 if already_tight_digit_roi else 620
    if h > 0 and h != target_h:
        scale = target_h / float(h)
        work = cv2.resize(
            work,
            (max(1, int(round(w * scale))), target_h),
            interpolation=cv2.INTER_CUBIC,
        )

    # Stage A: locate LCD screen on full frame with fallback to operator ROI crop.
    if already_tight_digit_roi:
        # Operator already provided a narrow ROI with digits, so extra LCD localization
        # often degrades quality on live phone streams.
        screen = work
    else:
        screen_crop = _detect_digital_segment_screen(work)
        if screen_crop is None:
            screen_crop = _detect_digital_segment_screen_edges(work)
        if screen_crop is None:
            warnings.append("segment_screen_not_found")
            screen = work
        else:
            sh, sw = screen_crop.shape[:2]
            wh, ww = work.shape[:2]
            screen_area_ratio = (sh * sw) / float(max(1, wh * ww))
            if screen_area_ratio < 0.07 or sh < max(48, int(wh * 0.09)) or sw < max(96, int(ww * 0.14)):
                warnings.append("segment_screen_not_found")
                screen = work
            else:
                screen = screen_crop
    if screen.size == 0:
        return CVResult(value=None, ok=False, error="OCR segment failed: empty screen crop", warnings=warnings or None)
    if screen.shape[0] < 220:
        scale = 220.0 / float(max(1, screen.shape[0]))
        screen = cv2.resize(
            screen,
            (max(1, int(round(screen.shape[1] * scale))), 220),
            interpolation=cv2.INTER_CUBIC,
        )
    _LAST_SEGMENT_DEBUG["screen_shape"] = [int(screen.shape[0]), int(screen.shape[1])]

    # Stage B: binarize and extract dominant digit row.
    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 3)
    blur_soft = cv2.GaussianBlur(gray, (0, 0), 1.2)
    gray = cv2.addWeighted(gray, 1.55, blur_soft, -0.55, 0)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
    _, th_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    th_adapt = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 4)
    dark_hsv = cv2.inRange(hsv, (0, 0, 0), (180, 255, 120))
    bin_variants = [
        cv2.morphologyEx(th_otsu, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))),
        cv2.morphologyEx(th_adapt, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))),
        cv2.morphologyEx(dark_hsv, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))),
    ]

    best_row: tuple[np.ndarray, np.ndarray, list[tuple[int, int, int, int]], list[tuple[int, int, int, int]], float] | None = None
    best_row_score = -1e18
    for row_bin_full in bin_variants:
        row_pack = _extract_digit_row_from_binary(row_bin_full)
        if row_pack is not None:
            row_bin, (y1, y2) = row_pack
            if row_bin.shape[0] > max(30, int(row_bin_full.shape[0] * 0.22)):
                row_counts = np.count_nonzero(row_bin, axis=1).astype(np.float32)
                peak = int(np.argmax(row_counts))
                half = max(10, min(row_bin.shape[0] // 4, int(row_bin_full.shape[0] * 0.08)))
                yy1 = max(0, peak - half)
                yy2 = min(row_bin.shape[0], peak + half + 1)
                row_bin = row_bin[yy1:yy2, :]
                y1 += yy1
                y2 = y1 + row_bin.shape[0]
            grouped = _group_digit_boxes(row_bin)
            if grouped is not None:
                digit_boxes, small_boxes = grouped
            else:
                digit_boxes = _split_digit_boxes_by_projection(row_bin)
                small_boxes = []
            if len(digit_boxes) >= 3:
                heights = np.array([b[3] for b in digit_boxes], dtype=np.float32)
                if heights.size > 0:
                    median_h = float(np.median(heights))
                    score = float(len(digit_boxes)) * 10.0
                    score += median_h
                    score += max(0.0, 25.0 - abs((y1 + y2) / 2.0 - row_bin_full.shape[0] * 0.52) * 0.2)
                    if score > best_row_score:
                        best_row_score = score
                        best_row = (
                            gray[y1:y2, :],
                            row_bin,
                            digit_boxes,
                            small_boxes,
                            score,
                        )
                    continue

        # Fallback for noisier real images: detect run directly on full binary image.
        run = _find_best_digit_run(row_bin_full)
        if run is None:
            continue
        (rx1, ry1, rx2, ry2), digit_boxes_full, small_boxes_full, run_score = run
        row_bin = row_bin_full[ry1:ry2, rx1:rx2]
        row_gray = gray[ry1:ry2, rx1:rx2]
        if row_bin.size == 0 or row_gray.size == 0:
            continue
        digit_boxes = [(x - rx1, y - ry1, bw, bh) for x, y, bw, bh in digit_boxes_full]
        small_boxes = [(x - rx1, y - ry1, bw, bh) for x, y, bw, bh in small_boxes_full]
        score = float(run_score) + float(len(digit_boxes)) * 2.5
        if score > best_row_score:
            best_row_score = score
            best_row = (row_gray, row_bin, digit_boxes, small_boxes, score)
    if best_row is None:
        if "segment_digit_row_not_found" not in warnings:
            warnings.append("segment_digit_row_not_found")
        if "segment_low_confidence" not in warnings:
            warnings.append("segment_low_confidence")
        return CVResult(value=None, ok=False, error="OCR segment failed: digit row not found", warnings=warnings or None)

    row_gray, row_bin, digit_boxes, small_boxes, _ = best_row
    _LAST_SEGMENT_DEBUG["row_shape"] = [int(row_bin.shape[0]), int(row_bin.shape[1])]
    all_digit_boxes = list(digit_boxes)
    proj_boxes = _split_digit_boxes_by_projection(row_bin)
    if 4 <= len(proj_boxes) <= 6:
        proj_median_h = float(np.median([b[3] for b in proj_boxes])) if proj_boxes else 0.0
        base_median_h = float(np.median([b[3] for b in digit_boxes])) if digit_boxes else 0.0
        if (
            4 <= len(digit_boxes) <= 6
            and abs(len(proj_boxes) - len(digit_boxes)) <= 1
            and proj_median_h >= base_median_h * 0.82
        ) or not (4 <= len(digit_boxes) <= 6):
            digit_boxes = proj_boxes
    digit_boxes = _select_digit_cluster(digit_boxes, row_bin.shape[1], row_bin.shape[0])

    # Stage C: keep only numeric zone, cut off unit labels on the right side.
    decimal_after = _collect_decimal_indices(digit_boxes, small_boxes, row_bin)
    x_left = min(b[0] for b in digit_boxes)
    x_right = max(b[0] + b[2] for b in digit_boxes)
    x_right_all = max(b[0] + b[2] for b in all_digit_boxes) if all_digit_boxes else x_right
    y_top = max(0, min(b[1] for b in digit_boxes) - max(2, int(row_bin.shape[0] * 0.10)))
    y_bottom = min(row_bin.shape[0], max(b[1] + b[3] for b in digit_boxes) + max(2, int(row_bin.shape[0] * 0.10)))
    pad_x = max(3, int((x_right - x_left) * 0.06))
    nz_x1 = max(0, x_left - pad_x)
    nz_x2 = min(row_bin.shape[1], x_right + pad_x)
    if nz_x2 <= nz_x1:
        if "segment_digit_row_not_found" not in warnings:
            warnings.append("segment_digit_row_not_found")
        if "segment_low_confidence" not in warnings:
            warnings.append("segment_low_confidence")
        return CVResult(value=None, ok=False, error="OCR segment failed: invalid numeric zone", warnings=warnings or None)
    num_bin = row_bin[y_top:y_bottom, nz_x1:nz_x2]
    num_gray = row_gray[y_top:y_bottom, nz_x1:nz_x2]

    shifted_boxes = [(x - nz_x1, y - y_top, bw, bh) for x, y, bw, bh in digit_boxes]
    shifted_small_boxes = [(x - nz_x1, y - y_top, bw, bh) for x, y, bw, bh in small_boxes]
    shifted_boxes = _resegment_digit_boxes_uniform(num_bin, shifted_boxes, shifted_small_boxes)
    decimal_after = _collect_decimal_indices(shifted_boxes, shifted_small_boxes, num_bin)
    _LAST_SEGMENT_DEBUG["decimal_after"] = sorted(decimal_after)
    _LAST_SEGMENT_DEBUG["digit_boxes"] = [list(map(int, box)) for box in shifted_boxes]

    intrusion_start = min(row_bin.shape[1], max(x_right, x_right_all) + max(2, int((x_right - x_left) * 0.04)))
    intrusion_zone = row_bin[:, intrusion_start:]
    if intrusion_zone.size > 0:
        labels, _lbl, stats, _ = cv2.connectedComponentsWithStats(intrusion_zone, connectivity=8)
        intrusion_hits = 0
        for i in range(1, labels):
            ww = int(stats[i, cv2.CC_STAT_WIDTH])
            hh = int(stats[i, cv2.CC_STAT_HEIGHT])
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < max(8, intrusion_zone.size // 450):
                continue
            if hh >= int(num_bin.shape[0] * 0.34) and ww >= int(num_bin.shape[1] * 0.04):
                intrusion_hits += 1
        if intrusion_hits > 0:
            warnings.append("segment_unit_text_intrusion")

    # Stage D: combine seven-segment decoder and OCR through token voting.
    token_votes: Counter[str] = Counter()
    token_conf_sums: dict[str, float] = {}
    token_score_sums: dict[str, float] = {}
    token_best_raw: dict[str, str] = {}
    token_best_conf: dict[str, float] = {}
    expected_digit_count = len(shifted_boxes)
    _LAST_SEGMENT_DEBUG["expected_digit_count"] = int(expected_digit_count)
    ocr_variant_winners: list[str] = []

    def _register_vote(
        base_token: str,
        *,
        raw: str,
        local_conf: float,
        source: str,
        position_ratio: float | None = None,
    ) -> None:
        if source == "ocr":
            digit_count = len(base_token.replace(".", ""))
            if expected_digit_count >= 3 and (digit_count > expected_digit_count or digit_count < expected_digit_count - 1):
                return
        best_token_local: str | None = None
        best_score_local = -1e18
        score_conf = local_conf if source == "segment" else local_conf * 0.65
        for token_candidate in _expand_segment_decimal_candidates(base_token):
            candidate_score = _score_segment_token(
                token_candidate,
                seg_conf=score_conf,
                source=source,
                has_decimal_mark=bool(decimal_after),
                decimal_indices=decimal_after,
                position_ratio=position_ratio,
            )
            if candidate_score > best_score_local:
                best_score_local = candidate_score
                best_token_local = token_candidate
        if best_token_local is None:
            return
        token_votes[best_token_local] += 1
        token_conf_sums[best_token_local] = token_conf_sums.get(best_token_local, 0.0) + local_conf
        token_score_sums[best_token_local] = token_score_sums.get(best_token_local, 0.0) + best_score_local
        if local_conf >= token_best_conf.get(best_token_local, -1.0):
            token_best_conf[best_token_local] = local_conf
            token_best_raw[best_token_local] = raw

    token_parts: list[str] = []
    seg_confs: list[float] = []
    for idx, (x, y, bw, bh) in enumerate(shifted_boxes):
        px = max(1, int(bw * 0.05))
        py = max(1, int(bh * 0.05))
        x1 = max(0, x - px)
        y1 = max(0, y - py)
        x2 = min(num_bin.shape[1], x + bw + px)
        y2 = min(num_bin.shape[0], y + bh + py)
        digit_img = num_bin[y1:y2, x1:x2]
        digit_gray = num_gray[y1:y2, x1:x2]
        digit_char, conf = _decode_seven_segment_digit_variants(digit_img, digit_gray)
        if digit_char is None:
            continue
        token_parts.append(digit_char)
        seg_confs.append(conf)
        if idx in decimal_after:
            token_parts.append(".")
    seg_token = _normalize_segment_token("".join(token_parts))
    seg_conf = float(sum(seg_confs) / max(1, len(seg_confs))) if seg_confs else 0.0
    _LAST_SEGMENT_DEBUG["seg_token"] = seg_token
    _LAST_SEGMENT_DEBUG["seg_conf"] = round(seg_conf, 4)
    if seg_token is not None:
        _register_vote(seg_token, raw=seg_token, local_conf=seg_conf, source="segment")

    ocr_scale = 3 if max(num_gray.shape[:2]) < 220 else 2
    ocr_w = max(1, int(round(num_gray.shape[1] * ocr_scale)))
    ocr_h = max(1, int(round(num_gray.shape[0] * ocr_scale)))
    ocr_gray = cv2.resize(num_gray, (ocr_w, ocr_h), interpolation=cv2.INTER_CUBIC)
    ocr_gray = cv2.medianBlur(ocr_gray, 3)
    ocr_blur = cv2.GaussianBlur(ocr_gray, (0, 0), 1.0)
    ocr_gray = cv2.addWeighted(ocr_gray, 1.6, ocr_blur, -0.6, 0)
    _, ocr_otsu_inv = cv2.threshold(ocr_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ocr_otsu = cv2.bitwise_not(ocr_otsu_inv)
    ocr_adapt_inv = cv2.adaptiveThreshold(ocr_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 3)
    ocr_adapt = cv2.bitwise_not(ocr_adapt_inv)
    ocr_num_bin = cv2.resize(num_bin, (ocr_w, ocr_h), interpolation=cv2.INTER_NEAREST)
    ocr_num_bin = cv2.morphologyEx(ocr_num_bin, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    ocr_variants = [
        ocr_gray,
        ocr_otsu_inv,
        ocr_otsu,
        ocr_adapt_inv,
        ocr_adapt,
        ocr_num_bin,
        cv2.bitwise_not(ocr_num_bin),
    ]
    for ocr_img in ocr_variants:
        best_variant: tuple[float, float, str, str, float | None] | None = None
        for psm in (13, 7, 8, 6):
            config = (
                f"--oem 1 --psm {psm} "
                "-c tessedit_char_whitelist=0123456789., "
                "-c classify_bln_numeric_mode=1"
            )
            try:
                raw = pytesseract.image_to_string(ocr_img, config=config).strip()
            except Exception:
                continue
            local_conf = 0.0
            try:
                data = pytesseract.image_to_data(ocr_img, config=config, output_type=pytesseract.Output.DICT)
                texts = list(data.get("text", []))
                confs = list(data.get("conf", []))
                lefts = list(data.get("left", []))
                widths = list(data.get("width", []))
                for idx_word, (conf_raw, text_raw) in enumerate(zip(confs, texts)):
                    if str(conf_raw).strip() in {"", "-1"} or not str(text_raw).strip():
                        continue
                    word_conf = float(conf_raw) / 100.0
                    local_conf = max(local_conf, word_conf)
                    position_ratio = None
                    try:
                        position_ratio = (
                            float(lefts[idx_word]) + float(widths[idx_word]) / 2.0
                        ) / float(max(1, ocr_img.shape[1]))
                    except (IndexError, TypeError, ValueError):
                        position_ratio = None
                    for token in _extract_segment_tokens(str(text_raw)):
                        preview_candidates = _expand_segment_decimal_candidates(token)
                        ranked_preview = sorted(
                            preview_candidates,
                            key=lambda item: _score_segment_token(
                                item,
                                seg_conf=word_conf * 0.65,
                                source="ocr",
                                has_decimal_mark=bool(decimal_after),
                                decimal_indices=decimal_after,
                                position_ratio=position_ratio,
                            ),
                            reverse=True,
                        )
                        if not ranked_preview:
                            continue
                        best_token_preview = ranked_preview[0]
                        best_score_preview = _score_segment_token(
                            best_token_preview,
                            seg_conf=word_conf * 0.65,
                            source="ocr",
                            has_decimal_mark=bool(decimal_after),
                            decimal_indices=decimal_after,
                            position_ratio=position_ratio,
                        )
                        candidate_preview = (
                            word_conf,
                            best_score_preview,
                            best_token_preview,
                            raw,
                            position_ratio,
                        )
                        if best_variant is None or candidate_preview[:2] > best_variant[:2]:
                            best_variant = candidate_preview
            except Exception:
                pass

            if best_variant is None:
                for token in _extract_segment_tokens(raw):
                    preview_candidates = _expand_segment_decimal_candidates(token)
                    ranked_preview = sorted(
                        preview_candidates,
                        key=lambda item: _score_segment_token(
                            item,
                            seg_conf=local_conf * 0.65,
                            source="ocr",
                            has_decimal_mark=bool(decimal_after),
                            decimal_indices=decimal_after,
                        ),
                        reverse=True,
                    )
                    if not ranked_preview:
                        continue
                    best_token_preview = ranked_preview[0]
                    best_score_preview = _score_segment_token(
                        best_token_preview,
                        seg_conf=local_conf * 0.65,
                        source="ocr",
                        has_decimal_mark=bool(decimal_after),
                        decimal_indices=decimal_after,
                    )
                    candidate_preview = (
                        local_conf,
                        best_score_preview,
                        best_token_preview,
                        raw,
                        None,
                    )
                    if best_variant is None or candidate_preview[:2] > best_variant[:2]:
                        best_variant = candidate_preview

            if best_variant is not None:
                best_local_conf, _best_local_score, best_variant_token, best_variant_raw, best_position = best_variant
                ocr_variant_winners.append(best_variant_token)
                _register_vote(
                    best_variant_token,
                    raw=best_variant_raw,
                    local_conf=best_local_conf,
                    source="ocr",
                    position_ratio=best_position,
                )

    if not token_votes:
        if "segment_low_confidence" not in warnings:
            warnings.append("segment_low_confidence")
        return CVResult(value=None, ok=False, error="OCR segment failed: no numeric candidates", warnings=warnings or None)

    ranked_tokens = sorted(
        token_votes.keys(),
        key=lambda token: (token_votes[token], token_conf_sums.get(token, 0.0), token_score_sums.get(token, 0.0)),
        reverse=True,
    )
    best_token = ranked_tokens[0]
    best_digits = best_token.replace(".", "")
    if len(best_digits) == max(1, expected_digit_count - 1):
        suffix_candidate = next(
            (
                token
                for token in ranked_tokens[1:]
                if len(token.replace(".", "")) == expected_digit_count and token.replace(".", "").endswith(best_digits)
            ),
            None,
        )
        if suffix_candidate is not None:
            best_token = suffix_candidate
    best_score = token_score_sums.get(best_token, 0.0) / float(max(1, token_votes[best_token]))
    best_raw = token_best_raw.get(best_token, best_token)
    best_conf = token_best_conf.get(best_token, 0.0)
    _LAST_SEGMENT_DEBUG["token_counter"] = dict(token_votes)
    _LAST_SEGMENT_DEBUG["selected_token"] = best_token
    _LAST_SEGMENT_DEBUG["ocr_variant_winners"] = list(ocr_variant_winners)
    _LAST_SEGMENT_DEBUG["top_tokens"] = [
        {
            "token": token,
            "votes": int(token_votes[token]),
            "conf_sum": round(float(token_conf_sums.get(token, 0.0)), 4),
            "score_sum": round(float(token_score_sums.get(token, 0.0)), 4),
            "raw": token_best_raw.get(token, token),
        }
        for token in ranked_tokens[:5]
    ]

    if len(ranked_tokens) > 1:
        alt_token = ranked_tokens[1]
        try:
            diff = abs(float(best_token) - float(alt_token))
        except ValueError:
            diff = 0.0
        vote_gap = token_votes[best_token] - token_votes[alt_token]
        if vote_gap <= 1 and diff > 0.0005:
            warnings.append("segment_conflicting_candidates")
    if "segment_conflicting_candidates" not in warnings and len(set(ocr_variant_winners)) > 1:
        try:
            winner_values = sorted({float(token) for token in ocr_variant_winners})
        except ValueError:
            winner_values = []
        if len(winner_values) > 1 and (winner_values[-1] - winner_values[0]) > 0.0005:
            warnings.append("segment_conflicting_candidates")

    confidence = max(best_conf, 1.0 / (1.0 + math.exp(-best_score / 2.8)))
    if (seg_token is not None and seg_conf < 0.45) or confidence < 0.60:
        warnings.append("segment_low_confidence")

    try:
        value = float(best_token)
    except ValueError:
        if "segment_low_confidence" not in warnings:
            warnings.append("segment_low_confidence")
        return CVResult(value=None, ok=False, error=f"OCR segment failed: '{best_raw}'", ocr_raw=best_raw, warnings=warnings or None)
    return CVResult(value=value, ok=True, ocr_raw=best_raw, warnings=warnings or None)


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
    warnings: list[str] = []
    roi_h, roi_w = image.shape[:2]
    _append_calibration_roi_warnings(warnings, calibration_data, roi_w, roi_h)

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
            warnings.append("tip_refined_dark_agree")
    if needle_method == "radial_override_far_hough":
        warnings.append("hough_disagreed_with_radial")
    if best_tip is None:
        return CVResult(value=None, ok=False, error="Needle near center not found")

    angle = _angle_from_center(center, best_tip)
    min_angle = _angle_from_center(center, min_point)
    max_angle = _angle_from_center(center, max_point)
    ratio, _span_used, arc_hint = _ratio_on_arc(min_angle, max_angle, angle)
    if arc_hint == "degenerate":
        return CVResult(value=None, ok=False, error="Invalid calibration angle span")
    if arc_hint == "short_clamped":
        warnings.append("tip_outside_minmax_span")
    value = float(min_value) + ratio * (float(max_value) - float(min_value))
    return CVResult(value=value, ok=True, warnings=warnings or None)


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
    """Единая точка входа CV для боевого пайплайна и проверки «как в проде».

    Без подмены аргументов используются только ``logger.roi_json`` и ``logger.calibration_json`` (и
    ``logger.gauge_type``). Интерактивная настройка в UI передаёт подмены через ``*_override`` до
    сохранения в БД — после Save config путь совпадает с фоновой обработкой при том же кадре.

    **Обнаружение прибора (ТЗ п.12, вариант 1):** границы прибора не ищутся автоматически; оператор
    задаёт ROI. Слабые геометрические проверки после кропа — в ``_append_roi_geometry_warnings``.
    """
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
    roi_geom_warnings: list[str] = []
    _append_roi_geometry_warnings(roi_geom_warnings, roi_image, roi_data, image.shape)
    if logger.gauge_type == GaugeType.digital:
        return _merge_cv_warnings(_recognize_digital(roi_image), roi_geom_warnings)
    if logger.gauge_type == GaugeType.digital_segment:
        result = _merge_cv_warnings(_recognize_digital_segment(roi_image), roi_geom_warnings)
        filtered_warnings = [w for w in (result.warnings or []) if not w.startswith("calibration_")]
        return replace(result, warnings=filtered_warnings or None)
    rx, ry = _roi_origin(roi_data)
    cal_roi = _calibration_to_roi_coords(calibration_data, rx, ry)
    return _merge_cv_warnings(_recognize_analog(roi_image, cal_roi), roi_geom_warnings)

