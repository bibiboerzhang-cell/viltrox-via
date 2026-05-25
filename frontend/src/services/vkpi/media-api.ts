import { apiFetch } from "../http";

export async function lookupCachedVideoUrl(token: string, platform: string, videoId: string) {
  const params = new URLSearchParams();
  params.set("platform", platform);
  params.set("video_id", videoId);
  try {
    const response = await apiFetch<{ hit?: boolean; cached_url?: string; cachedUrl?: string }>(
      `/api/admin/vkpi/media/video-cache/lookup?${params.toString()}`,
      {},
      token,
    );
    return response.hit ? String(response.cached_url || response.cachedUrl || "") : "";
  } catch {
    return "";
  }
}
