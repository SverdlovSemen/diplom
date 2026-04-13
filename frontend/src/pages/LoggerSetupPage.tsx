import React from "react";
import { useParams } from "react-router-dom";
import { getLogger, updateLogger } from "../api/loggers";
import { buildMediaUrl } from "../api/media";
import { captureNow, listMeasurements, testRecognize, type Measurement, type TestRecognizeResult } from "../api/measurements";
import type { GaugeType } from "../api/loggers";

const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

const DEFAULT_SNAPSHOT_ERROR = "Нет активного потока. Запустите трансляцию.";
const SNAPSHOT_FETCH_TIMEOUT_MS = 47_000;

type RoiRect = { x: number; y: number; w: number; h: number };
type Point = { x: number; y: number };
type ToolMode = "roi" | "center" | "min" | "max";
type CalibrationData = {
  center?: Point;
  min_point?: Point;
  max_point?: Point;
  min_value?: number;
  max_value?: number;
};
type QualitySummary = {
  samples: number;
  okCount: number;
  failCount: number;
  minValue: number | null;
  maxValue: number | null;
  meanValue: number | null;
  rangeValue: number | null;
  pass: boolean;
  notes: string[];
};

function qualityRecommendations(summary: QualitySummary): string[] {
  const tips: string[] = [];
  if (summary.failCount > 0) tips.push("Reduce ROI to dial only; avoid background and labels.");
  if (summary.rangeValue != null && summary.rangeValue < 0.5) tips.push("Needle likely locked on edge: re-place center/min/max points.");
  if (summary.notes.some((n) => n.includes("needle_not_found"))) tips.push("Increase contrast or reduce glare, then recalibrate points.");
  if (summary.notes.some((n) => n.includes("tip_outside_minmax_span"))) tips.push("Min/max points likely swapped or off-span.");
  if (summary.notes.some((n) => n.includes("tip_from_dark_pixels_fallback"))) tips.push("Detector is using fallback often; tighten ROI and improve lighting.");
  if (tips.length === 0) tips.push("Calibration quality looks good.");
  return tips;
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

function normalizeRect(a: { x: number; y: number }, b: { x: number; y: number }): RoiRect {
  const x1 = Math.min(a.x, b.x);
  const y1 = Math.min(a.y, b.y);
  const x2 = Math.max(a.x, b.x);
  const y2 = Math.max(a.y, b.y);
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk) as unknown as number[]);
  }
  return btoa(binary);
}

function safeParseRoiJson(value: string): RoiRect | null {
  try {
    const obj = JSON.parse(value) as Partial<RoiRect>;
    if (typeof obj.x !== "number" || typeof obj.y !== "number" || typeof obj.w !== "number" || typeof obj.h !== "number") return null;
    if (!Number.isFinite(obj.x) || !Number.isFinite(obj.y) || !Number.isFinite(obj.w) || !Number.isFinite(obj.h)) return null;
    return { x: obj.x, y: obj.y, w: obj.w, h: obj.h };
  } catch {
    return null;
  }
}

function safeParseCalibrationJson(value: string): CalibrationData | null {
  try {
    const raw = JSON.parse(value) as CalibrationData;
    if (!raw || typeof raw !== "object") return null;
    return raw;
  } catch {
    return null;
  }
}

/** backend возвращает tip_point в координатах вырезанного ROI; оверлей на полном snapshot — в координатах всего кадра. */
function tipPointToFullImage(tip: { x: number; y: number }, roi: RoiRect | null): { x: number; y: number } {
  if (!roi) return { x: tip.x, y: tip.y };
  return { x: tip.x + roi.x, y: tip.y + roi.y };
}

export function LoggerSetupPage(): React.ReactElement {
  const { loggerId } = useParams();
  const [gaugeType, setGaugeType] = React.useState<GaugeType>("analog");
  const [roiJson, setRoiJson] = React.useState('{"x": 0, "y": 0, "w": 640, "h": 480}');
  const [calibrationJson, setCalibrationJson] = React.useState(
    '{"center":{"x":320,"y":297},"min_point":{"x":231,"y":386},"max_point":{"x":409,"y":386},"min_value":0,"max_value":100}',
  );
  const [measurements, setMeasurements] = React.useState<Measurement[]>([]);
  const [status, setStatus] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const [snapshotTs, setSnapshotTs] = React.useState<number>(() => Date.now());
  /** Blob URL from GET snapshot (avoids silent broken image on 4xx/503). */
  const [snapshotObjectUrl, setSnapshotObjectUrl] = React.useState<string | null>(null);
  const [snapshotLoading, setSnapshotLoading] = React.useState(false);
  const [snapshotLoadError, setSnapshotLoadError] = React.useState<string | null>(null);
  const imgRef = React.useRef<HTMLImageElement | null>(null);
  const [imgDims, setImgDims] = React.useState<{ w: number; h: number } | null>(null);

  const [roiRect, setRoiRect] = React.useState<RoiRect | null>(null);
  const [dragStart, setDragStart] = React.useState<{ x: number; y: number } | null>(null);

  const [testResult, setTestResult] = React.useState<TestRecognizeResult | null>(null);
  const [testing, setTesting] = React.useState(false);
  const [toolMode, setToolMode] = React.useState<ToolMode>("roi");
  const [centerPoint, setCenterPoint] = React.useState<Point | null>(null);
  const [minPoint, setMinPoint] = React.useState<Point | null>(null);
  const [maxPoint, setMaxPoint] = React.useState<Point | null>(null);
  const [minScaleValue, setMinScaleValue] = React.useState<number>(0);
  const [maxScaleValue, setMaxScaleValue] = React.useState<number>(100);
  const [loadedRoiJson, setLoadedRoiJson] = React.useState<string | null>(null);
  const [calibrationDirtyByRoi, setCalibrationDirtyByRoi] = React.useState(false);
  const [qualityRunning, setQualityRunning] = React.useState(false);
  const [qualitySummary, setQualitySummary] = React.useState<QualitySummary | null>(null);
  const [configSaved, setConfigSaved] = React.useState(false);

  const refresh = React.useCallback(async () => {
    if (!loggerId) return;
    try {
      const logger = await getLogger(loggerId);
      setGaugeType(logger.gauge_type);
      if (logger.roi_json) setRoiJson(logger.roi_json);
      if (logger.calibration_json) setCalibrationJson(logger.calibration_json);
      setLoadedRoiJson(logger.roi_json ?? null);
      setCalibrationDirtyByRoi(false);
      setConfigSaved(true);
      const parsedCal = safeParseCalibrationJson(logger.calibration_json ?? "");
      if (parsedCal?.center) setCenterPoint(parsedCal.center);
      if (parsedCal?.min_point) setMinPoint(parsedCal.min_point);
      if (parsedCal?.max_point) setMaxPoint(parsedCal.max_point);
      if (typeof parsedCal?.min_value === "number" && Number.isFinite(parsedCal.min_value)) setMinScaleValue(parsedCal.min_value);
      if (typeof parsedCal?.max_value === "number" && Number.isFinite(parsedCal.max_value)) setMaxScaleValue(parsedCal.max_value);
      setMeasurements((await listMeasurements({ loggerId, limit: 20 })).items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load setup data");
    }
  }, [loggerId]);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  React.useEffect(() => {
    const parsed = safeParseRoiJson(roiJson);
    if (parsed) setRoiRect(parsed);
  }, [roiJson]);

  React.useEffect(() => {
    if (loadedRoiJson == null) return;
    const dirty = loadedRoiJson !== roiJson;
    setCalibrationDirtyByRoi(dirty);
    if (dirty) setConfigSaved(false);
  }, [loadedRoiJson, roiJson]);

  const hasRoi = React.useMemo(() => {
    const r = safeParseRoiJson(roiJson);
    return !!r && r.w >= 5 && r.h >= 5;
  }, [roiJson]);
  const hasCenter = centerPoint != null;
  const hasMin = minPoint != null;
  const hasMax = maxPoint != null;
  const hasScale = Number.isFinite(minScaleValue) && Number.isFinite(maxScaleValue) && minScaleValue !== maxScaleValue;
  const analogCalibrationReady = hasRoi && hasCenter && hasMin && hasMax && hasScale && !calibrationDirtyByRoi;
  const calibrationReady = gaugeType === "analog" ? analogCalibrationReady : hasRoi;
  const recommendedTool: ToolMode = !hasRoi ? "roi" : !hasCenter ? "center" : !hasMin ? "min" : !hasMax ? "max" : "roi";

  React.useEffect(() => {
    const payload: CalibrationData = {};
    if (centerPoint) payload.center = { x: Math.round(centerPoint.x), y: Math.round(centerPoint.y) };
    if (minPoint) payload.min_point = { x: Math.round(minPoint.x), y: Math.round(minPoint.y) };
    if (maxPoint) payload.max_point = { x: Math.round(maxPoint.x), y: Math.round(maxPoint.y) };
    if (Number.isFinite(minScaleValue)) payload.min_value = minScaleValue;
    if (Number.isFinite(maxScaleValue)) payload.max_value = maxScaleValue;
    setCalibrationJson(JSON.stringify(payload));
  }, [centerPoint, minPoint, maxPoint, minScaleValue, maxScaleValue]);

  React.useEffect(() => {
    if (gaugeType !== "analog") return;
    setToolMode(recommendedTool);
  }, [recommendedTool, gaugeType]);

  React.useEffect(() => {
    if (!loggerId) return;
    let cancelled = false;
    let createdBlobUrl: string | null = null;
    const url = `${apiBase}/api/v1/processing/loggers/${loggerId}/snapshot?ts=${snapshotTs}`;
    setTestResult(null);
    setQualitySummary(null);
    setSnapshotLoading(true);
    setSnapshotLoadError(null);
    setSnapshotObjectUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });

    void (async () => {
      const ac = new AbortController();
      const to = window.setTimeout(() => ac.abort(), SNAPSHOT_FETCH_TIMEOUT_MS);
      try {
        const res = await fetch(url, { signal: ac.signal });
        if (!res.ok) {
          let detail = res.status === 503 ? DEFAULT_SNAPSHOT_ERROR : `Снимок недоступен (${res.status})`;
          try {
            const j = (await res.json()) as { detail?: unknown };
            if (typeof j.detail === "string") detail = j.detail;
          } catch {
            /* ignore non-JSON body */
          }
          if (!cancelled) setSnapshotLoadError(detail);
          return;
        }
        const buf = await res.arrayBuffer();
        const u8 = new Uint8Array(buf);
        if (u8.length < 256 || u8[0] !== 0xff || u8[1] !== 0xd8) {
          if (!cancelled) setSnapshotLoadError("Сервер вернул не JPEG (возможно, поток мёртв). Обновите страницу или запустите publisher.");
          return;
        }
        const blob = new Blob([buf], { type: "image/jpeg" });
        const obj = URL.createObjectURL(blob);
        if (cancelled) {
          URL.revokeObjectURL(obj);
          return;
        }
        createdBlobUrl = obj;
        setSnapshotObjectUrl(obj);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof DOMException && e.name === "AbortError") {
          setSnapshotLoadError(
            "Превышено время ожидания снимка (~45 с). RTMP не отвечает — проверьте publisher и http://localhost:8080/stat",
          );
        } else {
          setSnapshotLoadError("Не удалось загрузить снимок. Проверьте, что backend доступен.");
        }
      } finally {
        window.clearTimeout(to);
        if (!cancelled) setSnapshotLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      if (createdBlobUrl) {
        URL.revokeObjectURL(createdBlobUrl);
        createdBlobUrl = null;
      }
    };
  }, [loggerId, snapshotTs, apiBase]);

  function updateRoiFromRect(rect: RoiRect): void {
    const next = {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      w: Math.round(rect.w),
      h: Math.round(rect.h),
    };
    setTestResult(null);
    setQualitySummary(null);
    setRoiJson(JSON.stringify(next));
    setRoiRect(next);
  }

  function toImageCoords(clientX: number, clientY: number): { x: number; y: number } | null {
    const img = imgRef.current;
    const dims = imgDims;
    if (!img || !dims) return null;
    const r = img.getBoundingClientRect();
    const dx = clientX - r.left;
    const dy = clientY - r.top;
    const x = (dx / r.width) * dims.w;
    const y = (dy / r.height) * dims.h;
    return { x: clamp(x, 0, dims.w), y: clamp(y, 0, dims.h) };
  }

  function onMouseDown(e: React.MouseEvent<HTMLDivElement>): void {
    if (toolMode !== "roi") return;
    const p = toImageCoords(e.clientX, e.clientY);
    if (!p) return;
    setError(null);
    setStatus(null);
    setDragStart(p);
    setRoiRect({ x: p.x, y: p.y, w: 1, h: 1 });
  }

  function onMouseMove(e: React.MouseEvent<HTMLDivElement>): void {
    if (toolMode !== "roi") return;
    if (!dragStart) return;
    const p = toImageCoords(e.clientX, e.clientY);
    if (!p) return;
    const rect = normalizeRect(dragStart, p);
    setRoiRect(rect);
  }

  function onMouseUp(): void {
    if (toolMode !== "roi") return;
    if (!dragStart || !roiRect) return;
    setDragStart(null);
    // минимальные размеры, чтобы не сохранить случайный клик
    if (roiRect.w < 5 || roiRect.h < 5) return;
    updateRoiFromRect(roiRect);
  }

  function onCanvasClick(e: React.MouseEvent<HTMLDivElement>): void {
    if (toolMode === "roi") return;
    const p = toImageCoords(e.clientX, e.clientY);
    if (!p) return;
    if (toolMode === "center") {
      setCenterPoint(p);
      setToolMode("min");
    }
    if (toolMode === "min") {
      setMinPoint(p);
      setToolMode("max");
    }
    if (toolMode === "max") {
      setMaxPoint(p);
    }
  }

  async function saveConfig(): Promise<void> {
    if (!loggerId) return;
    setError(null);
    setStatus(null);
    try {
      JSON.parse(roiJson);
      if (gaugeType === "digital") {
        await updateLogger(loggerId, {
          roi_json: roiJson,
          calibration_json: null,
          gauge_type: "digital",
        });
      } else {
        const parsedCal = JSON.parse(calibrationJson) as CalibrationData;
        if (!parsedCal.center || !parsedCal.min_point || !parsedCal.max_point) {
          throw new Error("Calibration needs center, min_point and max_point");
        }
        if (typeof parsedCal.min_value !== "number" || typeof parsedCal.max_value !== "number") {
          throw new Error("Calibration needs numeric min_value and max_value");
        }
        await updateLogger(loggerId, {
          roi_json: roiJson,
          calibration_json: calibrationJson,
          gauge_type: "analog",
        });
      }
      setStatus("Configuration saved");
      setLoadedRoiJson(roiJson);
      setCalibrationDirtyByRoi(false);
      setConfigSaved(true);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save configuration");
    }
  }

  async function runTestCapture(): Promise<void> {
    if (!loggerId) return;
    setError(null);
    setStatus(null);
    try {
      const m = await captureNow(loggerId);
      const rangeNote =
        m.out_of_range === true ? " · вне допустимого диапазона" : m.out_of_range === false ? " · в диапазоне" : "";
      setStatus(m.ok ? `Captured: ${m.value ?? "n/a"} ${m.unit}${rangeNote}` : `Capture error: ${m.error ?? "unknown"}`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Capture failed");
    }
  }

  async function runTestRecognizeProductionParity(): Promise<void> {
    if (!loggerId) return;
    setError(null);
    setStatus(null);
    setTesting(true);
    setTestResult(null);
    try {
      const r = await testRecognize(loggerId, { production_parity: true });
      setTestResult(r);
      const line = r.ok
        ? `Production parity: ${r.value ?? "n/a"} (frame_source=${r.frame_source ?? "?"})`
        : `Production parity error: ${r.error ?? "unknown"}`;
      setStatus(line);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Production parity test failed");
    } finally {
      setTesting(false);
    }
  }

  async function runTestRecognize(): Promise<void> {
    if (!loggerId) return;
    setError(null);
    setStatus(null);
    setTesting(true);
    try {
      let frame_jpeg_base64: string | undefined;
      if (snapshotObjectUrl) {
        const blob = await fetch(snapshotObjectUrl).then((res) => res.blob());
        frame_jpeg_base64 = arrayBufferToBase64(await blob.arrayBuffer());
      }
      const r = await testRecognize(loggerId, {
        frame_jpeg_base64,
        roi_json: roiJson,
        calibration_json: gaugeType === "analog" ? calibrationJson : undefined,
      });
      setTestResult(r);
      let line = r.ok ? `Recognized: ${r.value ?? "n/a"}` : `Recognize error: ${r.error ?? "unknown"}`;
      if (r.ocr_raw != null && r.ocr_raw !== "") {
        line += ` · OCR raw: «${r.ocr_raw}»`;
      }
      if (r.frame_source === "client_jpeg") {
        line += " (frame = current snapshot)";
      } else if (r.frame_source === "rtmp_capture") {
        line += " (new RTMP frame — refresh snapshot to match)";
      }
      setStatus(line);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Test recognize failed");
    } finally {
      setTesting(false);
    }
  }

  async function runQualityTestSeries(samples = 6): Promise<void> {
    if (!loggerId) return;
    if (gaugeType === "analog" && !calibrationReady) {
      setError("Finish ROI + center/min/max + scale values (ROI must match saved frame if you changed ROI)");
      return;
    }
    setQualityRunning(true);
    setError(null);
    setStatus(null);
    setQualitySummary(null);
    try {
      const values: number[] = [];
      let okCount = 0;
      let failCount = 0;
      const notes = new Set<string>();
      for (let i = 0; i < samples; i += 1) {
        const r = await testRecognize(loggerId, {
          roi_json: roiJson,
          calibration_json: calibrationJson,
        });
        if (r.ok && typeof r.value === "number" && Number.isFinite(r.value)) {
          values.push(r.value);
          okCount += 1;
        } else {
          failCount += 1;
          if (r.error) notes.add(r.error);
        }
        for (const w of r.analog_debug?.warnings ?? []) notes.add(w);
        await new Promise((resolve) => window.setTimeout(resolve, 600));
      }
      const minV = values.length ? Math.min(...values) : null;
      const maxV = values.length ? Math.max(...values) : null;
      const meanV = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
      const rangeV = minV != null && maxV != null ? maxV - minV : null;
      const clippedExtremeCount = values.filter((v) => v <= minScaleValue + 0.05 || v >= maxScaleValue - 0.05).length;
      const clippedRatio = values.length > 0 ? clippedExtremeCount / values.length : 1;
      if (clippedRatio > 0.6) notes.add("too_many_extreme_values");
      const fallbackCount = [...notes].filter((n) => n.includes("tip_from_dark_pixels_fallback")).length;
      const pass =
        okCount >= Math.max(5, Math.floor(samples * 0.8)) &&
        (rangeV == null || rangeV > 0.5) &&
        clippedRatio <= 0.6 &&
        fallbackCount <= 2;
      const summary: QualitySummary = {
        samples,
        okCount,
        failCount,
        minValue: minV == null ? null : Number(minV.toFixed(3)),
        maxValue: maxV == null ? null : Number(maxV.toFixed(3)),
        meanValue: meanV == null ? null : Number(meanV.toFixed(3)),
        rangeValue: rangeV == null ? null : Number(rangeV.toFixed(3)),
        pass,
        notes: [...notes].slice(0, 8),
      };
      setQualitySummary(summary);
      setStatus(pass ? "Quality test passed" : "Quality test needs recalibration (see notes)");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Quality test failed");
    } finally {
      setQualityRunning(false);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Logger setup</h1>

      <div className="rounded-lg border bg-white p-4 space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="font-medium">ROI setup</div>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            Gauge type
            <select
              className="rounded border px-2 py-1"
              value={gaugeType}
              onChange={(e) => {
                setGaugeType(e.target.value as GaugeType);
                setConfigSaved(false);
              }}
            >
              <option value="analog">analog</option>
              <option value="digital">digital</option>
            </select>
          </label>
        </div>
        <p className="text-xs text-slate-600 max-w-3xl">
          «Test recognize» использует кадр из снимка и при необходимости несохранённые ROI/калибровку из полей ниже. «Test as production» вызывает тот же путь CV, что и фоновый процесс: RTMP + только данные из БД после Save config (
          <span className="font-mono">production_parity</span>).
        </p>
        <div className="flex flex-wrap gap-2">
            <button className="rounded border px-3 py-2 text-sm" onClick={() => setSnapshotTs(Date.now())}>
              Refresh snapshot
            </button>
            <button
              className="rounded border px-3 py-2 text-sm"
              onClick={() => void runTestRecognize()}
              disabled={
                testing ||
                snapshotLoading ||
                !snapshotObjectUrl ||
                (gaugeType === "analog" && !calibrationReady)
              }
            >
              {testing ? "Testing…" : "Test recognize"}
            </button>
            <button
              className="rounded border border-slate-900 px-3 py-2 text-sm font-medium"
              type="button"
              title="Тот же recognize_from_image(image, logger), что и у фонового воркера; нужны сохранённые ROI/калибровка"
              onClick={() => void runTestRecognizeProductionParity()}
              disabled={testing || !configSaved || calibrationDirtyByRoi}
            >
              {testing ? "Testing…" : "Test as production"}
            </button>
            {gaugeType === "analog" ? (
              <button className="rounded border px-3 py-2 text-sm" onClick={() => void runQualityTestSeries(6)} disabled={qualityRunning || !calibrationReady}>
                {qualityRunning ? "Quality…" : "Quality x6"}
              </button>
            ) : null}
        </div>
        {gaugeType === "analog" ? (
          <div className="flex flex-wrap gap-2">
            <button className={`rounded border px-3 py-1.5 text-xs ${toolMode === "roi" ? "bg-slate-900 text-white" : ""}`} onClick={() => setToolMode("roi")}>
              1) ROI
            </button>
            <button
              className={`rounded border px-3 py-1.5 text-xs ${toolMode === "center" ? "bg-slate-900 text-white" : ""}`}
              onClick={() => setToolMode("center")}
              disabled={!hasRoi}
            >
              2) Center
            </button>
            <button
              className={`rounded border px-3 py-1.5 text-xs ${toolMode === "min" ? "bg-slate-900 text-white" : ""}`}
              onClick={() => setToolMode("min")}
              disabled={!hasCenter}
            >
              3) Min point
            </button>
            <button
              className={`rounded border px-3 py-1.5 text-xs ${toolMode === "max" ? "bg-slate-900 text-white" : ""}`}
              onClick={() => setToolMode("max")}
              disabled={!hasMin}
            >
              4) Max point
            </button>
          </div>
        ) : null}
        <div className="text-sm text-slate-600">
          {gaugeType === "analog"
            ? `${toolMode === "roi" ? "Drag on the image to select ROI." : "Click on the image to place calibration point."} Then click “Save config”.`
            : "Drag on the image to select ROI. Then click “Save config”."}
        </div>
        {gaugeType === "analog" ? (
          <div className="rounded border bg-blue-50 px-3 py-2 text-xs text-blue-900">
            Current step: {recommendedTool === "roi" ? "Select ROI" : recommendedTool === "center" ? "Click center" : recommendedTool === "min" ? "Click minimum point" : "Click maximum point"}
          </div>
        ) : null}
        {gaugeType === "analog" ? (
          <div className="rounded border bg-slate-50 px-3 py-2 text-xs text-slate-700">
            Steps: ROI {hasRoi ? "OK" : "TODO"} {"->"} Center {hasCenter ? "OK" : "TODO"} {"->"} Min {hasMin ? "OK" : "TODO"} {"->"} Max{" "}
            {hasMax ? "OK" : "TODO"} {"->"} Scale {hasScale ? "OK" : "TODO"} {"->"} Save {configSaved && !calibrationDirtyByRoi ? "OK" : "TODO"}.
          </div>
        ) : null}
        {gaugeType === "analog" && calibrationDirtyByRoi ? (
          <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            ROI changed since last save. Calibration points may be invalid for this ROI - place points again and save config.
          </div>
        ) : null}

        <div
          className="relative inline-block select-none"
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseUp}
          onClick={onCanvasClick}
        >
          {snapshotLoading ? (
            <div className="text-sm text-slate-500 py-6">
              Загрузка снимка… (захват RTMP, обычно до ~45 с; если долго — смотрите /stat и контейнер ffmpeg-test)
            </div>
          ) : null}
          {snapshotLoadError ? (
            <div className="rounded border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 max-w-xl">{snapshotLoadError}</div>
          ) : null}
          {snapshotObjectUrl ? (
            <img
              ref={imgRef}
              src={snapshotObjectUrl}
              alt="snapshot"
              className="max-w-full rounded border bg-slate-50"
              onError={() => {
                setSnapshotLoadError("Браузер не смог показать снимок. Нажмите «Refresh snapshot».");
                setSnapshotObjectUrl((prev) => {
                  if (prev) URL.revokeObjectURL(prev);
                  return null;
                });
              }}
              onLoad={(e) => {
                const el = e.currentTarget;
                setImgDims({ w: el.naturalWidth, h: el.naturalHeight });
                if (!safeParseRoiJson(roiJson) && el.naturalWidth > 0 && el.naturalHeight > 0) {
                  updateRoiFromRect({ x: 0, y: 0, w: el.naturalWidth, h: el.naturalHeight });
                }
              }}
            />
          ) : null}
          {roiRect && imgRef.current && imgDims ? (
            (() => {
              const img = imgRef.current!;
              const r = img.getBoundingClientRect();
              const sx = r.width / imgDims.w;
              const sy = r.height / imgDims.h;
              const left = roiRect.x * sx;
              const top = roiRect.y * sy;
              const width = roiRect.w * sx;
              const height = roiRect.h * sy;
              return (
                <div
                  className="absolute border-2 border-emerald-500 bg-emerald-500/10"
                  style={{ left, top, width, height }}
                />
              );
            })()
          ) : null}
          {gaugeType === "analog" && imgRef.current && imgDims && centerPoint ? (
            (() => {
              const img = imgRef.current!;
              const r = img.getBoundingClientRect();
              const sx = r.width / imgDims.w;
              const sy = r.height / imgDims.h;
              const left = centerPoint.x * sx - 6;
              const top = centerPoint.y * sy - 6;
              return <div className="absolute h-3 w-3 rounded-full bg-blue-600" style={{ left, top }} title="center" />;
            })()
          ) : null}
          {gaugeType === "analog" && imgRef.current && imgDims && minPoint ? (
            (() => {
              const img = imgRef.current!;
              const r = img.getBoundingClientRect();
              const sx = r.width / imgDims.w;
              const sy = r.height / imgDims.h;
              const left = minPoint.x * sx - 6;
              const top = minPoint.y * sy - 6;
              return <div className="absolute h-3 w-3 rounded-full bg-red-600" style={{ left, top }} title="min point" />;
            })()
          ) : null}
          {gaugeType === "analog" && imgRef.current && imgDims && maxPoint ? (
            (() => {
              const img = imgRef.current!;
              const r = img.getBoundingClientRect();
              const sx = r.width / imgDims.w;
              const sy = r.height / imgDims.h;
              const left = maxPoint.x * sx - 6;
              const top = maxPoint.y * sy - 6;
              return <div className="absolute h-3 w-3 rounded-full bg-violet-600" style={{ left, top }} title="max point" />;
            })()
          ) : null}
          {gaugeType === "analog" && imgRef.current && imgDims && testResult?.analog_debug?.tip_point ? (
            (() => {
              const img = imgRef.current!;
              const r = img.getBoundingClientRect();
              const sx = r.width / imgDims.w;
              const sy = r.height / imgDims.h;
              const tip = testResult.analog_debug?.tip_point;
              if (!tip) return null;
              const roi = roiRect ?? safeParseRoiJson(roiJson);
              const tipFull = tipPointToFullImage(tip, roi);
              const left = tipFull.x * sx - 5;
              const top = tipFull.y * sy - 5;
              return <div className="absolute h-2.5 w-2.5 rounded-full bg-emerald-600" style={{ left, top }} title="detected tip" />;
            })()
          ) : null}
          {gaugeType === "analog" && imgRef.current && imgDims ? (
            (() => {
              const img = imgRef.current!;
              const r = img.getBoundingClientRect();
              const sx = r.width / imgDims.w;
              const sy = r.height / imgDims.h;
              const tipRaw = testResult?.analog_debug?.tip_point ?? null;
              const roi = roiRect ?? safeParseRoiJson(roiJson);
              const tip = tipRaw ? tipPointToFullImage(tipRaw, roi) : null;
              const hasAnyLine = (centerPoint && minPoint) || (centerPoint && maxPoint) || (centerPoint && tip);
              if (!hasAnyLine) return null;
              const cx = centerPoint ? centerPoint.x * sx : null;
              const cy = centerPoint ? centerPoint.y * sy : null;
              const minx = minPoint ? minPoint.x * sx : null;
              const miny = minPoint ? minPoint.y * sy : null;
              const maxx = maxPoint ? maxPoint.x * sx : null;
              const maxy = maxPoint ? maxPoint.y * sy : null;
              const tipx = tip ? tip.x * sx : null;
              const tipy = tip ? tip.y * sy : null;
              return (
                <svg className="absolute inset-0 pointer-events-none" style={{ width: r.width, height: r.height }}>
                  {cx != null && cy != null && minx != null && miny != null ? <line x1={cx} y1={cy} x2={minx} y2={miny} stroke="#dc2626" strokeWidth={2} /> : null}
                  {cx != null && cy != null && maxx != null && maxy != null ? <line x1={cx} y1={cy} x2={maxx} y2={maxy} stroke="#7c3aed" strokeWidth={2} /> : null}
                  {cx != null && cy != null && tipx != null && tipy != null ? <line x1={cx} y1={cy} x2={tipx} y2={tipy} stroke="#059669" strokeWidth={2} strokeDasharray="5 3" /> : null}
                </svg>
              );
            })()
          ) : null}
        </div>

        {testResult?.roi_image ? (
          <div className="space-y-2">
            <div className="text-sm font-medium text-slate-700">ROI preview (cropped)</div>
            <img
              className="rounded border bg-slate-50"
              alt="roi"
              src={`data:image/jpeg;base64,${testResult.roi_image}`}
            />
          </div>
        ) : null}
        {testResult?.analog_debug ? (
          <div className="rounded border bg-slate-50 p-3 text-xs text-slate-700 space-y-1">
            <div>Analog debug: ratio={String(testResult.analog_debug.ratio ?? "n/a")} angle={String(testResult.analog_debug.angle ?? "n/a")}</div>
            <div>min_angle={String(testResult.analog_debug.min_angle ?? "n/a")} max_angle={String(testResult.analog_debug.max_angle ?? "n/a")} score={String(testResult.analog_debug.quality_score ?? "n/a")}</div>
            <div>warnings: {(testResult.analog_debug.warnings ?? []).join(", ") || "none"}</div>
          </div>
        ) : null}
        {(testResult?.cv_warnings?.length ?? 0) > 0 ? (
          <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950 space-y-1">
            <div className="font-medium">Предупреждения ROI / CV (ТЗ п.12, вариант 1)</div>
            <ul className="list-disc pl-4 space-y-0.5 font-mono">
              {(testResult?.cv_warnings ?? []).map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
            <div className="text-slate-600 normal-case">
              Пустой кроп — ошибка; мелкая зона или почти весь кадр — предупреждения. См. docs/tz_p12_detection_variant1_operator_roi.md
            </div>
          </div>
        ) : null}
        {gaugeType === "analog" && qualitySummary ? (
          <div className={`rounded border p-3 text-xs space-y-1 ${qualitySummary.pass ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
            <div>Quality gate: {qualitySummary.pass ? "PASS" : "RECALIBRATE"}</div>
            <div>samples={qualitySummary.samples}, ok={qualitySummary.okCount}, fail={qualitySummary.failCount}</div>
            <div>min={String(qualitySummary.minValue ?? "n/a")} max={String(qualitySummary.maxValue ?? "n/a")} mean={String(qualitySummary.meanValue ?? "n/a")} range={String(qualitySummary.rangeValue ?? "n/a")}</div>
            <div>notes: {qualitySummary.notes.join(", ") || "none"}</div>
            <div>actions: {qualityRecommendations(qualitySummary).join(" | ")}</div>
          </div>
        ) : null}
      </div>

      <div className="rounded-lg border bg-white p-4 space-y-3">
        <div className="text-sm text-slate-600">
          Logger ID: <span className="font-mono">{loggerId}</span>
        </div>
        <details className="rounded border bg-slate-50 p-3">
          <summary className="cursor-pointer text-sm font-medium">Advanced: ROI JSON</summary>
          <textarea
            className="mt-2 w-full rounded border p-2 font-mono text-xs"
            rows={5}
            value={roiJson}
            onChange={(e) => setRoiJson(e.target.value)}
          />
        </details>
        {gaugeType === "analog" ? (
          <>
            <label className="block">
              <span className="text-sm font-medium">Scale values</span>
              <div className="mt-1 grid grid-cols-2 gap-2">
                <input
                  className="rounded border p-2 text-sm"
                  type="number"
                  value={minScaleValue}
                  onChange={(e) => setMinScaleValue(Number(e.target.value))}
                  placeholder="min value"
                />
                <input
                  className="rounded border p-2 text-sm"
                  type="number"
                  value={maxScaleValue}
                  onChange={(e) => setMaxScaleValue(Number(e.target.value))}
                  placeholder="max value"
                />
              </div>
            </label>
            <label className="block">
              <span className="text-sm font-medium">Calibration JSON</span>
              <textarea
                className="mt-1 w-full rounded border p-2 font-mono text-xs"
                rows={7}
                value={calibrationJson}
                onChange={(e) => {
                  const txt = e.target.value;
                  setCalibrationJson(txt);
                  const parsed = safeParseCalibrationJson(txt);
                  if (!parsed) return;
                  setCenterPoint(parsed.center ?? null);
                  setMinPoint(parsed.min_point ?? null);
                  setMaxPoint(parsed.max_point ?? null);
                  if (typeof parsed.min_value === "number" && Number.isFinite(parsed.min_value)) setMinScaleValue(parsed.min_value);
                  if (typeof parsed.max_value === "number" && Number.isFinite(parsed.max_value)) setMaxScaleValue(parsed.max_value);
                }}
              />
            </label>
          </>
        ) : null}
        <div className="flex gap-2">
          <button
            className="rounded bg-slate-900 px-3 py-2 text-sm text-white disabled:opacity-50"
            onClick={() => void saveConfig()}
            disabled={gaugeType === "analog" ? !hasRoi || !hasCenter || !hasMin || !hasMax || !hasScale : !hasRoi}
          >
            Save config
          </button>
          <button className="rounded border px-3 py-2 text-sm" onClick={() => void runTestCapture()}>
            Test capture now
          </button>
        </div>
        {status ? <div className="text-sm text-green-700">{status}</div> : null}
        {error ? <div className="text-sm text-red-700">{error}</div> : null}
      </div>

      <div className="rounded-lg border bg-white">
        <div className="border-b px-4 py-3 text-sm font-medium text-slate-700">Last measurements</div>
        <div className="divide-y">
          {measurements.map((m) => (
            <div key={m.id} className="px-4 py-3 text-sm">
              <div>
                {m.ok ? `${m.value ?? "n/a"} ${m.unit}` : `Error: ${m.error ?? "unknown"}`}
                {m.out_of_range === true ? (
                  <span className="text-amber-800 font-medium"> · вне допустимого диапазона</span>
                ) : m.out_of_range === false ? (
                  <span className="text-slate-500"> · в диапазоне</span>
                ) : null}
                {m.cv_warnings_json ? (
                  <span className="text-xs text-slate-500" title={m.cv_warnings_json}>
                    {" "}
                    CV: {m.cv_warnings_json}
                  </span>
                ) : null}{" "}
                · {new Date(m.captured_at).toLocaleString()}
              </div>
              {m.image_path ? (
                <a className="underline underline-offset-4" target="_blank" rel="noreferrer" href={buildMediaUrl(m.image_path)}>
                  Open image
                </a>
              ) : null}
            </div>
          ))}
          {measurements.length === 0 ? <div className="px-4 py-3 text-slate-600">No measurements yet.</div> : null}
        </div>
      </div>
    </div>
  );
}

