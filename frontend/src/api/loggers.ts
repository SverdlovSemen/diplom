import { apiFetch } from "./client";

export type GaugeType = "analog" | "digital" | "digital_segment";
export type CaptureMode = "continuous" | "schedule";

export type Logger = {
  id: string;
  name: string;
  location: string | null;
  stream_key: string;
  gauge_type: GaugeType;
  unit: string;
  min_value: number | null;
  max_value: number | null;
  sample_interval_sec: number;
  enabled: boolean;
  capture_mode: CaptureMode;
  schedule_start_hour_utc: number | null;
  schedule_end_hour_utc: number | null;
  image_retention_days: number | null;
  roi_json: string | null;
  calibration_json: string | null;
  /** Персистентно: последний успешный кадр с потока */
  last_stream_seen_at: string | null;
  /** Персистентно: последняя зафиксированная проблема (нет publisher / ошибка захвата) */
  last_stream_gap_at: string | null;
  /** Персистентно: текст последней ошибки ingest */
  last_ingest_error: string | null;
  status: {
    stream_active: boolean;
    ingest_last_attempt_at?: string | null;
    ingest_last_success_at?: string | null;
    ingest_last_error?: string | null;
    last_measurement_at: string | null;
    last_ok: boolean | null;
    last_error: string | null;
    /** По БД: gap новее last_stream_seen — поток считался недоступным после последнего успеха */
    stream_unavailable_persisted: boolean;
  };
  created_at: string;
  updated_at: string;
};

export type LoggerCreate = Omit<
  Logger,
  "id" | "created_at" | "updated_at" | "status" | "last_stream_seen_at" | "last_stream_gap_at" | "last_ingest_error"
>;
export type LoggerUpdate = Partial<LoggerCreate>;
export type BulkMonitoringUpdate = {
  sample_interval_sec?: number;
  enabled?: boolean;
  capture_mode?: CaptureMode;
  schedule_start_hour_utc?: number | null;
  schedule_end_hour_utc?: number | null;
  image_retention_days?: number | null;
  apply_to_disabled?: boolean;
};

export async function listLoggers(): Promise<Logger[]> {
  return apiFetch<Logger[]>("/api/v1/loggers");
}

export async function createLogger(payload: LoggerCreate): Promise<Logger> {
  return apiFetch<Logger>("/api/v1/loggers", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getLogger(loggerId: string): Promise<Logger> {
  return apiFetch<Logger>(`/api/v1/loggers/${loggerId}`);
}

export async function updateLogger(loggerId: string, payload: LoggerUpdate): Promise<Logger> {
  return apiFetch<Logger>(`/api/v1/loggers/${loggerId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteLogger(loggerId: string): Promise<void> {
  return apiFetch<void>(`/api/v1/loggers/${loggerId}`, {
    method: "DELETE",
  });
}

export async function bulkUpdateMonitoring(payload: BulkMonitoringUpdate): Promise<{ updated: number }> {
  return apiFetch<{ updated: number }>("/api/v1/loggers/bulk-monitoring", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

