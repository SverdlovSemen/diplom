import { apiFetch } from "./client";

export type Measurement = {
  id: string;
  logger_id: string;
  value: number | null;
  unit: string;
  ok: boolean;
  error: string | null;
  /** null — границы в логере не заданы; true/false — вне/внутри допустимого диапазона логера */
  out_of_range: boolean | null;
  /** JSON-массив строк предупреждений CV (аналог) */
  cv_warnings_json: string | null;
  image_path: string | null;
  captured_at: string;
  created_at: string;
};

export type MeasurementListParams = {
  loggerId?: string;
  /** ISO 8601 (UTC), например из `date.toISOString()` */
  from?: string;
  to?: string;
  offset?: number;
  limit?: number;
};

export type MeasurementListResponse = {
  items: Measurement[];
  total: number;
};

export async function listMeasurements(params?: MeasurementListParams): Promise<MeasurementListResponse> {
  const q = new URLSearchParams();
  if (params?.loggerId) q.set("logger_id", params.loggerId);
  if (params?.from) q.set("from", params.from);
  if (params?.to) q.set("to", params.to);
  q.set("offset", String(params?.offset ?? 0));
  q.set("limit", String(params?.limit ?? 100));
  return apiFetch<MeasurementListResponse>(`/api/v1/measurements?${q.toString()}`);
}

const CHART_PAGE_SIZE = 500;

/** Все измерения за период для графика (пагинация B1, до maxPoints). Нужен выбранный loggerId. */
export async function fetchMeasurementsForChart(
  params: MeasurementStatsParams & { maxPoints?: number },
): Promise<Measurement[]> {
  const maxPoints = params.maxPoints ?? 10_000;
  if (!params.loggerId) return [];
  const collected: Measurement[] = [];
  let offset = 0;
  while (collected.length < maxPoints) {
    const { items, total } = await listMeasurements({
      loggerId: params.loggerId,
      from: params.from,
      to: params.to,
      offset,
      limit: CHART_PAGE_SIZE,
    });
    collected.push(...items);
    if (items.length === 0 || collected.length >= total) break;
    offset += items.length;
  }
  collected.sort((a, b) => new Date(a.captured_at).getTime() - new Date(b.captured_at).getTime());
  return collected.slice(0, maxPoints);
}

export type MeasurementStatsParams = {
  loggerId?: string;
  from?: string;
  to?: string;
};

/** Сводка за период (GET /api/v1/measurements/stats), те же фильтры, что у списка без пагинации. */
export type MeasurementStats = {
  period_from: string | null;
  period_to: string | null;
  logger_id: string | null;
  count: number;
  value_count: number;
  value_min: number | null;
  value_max: number | null;
  value_avg: number | null;
  recognition_fail_count: number;
  out_of_range_count: number;
  cv_warnings_count: number;
};

export type MeasurementAlert = {
  measurement_id: string;
  logger_id: string;
  logger_name: string;
  captured_at: string;
  value: number | null;
  unit: string;
  error: string | null;
  image_path: string | null;
};

export async function getMeasurementStats(params?: MeasurementStatsParams): Promise<MeasurementStats> {
  const q = new URLSearchParams();
  if (params?.loggerId) q.set("logger_id", params.loggerId);
  if (params?.from) q.set("from", params.from);
  if (params?.to) q.set("to", params.to);
  return apiFetch<MeasurementStats>(`/api/v1/measurements/stats?${q.toString()}`);
}

export async function listMeasurementAlerts(
  params?: MeasurementStatsParams & { limit?: number },
): Promise<MeasurementAlert[]> {
  const q = new URLSearchParams();
  if (params?.loggerId) q.set("logger_id", params.loggerId);
  if (params?.from) q.set("from", params.from);
  if (params?.to) q.set("to", params.to);
  q.set("limit", String(params?.limit ?? 50));
  return apiFetch<MeasurementAlert[]>(`/api/v1/measurements/alerts?${q.toString()}`);
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

/** Скачать CSV (GET /api/v1/measurements/export.csv), те же фильтры, что у списка/сводки. */
export async function downloadMeasurementsCsv(params?: MeasurementStatsParams): Promise<void> {
  const q = new URLSearchParams();
  if (params?.loggerId) q.set("logger_id", params.loggerId);
  if (params?.from) q.set("from", params.from);
  if (params?.to) q.set("to", params.to);
  const res = await fetch(`${apiBaseUrl}/api/v1/measurements/export.csv?${q.toString()}`, {
    method: "GET",
    headers: { Accept: "text/csv, */*" },
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail != null) msg = String(body.detail);
    } catch {
      // не JSON
    }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const a = document.createElement("a");
  const name = `measurements_export_${new Date().toISOString().slice(0, 19).replace(/[:.]/g, "-")}.csv`;
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.rel = "noopener";
  a.click();
  URL.revokeObjectURL(a.href);
}

export async function captureNow(loggerId: string): Promise<Measurement> {
  return apiFetch<Measurement>(`/api/v1/processing/loggers/${loggerId}/capture`, {
    method: "POST",
  });
}

export type TestRecognizeResult = {
  value: number | null;
  ok: boolean;
  error: string | null;
  roi_image: string | null;
  /** Сырой текст OCR для digital (whitelist цифр и точки) */
  ocr_raw?: string | null;
  /** client_jpeg — кадр из UI; rtmp_capture — новый RTMP; rtmp_production_parity — как process_logger_once (только БД) */
  frame_source?: string | null;
  analog_debug?: {
    tip_point?: { x: number; y: number } | null;
    angle?: number | null;
    min_angle?: number | null;
    max_angle?: number | null;
    ratio?: number | null;
    quality_score?: number | null;
    warnings?: string[];
  } | null;
  /** Предупреждения CV: геометрия ROI + аналог (см. docs/tz_p12_detection_variant1_operator_roi.md) */
  cv_warnings?: string[] | null;
};

export type TestRecognizeBody = {
  /** Тот же путь CV, что и фоновый процесс: RTMP + только roi/calibration из БД */
  production_parity?: boolean;
  frame_jpeg_base64?: string;
  roi_json?: string;
  /** Должен совпадать с полем Calibration в UI (center/min/max), иначе бэкенд возьмёт калибровку из БД. */
  calibration_json?: string;
};

export async function testRecognize(loggerId: string, body?: TestRecognizeBody): Promise<TestRecognizeResult> {
  return apiFetch<TestRecognizeResult>(`/api/v1/processing/loggers/${loggerId}/test-recognize`, {
    method: "POST",
    body: JSON.stringify(body ?? {}),
  });
}

