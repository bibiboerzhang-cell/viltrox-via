import { apiFetch } from "../http";

// MY KOL 板块页 · 看板扩展数据层(M3):
//   ① GET /api/admin/vkpi/my-kol/board-ext?days=30 —— 七组聚合(kpi_series/funnel/
//      platform_dist/fit_dist/contact_coverage/views_top/v_content)类型化 fetch;
//   ② GET /api/admin/vkpi/kol-pool/{id}/videos —— 单 KOL 全部 evidence 视频(类型化);
//   ③ classifyVContent —— 后端 my_kol_board_ext.classify_v_content 的前端同构复现
//      (口径逐字对齐:cooperation=project_id 非空且非 '0' / title_mention=标题小写含
//      viltrox / 其余 undetermined;派生规则非采集字段);
//   ④ mapLibraryRows / filterLibraryRows —— aggregate.pool_favorites 行 → 库行模型
//      (收藏/共享/认领桥/进行中)+ 纯函数过滤(V 筛选/平台/搜索)。
// 红线:纯读封装,零写库;绝不触 viltrox_fit_score 写点 / rule_v0;fit 分只作只读透传。

export type Row = Record<string, unknown>;

/* ============ ① board-ext 七组聚合 ============ */

export interface VkpiBoardExtGroup {
  status?: string;
  reason?: string;
  basis?: string | Record<string, string>;
  [key: string]: unknown;
}

export interface VkpiVContentGroup extends VkpiBoardExtGroup {
  total_evidence?: number;
  /** 至少一条 cooperation / title_mention evidence 的去重 KOL 数(全 evidence 口径) */
  v_kol_count?: number;
  tiers?: {
    cooperation?: number;
    title_mention?: number;
    title_mention_only?: number;
    overlap_both?: number;
    undetermined?: number;
  };
}

export interface VkpiMyKolBoardExtResponse {
  status?: string;
  days?: number;
  window?: { since?: string; until?: string; prev_since?: string; prev_until?: string };
  staff_scope_id?: number | null;
  kpi_series?: VkpiBoardExtGroup;
  funnel?: VkpiBoardExtGroup;
  platform_dist?: VkpiBoardExtGroup;
  fit_dist?: VkpiBoardExtGroup;
  contact_coverage?: VkpiBoardExtGroup;
  views_top?: VkpiBoardExtGroup;
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
}

export async function getMyKolPoolVideos(token: string, kolPoolId: number | string, limit = 200) {
  return apiFetch<{ items?: VkpiKolPoolVideoRow[]; total?: number; kol_pool_id?: number }>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/videos?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

/* ============ ③ V 相关三档判定(后端 classify_v_content 前端同构;口径逐字对齐) ============ */

export type VContentTier = "cooperation" | "title_mention" | "undetermined";

const VILTROX_TOKEN = "viltrox";

export function classifyVContent(projectId: unknown, titleText: unknown): VContentTier {
  const pid = projectId == null ? "" : String(projectId).trim();
  if (pid && pid !== "0") return "cooperation";
  if (String(titleText ?? "").toLowerCase().includes(VILTROX_TOKEN)) return "title_mention";
  return "undetermined";
}

/** 视频行的判定输入:后端 SQL 同口径 = video_title 与 title 拼接后小写匹配。 */
export function classifyVideoRow(video: VkpiKolPoolVideoRow): VContentTier {
  return classifyVContent(video.project_id, `${video.video_title || ""} ${video.title || ""}`);
}

export const V_TIER_LABEL: Record<VContentTier, string> = {
  cooperation: "合作产出",
  title_mention: "标题提及V",
  undetermined: "未判定",
};

/** image/carousel 类 evidence(IG 图文/轮播):展示照常,深析批次必须剔除(无视频可下)。 */
export function isImageKindVideo(video: VkpiKolPoolVideoRow): boolean {
  const kind = String(video.media_kind ?? video.evidence_type ?? "").trim().toLowerCase();
  return kind === "image" || kind === "carousel";
}

export interface ClassifiedVideo {
  video: VkpiKolPoolVideoRow;
  tier: VContentTier;
}

/** 详情弹窗小结口径:播放合计只算实测(NULL 剔除并计条数);V 相关=非 undetermined。 */
export function summarizeKolVideos(videos: VkpiKolPoolVideoRow[]) {
  const classified: ClassifiedVideo[] = videos.map((video) => ({ video, tier: classifyVideoRow(video) }));
  const measuredCount = classified.filter(({ video }) => video.view_count != null).length;
  const viewsTotal = classified.reduce((sum, { video }) => sum + (video.view_count != null ? Number(video.view_count) || 0 : 0), 0);
  return {
    classified,
    measuredCount,
    unmeasuredCount: classified.length - measuredCount,
    viewsTotal,
    vRelatedCount: classified.filter(({ tier }) => tier !== "undetermined").length,
    analyzedCount: classified.filter(({ video }) => Boolean(video.has_final_v1_cache)).length,
    unanalyzed: classified.filter(({ video }) => !video.has_final_v1_cache && !isImageKindVideo(video)).map(({ video }) => video),
  };
}

/** 二级筛选(仅 V 相关)+ 排序(时间/播放;未实测排最后)。纯函数,不改入参。 */
export function sortClassifiedVideos(classified: ClassifiedVideo[], vOnly: boolean, sortBy: "time" | "views"): ClassifiedVideo[] {
  const base = vOnly ? classified.filter(({ tier }) => tier !== "undetermined") : [...classified];
  if (sortBy === "views") base.sort((a, b) => (Number(b.video.view_count ?? -1) || -1) - (Number(a.video.view_count ?? -1) || -1));
  else base.sort((a, b) => String(b.video.publish_date || b.video.posted_at || "").localeCompare(String(a.video.publish_date || a.video.posted_at || "")));
  return base;
}

/** evidence 记录预览行(详情弹窗溯源;口径文案与后端 basis 对齐,禁编造)。 */
export function videoRecordRows(video: VkpiKolPoolVideoRow): Array<[string, string]> {
  return [
    ["表", "vkpi_kol_video_evidence"],
    ["id", `#${video.evidence_id ?? video.id ?? "—"}`],
    ["project_id", video.project_id != null ? `#${video.project_id}` : "—"],
    ["V 判定", `${V_TIER_LABEL[classifyVideoRow(video)]} · 派生规则(合作=挂项目 / 标题提及=标题含 viltrox 不分大小写 / 其余未判定)`],
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

export interface LibraryFilter {
  /** 「有 V 视频」筛选:行级可判据 = 已挂项目(合作口径);标题提及需进详情逐条判定 */
  vOnly: boolean;
  /** 平台原值小写;空 = 全部 */
  platform: string;
  /** 名称/handle 子串搜索(不区分大小写) */
  query: string;
}

export function filterLibraryRows(rows: KolLibraryRow[], filter: LibraryFilter): KolLibraryRow[] {
  const query = filter.query.trim().toLowerCase();
  return rows.filter((row) => {
    if (filter.vOnly && row.projects.length === 0) return false;
    if (filter.platform && row.platform !== filter.platform) return false;
    if (query && !`${row.name} ${row.handle}`.toLowerCase().includes(query)) return false;
    return true;
  });
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
