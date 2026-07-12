import { apiFetch } from "../http";

// 板块 KPI 按日时序统一端点数据层(挂账迸发①):
//   GET /api/admin/vkpi/board-series?board=<key>&days=30(kol-profile 带 &kol_id=,
//   sku360 带 &sku=)—— 8 板 KPI 卡真 sparkline / 环比数据源,形状 1:1 对齐
//   backend app/domains/dashboard/board_series.py(series/metrics/basis 三键逐指标,
//   禁编字段)。金样板 = myKolBoard-api(board-ext)同构。
// 取数纪律(诚实虚线三闸,boardSeriesVals/boardSeriesDelta 统一执行):
//   ① 信封非 ready(端点失败 / dealers 全表空 empty)→ null → KpiCard spempty;
//   ② 单指标降级(status=error / empty)→ null,同板其余指标照常;
//   ③ delta_pct=null(上窗无数)→ 药丸诚实不渲染,绝不编百分比。
// 红线:纯读封装,零写库;绝不触 viltrox_fit_score 写点 / rule_v0。

export type BoardSeriesKey =
  | "projects"
  | "events"
  | "kol-profile"
  | "autonomy"
  | "launchpad"
  | "sku360"
  | "creative"
  | "dealers";

/** 流量型日点:计数键 count(0 填齐);金额型日点:求和键 value(美分/整数原样) */
export interface VkpiBoardSeriesPoint {
  date?: string;
  count?: number;
  value?: number;
}

export interface VkpiBoardSeriesMetric {
  /** ready / empty(表未建或全表空,诚实空)/ error(单指标降级) */
  status?: string;
  reason?: string;
  current?: number | null;
  previous?: number | null;
  /** 环比(同等流逝窗);上窗 0 → null(前端诚实省略药丸) */
  delta_pct?: number | null;
  table?: string;
  /** rows(行计数)/ cents(美分原样)/ amount(整数原样) */
  unit?: string;
}

export interface VkpiBoardSeriesResponse {
  status?: string;
  reason?: string;
  board?: string;
  days?: number;
  window?: { since?: string; until?: string; prev_since?: string; prev_until?: string };
  /** 参数板回执(kol-profile: kol_id;sku360: sku/resolved_sku/alias_terms) */
  params?: Record<string, unknown>;
  series?: Record<string, VkpiBoardSeriesPoint[]>;
  metrics?: Record<string, VkpiBoardSeriesMetric>;
  basis?: Record<string, string>;
  method?: string;
  generated_at?: string;
}

export async function getBoardSeries(
  token: string,
  params: { board: BoardSeriesKey; days?: number; kolId?: number; sku?: string },
): Promise<VkpiBoardSeriesResponse> {
  const query = new URLSearchParams({ board: params.board, days: String(params.days ?? 30) });
  if (params.kolId != null) query.set("kol_id", String(params.kolId));
  if (params.sku) query.set("sku", params.sku);
  return apiFetch<VkpiBoardSeriesResponse>(`/api/admin/vkpi/board-series?${query.toString()}`, { timeoutMs: 15000 }, token);
}

/** 指标是否可上卡(信封 ready + 指标非降级);empty/error → false(spempty 让位)。 */
function metricReady(resp: VkpiBoardSeriesResponse | null | undefined, name: string): boolean {
  if (!resp || resp.status !== "ready") return false;
  const metric = resp.metrics?.[name];
  if (metric?.status && metric.status !== "ready") return false;
  return true;
}

/** 序列 → KpiCard series 数值数组;任何一闸未过 → null(卡面照旧诚实虚线不炸)。 */
export function boardSeriesVals(
  resp: VkpiBoardSeriesResponse | null | undefined,
  name: string,
): Array<number | null> | null {
  if (!metricReady(resp, name)) return null;
  const pts = resp!.series?.[name];
  if (!Array.isArray(pts) || pts.length === 0) return null;
  return pts.map((p) => {
    const n = Number(p?.count ?? p?.value);
    return Number.isFinite(n) ? n : 0;
  });
}

/** 环比 delta_pct;指标未就绪 / 上窗无数(null)→ null(药丸诚实不渲染)。 */
export function boardSeriesDelta(
  resp: VkpiBoardSeriesResponse | null | undefined,
  name: string,
): number | null {
  if (!metricReady(resp, name)) return null;
  const delta = resp!.metrics?.[name]?.delta_pct;
  return typeof delta === "number" && Number.isFinite(delta) ? delta : null;
}
