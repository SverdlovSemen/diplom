from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np


def _point_on_circle(cx: int, cy: int, radius: int, angle_deg: float) -> tuple[int, int]:
    rad = math.radians(angle_deg)
    x = int(round(cx + radius * math.cos(rad)))
    y = int(round(cy - radius * math.sin(rad)))
    return x, y


def _draw_frame(angle_deg: float, value: int, size: tuple[int, int] = (640, 480)) -> np.ndarray:
    width, height = size
    img = np.full((height, width, 3), 245, dtype=np.uint8)

    cx, cy = width // 2, int(height * 0.62)
    gauge_radius = min(width, height) // 3

    cv2.circle(img, (cx, cy), gauge_radius + 12, (230, 230, 230), -1)
    cv2.circle(img, (cx, cy), gauge_radius, (255, 255, 255), -1)
    cv2.circle(img, (cx, cy), gauge_radius, (70, 70, 70), 3)

    # Scale arc: from 225 deg (min) to -45 deg (max).
    start_angle = 225
    end_angle = -45
    tick_angles = np.linspace(start_angle, end_angle, 11)
    for idx, ang in enumerate(tick_angles):
        outer = _point_on_circle(cx, cy, gauge_radius - 5, float(ang))
        inner_len = 26 if idx % 5 == 0 else 16
        inner = _point_on_circle(cx, cy, gauge_radius - inner_len, float(ang))
        cv2.line(img, outer, inner, (130, 130, 130), 2, cv2.LINE_AA)

    # Draw min/max labels to make the frame look realistic.
    min_pos = _point_on_circle(cx, cy, gauge_radius - 46, start_angle)
    max_pos = _point_on_circle(cx, cy, gauge_radius - 46, end_angle)
    cv2.putText(img, "0", (min_pos[0] - 6, min_pos[1] + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.putText(img, "100", (max_pos[0] - 18, max_pos[1] + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.putText(img, "bar", (cx - 18, cy - 42), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (30, 30, 30), 2, cv2.LINE_AA)

    # Needle and center cap.
    needle_tip = _point_on_circle(cx, cy, gauge_radius - 34, angle_deg)
    cv2.line(img, (cx, cy), needle_tip, (10, 10, 10), 6, cv2.LINE_AA)
    cv2.circle(img, (cx, cy), 10, (10, 10, 10), -1)
    cv2.circle(img, (cx, cy), 4, (220, 220, 220), -1)

    value_text = f"{value:03d}"
    cv2.putText(img, value_text, (cx - 45, cy + 88), cv2.FONT_HERSHEY_DUPLEX, 1.2, (35, 35, 35), 2, cv2.LINE_AA)
    cv2.putText(img, "logger-1 analog test", (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (60, 60, 60), 2, cv2.LINE_AA)

    return img


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "test_images" / "analog_sequence"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 8 positions from low to high and back-like feel in loop.
    values = [8, 20, 34, 48, 62, 74, 86, 96]
    angles = np.linspace(225, -45, len(values))
    width, height = (640, 480)
    center = {"x": width // 2, "y": int(height * 0.62)}
    needle_radius = min(width, height) // 3 - 34
    min_point_xy = _point_on_circle(center["x"], center["y"], needle_radius, 225.0)
    max_point_xy = _point_on_circle(center["x"], center["y"], needle_radius, -45.0)

    for i, (value, angle) in enumerate(zip(values, angles), start=1):
        frame = _draw_frame(float(angle), int(value))
        out_file = out_dir / f"frame_{i:02d}.jpg"
        ok = cv2.imwrite(str(out_file), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise RuntimeError(f"Failed to write: {out_file}")

    calibration = {
        "center": center,
        "min_point": {"x": int(min_point_xy[0]), "y": int(min_point_xy[1])},
        "max_point": {"x": int(max_point_xy[0]), "y": int(max_point_xy[1])},
        "min_value": 0,
        "max_value": 100,
    }
    (out_dir / "calibration_logger1.json").write_text(
        json.dumps(calibration, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    print(f"Generated {len(values)} frames in: {out_dir}")


if __name__ == "__main__":
    main()
