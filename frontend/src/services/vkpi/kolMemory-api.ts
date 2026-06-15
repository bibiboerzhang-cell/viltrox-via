// KOL 长期记忆 API(W3)。纯聚合记忆 + 视频两层分层计划。
// 红线:全部端点零触 viltrox_fit_score;snapshot 显式不含 v6_fit/score 字段。
// 沿用 kolPool-api.ts 的 apiFetch<T>(path, init, token) 调用风格。
import { apiFetch } from "../http";

// GET /api/admin/vkpi/kol-memory/{kol_id}  (require_tab vkpi:read)
// 取 latest 快照(无则现算);返回 snapshot + timeline + lifecycle。
export async function getKolMemory(token: string, kolId: string | number) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/vkpi/kol-memory/${encodeURIComponent(String(kolId))}`,
    { cache: "no-store" },
    token,
  );
}

// POST /api/admin/vkpi/kol-memory/{kol_id}/rebuild  (require_tab vkpi:write)
// 纯聚合重建落快照,不烧 LLM、不触评分。
export async function rebuildKolMemory(token: string, kolId: string | number) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/vkpi/kol-memory/${encodeURIComponent(String(kolId))}/rebuild`,
    { method: "POST" },
    token,
  );
}

// GET /api/admin/vkpi/kol-memory/{kol_id}/video-fullscan-plan?top_n=5  (require_tab vkpi:read)
// 纯读分层:全量物化 metadata vs 限量深析 top-N;不入队、不写库、不烧 provider。
export async function getKolVideoFullscanPlan(token: string, kolId: string | number, topN = 5) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/vkpi/kol-memory/${encodeURIComponent(String(kolId))}/video-fullscan-plan?top_n=${topN}`,
    { cache: "no-store" },
    token,
  );
}
