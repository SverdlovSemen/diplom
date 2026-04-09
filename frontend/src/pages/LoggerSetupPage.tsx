import React from "react";
import { useParams } from "react-router-dom";
import { getLogger, updateLogger } from "../api/loggers";
import { captureNow, listMeasurements, testRecognize, type Measurement, type TestRecognizeResult } from "../api/measurements";

const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

const DEFAULT_SNAPSHOT_ERROR = "Нет активного потока. Запустите трансляцию.";
const SNAPSHOT_FETCH_TIMEOUT_MS = 47_000;

type RoiRect = { x: number; y: number; w: number; h: number };

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

export function LoggerSetupPage(): React.ReactElement {
  const { loggerId } = useParams();
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

  const refresh = React.useCallback(async () => {
    if (!loggerId) return;
    try {
      const logger = await getLogger(loggerId);
      if (logger.roi_json) setRoiJson(logger.roi_json);
      if (logger.calibration_json) setCalibrationJson(logger.calibration_json);
      setMeasurements(await listMeasurements(loggerId, 20));
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
    if (!loggerId) return;
    let cancelled = false;
    let createdBlobUrl: string | null = null;
    const url = `${apiBase}/api/v1/processing/loggers/${loggerId}/snapshot?ts=${snapshotTs}`;
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
    const p = toImageCoords(e.clientX, e.clientY);
    if (!p) return;
    setError(null);
    setStatus(null);
    setDragStart(p);
    setRoiRect({ x: p.x, y: p.y, w: 1, h: 1 });
  }

  function onMouseMove(e: React.MouseEvent<HTMLDivElement>): void {
    if (!dragStart) return;
    const p = toImageCoords(e.clientX, e.clientY);
    if (!p) return;
    const rect = normalizeRect(dragStart, p);
    setRoiRect(rect);
  }

  function onMouseUp(): void {
    if (!dragStart || !roiRect) return;
    setDragStart(null);
    // минимальные размеры, чтобы не сохранить случайный клик
    if (roiRect.w < 5 || roiRect.h < 5) return;
    updateRoiFromRect(roiRect);
  }

  async function saveConfig(): Promise<void> {
    if (!loggerId) return;
    setError(null);
    setStatus(null);
    try {
      JSON.parse(roiJson);
      JSON.parse(calibrationJson);
      await updateLogger(loggerId, { roi_json: roiJson, calibration_json: calibrationJson });
      setStatus("Configuration saved");
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
      setStatus(m.ok ? `Captured: ${m.value ?? "n/a"} ${m.unit}` : `Capture error: ${m.error ?? "unknown"}`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Capture failed");
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

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Logger setup</h1>

      <div className="rounded-lg border bg-white p-4 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="font-medium">ROI setup</div>
          <div className="flex gap-2">
            <button className="rounded border px-3 py-2 text-sm" onClick={() => setSnapshotTs(Date.now())}>
              Refresh snapshot
            </button>
            <button className="rounded border px-3 py-2 text-sm" onClick={() => void runTestRecognize()} disabled={testing}>
              {testing ? "Testing…" : "Test recognize"}
            </button>
          </div>
        </div>
        <div className="text-sm text-slate-600">Drag on the image to select ROI. Then click “Save config”.</div>

        <div
          className="relative inline-block select-none"
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseUp}
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
        <label className="block">
          <span className="text-sm font-medium">Calibration JSON</span>
          <textarea
            className="mt-1 w-full rounded border p-2 font-mono text-xs"
            rows={7}
            value={calibrationJson}
            onChange={(e) => setCalibrationJson(e.target.value)}
          />
        </label>
        <div className="flex gap-2">
          <button className="rounded bg-slate-900 px-3 py-2 text-sm text-white" onClick={() => void saveConfig()}>
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
                {m.ok ? `${m.value ?? "n/a"} ${m.unit}` : `Error: ${m.error ?? "unknown"}`} ·{" "}
                {new Date(m.captured_at).toLocaleString()}
              </div>
              {m.image_path ? (
                <a className="underline underline-offset-4" target="_blank" rel="noreferrer" href={`${apiBase}/media/${m.image_path}`}>
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

