// 创意资产库 API 封装(板块页范式改版)。
// - searchCreativeSegments:GET /api/admin/vkpi/creative-segments/search?query=&style=&focal=&limit=
//     已深析(final_v1)视频拆段索引:开头/分镜/产品露出 三类段级条目,
//     三路词表过滤(自由词/拍法/焦段),按所属视频播放数降序。
//     实现在 app.domains.content.creative_segments(纯已有分析文本的索引,
//     零切片文件、零外部调用、零 LLM、零写库)。
//     诚实态:深析库空 → status:"empty"+reason;过滤零命中 → matched=0 空 items;
//     内部异常不 500 → status:"error"+reason(调用方诚实降级,绝不编数据)。
//     缩略图三件套(cached/youtube/best)= pool_detail 同款毒缓存自愈链:
//     cached_image_url 已把「上游失败写盘的微型 SVG 占位」判无效,拿不到 = null,
//     前端诚实占位绝不编图。
// 红线:纯读检索,不触 viltrox_fit_score / rule_v0。

import { apiFetch } from "../http";

export interface CreativeSegmentTag {
  key: string;
  label: string;
}

export interface CreativeSegmentVideo {
  evidence_id: number | null;
  title: string;
  content_url: string;
  platform: string;
  view_count: number | null;
  posted_at: string | null;
  /** evidence 原始缩略图 URL(可能为受墙 CDN,前端走 image-proxy) */
  thumbnail_url?: string | null;
  /** 本地图缓存(毒缓存自愈后才算真图);形如 /api/vkpi-media/image-cache/<digest> */
  cached_thumbnail_url?: string | null;
  /** YouTube 官方缩略图(content_url 派生;ytimg/img.youtube 经同源 image-proxy) */
  youtube_thumbnail_url?: string | null;
  /** 链序 cached → raw → youtube;null = 三路皆无,前端诚实占位 */
  best_thumbnail?: string | null;
}

export interface CreativeSegmentKol {
  kol_pool_id: number | null;
  handle: string;
  display_name: string;
}

export interface CreativeSegmentSource {
  evidence_id: number | null;
  layer: string;
  derive_method: string;
}

export interface CreativeSegmentItem {
  segment_id: string;
  segment_type: string; // opening / scene / product_exposure
  description: string;
  timestamp: string | null;
  opening_types: CreativeSegmentTag[];
  styles: CreativeSegmentTag[];
  video_styles: CreativeSegmentTag[];
  focals: string[];
  source: CreativeSegmentSource;
  video: CreativeSegmentVideo;
  kol: CreativeSegmentKol;
}

export interface CreativeFacetEntry {
  key: string;
  label: string;
  segment_count: number;
}

export interface CreativeFacets {
  styles?: CreativeFacetEntry[];
  openings?: CreativeFacetEntry[];
  focals?: Array<{ focal: string; segment_count: number }>;
  segment_types?: CreativeFacetEntry[];
}

export interface CreativeSegmentsResponse {
  status: string; // ready / empty / error
  reason?: string;
  method?: string;
  filters?: { query?: string; style?: string; focal?: string };
  scanned_videos?: number;
  segment_count?: number;
  /** 扫描窗口内 distinct kol_pool_id(KPI 带真值) */
  kol_count?: number;
  matched?: number;
  returned?: number;
  items?: CreativeSegmentItem[];
  facets?: CreativeFacets;
  generated_at?: string;
  note?: string;
}

export interface CreativeSearchOptions {
  query?: string;
  style?: string;
  focal?: string;
  /** 端点封顶 200(单次全量,无 offset 分页 —— 超出封顶如实标注) */
  limit?: number;
}

export function searchCreativeSegments(
  apiToken: string,
  options: CreativeSearchOptions = {},
): Promise<CreativeSegmentsResponse> {
  const qs = new URLSearchParams({
    query: String(options.query || "").trim(),
    style: String(options.style || "").trim(),
    focal: String(options.focal || "").trim(),
    limit: String(options.limit ?? 200),
  });
  // 45s:聚合逐条解析全部 ready 深析 JSON(≈500 条),首载偏慢属实
  return apiFetch<CreativeSegmentsResponse>(
    `/api/admin/vkpi/creative-segments/search?${qs.toString()}`,
    { timeoutMs: 45000 },
    apiToken,
  );
}
