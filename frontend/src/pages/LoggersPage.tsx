import React from "react";
import { Link } from "react-router-dom";

import { createLogger, listLoggers, type Logger, type LoggerCreate } from "../api/loggers";
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
    roi_json: null,
    calibration_json: null,
  };
}

export function LoggersPage(): React.ReactElement {
  const [items, setItems] = React.useState<Logger[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [form, setForm] = React.useState<LoggerCreate>(defaultNewLogger());
  const [autoRefresh, setAutoRefresh] = React.useState(true);

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
          </select>
        </div>
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
            <div key={l.id} className="px-4 py-3 flex items-center justify-between">
              <div>
                <div className="font-medium">{l.name}</div>
                <div className="text-sm text-slate-600">
                  stream: <span className="font-mono">{l.stream_key}</span> · {l.gauge_type} · interval {l.sample_interval_sec}s
                </div>
                <div className="text-sm text-slate-600">
                  ingest:{" "}
                  <span className={l.status.stream_active ? "text-green-700" : "text-slate-500"}>
                    {l.status.stream_active ? "active" : "inactive"}
                  </span>
                  {l.status.ingest_last_error ? <span className="ml-2 text-red-700">ingest error: {l.status.ingest_last_error}</span> : null}
                  {l.status.last_measurement_at ? (
                    <span className="ml-2">
                      last: {new Date(l.status.last_measurement_at).toLocaleString()} ·{" "}
                      {l.status.last_ok === null ? "n/a" : l.status.last_ok ? "OK" : `Error: ${l.status.last_error ?? "unknown"}`}
                    </span>
                  ) : (
                    <span className="ml-2">last: n/a</span>
                  )}
                </div>
              </div>
              <Link
                className="text-sm font-medium text-slate-900 underline underline-offset-4"
                to={`/loggers/${l.id}/setup`}
              >
                Setup
              </Link>
              <button className="ml-4 text-sm underline" onClick={() => void onCapture(l.id)}>
                Capture now
              </button>
            </div>
          ))}
          {items?.length === 0 ? <div className="px-4 py-3 text-slate-600">No loggers yet.</div> : null}
          {items === null ? <div className="px-4 py-3 text-slate-600">Loading…</div> : null}
        </div>
      </div>
    </div>
  );
}

