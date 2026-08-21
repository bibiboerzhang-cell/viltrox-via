import { apiFetch } from "../http";

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
  /** final_v1 bounded brand verdict; null means the video has not produced this proof. */
  llm_viltrox_detected?: boolean | null;
  llm_viltrox_products?: string[] | null;
  llm_competitor_mentions?: string[] | null;
  /** board-ext recent_videos 由共享 SQL CTE 直接下发；逐 KOL 端点可缺席并由纯函数同构派生。 */
  v_tier?: VContentTier;
}

export async function getMyKolPoolVideos(token: string, kolPoolId: number | string, limit = 200) {
  return apiFetch<{ items?: VkpiKolPoolVideoRow[]; total?: number; kol_pool_id?: number }>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/videos?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

/* ============ ③ Viltrox 内容证据分级(项目 / final_v1 / 标题 / 未识别 / 未判定) ============ */

export type VContentTier = "cooperation" | "analysis_confirmed" | "title_mention" | "not_related" | "undetermined";
export type VideoRelationFilter = "all" | "viltrox" | "undetermined" | "not_related";

const VILTROX_TITLE_TOKENS = ["viltrox", "唯卓仕", "唯卓"] as const;

export function classifyVContent(
  projectId: unknown,
  titleText: unknown,
  analysis: { detected?: boolean | null; products?: unknown } = {},
): VContentTier {
  const pid = projectId == null ? "" : String(projectId).trim();
  if (pid && pid !== "0") return "cooperation";
  if (analysis.detected === true || (Array.isArray(analysis.products) && analysis.products.length > 0)) {
    return "analysis_confirmed";
  }
  const title = String(titleText ?? "").toLowerCase();
  if (VILTROX_TITLE_TOKENS.some((token) => title.includes(token))) return "title_mention";
  if (analysis.detected === false) return "not_related";
  return "undetermined";
}

/** Strongest available bounded proof wins: project > final_v1 positive > title brand token > final_v1 negative > unknown. */
export function classifyVideoRow(video: VkpiKolPoolVideoRow): VContentTier {
  if (["cooperation", "analysis_confirmed", "title_mention", "not_related", "undetermined"].includes(String(video.v_tier || ""))) {
    return video.v_tier as VContentTier;
  }
  return classifyVContent(video.project_id, `${video.video_title || ""} ${video.title || ""}`, {
    detected: video.llm_viltrox_detected,
    products: video.llm_viltrox_products,
  });
}

export const V_TIER_LABEL: Record<VContentTier, string> = {
  cooperation: "合作产出",
  analysis_confirmed: "深析确认Viltrox",
  title_mention: "标题品牌提及",
  not_related: "深析未识别Viltrox",
  undetermined: "未判定",
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
    ["Viltrox 判定", `${V_TIER_LABEL[classifyVideoRow(video)]} · 五档顺序(项目关联 / final_v1 正向 / 标题品牌词 / final_v1 明确非相关 / 未判定)`],
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
