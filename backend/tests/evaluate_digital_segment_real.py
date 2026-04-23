from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.cv import recognizer
from app.models.logger import GaugeType


def _read_expected(path: Path | None) -> dict[str, float]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for name, value in data.items():
        try:
            out[str(name)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _read_rois(path: Path | None) -> dict[str, dict[str, int]]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, int]] = {}
    for name, roi in data.items():
        if not isinstance(roi, dict):
            continue
        try:
            out[str(name)] = {
                "x": int(roi["x"]),
                "y": int(roi["y"]),
                "w": int(roi["w"]),
                "h": int(roi["h"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate digital_segment recognizer on real images.")
    parser.add_argument("--images-dir", default="/real", help="Directory with real images.")
    parser.add_argument("--expected-json", default="", help="Optional JSON file: {\"image.png\": 12.34}.")
    parser.add_argument("--rois-json", default="", help="Optional JSON file: {\"image.png\": {\"x\":1,\"y\":2,\"w\":3,\"h\":4}}.")
    parser.add_argument("--tolerance", type=float, default=1e-3, help="Absolute tolerance for value comparison.")
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    if not images_dir.exists():
        print(f"ERROR: images dir not found: {images_dir}")
        return 2

    expected = _read_expected(Path(args.expected_json)) if args.expected_json else {}
    rois = _read_rois(Path(args.rois_json)) if args.rois_json else {}
    files = sorted([p for p in images_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])
    if not files:
        print("ERROR: no image files found")
        return 2

    ok_count = 0
    cmp_count = 0
    cmp_pass = 0
    hard_failures = 0
    for fp in files:
        img = cv2.imread(str(fp))
        roi = rois.get(fp.name, {})
        logger = SimpleNamespace(
            gauge_type=GaugeType.digital_segment,
            roi_json=json.dumps(roi),
            calibration_json=None,
        )
        res = recognizer.recognize_from_image(img, logger)
        expected_val = expected.get(fp.name)
        reasons: list[str] = []
        within_tol = None
        delta = None
        token_counter = getattr(recognizer, "_LAST_SEGMENT_DEBUG", {}).get("token_counter", {})
        debug = getattr(recognizer, "_LAST_SEGMENT_DEBUG", {})

        if not res.ok:
            reasons.append("recognizer_not_ok")
        if expected_val is not None:
            cmp_count += 1
            if res.value is None:
                reasons.append("no_numeric_value")
            else:
                delta = abs(float(res.value) - expected_val)
                within_tol = delta <= args.tolerance
                if within_tol:
                    cmp_pass += 1
                else:
                    reasons.append(f"delta={delta:.6f}>tol={args.tolerance}")
        if expected_val is None:
            reasons.append("no_expected_value")
        warnings_joined = ",".join(res.warnings) if res.warnings else ""
        if warnings_joined:
            reasons.append(f"warnings={warnings_joined}")

        if not reasons or (reasons == [f"warnings={warnings_joined}"] and within_tol is True):
            status = "PASS"
        elif within_tol is True and res.ok:
            status = "PASS_WITH_WARNINGS"
        else:
            status = "FAIL"
            hard_failures += 1

        print(
            f"{fp.name}: {status} "
            f"pred={res.value} expected={expected_val} delta={delta} raw={res.ocr_raw!r} "
            f"counter={token_counter} "
            f"seg={debug.get('seg_token')!r} seg_conf={debug.get('seg_conf')} "
            f"digits={debug.get('expected_digit_count')} decimal={debug.get('decimal_after')} "
            f"screen={debug.get('screen_shape')} row={debug.get('row_shape')} boxes={debug.get('digit_boxes')} "
            f"top={debug.get('top_tokens')} reasons={';'.join(reasons) if reasons else '-'}"
        )
        if res.ok:
            ok_count += 1

    print(
        f"\nSummary: recognized={ok_count}/{len(files)} "
        f"compared={cmp_pass}/{cmp_count} hard_failures={hard_failures}"
    )
    return 0 if hard_failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
