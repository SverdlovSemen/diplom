from __future__ import annotations

import math

import numpy as np

from app.cv import recognizer


def _point_from_angle(center: tuple[float, float], angle_deg: float, radius: float) -> tuple[float, float]:
    rad = math.radians(angle_deg)
    return (center[0] + math.cos(rad) * radius, center[1] - math.sin(rad) * radius)


def test_detect_tip_prefers_min_side_when_radial_peak_near_min(monkeypatch) -> None:
    image = np.zeros((320, 320, 3), dtype=np.uint8)
    center = (160.0, 160.0)
    min_point = _point_from_angle(center, -135.0, 100.0)
    max_point = _point_from_angle(center, -45.0, 100.0)
    expected_len = 100.0

    # Подсовываем radial-пик у min (режим near_min_zone).
    monkeypatch.setattr(recognizer, "_best_angle_radial_darkness", lambda *args, **kwargs: (-133.0, 80.0))

    min_tip = _point_from_angle(center, -134.0, 104.0)
    anti_tip = _point_from_angle(center, 46.0, 106.0)
    lines = np.array(
        [
            [[int(round(min_tip[0])), int(round(min_tip[1])), int(round(anti_tip[0])), int(round(anti_tip[1]))]],
        ],
        dtype=np.int32,
    )

    best_tip, _score, method, debug = recognizer._detect_analog_needle_tip(
        image=image,
        center=center,
        min_point=min_point,
        max_point=max_point,
        expected_len=expected_len,
        near_center_thr=35.0,
        min_tip_len=20.0,
        lines=lines,
    )
    best_angle = recognizer._angle_from_center(center, best_tip)
    min_angle = recognizer._angle_from_center(center, min_point)
    anti_min_angle = min_angle + 180.0

    assert method == "hough_radial_agree"
    assert debug.get("near_min_zone") is True
    assert recognizer._angle_diff_deg(best_angle, min_angle) < recognizer._angle_diff_deg(best_angle, anti_min_angle)
    assert debug.get("best_candidate_reason") == "near_min_disambiguation"


def test_recognize_analog_postcheck_replaces_anti_min_candidate(monkeypatch) -> None:
    image = np.zeros((260, 260, 3), dtype=np.uint8)
    center = (130.0, 130.0)
    min_point = _point_from_angle(center, -140.0, 90.0)
    max_point = _point_from_angle(center, -40.0, 90.0)

    anti_tip = _point_from_angle(center, 40.0, 95.0)
    min_tip = _point_from_angle(center, -139.0, 96.0)

    fake_debug = {
        "radial_peak_angle": -138.5,
        "min_compatible_tip": {"x": min_tip[0], "y": min_tip[1]},
    }
    monkeypatch.setattr(
        recognizer,
        "_detect_analog_needle_tip",
        lambda *args, **kwargs: (anti_tip, 55.0, "hough_radial_agree", fake_debug),
    )

    calibration = {
        "center": {"x": center[0], "y": center[1]},
        "min_point": {"x": min_point[0], "y": min_point[1]},
        "max_point": {"x": max_point[0], "y": max_point[1]},
        "min_value": 0.0,
        "max_value": 100.0,
    }
    result = recognizer._recognize_analog(image, calibration)

    assert result.ok is True
    assert result.value is not None
    assert result.value <= 5.0
    assert result.warnings is not None
    assert "anti_min_postcheck_replaced_with_min_side_tip" in result.warnings
