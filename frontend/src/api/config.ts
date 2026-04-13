import { apiFetch } from "./client";

export type PublicConfig = {
  rtmp_base_url: string;
};

export async function getPublicConfig(): Promise<PublicConfig> {
  return apiFetch<PublicConfig>("/api/v1/config/public");
}
