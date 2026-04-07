import React from "react";
import { listMeasurements, type Measurement } from "../api/measurements";

const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export function DashboardPage(): React.ReactElement {
  const [items, setItems] = React.useState<Measurement[]>([]);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    void (async () => {
      try {
        setItems(await listMeasurements(undefined, 30));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load measurements");
      }
    })();
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      {error ? <div className="rounded-md border border-red-200 bg-red-50 p-3 text-red-800">{error}</div> : null}
      <div className="rounded-lg border bg-white">
        <div className="border-b px-4 py-3 font-medium">Recent measurements</div>
        <div className="divide-y">
          {items.map((m) => (
            <div key={m.id} className="px-4 py-3 text-sm">
              <div>
                logger <span className="font-mono">{m.logger_id.slice(0, 8)}</span> ·{" "}
                {m.value !== null ? `${m.value.toFixed(3)} ${m.unit}` : "n/a"} ·{" "}
                {new Date(m.captured_at).toLocaleString()}
              </div>
              <div className="text-slate-600">
                {m.ok ? "OK" : `Error: ${m.error ?? "unknown"}`}{" "}
                {m.image_path ? (
                  <a
                    className="underline underline-offset-4"
                    href={`${apiBase}/media/${m.image_path}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    image
                  </a>
                ) : null}
              </div>
            </div>
          ))}
          {items.length === 0 ? <div className="px-4 py-3 text-slate-600">No measurements yet.</div> : null}
        </div>
      </div>
    </div>
  );
}

