import { apiFetch } from "./client";

export type AdminRoleRequestStatus = "pending" | "approved" | "rejected";

export type AdminRoleRequest = {
  id: string;
  user_id: string;
  user_email: string;
  status: AdminRoleRequestStatus;
  review_comment: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
};

export function adminRoleRequestStatusLabel(status: AdminRoleRequestStatus | null | undefined): string {
  if (status === "pending") return "на рассмотрении";
  if (status === "approved") return "одобрена";
  if (status === "rejected") return "отклонена";
  return "не подана";
}

export async function createAdminRoleRequest(): Promise<void> {
  await apiFetch("/api/v1/admin-role-requests/", {
    method: "POST",
  });
}

export async function getMyAdminRoleRequest(): Promise<AdminRoleRequest | null> {
  return apiFetch<AdminRoleRequest | null>("/api/v1/admin-role-requests/me");
}

export async function listAdminRoleRequests(status?: AdminRoleRequestStatus): Promise<AdminRoleRequest[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<AdminRoleRequest[]>(`/api/v1/admin-role-requests/${q}`);
}

export async function approveAdminRoleRequest(id: string, reviewComment?: string): Promise<AdminRoleRequest> {
  return apiFetch<AdminRoleRequest>(`/api/v1/admin-role-requests/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ review_comment: reviewComment ?? null }),
  });
}

export async function rejectAdminRoleRequest(id: string, reviewComment?: string): Promise<AdminRoleRequest> {
  return apiFetch<AdminRoleRequest>(`/api/v1/admin-role-requests/${id}/reject`, {
    method: "POST",
    body: JSON.stringify({ review_comment: reviewComment ?? null }),
  });
}
