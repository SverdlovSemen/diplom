import React from "react";
import { MeasurementDynamicsChart } from "../components/MeasurementDynamicsChart";
import { listLoggers, type Logger } from "../api/loggers";
import { buildMediaUrl } from "../api/media";
import {
  downloadMeasurementsCsv,
  fetchMeasurementsForChart,
  getMeasurementStats,
  listMeasurementAlerts,
  type MeasurementAlert,
  listMeasurements,
  type Measurement,
  type MeasurementStats,
} from "../api/measurements";

function toDatetimeLocalValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function DashboardPage(): React.ReactElement {
  const [loggers, setLoggers] = React.useState<Logger[]>([]);
  const [loggerFilter, setLoggerFilter] = React.useState<string>("");
  const [fromLocal, setFromLocal] = React.useState<string>(() => {
    const to = new Date();
    const from = new Date(to.getTime() - 7 * 24 * 60 * 60 * 1000);
    return toDatetimeLocalValue(from);
  });
  const [toLocal, setToLocal] = React.useState<string>(() => toDatetimeLocalValue(new Date()));
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(30);
  const [items, setItems] = React.useState<Measurement[]>([]);
  const [total, setTotal] = React.useState(0);
  const [stats, setStats] = React.useState<MeasurementStats | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [chartPoints, setChartPoints] = React.useState<Measurement[]>([]);
  const [chartLoading, setChartLoading] = React.useState(false);
  const [chartError, setChartError] = React.useState<string | null>(null);
  const [alerts, setAlerts] = React.useState<MeasurementAlert[]>([]);

  React.useEffect(() => {
    void (async () => {
      try {
        const list = await listLoggers();
        setLoggers(list);
      } catch {
        /* список логеров — подсказка для фильтра; ошибка покажется при загрузке измерений */
      }
    })();
  }, []);

  const applyDefaults = React.useCallback(() => {
    const to = new Date();
    const from = new Date(to.getTime() - 7 * 24 * 60 * 60 * 1000);
    setFromLocal(toDatetimeLocalValue(from));
    setToLocal(toDatetimeLocalValue(to));
    setPage(1);
  }, []);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const fromIso = fromLocal ? new Date(fromLocal).toISOString() : undefined;
      const toIso = toLocal ? new Date(toLocal).toISOString() : undefined;
      const offset = (page - 1) * pageSize;
      const filterArgs = {
        loggerId: loggerFilter || undefined,
        from: fromIso,
        to: toIso,
      };
      const [{ items: next, total: t }, s] = await Promise.all([
        listMeasurements({ ...filterArgs, offset, limit: pageSize }),
        getMeasurementStats(filterArgs),
      ]);
      setItems(next);
      setTotal(t);
      setStats(s);
      setAlerts(await listMeasurementAlerts({ ...filterArgs, limit: 20 }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load measurements");
      setStats(null);
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  }, [fromLocal, toLocal, loggerFilter, page, pageSize]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize) || 1);

  React.useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [totalPages, page]);

  const chartParams = React.useMemo(
    () => ({
      loggerId: loggerFilter || undefined,
      from: fromLocal ? new Date(fromLocal).toISOString() : undefined,
      to: toLocal ? new Date(toLocal).toISOString() : undefined,
    }),
    [loggerFilter, fromLocal, toLocal],
  );

  React.useEffect(() => {
    if (!chartParams.loggerId) {
      setChartPoints([]);
      setChartError(null);
      setChartLoading(false);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    setChartError(null);
    void (async () => {
      try {
        const pts = await fetchMeasurementsForChart(chartParams);
        if (!cancelled) setChartPoints(pts);
      } catch (e) {
        if (!cancelled) {
          setChartPoints([]);
          setChartError(e instanceof Error ? e.message : "Не удалось загрузить данные графика");
        }
      } finally {
        if (!cancelled) setChartLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chartParams]);

  const chartUnit =
    chartPoints[0]?.unit ?? loggers.find((l) => l.id === loggerFilter)?.unit ?? "";

  const exportCsv = React.useCallback(async () => {
    setExporting(true);
    setError(null);
    try {
      const fromIso = fromLocal ? new Date(fromLocal).toISOString() : undefined;
      const toIso = toLocal ? new Date(toLocal).toISOString() : undefined;
      await downloadMeasurementsCsv({
        loggerId: loggerFilter || undefined,
        from: fromIso,
        to: toIso,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }, [fromLocal, toLocal, loggerFilter]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <div className="rounded-lg border bg-white p-4 space-y-3">
        <div className="font-medium">История измерений</div>
        <p className="text-sm text-slate-600">
          Фильтр по времени снимка (<code className="text-xs">captured_at</code>) и логеру. Пустые поля даты — без
          ограничения с этой стороны.
        </p>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-600">Логер</span>
            <select
              className="rounded-md border border-slate-300 px-2 py-1.5 min-w-[12rem]"
              value={loggerFilter}
              onChange={(e) => {
                setLoggerFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="">Все</option>
              {loggers.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({l.stream_key})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-600">С (локальное время)</span>
            <input
              type="datetime-local"
              className="rounded-md border border-slate-300 px-2 py-1.5"
              value={fromLocal}
              onChange={(e) => {
                setFromLocal(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-600">По</span>
            <input
              type="datetime-local"
              className="rounded-md border border-slate-300 px-2 py-1.5"
              value={toLocal}
              onChange={(e) => {
                setToLocal(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-600">На странице</span>
            <select
              className="rounded-md border border-slate-300 px-2 py-1.5"
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPage(1);
              }}
            >
              {[10, 30, 50, 100].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-800"
            onClick={() => void load()}
          >
            Обновить
          </button>
          <button
            type="button"
            disabled={exporting}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
            onClick={() => void exportCsv()}
          >
            {exporting ? "Экспорт…" : "Скачать CSV"}
          </button>
          <button type="button" className="text-sm text-slate-600 underline" onClick={applyDefaults}>
            Последние 7 дней
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
          {loading ? <span>Загрузка…</span> : null}
          <span>
            Записей: {total}
            {total > 0 ? ` · страница ${Math.min(page, totalPages)} из ${totalPages}` : null}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={page <= 1 || loading}
            className="rounded-md border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Назад
          </button>
          <button
            type="button"
            disabled={page >= totalPages || loading}
            className="rounded-md border px-3 py-1 text-sm disabled:opacity-50"
            onClick={() => setPage((p) => p + 1)}
          >
            Вперёд
          </button>
        </div>
      </div>
      {stats ? (
        <div className="rounded-lg border bg-white p-4 space-y-2">
          <div className="font-medium">Сводка за период</div>
          <p className="text-sm text-slate-600">
            Те же фильтры, что у списка ниже. Min/max/avg — по записям с числовым значением.
          </p>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-slate-600">Всего записей</dt>
              <dd className="font-mono">{stats.count}</dd>
            </div>
            <div>
              <dt className="text-slate-600">С числом (value)</dt>
              <dd className="font-mono">{stats.value_count}</dd>
            </div>
            <div>
              <dt className="text-slate-600">Min / max / avg</dt>
              <dd className="font-mono">
                {stats.value_min !== null && stats.value_max !== null && stats.value_avg !== null
                  ? `${stats.value_min.toFixed(3)} / ${stats.value_max.toFixed(3)} / ${stats.value_avg.toFixed(3)}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-slate-600">Ошибки распознавания (ok=false)</dt>
              <dd className="font-mono">{stats.recognition_fail_count}</dd>
            </div>
            <div>
              <dt className="text-slate-600">Вне допустимого диапазона</dt>
              <dd className="font-mono">{stats.out_of_range_count}</dd>
            </div>
            <div>
              <dt className="text-slate-600">Предупреждения CV (непустой json)</dt>
              <dd className="font-mono">{stats.cv_warnings_count}</dd>
            </div>
          </dl>
        </div>
      ) : null}
      <div className="rounded-lg border bg-white p-4 space-y-2">
        <div className="font-medium">Алерты аномалий (out_of_range)</div>
        <p className="text-sm text-slate-600">Последние 20 записей с выходом за допустимый диапазон.</p>
        <div className="divide-y">
          {alerts.map((a) => (
            <div key={a.measurement_id} className="py-2 text-sm">
              <div>
                <span className="font-medium text-amber-800">{a.logger_name}</span> ·{" "}
                {a.value !== null ? `${a.value.toFixed(3)} ${a.unit}` : "n/a"} · {new Date(a.captured_at).toLocaleString()}
              </div>
              <div className="text-slate-600">
                logger <span className="font-mono">{a.logger_id.slice(0, 8)}</span>
                {a.error ? <span className="ml-2 text-red-700">err: {a.error}</span> : null}
                {a.image_path ? (
                  <a className="ml-2 underline underline-offset-4" href={buildMediaUrl(a.image_path)} target="_blank" rel="noreferrer">
                    image
                  </a>
                ) : null}
              </div>
            </div>
          ))}
          {alerts.length === 0 ? <div className="py-2 text-sm text-slate-600">Нет аномалий в выбранном периоде.</div> : null}
        </div>
      </div>
      <div className="rounded-lg border bg-white p-4 space-y-2">
        <div className="font-medium">Динамика показаний</div>
        <p className="text-sm text-slate-600">
          График <code className="text-xs">value</code> по <code className="text-xs">captured_at</code> (те же фильтры
          периода). Данные подгружаются через API списка измерений (B1), до 10000 точек.
        </p>
        {!loggerFilter ? (
          <p className="text-sm text-slate-600">Выберите конкретный логер в фильтре выше.</p>
        ) : chartLoading ? (
          <p className="text-sm text-slate-600">Загрузка графика…</p>
        ) : chartError ? (
          <p className="text-sm text-red-700">{chartError}</p>
        ) : chartPoints.filter((m) => m.value != null && Number.isFinite(m.value)).length === 0 ? (
          <p className="text-sm text-slate-600">Нет числовых измерений в выбранном периоде.</p>
        ) : (
          <MeasurementDynamicsChart points={chartPoints} unit={chartUnit} height={320} />
        )}
      </div>
      {error ? <div className="rounded-md border border-red-200 bg-red-50 p-3 text-red-800">{error}</div> : null}
      <div className="rounded-lg border bg-white">
        <div className="border-b px-4 py-3 font-medium">Измерения</div>
        <div className="divide-y">
          {items.map((m) => (
            <div key={m.id} className="px-4 py-3 text-sm">
              <div>
                logger <span className="font-mono">{m.logger_id.slice(0, 8)}</span> ·{" "}
                {m.value !== null ? `${m.value.toFixed(3)} ${m.unit}` : "n/a"} ·{" "}
                {new Date(m.captured_at).toLocaleString()}
              </div>
              <div className="text-slate-600">
                {m.ok ? "OK" : `Error: ${m.error ?? "unknown"}`}
                {m.out_of_range === true ? (
                  <span className="ml-2 font-medium text-amber-800">Вне допустимого диапазона</span>
                ) : m.out_of_range === false ? (
                  <span className="ml-2 text-slate-500">В диапазоне</span>
                ) : null}
                {m.cv_warnings_json ? (
                  <span className="ml-2 text-xs text-slate-500" title={m.cv_warnings_json}>
                    CV: {m.cv_warnings_json}
                  </span>
                ) : null}{" "}
                {m.image_path ? (
                  <a
                    className="underline underline-offset-4"
                    href={buildMediaUrl(m.image_path)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    image
                  </a>
                ) : null}
              </div>
            </div>
          ))}
          {items.length === 0 && !loading ? <div className="px-4 py-3 text-slate-600">Нет записей по фильтру.</div> : null}
        </div>
      </div>
    </div>
  );
}
