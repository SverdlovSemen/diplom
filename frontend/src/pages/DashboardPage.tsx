import React from "react";
import { MeasurementDynamicsChart } from "../components/MeasurementDynamicsChart";
import { listLoggers, type Logger } from "../api/loggers";
import { buildMediaUrl } from "../api/media";
import {
  adminRoleRequestStatusLabel,
  createAdminRoleRequest,
  getMyAdminRoleRequest,
  type AdminRoleRequest,
} from "../api/adminRoleRequests";
import { ru } from "../i18n/ru";
import { useAuth } from "../auth/AuthContext";
import { EmptyState, FeedbackBanner, SkeletonRows } from "../ui/feedback";
import {
  downloadMeasurementsExport,
  fetchMeasurementsForChart,
  getMeasurementStats,
  listMeasurementAlerts,
  type MeasurementExportFormat,
  type MeasurementAlert,
  listMeasurements,
  type Measurement,
  type MeasurementStats,
} from "../api/measurements";

function humanizeMeasurementError(error: string | null): string {
  if (!error) return ru.common.unknown;
  if (/^OCR failed:/i.test(error)) return "OCR: не удалось распознать число";
  if (error.toLowerCase().includes("нереалистичный скачок")) return "измерение не принято";
  return error;
}

function toDatetimeLocalValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function DashboardPage(): React.ReactElement {
  const { user, refreshMe } = useAuth();
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
  const [exporting, setExporting] = React.useState<MeasurementExportFormat | null>(null);
  const [exportFormat, setExportFormat] = React.useState<MeasurementExportFormat>("xlsx");
  const [error, setError] = React.useState<string | null>(null);
  const [chartPoints, setChartPoints] = React.useState<Measurement[]>([]);
  const [chartLoading, setChartLoading] = React.useState(false);
  const [chartError, setChartError] = React.useState<string | null>(null);
  const [alerts, setAlerts] = React.useState<MeasurementAlert[]>([]);
  const [roleRequest, setRoleRequest] = React.useState<AdminRoleRequest | null>(null);
  const [roleRequestLoading, setRoleRequestLoading] = React.useState(false);
  const [roleRequestError, setRoleRequestError] = React.useState<string | null>(null);
  const defaultPageSize = 30;

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

  React.useEffect(() => {
    if (!user || user.role !== "viewer") return;
    void (async () => {
      try {
        const current = await getMyAdminRoleRequest();
        setRoleRequest(current);
      } catch {
        // non-blocking section
      }
    })();
  }, [user]);

  const applyDefaults = React.useCallback(() => {
    const to = new Date();
    const from = new Date(to.getTime() - 7 * 24 * 60 * 60 * 1000);
    setFromLocal(toDatetimeLocalValue(from));
    setToLocal(toDatetimeLocalValue(to));
    setPage(1);
  }, []);

  const resetFilters = React.useCallback(() => {
    setLoggerFilter("");
    applyDefaults();
    setPageSize(defaultPageSize);
  }, [applyDefaults]);

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
      setError(e instanceof Error ? e.message : ru.dashboard.loadFailed);
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
  const hasActiveFilters = loggerFilter !== "" || pageSize !== defaultPageSize;

  const exportReport = React.useCallback(async () => {
    setExporting(exportFormat);
    setError(null);
    try {
      const fromIso = fromLocal ? new Date(fromLocal).toISOString() : undefined;
      const toIso = toLocal ? new Date(toLocal).toISOString() : undefined;
      await downloadMeasurementsExport(
        exportFormat,
        {
          loggerId: loggerFilter || undefined,
          from: fromIso,
          to: toIso,
        },
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : ru.dashboard.exportFailed);
    } finally {
      setExporting(null);
    }
  }, [exportFormat, fromLocal, toLocal, loggerFilter]);

  const requestAdminRole = React.useCallback(async () => {
    setRoleRequestLoading(true);
    setRoleRequestError(null);
    try {
      await createAdminRoleRequest();
      const current = await getMyAdminRoleRequest();
      setRoleRequest(current);
    } catch (e) {
      setRoleRequestError(e instanceof Error ? e.message : "Не удалось подать заявку");
    } finally {
      setRoleRequestLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (roleRequest?.status === "approved") {
      void refreshMe();
    }
  }, [roleRequest, refreshMe]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">{ru.dashboard.title}</h1>
      <FeedbackBanner
        tone="info"
        message={
          user?.role === "viewer"
            ? ru.dashboard.observerModeHint
            : ru.dashboard.adminModeHint
        }
      />
      {user?.role === "viewer" ? (
        <div className="rounded-lg border bg-white p-4 space-y-2">
          <div className="font-medium">Заявка на роль администратора</div>
          <p className="text-sm text-slate-600">
            Вы можете подать заявку на повышение роли. Подтверждение выполняет действующий администратор.
          </p>
          <div className="text-sm">
            Статус:{" "}
            <span className="font-medium">{adminRoleRequestStatusLabel(roleRequest?.status)}</span>
          </div>
          {roleRequestError ? <FeedbackBanner tone="error" message={roleRequestError} /> : null}
          <button
            type="button"
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-800 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
            onClick={() => void requestAdminRole()}
            disabled={roleRequestLoading || roleRequest?.status === "pending"}
          >
            {roleRequestLoading ? "Отправка..." : "Подать заявку на администратора"}
          </button>
        </div>
      ) : null}
      <div className="rounded-lg border bg-white p-4 space-y-3">
        <div className="font-medium">История измерений</div>
        <p className="text-sm text-slate-600">
          Фильтр по времени снимка и логеру. Пустые поля даты — без ограничения с этой стороны.
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
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
            onClick={() => void load()}
          >
            Обновить
          </button>
          <button
            type="button"
            disabled={exporting !== null}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
            onClick={() => void exportReport()}
          >
            {exporting ? "Экспорт…" : "Скачать"}
          </button>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-600">Формат</span>
            <select
              className="rounded-md border border-slate-300 px-2 py-1.5"
              value={exportFormat}
              onChange={(e) => setExportFormat(e.target.value as MeasurementExportFormat)}
              disabled={exporting !== null}
            >
              <option value="xlsx">Excel (.xlsx)</option>
              <option value="pdf">PDF (сводка)</option>
              <option value="csv">CSV (сырые данные)</option>
            </select>
          </label>
          <button
            type="button"
            className="text-sm text-slate-600 underline rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
            onClick={applyDefaults}
          >
            Последние 7 дней
          </button>
          <button
            type="button"
            className="text-sm text-slate-600 underline rounded-sm disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
            onClick={resetFilters}
            disabled={!hasActiveFilters}
          >
            Сбросить фильтры
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
            className="rounded-md border px-3 py-1 text-sm disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Назад
          </button>
          <button
            type="button"
            disabled={page >= totalPages || loading}
            className="rounded-md border px-3 py-1 text-sm disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
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
            Те же фильтры, что у списка ниже. Мин/макс/среднее — по очищенным значениям:
            ok=true, без критичных CV-предупреждений; для analog дополнительно в пределах шкалы.
          </p>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-slate-600">Всего записей</dt>
              <dd className="font-mono">{stats.count}</dd>
            </div>
            <div>
              <dt className="text-slate-600">С числом</dt>
              <dd className="font-mono">{stats.value_count}</dd>
            </div>
            <div>
              <dt className="text-slate-600">{ru.dashboard.summaryMinMaxAvg}</dt>
              <dd className="font-mono">
                {stats.value_min !== null && stats.value_max !== null && stats.value_avg !== null
                  ? `${stats.value_min.toFixed(3)} / ${stats.value_max.toFixed(3)} / ${stats.value_avg.toFixed(3)}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-slate-600">Ошибки распознавания</dt>
              <dd className="font-mono">{stats.recognition_fail_count}</dd>
            </div>
            <div>
              <dt className="text-slate-600">Вне допустимого диапазона</dt>
              <dd className="font-mono">{stats.out_of_range_count}</dd>
            </div>
          </dl>
        </div>
      ) : null}
      <div className="rounded-lg border bg-white p-4 space-y-2">
        <div className="font-medium">Алерты аномалий</div>
        <p className="text-sm text-slate-600">Последние 20 записей с выходом за допустимый диапазон.</p>
        <div className="divide-y">
          {alerts.map((a) => (
            <div key={a.measurement_id} className="py-2 text-sm">
              <div>
                <span className="font-medium text-amber-800">{a.logger_name}</span> ·{" "}
                {a.value !== null ? `${a.value.toFixed(3)} ${a.unit}` : ru.common.notAvailableShort} ·{" "}
                {new Date(a.captured_at).toLocaleString()}
              </div>
              <div className="text-slate-600">
                {ru.common.loggerShort} <span className="font-mono">{a.logger_id.slice(0, 8)}</span>
                {a.error ? <span className="ml-2 text-red-700">{ru.dashboard.errorShortPrefix} {humanizeMeasurementError(a.error)}</span> : null}
                {a.image_path ? (
                  <a className="ml-2 underline underline-offset-4" href={buildMediaUrl(a.image_path)} target="_blank" rel="noreferrer">
                    {ru.common.image}
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
          График значения по времени кадра. Данные подгружаются через API списка измерений, до 10000 точек.
        </p>
        {!loggerFilter ? (
          <EmptyState message="Выберите конкретный логер в фильтре выше." />
        ) : chartLoading ? (
          <p className="text-sm text-slate-600">{ru.common.loading}</p>
        ) : chartError ? (
          <FeedbackBanner tone="error" message={chartError} />
        ) : chartPoints.filter((m) => m.value != null && Number.isFinite(m.value)).length === 0 ? (
          <EmptyState message="Нет числовых измерений в выбранном периоде." />
        ) : (
          <MeasurementDynamicsChart points={chartPoints} unit={chartUnit} height={320} />
        )}
      </div>
      {error ? <FeedbackBanner tone="error" message={error} /> : null}
      <div className="rounded-lg border bg-white">
        <div className="border-b px-4 py-3 font-medium">Измерения</div>
        <div className="divide-y">
          {loading && items.length === 0 ? <SkeletonRows count={4} /> : null}
          {items.map((m) => (
            <div key={m.id} className="px-4 py-3 text-sm">
              <div>
                {ru.common.loggerShort} <span className="font-mono">{m.logger_id.slice(0, 8)}</span> ·{" "}
                {m.value !== null ? `${m.value.toFixed(3)} ${m.unit}` : ru.common.notAvailableShort} ·{" "}
                {new Date(m.captured_at).toLocaleString()}
              </div>
              <div className="flex flex-wrap items-center gap-2 text-slate-600">
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    m.ok ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"
                  }`}
                >
                  {m.ok ? ru.common.ok : `${ru.common.errorPrefix} ${humanizeMeasurementError(m.error)}`}
                </span>
                {m.out_of_range === true ? (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                    Вне допустимого диапазона
                  </span>
                ) : m.out_of_range === false ? (
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">В диапазоне</span>
                ) : null}
                {m.image_path ? (
                  <a
                    className="underline underline-offset-4"
                    href={buildMediaUrl(m.image_path)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {ru.common.image}
                  </a>
                ) : null}
              </div>
            </div>
          ))}
          {items.length === 0 && !loading ? <EmptyState message="Нет записей по фильтру." /> : null}
        </div>
      </div>
    </div>
  );
}
