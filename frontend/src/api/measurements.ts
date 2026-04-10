import { apiFetch } from "./client";

export type Measurement = {
  id: string;
  logger_id: string;
  value: number | null;
  unit: string;
  ok: boolean;
  error: string | null;
  image_path: string | null;
  captured_at: string;
  created_at: string;
};

export async function listMeasurements(loggerId?: string, limit = 100): Promise<Measurement[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (loggerId) params.set("logger_id", loggerId);
  return apiFetch<Measurement[]>(`/api/v1/measurements?${params.toString()}`);
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
  /** client_jpeg — тот же кадр, что в UI; rtmp_capture — новый захват с потока */
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
};

export type TestRecognizeBody = {
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

