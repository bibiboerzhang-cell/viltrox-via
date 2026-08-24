import { apiFetch, jsonBody } from "../http";
import {
  MY_KOL_VIDEO_RECOVERY_CONTRACT,
  normalizeTaskState,
  normalizeVideoTasks,
  type VkpiTaskState,
  type VkpiVideoPageInfo,
  type VkpiVideoTasks,
} from "./myKolVideoTasks";

// MY KOL 板块页 · 看板扩展数据层(M3/M4):
//   ① GET /api/admin/vkpi/my-kol/board-ext?days=30 —— 八组聚合(kpi_series/funnel/
//      platform_dist/fit_dist/contact_coverage/views_top/recent_videos/v_content)
//      类型化 fetch;M4 把各组契约逐组类型化(形状 1:1 对齐 backend
//      my_kol_board_ext.py 构建器,禁编字段);v_content 五档互斥证据 + v_kol_ids /
//      v_kol_ids_truncated / tiers_by_kol(库「有 V 视频」名单精确过滤 + KOL 级
//      去重计数);recent_videos(内容墙)= 收藏集最近采集视频
//      带缩略图三件套(创意库同一条毒缓存自愈链,best_thumbnail 语义)。
//   ② GET /api/admin/vkpi/kol-pool/{id}/videos —— 单 KOL 全部 evidence 视频(类型化);
//   ③ classifyVContent —— 后端 my_kol_board_ext.classify_v_content 的前端同构复现:
//      项目关联 > latest ready final_v1 positive/products > 中英文标题品牌词 >
//      final_v1 explicit false > 未判定；只消费结构化证据，不暴露深析原文;
//   ④ mapLibraryRows / filterLibraryRows —— aggregate.pool_favorites 行 → 库行模型
//      (收藏/共享/认领桥/进行中)+ 纯函数过滤(V 名单精确/平台/搜索/漏斗阶段)。
// 红线:纯读封装,零写库;绝不触 viltrox_fit_score 写点 / rule_v0;fit 分只作只读透传。

export type Row = Record<string, unknown>;

/* ============ ① board-ext 七组聚合(逐组契约对齐 my_kol_board_ext.py,禁编字段) ============ */

export interface VkpiBoardExtGroup {
  status?: string;
  reason?: string;
  basis?: string | Record<string, string>;
  [key: string]: unknown;
}

/** 存量型日点:快照缺日 value=null 如实断点(绝不 0 填冒充清零) */
export interface VkpiSeriesPoint {
  date?: string;
  value?: number | null;
}

/** 流量型日点:计数 0 填齐(今天为进行中日) */
export interface VkpiCountPoint {
  date?: string;
  count?: number;
}

/** 环比读数:上窗缺数 → delta_pct=null(前端诚实省略药丸) */
export interface VkpiKpiSeriesMetric {
  current?: number | null;
  previous?: number | null;
  delta_pct?: number | null;
  table?: string;
}

export interface VkpiKolViewsPoint {
  status?: string;
  views_total?: number;
  measured?: number;
  evidence_total?: number;
  /** view_count 非空行 / 全 evidence 行;分母 0 → null */
  fill_rate?: number | null;
  basis?: string;
}

export interface VkpiKpiSeriesGroup extends VkpiBoardExtGroup {
  granularity?: string;
  series?: {
    pool_followers?: VkpiSeriesPoint[];
    new_videos?: VkpiCountPoint[];
    official_followers?: VkpiSeriesPoint[];
    official_views?: VkpiSeriesPoint[];
  };
  metrics?: {
    pool_followers?: VkpiKpiSeriesMetric;
    new_videos?: VkpiKpiSeriesMetric;
    official_followers?: VkpiKpiSeriesMetric;
    official_views?: VkpiKpiSeriesMetric;
  };
  /** KOL 内容播放:点时实测无时序 → 无 series 无 delta(诚实缺席) */
  kol_views?: VkpiKolViewsPoint;
}

export interface VkpiFunnelSegment {
  stage?: string;
  label?: string;
  count?: number;
  /** 该段吸收的真库 raw 阶段值(库行过滤联动用同一份真值,零本地猜) */
  raw_stages?: string[];
}

export interface VkpiFunnelGroup extends VkpiBoardExtGroup {
  total?: number;
  items?: VkpiFunnelSegment[];
  /** 未识别新阶段值的诚实桶(绝不吞行) */
  other?: Array<{ stage?: string; count?: number }>;
}

export interface VkpiPlatformDistGroup extends VkpiBoardExtGroup {
  items?: Array<{ platform?: string; count?: number }>;
  total?: number;
}

export interface VkpiFitBucket {
  bucket?: string;
  min?: number;
  max?: number;
  count?: number;
}

export interface VkpiFitDistGroup extends VkpiBoardExtGroup {
  buckets?: VkpiFitBucket[];
  /** fit 分为空的诚实桶(绝不当 0 分) */
  unscored?: number;
  scored_total?: number;
  total?: number;
}

export interface VkpiContactCoverageGroup extends VkpiBoardExtGroup {
  types?: Array<{ contact_type?: string; count?: number }>;
  covered?: number;
  total?: number;
  /** 收藏集覆盖率;分母 0 → null */
  coverage?: number | null;
}

export interface VkpiViewsTopItem {
  kol_pool_id?: number;
  display_name?: string;
  handle?: string;
  platform?: string;
  total_views?: number;
  video_count?: number;
}

export interface VkpiViewsTopGroup extends VkpiBoardExtGroup {
  items?: VkpiViewsTopItem[];
}

/** 内容墙条目:形状 = /kol-pool/{id}/videos 行的超集(KOL 归属 + 缩略图三件套增量),
    两数据源(board-ext 聚合 / 逐 KOL 端点)共用同一套卡渲染与 V 判定。 */
export interface VkpiRecentVideoItem extends VkpiKolPoolVideoRow {
  /** 收藏集 KOL 展示名(board-ext 下发;逐 KOL 端点行缺席时由调用方补) */
  kol_name?: string;
  kol_handle?: string;
  /** YouTube 官方缩略图(content_url 派生;ytimg/img.youtube 经同源 image-proxy) */
  youtube_thumbnail_url?: string | null;
  /** 毒缓存自愈链产物,链序 cached → raw → youtube;null = 三路皆无,前端诚实占位 */
  best_thumbnail?: string | null;
  /** evidence 采集入库时间(created_at;发布时间缺席时的墙排序回退) */
  collected_at?: string | null;
}

export interface VkpiRecentVideosGroup extends VkpiBoardExtGroup {
  items?: VkpiRecentVideoItem[];
  /** 后端封顶常量(SQL + Python 双封顶)原样透出 */
  limit?: number;
}

export interface VkpiVContentGroup extends VkpiBoardExtGroup {
  total_evidence?: number;
  /** 五档中前三档正向证据的 evidence 条数。 */
  v_related_evidence?: number;
  /** 至少一条 cooperation / analysis_confirmed / title_mention evidence 的去重 KOL 数。 */
  v_kol_count?: number;
  /** 同一正向判据去重 kol_pool_id 升序名单(封顶 2000;库「有 V 视频」精确过滤用) */
  v_kol_ids?: number[];
  /** 名单超封顶被截断 —— 前端过滤必须如实降级提示,绝不装全量 */
  v_kol_ids_truncated?: boolean;
  /** 截断时后端给的原话说明(原样透出) */
  v_kol_ids_note?: string;
  tiers?: {
    cooperation?: number;
    analysis_confirmed?: number;
    title_mention?: number;
    not_related?: number;
    undetermined?: number;
  };
  /** KOL 级去重计数(同一 KOL 跨 evidence 可落多档,与条数级 tiers 口径区分) */
  tiers_by_kol?: {
    cooperation_kols?: number;
    analysis_confirmed_kols?: number;
    title_mention_kols?: number;
    not_related_kols?: number;
    undetermined_kols?: number;
  };
}

export interface VkpiMyKolBoardExtResponse {
  status?: string;
  days?: number;
  window?: { since?: string; until?: string; prev_since?: string; prev_until?: string };
  staff_scope_id?: number | null;
  kpi_series?: VkpiKpiSeriesGroup;
  funnel?: VkpiFunnelGroup;
  platform_dist?: VkpiPlatformDistGroup;
  fit_dist?: VkpiFitDistGroup;
  contact_coverage?: VkpiContactCoverageGroup;
  views_top?: VkpiViewsTopGroup;
  recent_videos?: VkpiRecentVideosGroup;
  v_content?: VkpiVContentGroup;
  method?: string;
  generated_at?: string;
}

export async function getMyKolBoardExt(token: string, params: { days?: number; staffId?: number } = {}) {
  const query = new URLSearchParams({ days: String(params.days ?? 30) });
  if (params.staffId != null) query.set("staff_id", String(params.staffId));
  return apiFetch<VkpiMyKolBoardExtResponse>(`/api/admin/vkpi/my-kol/board-ext?${query.toString()}`, {}, token);
}

/* ============ ② 单 KOL 全部 evidence 视频(/kol-pool/{id}/videos 类型化) ============ */

export type ContentTrackingStatus = "tracked" | "failed" | "stale" | "insufficient_history" | "unavailable";
export type ContentFreshness = "fresh" | "stale" | "never" | "unavailable";

export interface VkpiContentMetricAttempt {
  status?: "success" | "failed" | "legacy_current_only" | string;
  fetched_at?: string | null;
  views?: number | null;
  likes?: number | null;
  comments?: number | null;
  shares?: number | null;
}

export interface VkpiKolVideoProductLink {
  product_sku?: string;
  relation_type?: string;
  source?: string;
  confidence?: number | null;
  model_name?: string | null;
  marketing_name?: string | null;
}

export interface VkpiKolPoolVideoRow {
  evidence_id?: number;
  id?: number;
  kol_pool_id?: number;
  project_id?: number | null;
  content_url?: string;
  platform?: string;
  title?: string;
  video_title?: string;
  thumbnail_url?: string | null;
  cached_thumbnail_url?: string | null;
  cached_video_url?: string | null;
  view_count?: number | null;
  like_count?: number | null;
  comment_count?: number | null;
  share_count?: number | null;
  duration_seconds?: number | null;
  publish_date?: string | null;
  posted_at?: string | null;
  evidence_type?: string | null;
  media_kind?: string | null;
  image_urls?: string[] | null;
  has_final_v1_cache?: boolean;
  has_keyframe_qa_cache?: boolean;
  /** 深析结构化品牌证据三态(画面/字幕/口播必须有带时间戳证据才 present;absent 须完整查过
   *  画面+音频;其余 unknown)。board-ext recent_videos 下发;逐 KOL 端点可缺席(null)。 */
  llm_viltrox_status?: VBrandEvidenceStatus | null;
  /** 旧版布尔结论;只在结构化三态缺席时作回退。null = 未深析。 */
  llm_viltrox_detected?: boolean | null;
  llm_viltrox_products?: string[] | null;
  llm_competitor_mentions?: string[] | null;
  /** board-ext recent_videos 由共享 SQL CTE 直接下发；逐 KOL 端点可缺席并由纯函数同构派生。 */
  v_tier?: VContentTier;
  /** 指标快照读模型；缺迁移旧库返回 unavailable，绝不触发刷新。 */
  last_attempt?: VkpiContentMetricAttempt | null;
  last_success?: VkpiContentMetricAttempt | null;
  sample_count?: number;
  attempt_count?: number;
  views_delta_24h?: number | null;
  views_delta_7d?: number | null;
  delta_24h_status?: "ready" | "insufficient_history" | string;
  delta_7d_status?: "ready" | "insufficient_history" | string;
  freshness?: ContentFreshness;
  tracking_status?: ContentTrackingStatus;
  history_capped?: boolean;
  /** 由服务端 evidence-product 关联表返回；只用于展示，不在前端推断产品。 */
  product_links?: VkpiKolVideoProductLink[];
  product_skus?: string[];
  /** 契约 my_kol_video_recovery_v1:COALESCE(publish_date, posted_at, created_at),keyset 排序键。 */
  published_at?: string | null;
  /** 播放追踪 / 深析 / 关键帧复核的持久任务态 + 数据真值;只有 /my-kol/{id}/videos 下发,
   *  board-ext recent_videos 缺席(null)。形状与门面文案见 services/vkpi/myKolVideoTasks.ts。 */
  tasks?: VkpiVideoTasks | null;
}

/** GET /my-kol/{id}/videos 响应(契约 my_kol_video_recovery_v1;顶层 total/returned/has_more/next_cursor 为 page 的镜像)。 */
export interface VkpiMyKolVideoPage {
  contract?: typeof MY_KOL_VIDEO_RECOVERY_CONTRACT | string;
  kol_pool_id?: number;
  read_only?: boolean;
  profile_crawl?: VkpiTaskState;
  items?: VkpiKolPoolVideoRow[];
  summary?: { total?: number; views_total?: number; views_measured?: number; final_v1_ready?: number };
  page?: VkpiVideoPageInfo;
  total?: number;
  returned?: number;
  has_more?: boolean;
  next_cursor?: string | null;
}

/**
 * 读一页视频库(keyset 游标,published_at DESC, id DESC)。cursor 只能是上一页 page.next_cursor
 * 的不透明串;旧 offset/v2 游标服务端 400。响应里的 TaskState 已归一(缺席→未发起/不可用)。
 */
export async function getMyKolPoolVideos(token: string, kolPoolId: number | string, limit = 60, cursor?: string | null) {
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor) query.set("cursor", cursor);
  const response = await apiFetch<VkpiMyKolVideoPage>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/videos?${query.toString()}`,
    {},
    token,
  );
  return normalizeVideoPage(response);
}

export function normalizeVideoPage(response: VkpiMyKolVideoPage | null | undefined): VkpiMyKolVideoPage {
  const source = response && typeof response === "object" ? response : {};
  const items = (Array.isArray(source.items) ? source.items : []).map((video) => ({
    ...video,
    tasks: video && typeof video === "object" && "tasks" in video ? normalizeVideoTasks((video as VkpiKolPoolVideoRow).tasks) : null,
  }));
  const page = source.page && typeof source.page === "object" ? source.page : null;
  const hasMore = Boolean(page ? page.has_more : source.has_more);
  const nextCursor = (page ? page.next_cursor : source.next_cursor) || null;
  return {
    ...source,
    items,
    profile_crawl: source.profile_crawl ? normalizeTaskState(source.profile_crawl) : undefined,
    page: {
      limit: Number(page?.limit ?? items.length) || items.length,
      returned: Number(page?.returned ?? source.returned ?? items.length) || items.length,
      has_more: hasMore && Boolean(nextCursor),
      next_cursor: hasMore ? nextCursor : null,
      cursor_kind: String(page?.cursor_kind || "published_at_id"),
      order: String(page?.order || "published_at_desc_id_desc"),
    },
    total: Number(source.total ?? source.summary?.total ?? items.length) || 0,
    returned: Number(source.returned ?? page?.returned ?? items.length) || items.length,
    has_more: hasMore && Boolean(nextCursor),
    next_cursor: hasMore ? nextCursor : null,
  };
}

export interface VkpiMyKolVideoQueueResponse {
  status?: "queued" | "already_queued" | string;
  job_id?: number;
  job_type?: string;
  evidence_id?: number;
  kol_pool_id?: number;
  product_skus?: string[];
  product_links_created?: number;
  existing_evidence?: boolean;
  new_url_resolution_supported?: boolean;
  /** HTTP 边界只排队，服务端应始终返回 false。 */
  provider_calls_performed?: boolean;
}

/** 关联一条已归属当前 KOL 的 evidence URL 与产品，并排队刷新指标。 */
export async function trackMyKolExistingVideo(
  token: string,
  kolPoolId: number | string,
  payload: { contentUrl: string; productSkus?: string[] },
) {
  return apiFetch<VkpiMyKolVideoQueueResponse>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/videos`,
    {
      method: "POST",
      body: jsonBody({
        content_url: payload.contentUrl,
        product_skus: payload.productSkus ?? [],
      }),
    },
    token,
  );
}

/** 为一条已存在且归属当前 KOL 的 evidence 排队指标刷新；浏览器不直连 provider。 */
export async function refreshMyKolVideoMetrics(
  token: string,
  kolPoolId: number | string,
  evidenceId: number | string,
) {
  return apiFetch<VkpiMyKolVideoQueueResponse>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/videos/${encodeURIComponent(String(evidenceId))}/refresh`,
    { method: "POST" },
    token,
  );
}

/* ============ ②b 单 KOL 最近内容跟进(显式订阅,与“已抓取”严格分层) ============ */

export * from "./myKolContentMonitoring-api";

/* ============ ②c 已深析 YouTube 视频的 Gemini 关键帧复核 ============ */

export interface VkpiKeyframeQaQueueResponse {
  status?: "queued" | "already_queued" | "already_reviewed" | "unsupported_platform" | "final_v1_not_ready" | "ai_disabled" | string;
  kol_pool_id?: number;
  evidence_id?: number;
  derive_method?: string;
  reason?: string;
  provider_gate_reason?: string;
  provider_calls?: boolean;
  write_db?: boolean;
  job?: { id?: number; status?: string } | null;
  cache?: { id?: number; model?: string; updated_at?: string | null } | null;
}

/**
 * 只把已存在的 ready final_v1 交给后台关键帧 QA 队列。
 * HTTP 回执仅表示排队/已有结果，不代表 Gemini 已在本次请求完成分析。
 */
export async function enqueueMyKolVideoKeyframeQa(
  token: string,
  kolPoolId: number | string,
  evidenceId: number | string,
) {
  return apiFetch<VkpiKeyframeQaQueueResponse>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/enqueue-video-keyframe-qa`,
    { method: "POST", body: jsonBody({ evidence_id: evidenceId }) },
    token,
  );
}

/* ——— 一键「数据关注」(收口波 2026-08-23 · D 车道):POST …/videos/{evidence_id}/data-watch ——— */

export interface VkpiDataWatchSkuCandidate {
  sku_code?: string;
  sku_name?: string;
}

/** 契约(与后端 video_data_watch 并行锁定,禁编字段):
 *  status=tracking → 已登记(skus + sku_source: manual|existing|auto,refresh 为排队真值);
 *  status=sku_required → 服务端认不出关联产品(HTTP 200 + candidates 候选),
 *  由前端回落「关联 SKU」流程 —— 绝不无 SKU 假登记。 */
export interface VkpiDataWatchResponse {
  status?: "tracking" | "sku_required" | string;
  evidence_id?: number;
  kol_pool_id?: number;
  skus?: string[];
  sku_source?: "manual" | "existing" | "auto" | string;
  sku_provenance?: {
    relation_type?: "manual" | "detected" | "confirmed" | "existing" | string;
    source?: "title_alias_v1" | "my_kol_video_tracking" | "existing_link" | string;
    confidence?: number | null;
    requires_human_confirmation?: boolean;
  };
  tracking?: "active" | string;
  refresh?: "queued" | "already_queued" | string;
  candidates?: VkpiDataWatchSkuCandidate[];
}

/** 一键数据关注:自动关联产品 + 登记持久追踪 + 排队指标刷新(只排队,不冒充实时完成)。
    productSkus 缺省发空体,让服务端按「已关联 > 自动识别」顺序认 SKU。 */
export async function dataWatchMyKolVideo(
  token: string,
  kolPoolId: number | string,
  evidenceId: number | string,
  productSkus?: string[],
) {
  return apiFetch<VkpiDataWatchResponse>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/videos/${encodeURIComponent(String(evidenceId))}/data-watch`,
    { method: "POST", body: jsonBody(productSkus && productSkus.length ? { product_skus: productSkus } : {}) },
    token,
  );
}

function signedDelta(value: number): string {
  const normalized = Number(value);
  if (!Number.isFinite(normalized)) return "待积累";
  return `${normalized > 0 ? "+" : ""}${normalized.toLocaleString()}`;
}

function refreshStamp(video: VkpiKolPoolVideoRow): string {
  if (video.last_success?.status === "legacy_current_only") return "";
  const raw = String(video.last_success?.fetched_at || "").trim();
  return raw ? raw.replace("T", " ").slice(0, 16) : "";
}

/** 快照趋势文案；明确是最后一次抓取，不使用“实时”表述。空串表示来源未下发趋势字段。 */
export function videoTrendText(video: VkpiKolPoolVideoRow): string {
  const status = video.tracking_status;
  if (!status) return "";
  const stamp = refreshStamp(video);
  if (status === "unavailable") return "趋势追踪不可用";
  if (status === "failed") return `刷新失败${stamp ? ` · 上次成功 ${stamp}` : ""}`;
  if (status === "insufficient_history") return `趋势待积累${stamp ? ` · 最后刷新 ${stamp}` : ""}`;
  const delta24 = video.delta_24h_status === "ready" && video.views_delta_24h != null
    ? signedDelta(video.views_delta_24h)
    : "待积累";
  const delta7 = video.delta_7d_status === "ready" && video.views_delta_7d != null
    ? signedDelta(video.views_delta_7d)
    : "待积累";
  const prefix = status === "stale" || video.freshness === "stale" ? "数据已陈旧 · " : "";
  return `${prefix}24h ${delta24} · 7d ${delta7}${stamp ? ` · 最后刷新 ${stamp}` : ""}`;
}

/* ============ ③ Viltrox 内容证据分级(项目 / final_v1 / 标题 / 未识别 / 未判定) ============ */

export type VContentTier = "cooperation" | "analysis_confirmed" | "title_mention" | "not_related" | "undetermined";
export type VideoRelationFilter = "all" | "viltrox" | "undetermined" | "not_related";
/** 深析结构化品牌证据三态(与后端 v_content CTE 同口径)。 */
export type VBrandEvidenceStatus = "present" | "absent" | "unknown";

const VILTROX_TITLE_TOKENS = ["viltrox", "唯卓仕", "唯卓"] as const;

function normalizeBrandStatus(value: unknown): VBrandEvidenceStatus | "" {
  const text = String(value ?? "").trim().toLowerCase();
  return text === "present" || text === "absent" || text === "unknown" ? text : "";
}

/**
 * 五档分级,与后端 my_kol_board_ext_sql.V_CONTENT_CLASSIFIED_CTE 逐条同构(前端只在服务端未下发 v_tier 时派生):
 *   项目关联 > 结构化证据 present(画面/字幕/口播) > [无结构化块时]旧布尔 true/产品非空 >
 *   标题品牌词 > 结构化证据 absent > [无结构化块时]旧布尔 false > 待深析(含 unknown)。
 * 员工反馈 #4 要点:unknown(未完整检查/画面不清/口播不可辨)绝不当「不相关」,落「待深析」单独一档;
 * 只有 absent(完整查过画面+音频仍没见 V)才是「深析未见 V」。
 */
export function classifyVContent(
  projectId: unknown,
  titleText: unknown,
  analysis: { status?: VBrandEvidenceStatus | string | null; detected?: boolean | null; products?: unknown } = {},
): VContentTier {
  const pid = projectId == null ? "" : String(projectId).trim();
  if (pid && pid !== "0") return "cooperation";
  const status = normalizeBrandStatus(analysis.status);
  const legacyPositive = analysis.detected === true || (Array.isArray(analysis.products) && analysis.products.length > 0);
  if (status === "present" || (status === "" && legacyPositive)) return "analysis_confirmed";
  const title = String(titleText ?? "").toLowerCase();
  if (VILTROX_TITLE_TOKENS.some((token) => title.includes(token))) return "title_mention";
  if (status === "absent" || (status === "" && analysis.detected === false)) return "not_related";
  return "undetermined";
}

/** Strongest available bounded proof wins: project > structured present > title brand token > structured absent > pending. */
export function classifyVideoRow(video: VkpiKolPoolVideoRow): VContentTier {
  if (["cooperation", "analysis_confirmed", "title_mention", "not_related", "undetermined"].includes(String(video.v_tier || ""))) {
    return video.v_tier as VContentTier;
  }
  return classifyVContent(video.project_id, `${video.video_title || ""} ${video.title || ""}`, {
    status: video.llm_viltrox_status,
    detected: video.llm_viltrox_detected,
    products: video.llm_viltrox_products,
  });
}

// 门面文案(卡片角标 / 筛选 chip):只说业务口径,不露内部术语。
//   「画面/口播识别 V」= 深析在画面、字幕或口播里拿到带时间戳的 Viltrox 证据(标题单独不算);
//   「待深析」= 还没深析,或深析未完整检查/证据不足 —— 不是「不相关」。
export const V_TIER_LABEL: Record<VContentTier, string> = {
  cooperation: "合作产出",
  analysis_confirmed: "画面/口播识别 V",
  title_mention: "标题提及 V",
  not_related: "深析未见 V",
  undetermined: "待深析",
};

export function isVRelatedTier(tier: VContentTier): boolean {
  return tier === "cooperation" || tier === "analysis_confirmed" || tier === "title_mention";
}

function normalizeRelationFilter(filter: boolean | VideoRelationFilter): VideoRelationFilter {
  return typeof filter === "boolean" ? (filter ? "viltrox" : "all") : filter;
}

export function filterClassifiedVideos<T extends VkpiKolPoolVideoRow>(
  classified: Array<{ video: T; tier: VContentTier }>,
  filter: boolean | VideoRelationFilter,
): Array<{ video: T; tier: VContentTier }> {
  const normalized = normalizeRelationFilter(filter);
  if (normalized === "viltrox") return classified.filter(({ tier }) => isVRelatedTier(tier));
  if (normalized === "undetermined") return classified.filter(({ tier }) => tier === "undetermined");
  if (normalized === "not_related") return classified.filter(({ tier }) => tier === "not_related");
  return [...classified];
}

/** image/carousel 类 evidence(IG 图文/轮播):展示照常,深析批次必须剔除(无视频可下)。 */
export function isImageKindVideo(video: VkpiKolPoolVideoRow): boolean {
  const kind = String(video.media_kind ?? video.evidence_type ?? "").trim().toLowerCase();
  return kind === "image" || kind === "carousel";
}

export interface ClassifiedVideo {
  video: VkpiKolPoolVideoRow;
  tier: VContentTier;
}

/** 详情弹窗小结口径:播放合计只算实测(NULL 剔除并计条数);品牌相关只算三类正向证据。 */
export function summarizeKolVideos(videos: VkpiKolPoolVideoRow[]) {
  const classified: ClassifiedVideo[] = videos.map((video) => ({ video, tier: classifyVideoRow(video) }));
  const measuredCount = classified.filter(({ video }) => video.view_count != null).length;
  const viewsTotal = classified.reduce((sum, { video }) => sum + (video.view_count != null ? Number(video.view_count) || 0 : 0), 0);
  return {
    classified,
    measuredCount,
    unmeasuredCount: classified.length - measuredCount,
    viewsTotal,
    vRelatedCount: classified.filter(({ tier }) => isVRelatedTier(tier)).length,
    unrelatedCount: classified.filter(({ tier }) => tier === "not_related").length,
    undeterminedCount: classified.filter(({ tier }) => tier === "undetermined").length,
    analyzedCount: classified.filter(({ video }) => Boolean(video.has_final_v1_cache)).length,
    unanalyzed: classified.filter(({ video }) => !video.has_final_v1_cache && !isImageKindVideo(video)).map(({ video }) => video),
  };
}

/** 二级关系筛选 + 排序(时间/播放;未实测排最后)。纯函数,不改入参。
    泛型:内容墙条目(VkpiRecentVideoItem)与弹窗行共用,不丢子类型字段。 */
export function sortClassifiedVideos<T extends VkpiKolPoolVideoRow>(
  classified: Array<{ video: T; tier: VContentTier }>,
  filter: boolean | VideoRelationFilter,
  sortBy: "time" | "views",
): Array<{ video: T; tier: VContentTier }> {
  const base = filterClassifiedVideos(classified, filter);
  if (sortBy === "views") base.sort((a, b) => {
    const aViews = a.video.view_count == null ? Number.NEGATIVE_INFINITY : Number(a.video.view_count);
    const bViews = b.video.view_count == null ? Number.NEGATIVE_INFINITY : Number(b.video.view_count);
    return (Number.isFinite(bViews) ? bViews : Number.NEGATIVE_INFINITY)
      - (Number.isFinite(aViews) ? aViews : Number.NEGATIVE_INFINITY);
  });
  else base.sort((a, b) => String(b.video.publish_date || b.video.posted_at || "").localeCompare(String(a.video.publish_date || a.video.posted_at || "")));
  return base;
}

/** evidence 记录预览行(详情弹窗溯源;口径文案与后端 basis 对齐,禁编造)。 */
export function videoRecordRows(video: VkpiKolPoolVideoRow): Array<[string, string]> {
  return [
    ["表", "vkpi_kol_video_evidence"],
    ["id", `#${video.evidence_id ?? video.id ?? "—"}`],
    ["project_id", video.project_id != null ? `#${video.project_id}` : "—"],
    ["Viltrox 判定", `${V_TIER_LABEL[classifyVideoRow(video)]} · 五档顺序(项目关联 / 画面·字幕·口播证据 present / 标题品牌词 / 证据 absent / 待深析=未深析或 unknown)`],
    ["证据三态", video.llm_viltrox_status ? video.llm_viltrox_status : video.llm_viltrox_detected == null ? "未深析" : `旧布尔 ${video.llm_viltrox_detected ? "true" : "false"}(无结构化证据块)`],
    ["view_count", video.view_count != null ? String(video.view_count) : "NULL(未实测 ≠ 0 播放)"],
    ["发布时间", String(video.publish_date || video.posted_at || "—")],
    ["深析缓存", video.has_final_v1_cache ? "final_v1 ready" : "无(可入队深析)"],
    ["原帖", String(video.content_url || "—")],
  ];
}

/* ============ ④ 库行模型:aggregate.pool_favorites → KolLibraryRow ============ */

export interface KolLibraryRowProject {
  project_id?: number | string | null;
  project_name?: string | null;
  stage?: string | null;
  stage_status?: string | null;
}

export interface KolLibraryRow {
  poolId: number;
  name: string;
  handle: string;
  /** 平台原值小写(youtube/instagram/…),徽章展示由 UI 层负责 */
  platform: string;
  followers: number | null;
  /** viltrox_fit_score 只读透传(展示层绝不回写) */
  fit: number | null;
  avatarUrl: string;
  profileUrl: string;
  country: string;
  isShared: boolean;
  sharedByName: string;
  projects: KolLibraryRowProject[];
  /** 本人 active 认领桥(claims FK 是 kols.id,按平台+名称桥接;真值以 viewer-context 为准) */
  claim: { id: string; expiresAt: string } | null;
  /** 第一条邮箱类联系方式(仅批量导出 CSV 用,导出时脱敏;行内展示不消费) */
  email: string;
  createdAt: string;
}

function num(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseProjects(fav: Row): KolLibraryRowProject[] {
  // aggregate 已给 projects 数组;projects_json 可能以字符串到达(psycopg/json_agg 已知形态),防御性解析。
  const raw = fav.projects ?? fav.projects_json;
  if (Array.isArray(raw)) return raw as KolLibraryRowProject[];
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? (parsed as KolLibraryRowProject[]) : [];
    } catch {
      return [];
    }
  }
  return [];
}

function pickEmail(contacts: unknown): string {
  if (!Array.isArray(contacts)) return "";
  for (const raw of contacts) {
    if (!raw || typeof raw !== "object") continue;
    const item = raw as Row;
    const type = String(item.contact_type || "").toLowerCase();
    const value = String(item.contact_value || "").trim();
    if (value && (type.includes("email") || value.includes("@"))) return value;
  }
  return "";
}

export function mapLibraryRows(favorites: Row[] | undefined, claims: Row[] | undefined): KolLibraryRow[] {
  // 本人 active 认领(vkpi_kol_claims FK 是 kols.id,不是 kol_pool_id)——只能按
  // 平台+名称/handle 桥接做「已认领」徽的行级提示;详情内以 viewer-context 端点为真值。
  const claimByKey = new Map<string, { id: string; expiresAt: string }>();
  (claims || []).forEach((claim) => {
    if (String(claim.status || "").toLowerCase() !== "active") return;
    const name = String(claim.kol_name || "").trim().toLowerCase();
    const platform = String(claim.kol_platform || "").trim().toLowerCase();
    if (!name) return;
    claimByKey.set(`${platform}:${name}`, {
      id: String(claim.id ?? ""),
      expiresAt: String(claim.expires_at || ""),
    });
  });
  return (favorites || []).map((fav) => {
    const platform = String(fav.platform || "").trim().toLowerCase();
    const handle = String(fav.handle || "");
    const name = String(fav.display_name || handle || "—");
    const claim =
      claimByKey.get(`${platform}:${name.toLowerCase()}`) ||
      claimByKey.get(`${platform}:${handle.toLowerCase()}`) ||
      null;
    return {
      poolId: Number(fav.kol_pool_id) || 0,
      name,
      handle,
      platform,
      followers: num(fav.followers),
      fit: num(fav.viltrox_fit_score),
      avatarUrl: String(fav.avatar_url || ""),
      profileUrl: String(fav.profile_url || ""),
      country: String(fav.country || ""),
      isShared: Boolean(fav.is_shared),
      sharedByName: String(fav.shared_by_name || ""),
      projects: parseProjects(fav),
      claim,
      email: pickEmail(fav.contacts),
      createdAt: String(fav.created_at || ""),
    };
  });
}

/** 合作漏斗点段联动:段(canonical)+ 展示名 + 该段吸收的真库 raw 阶段值(board-ext 下发) */
export interface LibraryStageFilter {
  stage: string;
  label: string;
  rawStages: string[];
}

export interface LibraryFilter {
  /** 「有 V 视频」筛选:优先 board-ext v_kol_ids 名单精确过滤;名单缺席时降级为已挂项目近似 */
  vOnly: boolean;
  /** 平台原值小写;空 = 全部 */
  platform: string;
  /** 名称/handle 子串搜索(不区分大小写) */
  query: string;
  /** 合作漏斗点段过滤(按行内项目 raw 阶段匹配);null/缺席 = 不过滤 */
  stage?: LibraryStageFilter | null;
}

/**
 * 纯函数过滤。vKolIds = board-ext v_content.v_kol_ids 的 Set(精确名单);
 * 传 null/缺席 = 名单未就绪 → vOnly 降级为「已挂项目」近似(调用方须如实标注降级)。
 */
export function filterLibraryRows(
  rows: KolLibraryRow[],
  filter: LibraryFilter,
  vKolIds?: ReadonlySet<number> | null,
): KolLibraryRow[] {
  const query = filter.query.trim().toLowerCase();
  const stageRaws = filter.stage ? new Set(filter.stage.rawStages.map((s) => s.trim().toLowerCase())) : null;
  return rows.filter((row) => {
    if (filter.vOnly) {
      if (vKolIds) {
        if (!vKolIds.has(row.poolId)) return false;
      } else if (row.projects.length === 0) return false;
    }
    if (stageRaws && !row.projects.some((p) => stageRaws.has(String(p.stage || "").trim().toLowerCase()))) return false;
    if (filter.platform && row.platform !== filter.platform) return false;
    if (query && !`${row.name} ${row.handle}`.toLowerCase().includes(query)) return false;
    return true;
  });
}

/** 平台名门面映射:unknown→未知 / media→媒体站;其余首字母大写(youtube→Youtube)。
    只管显示 —— 过滤键/联动状态仍用平台原值小写,绝不改数据。 */
const PLATFORM_LABEL_ZH: Record<string, string> = { unknown: "未知", media: "媒体站" };

export function platformLabel(platform: string): string {
  const key = String(platform || "").trim().toLowerCase();
  if (!key) return PLATFORM_LABEL_ZH.unknown;
  return PLATFORM_LABEL_ZH[key] || key.charAt(0).toUpperCase() + key.slice(1);
}

/** 平台 strip 选项:按库内真实出现的平台计数降序,至多 top 个(全部 + N 平台)。 */
export function libraryPlatformOptions(rows: KolLibraryRow[], top = 6): Array<{ platform: string; count: number }> {
  const counts = new Map<string, number>();
  rows.forEach((row) => {
    const key = row.platform || "unknown";
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return [...counts.entries()]
    .map(([platform, count]) => ({ platform, count }))
    .sort((a, b) => b.count - a.count || a.platform.localeCompare(b.platform))
    .slice(0, top);
}

/* ============ ⑤ 数值跟进(车道 L4 · 2026-08-22):被追踪视频的快照趋势 ============
   GET /my-kol/{id}/metrics/trends 与 GET /my-kol/metrics/tracking-overview 同一契约
   my_kol_metric_trends_v1(形状 1:1 对齐 backend kol/video_tracking_trends.py,禁编字段)。
   7d/30d 增量 = 最近一次实测 − 窗口基线;partial = 历史不足整窗,按实际覆盖天数如实标注;
   下次刷新只是按调度层级的估算,调度未开启时 reason=scheduler_disabled(前端不摆假时间)。 */

export type MetricWindowKey = "7d" | "30d";
export type TrendMetricKey = "views" | "likes" | "comments";

export interface VkpiMetricWindowDelta {
  delta?: number | null;
  daily_avg?: number | null;
  status?: "ready" | "partial" | "insufficient_history" | "metric_missing" | string;
  covered_days?: number | null;
  baseline_at?: string | null;
  baseline_value?: number | null;
}

export interface VkpiMetricSnapshotPoint {
  fetched_at?: string | null;
  status?: string;
  views?: number | null;
  likes?: number | null;
  comments?: number | null;
  shares?: number | null;
  error_code?: string | null;
}

export interface VkpiTrackedVideoTrend {
  evidence_id?: number;
  kol_pool_id?: number;
  kol_name?: string;
  title?: string;
  content_url?: string;
  platform?: string;
  published_at?: string | null;
  tracking?: {
    status?: "active" | "paused" | string;
    source?: string;
    pause_reason?: string;
    last_enqueued_at?: string | null;
    last_enqueue_status?: string;
    history?: "ready" | "single_sample" | "never_measured" | string;
    next_refresh?: { tier?: string; estimated_at?: string | null; reason?: string };
  };
  latest?: VkpiMetricSnapshotPoint | null;
  last_attempt?: VkpiMetricSnapshotPoint | null;
  sample_count?: number;
  failed_count?: number;
  attempt_count?: number;
  history_capped?: boolean;
  windows?: Partial<Record<MetricWindowKey, Partial<Record<TrendMetricKey, VkpiMetricWindowDelta>>>>;
  series?: VkpiMetricSnapshotPoint[];
}

export interface VkpiMetricTrendsResponse {
  contract?: string;
  kol_pool_id?: number;
  read_only?: boolean;
  generated_at?: string;
  scope?: { mode?: string; staff_scope_id?: number | null; membership?: string };
  scheduler?: {
    task_key?: string;
    /** null = 注册表不可读(诚实未知),false = 未开启,true = 已开启 */
    enabled?: boolean | null;
    interval_hours?: number;
    cadence_hours?: Partial<Record<"hot" | "warm" | "cold", number>>;
    failed_backoff_hours?: number;
  };
  summary?: {
    tracked_total?: number;
    active?: number;
    paused?: number;
    measured?: number;
    with_history?: number;
    views_latest_total?: number | null;
    windows?: Partial<Record<MetricWindowKey, Partial<Record<TrendMetricKey, { delta?: number | null; videos?: number }>>>>;
  };
  items?: VkpiTrackedVideoTrend[];
  truncated?: boolean;
  empty_reason?: "no_tracked_videos" | string | null;
}

export async function getMyKolMetricTrends(token: string, kolPoolId: number | string, limit = 60) {
  const query = new URLSearchParams({ limit: String(limit) });
  return apiFetch<VkpiMetricTrendsResponse>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/metrics/trends?${query.toString()}`,
    {},
    token,
  );
}

export async function getMyKolTrackingOverview(token: string, params: { limit?: number; staffId?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 60) });
  if (params.staffId) query.set("staff_id", String(params.staffId));
  return apiFetch<VkpiMetricTrendsResponse>(`/api/admin/vkpi/my-kol/metrics/tracking-overview?${query.toString()}`, {}, token);
}

/** 增量文案:ready → 「+1,234」;partial → 「+1,234(仅 3 天)」;其余 → 「待积累」。 */
export function windowDeltaText(win: VkpiMetricWindowDelta | undefined): string {
  if (!win || win.delta == null || (win.status !== "ready" && win.status !== "partial")) return "待积累";
  const value = Number(win.delta);
  if (!Number.isFinite(value)) return "待积累";
  const signed = `${value > 0 ? "+" : ""}${value.toLocaleString()}`;
  if (win.status === "partial" && win.covered_days != null) return `${signed}(仅 ${Math.max(1, Math.round(win.covered_days))} 天)`;
  return signed;
}

/** 日均增速文案;覆盖不足 1 天后端给 null → 「—」(不外推)。 */
export function dailyAvgText(win: VkpiMetricWindowDelta | undefined): string {
  if (!win || win.daily_avg == null || !Number.isFinite(Number(win.daily_avg))) return "—";
  const value = Number(win.daily_avg);
  return `${value > 0 ? "+" : ""}${Math.round(value).toLocaleString()}/天`;
}

/** 下次刷新文案:未开启自动刷新 / 已暂停 / 估算时间(按浏览器时区由调用方格式化)。 */
export function nextRefreshText(item: VkpiTrackedVideoTrend, formatStamp: (ts: string) => string): string {
  const next = item.tracking?.next_refresh;
  if (item.tracking?.status === "paused") return `已暂停${item.tracking.pause_reason ? ` · ${item.tracking.pause_reason}` : ""}`;
  if (!next) return "—";
  if (next.reason === "scheduler_disabled") return "自动刷新未开启";
  if (next.reason === "scheduler_state_unknown") return "自动刷新状态未知";
  if (next.estimated_at) return `预计 ${formatStamp(next.estimated_at)} 后`;
  return "—";
}

/** 视频当前状态徽:实测历史 / 仅一次实测 / 从未实测 / 最近一次失败。 */
export function trendStateBadge(item: VkpiTrackedVideoTrend): { label: string; tone: "good" | "warn" | "crit" | "muted" } {
  if (item.last_attempt?.status === "failed" && !item.latest) return { label: "最近刷新失败", tone: "crit" };
  if (!item.latest) return { label: "尚未实测", tone: "muted" };
  if (item.last_attempt?.status === "failed") return { label: "最近一次失败", tone: "warn" };
  if (item.tracking?.history === "single_sample") return { label: "仅一次实测", tone: "warn" };
  return { label: "持续实测中", tone: "good" };
}
