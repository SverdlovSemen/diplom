## digital_segment real dataset

This folder contains real LCD meter photos for `digital_segment` validation:

- `image1.png`
- `image2.png`
- `image3.png`
- `image4.png`
- `image5.png`

Ground-truth values (see `expected_values.json`):

- `image1.png` -> `15.204`
- `image2.png` -> `0.0`
- `image3.png` -> `0.0002`
- `image4.png` -> `0.003`
- `image5.png` -> `0.0`

Validation notes:

- For acceptance, use at least 4 images from this set with known expected values.
- Decimal comma is accepted in source meter value; backend normalizes to dot.
- Negative values must preserve `-`.
- Keep ROI tight to LCD digits and exclude side labels (`MWh`, `MBTч`, `Gcal`).

### Validation strategy

- `digital_segment` now uses a staged pipeline: LCD screen localization -> digit row extraction -> numeric zone isolation -> seven-segment decoding.
- OCR is used only as fallback on a narrow crop with numeric token filtering.
- Production path still depends on operator ROI in Logger setup (`recognize_from_image` crops ROI first).
- Typical warnings for this mode: `segment_screen_not_found`, `segment_digit_row_not_found`, `segment_unit_text_intrusion`, `segment_low_confidence`, `segment_conflicting_candidates`.

Optional automated check from Docker test runner:

1. Copy `expected_values.example.json` to `expected_values.json` and fill real values (or use the committed `expected_values.json`).
2. Run:

```bash
docker compose -p gauge-reader-system -f docker-compose.yml -f docker-compose.tests.yml run --rm -v "C:/files/Projects/Python/diplom/gauge-reader-system/test_images/digital_segment_real:/real" backend-tests python tests/evaluate_digital_segment_real.py --images-dir /real --expected-json /real/expected_values.json --tolerance 0.0005
```

`--tolerance` should be small for readings like `0.0002` (e.g. `0.0005` or `1e-4`).

For final acceptance, always use **UI**: Refresh snapshot → tight ROI on LCD only → Save config → Test recognize → Test as production.
