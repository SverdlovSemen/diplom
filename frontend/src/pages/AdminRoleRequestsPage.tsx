import React from "react";

import {
  adminRoleRequestStatusLabel,
  approveAdminRoleRequest,
  listAdminRoleRequests,
  rejectAdminRoleRequest,
  type AdminRoleRequest,
} from "../api/adminRoleRequests";
import { FeedbackBanner } from "../ui/feedback";

export function AdminRoleRequestsPage(): React.ReactElement {
  const [items, setItems] = React.useState<AdminRoleRequest[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [busyId, setBusyId] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listAdminRoleRequests());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить заявки");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function review(id: string, action: "approve" | "reject"): Promise<void> {
    setBusyId(id);
    setError(null);
    try {
      if (action === "approve") {
        await approveAdminRoleRequest(id);
      } else {
        await rejectAdminRoleRequest(id);
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось обработать заявку");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Заявки на роль администратора</h1>
      {error ? <FeedbackBanner tone="error" message={error} /> : null}
      <div className="rounded-lg border bg-white">
        <div className="border-b px-4 py-3 text-sm text-slate-600">
          {loading ? "Загрузка..." : `Всего заявок: ${items.length}`}
        </div>
        <div className="divide-y">
          {items.map((item) => (
            <div key={item.id} className="px-4 py-3">
              <div className="text-sm">
                <span className="font-medium">{item.user_email}</span> · статус:{" "}
                {adminRoleRequestStatusLabel(item.status)}
              </div>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  className="rounded-md bg-emerald-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                  onClick={() => void review(item.id, "approve")}
                  disabled={item.status !== "pending" || busyId === item.id}
                >
                  Одобрить
                </button>
                <button
                  type="button"
                  className="rounded-md bg-red-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                  onClick={() => void review(item.id, "reject")}
                  disabled={item.status !== "pending" || busyId === item.id}
                >
                  Отклонить
                </button>
              </div>
            </div>
          ))}
          {!loading && items.length === 0 ? (
            <div className="px-4 py-3 text-sm text-slate-600">Заявок пока нет.</div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
