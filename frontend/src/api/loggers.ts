import { apiFetch } from "./client";

export type GaugeType = "analog" | "digital";

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
  roi_json: string | null;
  calibration_json: string | null;
  status: {
    stream_active: boolean;
    ingest_last_attempt_at?: string | null;
    ingest_last_success_at?: string | null;
    ingest_last_error?: string | null;
    last_measurement_at: string | null;
    last_ok: boolean | null;
    last_error: string | null;
  };
  created_at: string;
  updated_at: string;
};

export type LoggerCreate = Omit<Logger, "id" | "created_at" | "updated_at" | "status">;
export type LoggerUpdate = Partial<LoggerCreate>;

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

