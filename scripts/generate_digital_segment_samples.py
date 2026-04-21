from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def make_frame(text: str, w: int = 960, h: int = 360) -> np.ndarray:
    frame = np.full((h, w, 3), (148, 186, 146), dtype=np.uint8)
    cv2.rectangle(frame, (18, 18), (w - 18, h - 18), (118, 154, 118), thickness=4)
    cv2.putText(
        frame,
        text,
        (56, int(h * 0.72)),
        cv2.FONT_HERSHEY_SIMPLEX,
        4.0,
        (10, 10, 10),
        12,
        cv2.LINE_AA,
    )
    return frame


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "test_images" / "digital_segment"
    out_dir.mkdir(parents=True, exist_ok=True)

    samples = [
        "12.3",
        "47.8",
        "105.2",
        "888.8",
        "-12.4",
    ]

    for idx, text in enumerate(samples, start=1):
        img = make_frame(text)
        out_path = out_dir / f"frame_{idx:02d}.jpg"
        cv2.imwrite(str(out_path), img, [cv2.IMWRITE_JPEG_QUALITY, 95])

    print(f"Generated {len(samples)} files in: {out_dir}")


if __name__ == "__main__":
    main()
