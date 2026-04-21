import React from "react";
import { Link } from "react-router-dom";

import { getPublicConfig } from "../api/config";
import {
  bulkUpdateMonitoring,
  createLogger,
  deleteLogger,
  listLoggers,
  updateLogger,
  type Logger,
  type LoggerCreate,
  type LoggerUpdate,
} from "../api/loggers";
import { captureNow } from "../api/measurements";

function defaultNewLogger(): LoggerCreate {
  return {
    name: "Logger 1",
    location: null,
    stream_key: "logger-1",
    gauge_type: "analog",
    unit: "unit",
    min_value: null,
    max_value: null,
    sample_interval_sec: 5,
    enabled: true,
    capture_mode: "continuous",
    schedule_start_hour_utc: null,
    schedule_end_hour_utc: null,
    image_retention_days: null,
    roi_json: null,
    calibration_json: null,
  };
}

function parseOptionalFloat(raw: string): number | null {
  const t = raw.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

function parseInterval(raw: string, fallback: number): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  const rounded = Math.round(n);
  if (rounded < 1) return 1;
  if (rounded > 86400) return 86400;
  return rounded;
}

function parseOptionalInt(raw: string): number | null {
  const t = raw.trim();
  if (!t) return null;
  const n = Number(t);
  if (!Number.isFinite(n)) return null;
  return Math.round(n);
}

type LoggerRowProps = {
  logger: Logger;
  rtmpBaseUrl: string | null;
  onCapture: (loggerId: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  setPageError: (message: string | null) => void;
};

function LoggerRow({ logger, rtmpBaseUrl, onCapture, onRefresh, setPageError }: LoggerRowProps): React.ReactElement {
  const [editing, setEditing] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [draft, setDraft] = React.useState<LoggerUpdate>({
    name: logger.name,
    location: logger.location,
    gauge_type: logger.gauge_type,
    unit: logger.unit,
    min_value: logger.min_value,
    max_value: logger.max_value,
    sample_interval_sec: logger.sample_interval_sec,
    enabled: logger.enabled,
    capture_mode: logger.capture_mode,
    schedule_start_hour_utc: logger.schedule_start_hour_utc,
    schedule_end_hour_utc: logger.schedule_end_hour_utc,
    image_retention_days: logger.image_retention_days,
  });

  React.useEffect(() => {
    if (editing) return;
    setDraft({
      name: logger.name,
      location: logger.location,
      gauge_type: logger.gauge_type,
      unit: logger.unit,
      min_value: logger.min_value,
      max_value: logger.max_value,
      sample_interval_sec: logger.sample_interval_sec,
      enabled: logger.enabled,
      capture_mode: logger.capture_mode,
      schedule_start_hour_utc: logger.schedule_start_hour_utc,
      schedule_end_hour_utc: logger.schedule_end_hour_utc,
      image_retention_days: logger.image_retention_days,
    });
  }, [logger, editing]);

  async function onSave(): Promise<void> {
    setSaving(true);
    setPageError(null);
    try {
      await updateLogger(logger.id, draft);
      setEditing(false);
      await onRefresh();
    } catch (e) {
      setPageError(e instanceof Error ? e.message : "Failed to update logger");
    } finally {
      setSaving(false);
    }
  }

  async function onDelete(): Promise<void> {
    const ok = window.confirm(`Удалить логер "${logger.name}"? Это удалит и его измерения.`);
    if (!ok) return;
    setDeleting(true);
    setPageError(null);
    try {
      await deleteLogger(logger.id);
      await onRefresh();
    } catch (e) {
      setPageError(e instanceof Error ? e.message : "Failed to delete logger");
    } finally {
      setDeleting(false);
    }
  }

  const fullIngestUrl = rtmpBaseUrl ? `${rtmpBaseUrl}/${logger.stream_key}` : null;

  return (
    <div className="px-4 py-3 space-y-2">
      {!editing ? (
        <>
          <div className="font-medium">{logger.name}</div>
          <div className="text-sm text-slate-600">
            stream: <span className="font-mono">{logger.stream_key}</span> · {logger.gauge_type} · interval{" "}
            {logger.sample_interval_sec}s · {logger.enabled ? "enabled" : "disabled"}
          </div>
          <div className="text-xs text-slate-600">
            mode: {logger.capture_mode}
            {logger.capture_mode === "schedule"
              ? ` (${logger.schedule_start_hour_utc ?? "?"}:00-${logger.schedule_end_hour_utc ?? "?"}:00 UTC)`
              : ""}
            {logger.image_retention_days ? ` · retention: ${logger.image_retention_days}d` : " · retention: off"}
          </div>
          {fullIngestUrl ? (
            <div className="text-xs text-slate-600">
              ingest URL: <span className="font-mono">{fullIngestUrl}</span>
            </div>
          ) : null}
          <div className="text-sm text-slate-600">
            ingest:{" "}
            <span className={logger.status.stream_active ? "text-green-700" : "text-slate-500"}>
              {logger.status.stream_active ? "active" : "inactive"}
            </span>
            {logger.status.stream_unavailable_persisted ? (
              <span className="ml-2 text-amber-800 font-medium" title="По данным БД после последнего успешного кадра">
                недоступен (БД)
              </span>
            ) : null}
            {logger.last_ingest_error ? (
              <span className="ml-2 text-red-700" title={`gap: ${logger.last_stream_gap_at ?? "—"}`}>
                БД: {logger.last_ingest_error}
              </span>
            ) : null}
            {logger.status.ingest_last_error ? (
              <span className="ml-2 text-red-700">ingest error: {logger.status.ingest_last_error}</span>
            ) : null}
            {logger.status.last_measurement_at ? (
              <span className="ml-2">
                last: {new Date(logger.status.last_measurement_at).toLocaleString()} ·{" "}
                {logger.status.last_ok === null ? "n/a" : logger.status.last_ok ? "OK" : `Error: ${logger.status.last_error ?? "unknown"}`}
              </span>
            ) : (
              <span className="ml-2">last: n/a</span>
            )}
          </div>
          <div className="flex items-center gap-4">
            <Link className="text-sm font-medium text-slate-900 underline underline-offset-4" to={`/loggers/${logger.id}/setup`}>
              Setup
            </Link>
            <button className="text-sm underline" onClick={() => void onCapture(logger.id)}>
              Capture now
            </button>
            <button className="text-sm underline" onClick={() => setEditing(true)}>
              Edit
            </button>
            <button className="text-sm text-red-700 underline disabled:opacity-50" onClick={() => void onDelete()} disabled={deleting}>
              {deleting ? "Deleting..." : "Delete"}
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            <input
              className="rounded border px-2 py-1"
              value={draft.name ?? ""}
              onChange={(e) => setDraft((s) => ({ ...s, name: e.target.value }))}
              placeholder="Name"
            />
            <input
              className="rounded border px-2 py-1"
              value={draft.location ?? ""}
              onChange={(e) => setDraft((s) => ({ ...s, location: e.target.value || null }))}
              placeholder="Location"
            />
            <select
              className="rounded border px-2 py-1"
              value={draft.gauge_type ?? logger.gauge_type}
              onChange={(e) => setDraft((s) => ({ ...s, gauge_type: e.target.value as Logger["gauge_type"] }))}
            >
              <option value="analog">analog</option>
              <option value="digital">digital</option>
              <option value="digital_segment">digital_segment</option>
            </select>
            <input
              className="rounded border px-2 py-1"
              value={draft.unit ?? ""}
              onChange={(e) => setDraft((s) => ({ ...s, unit: e.target.value }))}
              placeholder="Unit"
            />
            <input
              className="rounded border px-2 py-1"
              type="number"
              step="1"
              min={1}
              value={draft.sample_interval_sec ?? logger.sample_interval_sec}
              onChange={(e) =>
                setDraft((s) => ({
                  ...s,
                  sample_interval_sec: parseInterval(e.target.value, logger.sample_interval_sec),
                }))
              }
              placeholder="sample interval sec"
            />
            <label className="flex items-center gap-2 rounded border px-2 py-1 text-sm">
              <input
                type="checkbox"
                checked={draft.enabled ?? logger.enabled}
                onChange={(e) => setDraft((s) => ({ ...s, enabled: e.target.checked }))}
              />
              enabled
            </label>
            <select
              className="rounded border px-2 py-1"
              value={draft.capture_mode ?? logger.capture_mode}
              onChange={(e) => {
                const mode = e.target.value as Logger["capture_mode"];
                setDraft((s) => ({
                  ...s,
                  capture_mode: mode,
                  schedule_start_hour_utc: mode === "schedule" ? (s.schedule_start_hour_utc ?? 0) : null,
                  schedule_end_hour_utc: mode === "schedule" ? (s.schedule_end_hour_utc ?? 23) : null,
                }));
              }}
            >
              <option value="continuous">continuous</option>
              <option value="schedule">schedule</option>
            </select>
            <input
              className="rounded border px-2 py-1"
              type="number"
              min={0}
              max={23}
              value={draft.schedule_start_hour_utc ?? ""}
              onChange={(e) => setDraft((s) => ({ ...s, schedule_start_hour_utc: parseOptionalInt(e.target.value) }))}
              placeholder="start hour UTC (0-23)"
              disabled={(draft.capture_mode ?? logger.capture_mode) !== "schedule"}
            />
            <input
              className="rounded border px-2 py-1"
              type="number"
              min={0}
              max={23}
              value={draft.schedule_end_hour_utc ?? ""}
              onChange={(e) => setDraft((s) => ({ ...s, schedule_end_hour_utc: parseOptionalInt(e.target.value) }))}
              placeholder="end hour UTC (0-23)"
              disabled={(draft.capture_mode ?? logger.capture_mode) !== "schedule"}
            />
            <input
              className="rounded border px-2 py-1"
              type="number"
              min={1}
              max={3650}
              value={draft.image_retention_days ?? ""}
              onChange={(e) => setDraft((s) => ({ ...s, image_retention_days: parseOptionalInt(e.target.value) }))}
              placeholder="image retention days (optional)"
            />
            <input
              className="rounded border px-2 py-1"
              value={draft.min_value ?? ""}
              onChange={(e) => setDraft((s) => ({ ...s, min_value: parseOptionalFloat(e.target.value) }))}
              placeholder="Допустимый min"
            />
            <input
              className="rounded border px-2 py-1"
              value={draft.max_value ?? ""}
              onChange={(e) => setDraft((s) => ({ ...s, max_value: parseOptionalFloat(e.target.value) }))}
              placeholder="Допустимый max"
            />
          </div>
          <p className="text-xs text-slate-600">
            stream_key редактируется только при создании; параметры распознавания меняются на странице Setup.
          </p>
          <div className="flex items-center gap-3">
            <button
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-800 disabled:opacity-50"
              onClick={() => void onSave()}
              disabled={saving}
            >
              {saving ? "Saving..." : "Save"}
            </button>
            <button
              className="rounded-md border px-3 py-1.5 text-sm hover:bg-slate-50"
              onClick={() => setEditing(false)}
              disabled={saving}
            >
              Cancel
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export function LoggersPage(): React.ReactElement {
  const [items, setItems] = React.useState<Logger[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [form, setForm] = React.useState<LoggerCreate>(defaultNewLogger());
  const [autoRefresh, setAutoRefresh] = React.useState(true);
  const [rtmpBaseUrl, setRtmpBaseUrl] = React.useState<string | null>(null);
  const [bulkApplying, setBulkApplying] = React.useState(false);
  const [bulkConfirm, setBulkConfirm] = React.useState(false);
  const [bulkResult, setBulkResult] = React.useState<string | null>(null);
  const [bulkForm, setBulkForm] = React.useState({
    sample_interval_sec: "",
    enabled: "unchanged" as "unchanged" | "true" | "false",
    capture_mode: "unchanged" as "unchanged" | Logger["capture_mode"],
    schedule_start_hour_utc: "",
    schedule_end_hour_utc: "",
    image_retention_days: "",
    apply_to_disabled: true,
  });

  const refresh = React.useCallback(async () => {
    setError(null);
    try {
      const data = await listLoggers();
      setItems(data);
    } catch (e) {
      setItems([]);
      setError(e instanceof Error ? e.message : "Failed to load loggers");
    }
  }, []);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  React.useEffect(() => {
    void (async () => {
      try {
        const cfg = await getPublicConfig();
        setRtmpBaseUrl(cfg.rtmp_base_url || null);
      } catch {
        setRtmpBaseUrl(null);
      }
    })();
  }, []);

  React.useEffect(() => {
    if (!autoRefresh) return;
    const t = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => window.clearInterval(t);
  }, [autoRefresh, refresh]);

  async function onCreate(): Promise<void> {
    setCreating(true);
    setError(null);
    try {
      await createLogger(form);
      setForm(defaultNewLogger());
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create logger");
    } finally {
      setCreating(false);
    }
  }

  async function onCapture(loggerId: string): Promise<void> {
    setError(null);
    try {
      await captureNow(loggerId);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Capture failed");
    }
  }

  async function onBulkApply(): Promise<void> {
    if (!bulkConfirm) {
      setError("Подтвердите массовое применение настроек");
      return;
    }
    const payload: Record<string, unknown> = {};
    if (bulkForm.sample_interval_sec.trim()) payload.sample_interval_sec = parseInterval(bulkForm.sample_interval_sec, 5);
    if (bulkForm.enabled !== "unchanged") payload.enabled = bulkForm.enabled === "true";
    if (bulkForm.capture_mode !== "unchanged") payload.capture_mode = bulkForm.capture_mode;
    if (bulkForm.schedule_start_hour_utc.trim()) payload.schedule_start_hour_utc = parseOptionalInt(bulkForm.schedule_start_hour_utc);
    if (bulkForm.schedule_end_hour_utc.trim()) payload.schedule_end_hour_utc = parseOptionalInt(bulkForm.schedule_end_hour_utc);
    if (bulkForm.image_retention_days.trim()) payload.image_retention_days = parseOptionalInt(bulkForm.image_retention_days);
    payload.apply_to_disabled = bulkForm.apply_to_disabled;
    if (Object.keys(payload).length <= 1) {
      setError("Укажите хотя бы одно поле для массового изменения");
      return;
    }
    setBulkApplying(true);
    setError(null);
    setBulkResult(null);
    try {
      const res = await bulkUpdateMonitoring(payload);
      setBulkResult(`Обновлено логеров: ${res.updated}`);
      setBulkConfirm(false);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Bulk update failed");
    } finally {
      setBulkApplying(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Loggers</h1>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          Auto refresh
        </label>
      </div>

      <div className="rounded-lg border bg-white p-4 space-y-3">
        <div className="font-medium">Global monitoring settings (all loggers)</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <input
            className="rounded border px-2 py-1"
            placeholder="sample interval sec"
            value={bulkForm.sample_interval_sec}
            onChange={(e) => setBulkForm((s) => ({ ...s, sample_interval_sec: e.target.value }))}
          />
          <select
            className="rounded border px-2 py-1"
            value={bulkForm.enabled}
            onChange={(e) => setBulkForm((s) => ({ ...s, enabled: e.target.value as "unchanged" | "true" | "false" }))}
          >
            <option value="unchanged">enabled: unchanged</option>
            <option value="true">enabled: true</option>
            <option value="false">enabled: false</option>
          </select>
          <select
            className="rounded border px-2 py-1"
            value={bulkForm.capture_mode}
            onChange={(e) => setBulkForm((s) => ({ ...s, capture_mode: e.target.value as "unchanged" | Logger["capture_mode"] }))}
          >
            <option value="unchanged">capture_mode: unchanged</option>
            <option value="continuous">capture_mode: continuous</option>
            <option value="schedule">capture_mode: schedule</option>
          </select>
          <label className="flex items-center gap-2 rounded border px-2 py-1 text-sm">
            <input
              type="checkbox"
              checked={bulkForm.apply_to_disabled}
              onChange={(e) => setBulkForm((s) => ({ ...s, apply_to_disabled: e.target.checked }))}
            />
            include disabled loggers
          </label>
          <input
            className="rounded border px-2 py-1"
            placeholder="schedule start hour UTC"
            value={bulkForm.schedule_start_hour_utc}
            onChange={(e) => setBulkForm((s) => ({ ...s, schedule_start_hour_utc: e.target.value }))}
          />
          <input
            className="rounded border px-2 py-1"
            placeholder="schedule end hour UTC"
            value={bulkForm.schedule_end_hour_utc}
            onChange={(e) => setBulkForm((s) => ({ ...s, schedule_end_hour_utc: e.target.value }))}
          />
          <input
            className="rounded border px-2 py-1"
            placeholder="image retention days"
            value={bulkForm.image_retention_days}
            onChange={(e) => setBulkForm((s) => ({ ...s, image_retention_days: e.target.value }))}
          />
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={bulkConfirm} onChange={(e) => setBulkConfirm(e.target.checked)} />
          Я подтверждаю массовое применение настроек ко всем выбранным логерам
        </label>
        <div className="flex items-center gap-3">
          <button
            className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            onClick={() => void onBulkApply()}
            disabled={bulkApplying}
          >
            {bulkApplying ? "Applying..." : "Apply globally"}
          </button>
          {bulkResult ? <span className="text-sm text-slate-700">{bulkResult}</span> : null}
        </div>
      </div>

      <div className="rounded-lg border bg-white p-4 space-y-3">
        <div className="font-medium">Create logger</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <input
            className="rounded border px-2 py-1"
            placeholder="Name"
            value={form.name}
            onChange={(e) => setForm((s) => ({ ...s, name: e.target.value }))}
          />
          <input
            className="rounded border px-2 py-1"
            placeholder="Location"
            value={form.location ?? ""}
            onChange={(e) => setForm((s) => ({ ...s, location: e.target.value || null }))}
          />
          <input
            className="rounded border px-2 py-1"
            placeholder="RTMP stream key"
            value={form.stream_key}
            onChange={(e) => setForm((s) => ({ ...s, stream_key: e.target.value }))}
          />
          <select
            className="rounded border px-2 py-1"
            value={form.gauge_type}
            onChange={(e) => setForm((s) => ({ ...s, gauge_type: e.target.value as Logger["gauge_type"] }))}
          >
            <option value="analog">analog</option>
            <option value="digital">digital</option>
            <option value="digital_segment">digital_segment</option>
          </select>
          <input
            className="rounded border px-2 py-1"
            placeholder="Допустимый min (опционально)"
            value={form.min_value ?? ""}
            onChange={(e) => setForm((s) => ({ ...s, min_value: parseOptionalFloat(e.target.value) }))}
          />
          <input
            className="rounded border px-2 py-1"
            placeholder="Допустимый max (опционально)"
            value={form.max_value ?? ""}
            onChange={(e) => setForm((s) => ({ ...s, max_value: parseOptionalFloat(e.target.value) }))}
          />
          <select
            className="rounded border px-2 py-1"
            value={form.capture_mode}
            onChange={(e) =>
              setForm((s) => ({
                ...s,
                capture_mode: e.target.value as Logger["capture_mode"],
                schedule_start_hour_utc: e.target.value === "schedule" ? (s.schedule_start_hour_utc ?? 0) : null,
                schedule_end_hour_utc: e.target.value === "schedule" ? (s.schedule_end_hour_utc ?? 23) : null,
              }))
            }
          >
            <option value="continuous">continuous</option>
            <option value="schedule">schedule</option>
          </select>
          <input
            className="rounded border px-2 py-1"
            type="number"
            min={0}
            max={23}
            placeholder="Schedule start UTC (0-23)"
            value={form.schedule_start_hour_utc ?? ""}
            disabled={form.capture_mode !== "schedule"}
            onChange={(e) => setForm((s) => ({ ...s, schedule_start_hour_utc: parseOptionalInt(e.target.value) }))}
          />
          <input
            className="rounded border px-2 py-1"
            type="number"
            min={0}
            max={23}
            placeholder="Schedule end UTC (0-23)"
            value={form.schedule_end_hour_utc ?? ""}
            disabled={form.capture_mode !== "schedule"}
            onChange={(e) => setForm((s) => ({ ...s, schedule_end_hour_utc: parseOptionalInt(e.target.value) }))}
          />
          <input
            className="rounded border px-2 py-1"
            type="number"
            min={1}
            max={3650}
            placeholder="Image retention days (optional)"
            value={form.image_retention_days ?? ""}
            onChange={(e) => setForm((s) => ({ ...s, image_retention_days: parseOptionalInt(e.target.value) }))}
          />
        </div>
        <p className="text-xs text-slate-600">
          Поля min/max — это <strong>допустимый диапазон показаний</strong> для контроля (ТЗ); не путать с min/max на шкале в калибровке аналога на странице Setup.
        </p>
        {rtmpBaseUrl ? (
          <p className="text-xs text-slate-600">
            RTMP ingest URL для нового логера: <span className="font-mono">{`${rtmpBaseUrl}/${form.stream_key}`}</span>
          </p>
        ) : null}
        <button
          className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          onClick={() => void onCreate()}
          disabled={creating}
        >
          Create logger
        </button>
      </div>

      {error ? <div className="rounded-md border border-red-200 bg-red-50 p-3 text-red-800">{error}</div> : null}

      <div className="rounded-lg border bg-white">
        <div className="border-b px-4 py-3 text-sm font-medium text-slate-700">Configured loggers</div>
        <div className="divide-y">
          {(items ?? []).map((l) => (
            <LoggerRow
              key={l.id}
              logger={l}
              rtmpBaseUrl={rtmpBaseUrl}
              onCapture={onCapture}
              onRefresh={refresh}
              setPageError={setError}
            />
          ))}
          {items?.length === 0 ? <div className="px-4 py-3 text-slate-600">No loggers yet.</div> : null}
          {items === null ? <div className="px-4 py-3 text-slate-600">Loading…</div> : null}
        </div>
      </div>
    </div>
  );
}

