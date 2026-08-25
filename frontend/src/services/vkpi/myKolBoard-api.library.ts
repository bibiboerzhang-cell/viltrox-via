// MY KOL 库行模型(原 myKolBoard-api.ts 第 ④ 段;因该文件触顶 1000 行硬卫兵而外迁)。
// 纯函数 + 类型,零网络调用;由 myKolBoard-api.ts `export *` 原样再导出,
// 所有既有 `from "./myKolBoard-api"` 的导入点保持不变。
// 红线:纯读,零写库;viltrox_fit_score 只作只读透传,绝不回写。

type Row = Record<string, unknown>;

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
