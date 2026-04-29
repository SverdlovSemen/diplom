import React from "react";
import { useParams } from "react-router-dom";
import { getLogger, updateLogger } from "../api/loggers";
import { getStoredAccessToken } from "../api/client";
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
  const hasTooManyExtremes = summary.notes.includes("too_many_extreme_values");
  if (summary.failCount > 0) tips.push("Сузьте ROI до шкалы, исключите фон и подписи.");
  if (summary.rangeValue != null && summary.rangeValue < 0.3 && hasTooManyExtremes) {
    tips.push("Стрелка вероятно залипает у края: переустановите center/min/max точки.");
  }
  if (summary.notes.some((n) => n.includes("needle_not_found"))) tips.push("Увеличьте контраст или уменьшите блики, затем перекалибруйте точки.");
  if (summary.notes.some((n) => n.includes("tip_outside_minmax_span"))) tips.push("Точки min/max, вероятно, перепутаны или поставлены вне диапазона.");
  if (summary.notes.some((n) => n.includes("tip_from_dark_pixels_fallback"))) tips.push("Детектор часто использует fallback: сузьте ROI и улучшите освещение.");
  if (tips.length === 0) tips.push("Качество калибровки хорошее.");
  return tips;
}

function humanizeMeasurementError(error: string | null | undefined): string {
  if (!error) return "неизвестно";
  if (error.toLowerCase().includes("нереалистичный скачок")) return "измерение не принято";
  return error;
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
      setError(e instanceof Error ? e.message : "Не удалось загрузить данные настройки");
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
    if (gaugeType !== "digital") return;
    setToolMode("roi");
  }, [gaugeType]);

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
        const token = getStoredAccessToken();
        const res = await fetch(url, {
          signal: ac.signal,
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        });
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
          gauge_type: gaugeType,
        });
      } else {
        const parsedCal = JSON.parse(calibrationJson) as CalibrationData;
        if (!parsedCal.center || !parsedCal.min_point || !parsedCal.max_point) {
          throw new Error("Для калибровки нужны точки center, min_point и max_point");
        }
        if (typeof parsedCal.min_value !== "number" || typeof parsedCal.max_value !== "number") {
          throw new Error("Для калибровки нужны числовые значения min_value и max_value");
        }
        await updateLogger(loggerId, {
          roi_json: roiJson,
          calibration_json: calibrationJson,
          gauge_type: "analog",
        });
      }
      setStatus("Конфигурация сохранена");
      setLoadedRoiJson(roiJson);
      setCalibrationDirtyByRoi(false);
      setConfigSaved(true);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить конфигурацию");
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
      setStatus(
        m.ok
          ? `Снимок получен: ${m.value ?? "н/д"} ${m.unit}${rangeNote}`
          : `Ошибка захвата: ${humanizeMeasurementError(m.error)}`,
      );
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выполнить захват");
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
        ? `Сравнение с production: ${r.value ?? "н/д"} (источник кадра=${r.frame_source ?? "?"})`
        : `Ошибка сравнения с production: ${humanizeMeasurementError(r.error)}`;
      setStatus(line);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Тест сравнения с production не выполнен");
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
      let line = r.ok ? `Распознано: ${r.value ?? "н/д"}` : `Ошибка распознавания: ${humanizeMeasurementError(r.error)}`;
      if (r.frame_source === "client_jpeg") {
        line += " (кадр = текущий снимок)";
      } else if (r.frame_source === "rtmp_capture") {
        line += " (новый RTMP-кадр — обновите снимок для сверки)";
      }
      setStatus(line);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Тест распознавания не выполнен");
    } finally {
      setTesting(false);
    }
  }

  async function runQualityTestSeries(samples = 6): Promise<void> {
    if (!loggerId) return;
    if (gaugeType === "analog" && !calibrationReady) {
      setError("Завершите ROI + center/min/max + значения шкалы (ROI должен соответствовать сохраненному кадру после изменения)");
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
        (rangeV == null || rangeV > 0.3) &&
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
      setStatus(pass ? "Тест качества пройден" : "Тест качества требует перекалибровки (см. замечания)");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Тест качества не выполнен");
    } finally {
      setQualityRunning(false);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Настройка логера</h1>

      <div className="rounded-lg border bg-white p-4 space-y-3 sticky top-2 z-10">
        <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
          <div className="font-medium">Панель настройки и тестов</div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              Тип датчика
              <select
                className="rounded border px-2 py-1"
                value={gaugeType}
                onChange={(e) => {
                  setGaugeType(e.target.value as GaugeType);
                  setConfigSaved(false);
                }}
              >
                <option value="analog">аналоговый</option>
                <option value="digital">цифровой</option>
              </select>
            </label>
            {gaugeType === "analog" ? (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-slate-700">Шкала</span>
                <input
                  className="w-24 rounded border px-2 py-1"
                  type="number"
                  value={minScaleValue}
                  onChange={(e) => setMinScaleValue(Number(e.target.value))}
                  placeholder="min"
                />
                <span className="text-slate-500">→</span>
                <input
                  className="w-24 rounded border px-2 py-1"
                  type="number"
                  value={maxScaleValue}
                  onChange={(e) => setMaxScaleValue(Number(e.target.value))}
                  placeholder="max"
                />
              </div>
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="rounded border px-3 py-2 text-sm"
            onClick={() => setSnapshotTs(Date.now())}
            title="Запрашивает новый snapshot из RTMP и обновляет изображение на экране."
          >
            Обновить снимок
          </button>
          <button
            className="rounded border px-3 py-2 text-sm"
            onClick={() => void runTestRecognize()}
            title="Проверяет распознавание по текущему снимку/настройкам из UI. В БД не записывает."
            disabled={
              testing ||
              snapshotLoading ||
              !snapshotObjectUrl ||
              (gaugeType === "analog" && !calibrationReady)
            }
          >
            {testing ? "Тест..." : "Тест распознавания"}
          </button>
          <button
            className="rounded border border-slate-900 px-3 py-2 text-sm font-medium"
            type="button"
            title="Проверяет боевой путь: новый кадр RTMP + только сохраненные настройки из БД. В БД не записывает."
            onClick={() => void runTestRecognizeProductionParity()}
            disabled={testing || !configSaved || calibrationDirtyByRoi}
          >
            {testing ? "Тест..." : "Тест как в production"}
          </button>
          {gaugeType === "analog" ? (
            <button
              className="rounded border px-3 py-2 text-sm"
              onClick={() => void runQualityTestSeries(6)}
              title="Запускает 6 быстрых распознаваний подряд, оценивает стабильность и выводит рекомендации. В БД не записывает."
              disabled={qualityRunning || !calibrationReady}
            >
              {qualityRunning ? "Качество..." : "Качество x6"}
            </button>
          ) : null}
          <button
            className="rounded bg-slate-900 px-3 py-2 text-sm text-white disabled:opacity-50"
            onClick={() => void saveConfig()}
            title="Сохраняет текущие ROI/калибровку в БД. После этого их использует production-пайплайн."
            disabled={gaugeType === "analog" ? !hasRoi || !hasCenter || !hasMin || !hasMax || !hasScale : !hasRoi}
          >
            Сохранить конфигурацию
          </button>
          <button
            className="rounded border px-3 py-2 text-sm"
            onClick={() => void runTestCapture()}
            title="Делает боевой захват и сохраняет измерение в БД (появится в списке последних измерений)."
          >
            Тестовый захват
          </button>
        </div>
        <details className="rounded border bg-slate-50 px-3 py-2 text-xs text-slate-700">
          <summary className="cursor-pointer font-medium text-sm">Шпаргалка по кнопкам</summary>
          <div className="mt-2 space-y-1">
            <div><span className="font-medium">Обновить снимок:</span> новый кадр на экране, без записи в БД.</div>
            <div><span className="font-medium">Тест распознавания:</span> тест по текущему UI-снимку/настройкам, без записи в БД.</div>
            <div><span className="font-medium">Тест как в production:</span> тест как у фонового воркера (RTMP + настройки из БД), без записи в БД.</div>
            {gaugeType === "analog" ? (
              <div><span className="font-medium">Качество x6:</span> 6 распознаваний подряд, проверка стабильности и подсказки по калибровке.</div>
            ) : null}
            <div><span className="font-medium">Сохранить конфигурацию:</span> записывает ROI/калибровку в БД.</div>
            <div><span className="font-medium">Тестовый захват:</span> боевой прогон с записью измерения в БД.</div>
          </div>
        </details>
        <div className="grid gap-2 lg:grid-cols-2">
          <div className="rounded border bg-slate-50 px-3 py-2 text-sm text-slate-700 min-h-11 flex items-center">
            {status ?? "Результат операций будет показан здесь"}
          </div>
          <div className={`rounded border px-3 py-2 text-sm min-h-11 flex items-center ${error ? "border-red-300 bg-red-50 text-red-700" : "border-slate-200 bg-slate-50 text-slate-500"}`}>
            {error ?? "Ошибок нет"}
          </div>
        </div>
      </div>

      <div className="rounded-lg border bg-white p-4 space-y-3">
        <div className="font-medium">Настройка ROI и калибровки</div>
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
              2) Центр
            </button>
            <button
              className={`rounded border px-3 py-1.5 text-xs ${toolMode === "min" ? "bg-slate-900 text-white" : ""}`}
              onClick={() => setToolMode("min")}
              disabled={!hasCenter}
            >
              3) Точка min
            </button>
            <button
              className={`rounded border px-3 py-1.5 text-xs ${toolMode === "max" ? "bg-slate-900 text-white" : ""}`}
              onClick={() => setToolMode("max")}
              disabled={!hasMin}
            >
              4) Точка max
            </button>
          </div>
        ) : null}
        <div className="text-sm text-slate-600">
          {gaugeType === "analog"
            ? `${toolMode === "roi" ? "Перетащите по изображению, чтобы выбрать ROI." : "Кликните по изображению, чтобы установить точку калибровки."} Затем нажмите «Сохранить конфигурацию».`
            : "Перетащите по изображению, чтобы выбрать ROI. Затем нажмите «Сохранить конфигурацию»."}
        </div>
        {gaugeType === "analog" ? (
          <div className="rounded border bg-blue-50 px-3 py-2 text-xs text-blue-900">
            Текущий шаг:{" "}
            {recommendedTool === "roi"
              ? "Выберите ROI"
              : recommendedTool === "center"
                ? "Укажите центр"
                : recommendedTool === "min"
                  ? "Укажите минимальную точку"
                  : "Укажите максимальную точку"}
          </div>
        ) : null}
        {gaugeType === "analog" ? (
          <div className="rounded border bg-slate-50 px-3 py-2 text-xs text-slate-700">
            Шаги: ROI {hasRoi ? "ОК" : "TODO"} {"->"} Центр {hasCenter ? "ОК" : "TODO"} {"->"} Min {hasMin ? "ОК" : "TODO"} {"->"} Max{" "}
            {hasMax ? "ОК" : "TODO"} {"->"} Шкала {hasScale ? "ОК" : "TODO"} {"->"} Сохранить{" "}
            {configSaved && !calibrationDirtyByRoi ? "ОК" : "TODO"}.
          </div>
        ) : null}
        {gaugeType === "analog" && calibrationDirtyByRoi ? (
          <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            ROI изменился после последнего сохранения. Точки калибровки могут быть недействительны для этого ROI — укажите точки заново и сохраните конфигурацию.
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
                setSnapshotLoadError("Браузер не смог показать снимок. Нажмите «Обновить снимок».");
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
              return <div className="absolute h-3 w-3 rounded-full bg-blue-600" style={{ left, top }} title="центр" />;
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
              return <div className="absolute h-3 w-3 rounded-full bg-red-600" style={{ left, top }} title="точка min" />;
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
              return <div className="absolute h-3 w-3 rounded-full bg-violet-600" style={{ left, top }} title="точка max" />;
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
              return <div className="absolute h-2.5 w-2.5 rounded-full bg-emerald-600" style={{ left, top }} title="обнаруженный кончик стрелки" />;
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
            <div className="text-sm font-medium text-slate-700">Предпросмотр ROI (вырезка)</div>
            <img
              className="rounded border bg-slate-50"
              alt="roi"
              src={`data:image/jpeg;base64,${testResult.roi_image}`}
            />
          </div>
        ) : null}
        {testResult?.analog_debug ? (
          <div className="rounded border bg-slate-50 p-3 text-xs text-slate-700 space-y-1">
            <div>Отладка аналога: ratio={String(testResult.analog_debug.ratio ?? "н/д")} angle={String(testResult.analog_debug.angle ?? "н/д")}</div>
            <div>min_angle={String(testResult.analog_debug.min_angle ?? "н/д")} max_angle={String(testResult.analog_debug.max_angle ?? "н/д")} score={String(testResult.analog_debug.quality_score ?? "н/д")}</div>
            <div>предупреждения: {(testResult.analog_debug.warnings ?? []).join(", ") || "нет"}</div>
          </div>
        ) : null}
          {gaugeType === "analog" && qualitySummary ? (
          <div className={`rounded border p-3 text-xs space-y-1 ${qualitySummary.pass ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
            <div>Проверка качества: {qualitySummary.pass ? "ПРОЙДЕНО" : "ПЕРЕКАЛИБРОВАТЬ"}</div>
            <div>samples={qualitySummary.samples}, ok={qualitySummary.okCount}, fail={qualitySummary.failCount}</div>
            <div>min={String(qualitySummary.minValue ?? "n/a")} max={String(qualitySummary.maxValue ?? "n/a")} mean={String(qualitySummary.meanValue ?? "n/a")} range={String(qualitySummary.rangeValue ?? "n/a")}</div>
            <div>заметки: {qualitySummary.notes.join(", ") || "нет"}</div>
            <div>действия: {qualityRecommendations(qualitySummary).join(" | ")}</div>
          </div>
        ) : null}
      </div>

      <div className="rounded-lg border bg-white p-4 space-y-3">
        <div className="text-sm text-slate-600">
          ID логера: <span className="font-mono">{loggerId}</span>
        </div>
        <details className="rounded border bg-slate-50 p-3">
          <summary className="cursor-pointer text-sm font-medium">Расширенно: ROI JSON</summary>
          <textarea
            className="mt-2 w-full rounded border p-2 font-mono text-xs"
            rows={5}
            value={roiJson}
            onChange={(e) => setRoiJson(e.target.value)}
          />
        </details>
        {gaugeType === "analog" ? (
          <label className="block">
            <span className="text-sm font-medium">Калибровка JSON</span>
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
        ) : null}
      </div>

      <div className="rounded-lg border bg-white">
        <div className="border-b px-4 py-3 text-sm font-medium text-slate-700">Последние измерения</div>
        <div className="divide-y">
          {measurements.map((m) => (
            <div key={m.id} className="px-4 py-3 text-sm">
              <div>
                {m.ok ? `${m.value ?? "н/д"} ${m.unit}` : `Ошибка: ${humanizeMeasurementError(m.error)}`}
                {m.out_of_range === true ? (
                  <span className="text-amber-800 font-medium"> · вне допустимого диапазона</span>
                ) : m.out_of_range === false ? (
                  <span className="text-slate-500"> · в диапазоне</span>
                ) : null}
                · {new Date(m.captured_at).toLocaleString()}
              </div>
              {m.image_path ? (
                <a className="underline underline-offset-4" target="_blank" rel="noreferrer" href={buildMediaUrl(m.image_path)}>
                  Открыть изображение
                </a>
              ) : null}
            </div>
          ))}
          {measurements.length === 0 ? <div className="px-4 py-3 text-slate-600">Измерений пока нет.</div> : null}
        </div>
      </div>
    </div>
  );
}

