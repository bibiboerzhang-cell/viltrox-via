import { apiFetch } from "../http";

// 镜头出镜洞察(车道 L4 · 2026-08-22)· 纯读封装:
//   ① GET /api/admin/vkpi/lens-insights/summary?scope=collection|all —— 按镜头家族/SKU 聚合
//      (出镜视频数 / KOL 数 / 总播放·未实测剔除 / 证据来源分布 / 样例视频)+ 覆盖率 + 未归一原文;
//   ② GET /api/admin/vkpi/lens-insights/kol/{id} —— 单 KOL 用过的镜头。
//   数据源 = vkpi_kol_lens_evidence(迁移 287,回填脚本从深析散文抽取并按产品目录归一);
//   形状 1:1 对齐 backend kol/lens_evidence_store.py,禁编字段。
// 红线:纯读零写库;绝不触 viltrox_fit_score / rule_v0;门面文案不露内部术语(口径进溯源)。

export type LensModality = "visual" | "text" | "voice" | "unspecified";
export type LensResolution = "sku" | "family" | "unresolved";

export interface LensSampleVideo {
  evidence_id?: number;
  kol_pool_id?: number | null;
  kol_name?: string;
  title?: string;
  content_url?: string;
  platform?: string;
  view_count?: number | null;
  modalities?: LensModality[];
}

export interface LensExposureItem {
  lens_key?: string;
  display_name?: string;
  category_main?: string;
  resolution?: LensResolution | string;
  skus?: string[];
  candidate_skus?: string[];
  videos?: number;
  kols?: number;
  /** 点时实测 Σ view_count;全部未实测 → null(绝不当 0) */
  views_total?: number | null;
  views_measured_videos?: number;
  mention_rows?: number;
  modalities?: Partial<Record<LensModality, number>>;
  samples?: LensSampleVideo[];
}

export interface LensUnresolvedItem {
  mention?: string;
  videos?: number;
  kols?: number;
  candidate_skus?: string[];
}

export interface LensCoverage {
  analysed_videos?: number;
  scanned_videos?: number;
  videos_with_products?: number;
  unscanned_videos?: number;
}

export interface LensInsightsSummary {
  contract?: string;
  read_only?: boolean;
  generated_at?: string;
  scope?: { mode?: string; staff_scope_id?: number | null; membership?: string };
  coverage?: LensCoverage;
  summary?: {
    lenses?: number;
    videos_with_products?: number;
    kols_with_products?: number;
    mention_rows?: number;
    unresolved_rows?: number;
    modalities?: Partial<Record<LensModality, number>>;
  };
  modality_labels?: Partial<Record<LensModality, string>>;
  lenses?: LensExposureItem[];
  lenses_truncated?: boolean;
  unresolved?: LensUnresolvedItem[];
  empty_reason?: string | null;
}

export interface KolLensUsage {
  contract?: string;
  kol_pool_id?: number;
  coverage?: LensCoverage;
  modality_labels?: Partial<Record<LensModality, string>>;
  lenses?: LensExposureItem[];
  unresolved?: LensUnresolvedItem[];
  empty_reason?: "no_lens_evidence" | "no_analysed_videos" | string | null;
}

export const LENS_MODALITY_LABEL: Record<LensModality, string> = {
  visual: "画面",
  text: "字幕·文字",
  voice: "口播",
  unspecified: "未注明",
};

export const LENS_RESOLUTION_LABEL: Record<string, string> = {
  sku: "型号已确认",
  family: "系列已确认",
  unresolved: "未能对上目录",
};

export async function getLensInsightsSummary(
  token: string,
  params: { scope?: "collection" | "all"; staffId?: number; limit?: number } = {},
) {
  const query = new URLSearchParams();
  if (params.scope) query.set("scope", params.scope);
  if (params.staffId) query.set("staff_id", String(params.staffId));
  if (params.limit) query.set("limit", String(params.limit));
  const suffix = query.toString();
  return apiFetch<LensInsightsSummary>(`/api/admin/vkpi/lens-insights/summary${suffix ? `?${suffix}` : ""}`, {}, token);
}

export async function getLensInsightsForKol(token: string, kolPoolId: number | string) {
  return apiFetch<KolLensUsage>(`/api/admin/vkpi/lens-insights/kol/${encodeURIComponent(String(kolPoolId))}`, {}, token);
}

/** 证据来源分布 → 有计数的标签列表(零计数不摆);顺序固定 画面 → 字幕·文字 → 口播 → 未注明。 */
export function modalityChips(
  counts: Partial<Record<LensModality, number>> | undefined,
  labels?: Partial<Record<LensModality, string>>,
): Array<{ key: LensModality; label: string; count: number }> {
  const order: LensModality[] = ["visual", "text", "voice", "unspecified"];
  return order
    .map((key) => ({ key, label: labels?.[key] || LENS_MODALITY_LABEL[key], count: Number(counts?.[key]) || 0 }))
    .filter((item) => item.count > 0);
}

/** 总播放文案:未实测条数如实注明;全部未实测 → 「未实测」而非 0。 */
export function lensViewsText(item: LensExposureItem): string {
  const measured = Number(item.views_measured_videos) || 0;
  const videos = Number(item.videos) || 0;
  if (item.views_total == null || measured === 0) return "播放未实测";
  const gap = videos - measured;
  return `播放 ${item.views_total.toLocaleString()}${gap > 0 ? ` · ${gap} 条未实测` : ""}`;
}
