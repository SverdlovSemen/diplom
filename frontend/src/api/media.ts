import { getStoredAccessToken } from "./client";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";

export function buildMediaUrl(imagePath: string): string {
  const rel = imagePath.startsWith("/") ? imagePath.slice(1) : imagePath;
  const token = getStoredAccessToken();
  const url = apiBaseUrl ? new URL(`${apiBaseUrl}/media/${rel}`) : new URL(`/media/${rel}`, window.location.origin);
  if (token) url.searchParams.set("access_token", token);
  return url.toString();
}
