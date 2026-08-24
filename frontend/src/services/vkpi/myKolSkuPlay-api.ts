import { apiFetch } from "../http";

// MY KOL 单品播放数据(波 D·C 车道前端 / B 车道后端 my_kol_sku_play_overview_v1):
//   GET  /api/admin/vkpi/my-kol/sku-play-overview
//        —— 被追踪视频按单品(SKU)聚合的播放总览(纯读;收藏 ∪ 授权共享口径,
//           员工恒看本人,管理层全团队,后端 scope 裁剪)。
// data-watch 的 POST 客户端与应答类型只有一份真源:myKolBoard-api.ts 的
// dataWatchMyKolVideo / VkpiDataWatchResponse(墙+详情两个入口都走它,别在本文件复刻)。
// 诚实口径:null = 未实测(绝不当 0 播放);total_views 只汇总实测过的视频;
// delta 需要窗口内 ≥2 次实测,不够 = null 如实待积累。全部字段按「缺席不渲染」读。

/** 播放增量窗口(d1/d7/d30 = 最近实测 − 窗口基线;null = 样本不足不编数)。 */
export interface SkuPlayDelta {
  d1?: number | null;
  d7?: number | null;
  d30?: number | null;
}

export interface SkuPlayItem {
  evidence_id: number;
  kol_pool_id: number;
  kol_name?: string;
  platform?: string;
  title?: string;
  content_url?: string;
  view_count?: number | null;
  like_count?: number | null;
  measured_at?: string | null;
  delta?: SkuPlayDelta;
  tracking_status?: "active" | "paused" | string;
}

export interface SkuPlayGroup {
  sku_code: string;
  sku_name?: string;
  videos?: number;
  kols?: number;
  latest_measured_at?: string | null;
  total_views?: number | null;
  delta?: SkuPlayDelta;
  items?: SkuPlayItem[];
}

export interface VkpiMyKolSkuPlayOverviewResponse {
  contract?: string;
  generated_at?: string | null;
  summary?: {
    skus?: number;
    videos?: number;
    kols?: number;
    measured_videos?: number;
  };
  groups?: SkuPlayGroup[];
  truncated?: boolean;
  empty_reason?: "no_tracked_sku_videos" | string | null;
}

export async function fetchSkuPlayOverview(token: string) {
  return apiFetch<VkpiMyKolSkuPlayOverviewResponse>("/api/admin/vkpi/my-kol/sku-play-overview", {}, token);
}

/** 实测读数文案:有值 → 「1,234」;null → 「未实测」(未实测 ≠ 0 播放)。 */
export function skuPlayCountText(value: number | null | undefined): string {
  if (value != null && Number.isFinite(Number(value))) return Number(value).toLocaleString();
  return "未实测";
}
