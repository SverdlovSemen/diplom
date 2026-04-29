from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import pytesseract

from app.cv.recognizer import _recognize_digital, recognize_from_image
from app.models.logger import GaugeType


_DEFAULT_FIXTURE_DIR = Path(
    r"C:\Users\&\.cursor\projects\c-files-Projects-Python-diplom\assets"
)

_DIGITAL_61_CASES = [
    (
        "c__Users___AppData_Roaming_Cursor_User_workspaceStorage_658f3d8b4c5307da80e1597bd33c8953_images_image-b4b8fca9-687b-4cee-8b1e-2e1612408fb2.png",
        {"x": 0, "y": 540, "w": 525, "h": 280},
    ),
    (
        "c__Users___AppData_Roaming_Cursor_User_workspaceStorage_658f3d8b4c5307da80e1597bd33c8953_images_image-23fa202a-28a7-43bf-b8bb-93893175eaeb.png",
        {"x": 43, "y": 248, "w": 498, "h": 240},
    ),
    (
        "c__Users___AppData_Roaming_Cursor_User_workspaceStorage_658f3d8b4c5307da80e1597bd33c8953_images_image-ed1210ec-3b74-4e51-b2c3-4ef28843cfb6.png",
        {"x": 92, "y": 453, "w": 399, "h": 194},
    ),
    (
        "c__Users___AppData_Roaming_Cursor_User_workspaceStorage_658f3d8b4c5307da80e1597bd33c8953_images_image-4064c076-e5ca-4624-9790-b51dae341496.png",
        {"x": 84, "y": 406, "w": 410, "h": 195},
    ),
]


def _fixture_dir() -> Path:
    return Path(os.environ.get("DIGITAL_OCR_FIXTURE_DIR", str(_DEFAULT_FIXTURE_DIR)))


def _require_tesseract() -> None:
    try:
        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError:
        pytest.skip("Tesseract binary is not installed")


@pytest.mark.parametrize(("filename", "roi"), _DIGITAL_61_CASES)
def test_digital_ocr_recognizes_provided_61_frames(filename: str, roi: dict[str, int]) -> None:
    _require_tesseract()
    path = _fixture_dir() / filename
    if not path.exists():
        pytest.skip(f"digital OCR fixture is unavailable: {path}")

    image = cv2.imread(str(path))
    assert image is not None
    target = SimpleNamespace(
        gauge_type=GaugeType.digital,
        roi_json=json.dumps(roi),
        calibration_json=None,
        min_value=0.0,
        max_value=100.0,
    )

    result = recognize_from_image(image, target)

    assert result.ok is True, result
    assert result.value == pytest.approx(61.0, abs=0.5)
    assert result.value != pytest.approx(86.02253753322057, abs=0.01)


def test_digital_ocr_rejects_blank_frame_without_tesseract() -> None:
    image = np.zeros((180, 360, 3), dtype=np.uint8)

    result = _recognize_digital(image, bounds=(0.0, 100.0))

    assert result.ok is False
    assert result.value is None
    assert result.error in {
        "OCR не удалось распознать число",
        "Кадр невалиден для OCR (тёмный/однотонный/без цифр)",
    }


def test_digital_ocr_preserves_stable_negative_sign(monkeypatch) -> None:
    image = np.zeros((180, 360, 3), dtype=np.uint8)
    cv2.putText(image, "-12.4", (20, 115), cv2.FONT_HERSHEY_SIMPLEX, 2.4, (255, 255, 255), 8)
    raw_sequence = ["-12.4", "12.4"] * 12 + ["124"] * 8
    last_raw = {"value": ""}

    def fake_image_to_string(_img: np.ndarray, config: str) -> str:
        raw = raw_sequence.pop(0)
        last_raw["value"] = raw
        return raw

    def fake_image_to_data(_img: np.ndarray, config: str, output_type: object) -> dict[str, list[str]]:
        conf = "40" if last_raw["value"] == "12.4" else "0"
        return {"conf": [conf], "text": [last_raw["value"]]}

    monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(pytesseract, "image_to_data", fake_image_to_data)

    result = _recognize_digital(image, bounds=None)

    assert result.ok is True
    assert result.value == pytest.approx(-12.4)

