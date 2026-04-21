from __future__ import annotations

import json
from types import SimpleNamespace

import cv2
import numpy as np

from app.cv import recognizer
from app.models.logger import GaugeType


def _make_segment_like_frame(text: str, *, w: int = 640, h: int = 220) -> np.ndarray:
    # LCD-like background: muted green.
    img = np.full((h, w, 3), (150, 185, 150), dtype=np.uint8)
    cv2.rectangle(img, (10, 10), (w - 10, h - 10), (130, 165, 130), thickness=3)
    cv2.putText(
        img,
        text,
        (30, int(h * 0.72)),
        cv2.FONT_HERSHEY_SIMPLEX,
        3.0,
        (15, 15, 15),
        8,
        cv2.LINE_AA,
    )
    return img


def _logger_stub(gauge_type: GaugeType, roi: dict[str, int] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        gauge_type=gauge_type,
        roi_json=json.dumps(roi or {}),
        calibration_json=None,
    )


def test_digital_segment_pipeline_prefers_numeric_token(monkeypatch) -> None:
    frame = _make_segment_like_frame("123.4")

    def fake_ocr(_img, config: str) -> str:
        if "--psm 7" in config:
            return "123.4"
        return "noise"

    def fake_ocr_data(_img, config: str, output_type):  # noqa: ANN001
        return {"conf": ["91"], "text": ["123.4"]}

    monkeypatch.setattr(recognizer.pytesseract, "image_to_string", fake_ocr)
    monkeypatch.setattr(recognizer.pytesseract, "image_to_data", fake_ocr_data)

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is True
    assert result.value == 123.4
    assert result.ocr_raw == "123.4"


def test_digital_segment_pipeline_handles_ocr_failure(monkeypatch) -> None:
    frame = _make_segment_like_frame("88.8")

    monkeypatch.setattr(recognizer.pytesseract, "image_to_string", lambda _img, config: "???")
    monkeypatch.setattr(
        recognizer.pytesseract,
        "image_to_data",
        lambda _img, config, output_type: {"conf": ["-1"], "text": [""]},
    )

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is False
    assert result.value is None
    assert result.error is not None
    assert "OCR segment failed" in result.error


def test_digital_segment_restores_leading_minus_when_ocr_misses_it(monkeypatch) -> None:
    frame = _make_segment_like_frame("-12.4")

    monkeypatch.setattr(recognizer.pytesseract, "image_to_string", lambda _img, config: "12.4")
    monkeypatch.setattr(
        recognizer.pytesseract,
        "image_to_data",
        lambda _img, config, output_type: {"conf": ["88"], "text": ["12.4"]},
    )

    result = recognizer._recognize_digital_segment(frame)
    assert result.ok is True
    assert result.value == -12.4


def test_recognize_from_image_routes_to_digital_segment(monkeypatch) -> None:
    frame = _make_segment_like_frame("456")
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


def test_recognize_from_image_keeps_old_digital_route(monkeypatch) -> None:
    frame = _make_segment_like_frame("789")
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
