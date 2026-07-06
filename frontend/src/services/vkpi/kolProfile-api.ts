// 件④ KOL 档案页 · 远端块 API(纯前端聚合,零新后端)。
// 这里只封装「兄弟件」的端点(件① signature / 件② production-leadtime / 竞业 competing-activity),
// 契约由对方定,形状未知 → 一律 Row 宽类型,页面侧做防御渲染;端点未挂载(404/网络错)由调用方
// try/catch 走诚实空态,页面本体不炸。既有端点(detail-bundle/cooperation/llm-deep-analysis/
// intelligence-card/competitors)直接复用 services/vkpi/kolPool-api,不重造。

import { apiFetch } from "../http";

export type Row = Record<string, unknown>;

// ── 件①:招牌内容面板(什么最灵)──
// GET /kol-pool/{id}/signature — 兄弟代理端点,未上线前 404 → 调用方降级空态。
export async function getKolSignaturePanel(token: string, kolPoolId: string | number) {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/signature`,
    { cache: "no-store" },
    token,
  );
}

// ── 件②:制作周期(接单→发布 leadtime)──
// GET /kol-pool/{id}/production-leadtime — 兄弟代理端点,同上降级。
export async function getKolProductionLeadtime(token: string, kolPoolId: string | number) {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/production-leadtime`,
    { cache: "no-store" },
    token,
  );
}

// ── 竞业活动(信用块)──
// GET /kol-pool/{id}/competing-activity — 兄弟代理端点;页面侧失败后兜底既有
// getKolPoolCompetitors(/kol-pool/{id}/competitors,已在线),保证信用块永远有真数据可讲。
export async function getKolCompetingActivity(token: string, kolPoolId: string | number) {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/competing-activity`,
    { cache: "no-store" },
    token,
  );
}
