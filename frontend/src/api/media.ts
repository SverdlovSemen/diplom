import { getStoredAccessToken } from "./client";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export function buildMediaUrl(imagePath: string): string {
  const rel = imagePath.startsWith("/") ? imagePath.slice(1) : imagePath;
  const token = getStoredAccessToken();
  const url = new URL(`${apiBaseUrl}/media/${rel}`);
  if (token) url.searchParams.set("access_token", token);
  return url.toString();
}
