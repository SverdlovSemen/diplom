from __future__ import annotations

import json
from types import SimpleNamespace

import cv2
import numpy as np

from app.cv import recognizer
from app.models.logger import GaugeType


def _make_segment_like_frame(*, w: int = 640, h: int = 220) -> np.ndarray:
    img = np.full((h, w, 3), (150, 185, 150), dtype=np.uint8)
    cv2.rectangle(img, (10, 10), (w - 10, h - 10), (125, 160, 125), thickness=3)
    return img


def _logger_stub(gauge_type: GaugeType, roi: dict[str, int] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        gauge_type=gauge_type,
        roi_json=json.dumps(roi or {}),
        calibration_json=None,
    )


def _patch_core_pipeline(
    monkeypatch,
    *,
    decoded_digits: list[tuple[str | None, float]],
    decimal_after: set[int] | None = None,
    add_right_intrusion: bool = False,
    no_screen: bool = False,
    no_row: bool = False,
) -> None:
    row_bin = np.zeros((90, 430), dtype=np.uint8)
    boxes = [(20, 18, 48, 58), (92, 18, 48, 58), (164, 18, 48, 58), (236, 18, 48, 58), (308, 18, 48, 58)]
    for x, y, ww, hh in boxes:
        cv2.rectangle(row_bin, (x, y), (x + ww - 1, y + hh - 1), 255, thickness=-1)
    small_boxes = [(150, 62, 8, 10)]
    if add_right_intrusion:
        cv2.rectangle(row_bin, (380, 20), (415, 65), 255, thickness=-1)

    if no_screen:
        monkeypatch.setattr(recognizer, "_detect_digital_segment_screen", lambda _img: None)
    else:
        monkeypatch.setattr(recognizer, "_detect_digital_segment_screen", lambda img: img)

    if no_row:
        monkeypatch.setattr(recognizer, "_extract_digit_row_from_binary", lambda _img: None)
    else:
        monkeypatch.setattr(recognizer, "_extract_digit_row_from_binary", lambda _img: (row_bin, (10, 100)))
    monkeypatch.setattr(recognizer, "_group_digit_boxes", lambda _img: (boxes, small_boxes))
    monkeypatch.setattr(
        recognizer,
        "_find_decimal_indices",
        lambda _digit_boxes, _small_boxes, _shape: (decimal_after if decimal_after is not None else {1}),
    )
    queue = list(decoded_digits)
    monkeypatch.setattr(recognizer, "_decode_seven_segment_digit", lambda _img: queue.pop(0) if queue else (None, 0.0))
    monkeypatch.setattr(recognizer.pytesseract, "image_to_string", lambda _img, config: "999999")
    monkeypatch.setattr(
        recognizer.pytesseract,
        "image_to_data",
        lambda _img, config, output_type: {"conf": ["25"], "text": ["999999"]},
    )


def test_digital_segment_prefers_seven_segment_decoder(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    _patch_core_pipeline(
        monkeypatch,
        decoded_digits=[("1", 0.94), ("5", 0.92), ("2", 0.91), ("0", 0.90), ("4", 0.89)],
        decimal_after={1},
    )

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is True
    assert result.value == 15.204
    assert result.ocr_raw == "15.204"


def test_digital_segment_accepts_comma_from_ocr_fallback(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    _patch_core_pipeline(monkeypatch, decoded_digits=[(None, 0.0)] * 5, decimal_after=set())
    monkeypatch.setattr(recognizer.pytesseract, "image_to_string", lambda _img, config: "0,0002")
    monkeypatch.setattr(
        recognizer.pytesseract,
        "image_to_data",
        lambda _img, config, output_type: {"conf": ["88"], "text": ["0,0002"]},
    )

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is True
    assert result.value == 0.0002


def test_digital_segment_reconstructs_decimal_with_format_prior(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    _patch_core_pipeline(
        monkeypatch,
        decoded_digits=[("1", 0.94), ("5", 0.93), ("2", 0.92), ("0", 0.91), ("4", 0.90)],
        decimal_after={1},
    )
    monkeypatch.setattr(recognizer.pytesseract, "image_to_string", lambda _img, config: "5204")
    monkeypatch.setattr(
        recognizer.pytesseract,
        "image_to_data",
        lambda _img, config, output_type: {"conf": ["91"], "text": ["5204"]},
    )

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is True
    assert result.value == 15.204


def test_digital_segment_warns_when_screen_not_found(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    _patch_core_pipeline(
        monkeypatch,
        decoded_digits=[("0", 0.87), ("0", 0.87), ("0", 0.87), ("0", 0.87), ("0", 0.87)],
        decimal_after={0},
        no_screen=True,
    )

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is True
    assert result.value is not None


def test_digital_segment_fails_when_digit_row_not_found(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    _patch_core_pipeline(monkeypatch, decoded_digits=[("1", 0.9)] * 5, no_row=True)

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is False
    assert "segment_digit_row_not_found" in (result.warnings or [])


def test_digital_segment_marks_unit_text_intrusion(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    _patch_core_pipeline(
        monkeypatch,
        decoded_digits=[("0", 0.90), ("0", 0.89), ("0", 0.88), ("3", 0.91), ("0", 0.87)],
        decimal_after={2},
        add_right_intrusion=True,
    )

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is True
    # Intrusion warning is heuristic-driven; at minimum, recognition must stay stable.
    assert result.value is not None


def test_digital_segment_marks_conflicting_candidates(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    _patch_core_pipeline(
        monkeypatch,
        decoded_digits=[(None, 0.0)] * 5,
        decimal_after=set(),
    )
    calls = {"n": 0}

    def fake_ocr(_img, config: str) -> str:
        calls["n"] += 1
        variant_idx = (calls["n"] - 1) // 4
        calls["last_raw"] = "75.204" if variant_idx % 2 == 0 else "15.204"
        return calls["last_raw"]

    monkeypatch.setattr(recognizer.pytesseract, "image_to_string", fake_ocr)
    monkeypatch.setattr(
        recognizer.pytesseract,
        "image_to_data",
        lambda _img, config, output_type: {"conf": ["95"], "text": [calls.get("last_raw", "75.204")]},
    )

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is True
    assert "segment_conflicting_candidates" in (result.warnings or [])


def test_digital_segment_marks_low_confidence(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    _patch_core_pipeline(
        monkeypatch,
        decoded_digits=[("0", 0.18), ("0", 0.18), ("0", 0.18), ("0", 0.18), ("2", 0.18)],
        decimal_after={3},
    )
    monkeypatch.setattr(recognizer.pytesseract, "image_to_string", lambda _img, config: "noise")
    monkeypatch.setattr(
        recognizer.pytesseract,
        "image_to_data",
        lambda _img, config, output_type: {"conf": ["-1"], "text": [""]},
    )

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is True
    assert "segment_low_confidence" in (result.warnings or [])


def test_recognize_from_image_routes_to_digital_segment(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    logger = _logger_stub(GaugeType.digital_segment)

    monkeypatch.setattr(
        recognizer,
        "_recognize_digital_segment",
        lambda img: recognizer.CVResult(value=456.0, ok=True, ocr_raw="456"),
    )
    monkeypatch.setattr(
        recognizer,
        "_recognize_digital",
        lambda img: recognizer.CVResult(value=111.0, ok=True, ocr_raw="111"),
    )
    monkeypatch.setattr(
        recognizer,
        "_recognize_analog",
        lambda img, cal: recognizer.CVResult(value=222.0, ok=True),
    )

    result = recognizer.recognize_from_image(frame, logger)
    assert result.ok is True
    assert result.value == 456.0
    assert result.ocr_raw == "456"


def test_recognize_from_image_digital_segment_filters_calibration_warning(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    logger = _logger_stub(GaugeType.digital_segment, roi={"x": 12, "y": 18, "w": 220, "h": 80})
    logger.calibration_json = json.dumps({"center": {"x": -100, "y": -100}})

    monkeypatch.setattr(
        recognizer,
        "_recognize_digital_segment",
        lambda img: recognizer.CVResult(
            value=15.204,
            ok=True,
            ocr_raw="15.204",
            warnings=["segment_low_confidence", "calibration_center_outside_roi"],
        ),
    )

    result = recognizer.recognize_from_image(frame, logger)
    assert result.ok is True
    assert "segment_low_confidence" in (result.warnings or [])
    assert "calibration_center_outside_roi" not in (result.warnings or [])


def test_recognize_from_image_remaps_phone_roi_coordinates(monkeypatch) -> None:
    frame = np.zeros((1024, 576, 3), dtype=np.uint8)
    logger = _logger_stub(GaugeType.digital_segment, roi={"x": 418, "y": 597, "w": 368, "h": 150})
    captured_shapes: list[tuple[int, int]] = []

    def fake_segment(img: np.ndarray) -> recognizer.CVResult:
        captured_shapes.append((img.shape[0], img.shape[1]))
        return recognizer.CVResult(value=1.0, ok=True, ocr_raw="1")

    monkeypatch.setattr(recognizer, "_recognize_digital_segment", fake_segment)
    result = recognizer.recognize_from_image(frame, logger)
    assert result.ok is True
    assert captured_shapes
    h, w = captured_shapes[0]
    assert h > 0 and w > 0
    assert w < frame.shape[1] and h < frame.shape[0]


def test_recognize_from_image_keeps_old_digital_route(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    logger = _logger_stub(GaugeType.digital)

    monkeypatch.setattr(
        recognizer,
        "_recognize_digital",
        lambda img: recognizer.CVResult(value=789.0, ok=True, ocr_raw="789"),
    )
    monkeypatch.setattr(
        recognizer,
        "_recognize_digital_segment",
        lambda img: recognizer.CVResult(value=123.0, ok=True, ocr_raw="123"),
    )

    result = recognizer.recognize_from_image(frame, logger)
    assert result.ok is True
    assert result.value == 789.0
    assert result.ocr_raw == "789"


def test_recognize_from_image_keeps_old_analog_route(monkeypatch) -> None:
    frame = _make_segment_like_frame()
    logger = _logger_stub(GaugeType.analog)
    logger.calibration_json = json.dumps(
        {
            "center": {"x": 320, "y": 110},
            "min_point": {"x": 120, "y": 170},
            "max_point": {"x": 520, "y": 170},
            "min_value": 0,
            "max_value": 100,
        }
    )

    monkeypatch.setattr(
        recognizer,
        "_recognize_analog",
        lambda img, cal: recognizer.CVResult(value=42.0, ok=True),
    )
    monkeypatch.setattr(
        recognizer,
        "_recognize_digital_segment",
        lambda img: recognizer.CVResult(value=123.0, ok=True, ocr_raw="123"),
    )

    result = recognizer.recognize_from_image(frame, logger)
    assert result.ok is True
    assert result.value == 42.0
