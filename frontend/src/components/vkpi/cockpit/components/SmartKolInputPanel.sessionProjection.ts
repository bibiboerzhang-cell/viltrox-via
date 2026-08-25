import {
  type VkpiKolRecallItem,
  type VkpiKolRecallResponse,
  type VkpiKolSearchHistoryItem,
} from "../../../../domains/kol";

import { asRecord, cleanText, display, type Row } from "./SmartKolInputPanel.helpers";
import {
  recallCandidateDistribution,
  recallCandidateFacets,
  recallMatchEvidence,
} from "./SmartKolInputPanel.evidence";

/* ============ 来源标注:库内 / 新发现 / 你提供的 ============
   用户诉求:每次搜索出来的人,哪些是从我们自己库里捞的、哪些是这次现场从平台上新找到的,要一眼看出来。
   这一段是「来源」的唯一口径:卡片徽标、顶部分布、以后任何新入口都必须调这里的纯函数,不许各写各的。
   判不出来就返回 unknown → 门面不摆徽标(诚实空态),绝不猜一个标上去。 */

export type ResultOriginKind = "local" | "online" | "provided" | "unknown";
/** field=读到了明确的来源字段;inferred=按条目类型回退推断;none=判不出。 */
export type ResultOriginBasis = "field" | "inferred" | "none";
export type ResultOrigin = { kind: ResultOriginKind; basis: ResultOriginBasis };

export const RESULT_ORIGIN_UNKNOWN: ResultOrigin = Object.freeze({ kind: "unknown", basis: "none" });

/** 后端来源字段的候选键名。写端落哪个名字都能读到;一个都读不到才回退推断。 */
export const RESULT_ORIGIN_FIELD_KEYS = Object.freeze([
  "result_origin",
  "origin",
  "origin_lane",
  "source_origin",
  "origin_source",
  "discovery_origin",
]);

/** 来源字段取值 → 来源分类。只认这张表里的词;认不出的取值不当作判据,继续走回退推断。 */
const RESULT_ORIGIN_VALUES: Record<string, ResultOriginKind> = {
  local: "local",
  local_pool: "local",
  local_recall: "local",
  pool: "local",
  pool_local: "local",
  recall: "local",
  library: "local",
  inventory: "local",
  existing: "local",
  existing_kol: "local",
  online: "online",
  // online_new 是后端落库的四个字面量之一(迁移 301 的 CHECK / ITEM_ORIGIN_VALUES:
  // local_pool · online_new · operator_url · unknown)。这张表里少了它,后端明明已经判好
  // 并写进库的「本次新发现」会被当成认不出的取值、退回按 item_type 猜 —— 正是本波要根治的
  // 「前端猜」。第四个取值 unknown 刻意不列:它表示「后端也没判出来」,读端应继续走回退推断
  // (item_type / url_type)争取判得更准,不许把「没判出来」当成一个确定结论。
  online_new: "online",
  online_discovery: "online",
  platform: "online",
  platform_discovery: "online",
  platform_discovery_strict: "online",
  discovery: "online",
  federated: "online",
  net_new: "online",
  provided: "provided",
  url: "provided",
  operator_url: "provided",
  operator_input: "provided",
  manual: "provided",
  manual_url: "provided",
  user_url: "provided",
  pasted: "provided",
};

/** 条目类型 → 来源。这张表对齐后端写入端(唯一真源):
 *  search_sessions_attach.py:737 recall_candidate(本地池召回)
 *  search_sessions_attach.py:837 existing_kol(现场搜到、但人已经在我们库里)
 *  search_sessions_attach.py:871 new_creator(现场新找到)
 *  profile_online_qualification.py:36 ONLINE_ITEM_TYPE=online_qualified_candidate
 *  search_sessions_attach_jobs.py:38 url_video / url_profile(操作员贴链接)
 *  改动这张表 = 改口径,必须同时改后端写入端和 SmartKolInputPanel.resultOrigin.test.ts 的契约用例。 */
export const RESULT_ORIGIN_BY_ITEM_TYPE: Readonly<Record<string, ResultOriginKind>> = Object.freeze({
  recall_candidate: "local",
  existing_kol: "local",
  online_qualified_candidate: "online",
  new_creator: "online",
  url_profile: "provided",
  url_video: "provided",
});

/* 陷阱(2026-08-25 线上实测):payload.source="platform_discovery" 在 new_creator(1100/1100)
   和 existing_kol(427/427)上都有。拿 payload.source 判「新发现」会把 427 条库内老人误标成新发现。
   所以回退推断只认 item_type,一律不认 payload.source。 */

const RESULT_ORIGIN_RANK: Readonly<Record<ResultOriginKind, number>> = Object.freeze({
  local: 3,
  provided: 2,
  online: 1,
  unknown: 0,
});

function originRecords(item: unknown): Row[] {
  const row = asRecord(item);
  return [row, asRecord(row.payload), asRecord(row.source_fields)];
}

function firstText(records: Row[], key: string): string {
  for (const record of records) {
    const value = cleanText(record[key]);
    if (value) return value;
  }
  return "";
}

/** 单条结果的来源判定。吃三种形状:原始会话条目、投影后的候选行、严格名单行的 item。 */
export function resultOriginOf(item: unknown): ResultOrigin {
  const records = originRecords(item);
  for (const record of records) {
    for (const key of RESULT_ORIGIN_FIELD_KEYS) {
      const mapped = RESULT_ORIGIN_VALUES[cleanText(record[key]).toLowerCase()];
      if (mapped) return { kind: mapped, basis: "field" };
    }
  }
  const byType = RESULT_ORIGIN_BY_ITEM_TYPE[firstText(records, "item_type").toLowerCase()];
  if (byType) return { kind: byType, basis: "inferred" };
  // 贴进来但没认出平台的链接:后端记成 item_type='unknown',payload 仍带 url_type(线上 4 条)。
  if (firstText(records, "url_type")) return { kind: "provided", basis: "inferred" };
  // 旧数据只写过中文 type_label、没写来源字段 —— 沿用门面已经说过的话,不另编一套。
  const legacy = firstText(records, "type_label");
  if (legacy === "库内已有") return { kind: "local", basis: "inferred" };
  if (legacy === "全网发现" || legacy === "联网净新增") return { kind: "online", basis: "inferred" };
  return RESULT_ORIGIN_UNKNOWN;
}

export type ResultOriginBadge = {
  kind: Exclude<ResultOriginKind, "unknown">;
  label: string;
  title: string;
  /** 只给颜色,不给尺寸:卡片和顶部分布各自补尺寸类,保证两处视觉同源。 */
  toneClassName: string;
};

const RESULT_ORIGIN_BADGES: Readonly<Record<Exclude<ResultOriginKind, "unknown">, ResultOriginBadge>> = Object.freeze({
  local: {
    kind: "local",
    label: "库内",
    title: "这一位本来就在我们自己的达人库里",
    toneClassName: "border-white/[0.14] bg-white/[0.05] text-slate-300",
  },
  online: {
    kind: "online",
    label: "新发现",
    title: "这一位是本次现场从平台上新找到的,之前不在我们库里",
    toneClassName: "border-emerald-300/45 bg-emerald-400/[0.16] text-emerald-100",
  },
  provided: {
    kind: "provided",
    label: "你提供的",
    title: "这一位来自你自己贴进来的链接",
    toneClassName: "border-cyan-300/30 bg-cyan-400/[0.10] text-cyan-100",
  },
});

/** 判不出来源就返回 null → 调用方什么都不摆(不许退化成「未知」徽标)。 */
export function resultOriginBadge(item: unknown): ResultOriginBadge | null {
  const { kind } = resultOriginOf(item);
  return kind === "unknown" ? null : RESULT_ORIGIN_BADGES[kind];
}

export function resultOriginBadgeOfKind(kind: ResultOriginKind): ResultOriginBadge | null {
  return kind === "unknown" ? null : RESULT_ORIGIN_BADGES[kind];
}

export type ResultOriginCounts = {
  total: number;
  local: number;
  online: number;
  provided: number;
  unknown: number;
  /** displayed=按本页已显示的结果现数;session=按整场搜索的全部条目;summary=用服务端已经算好的数。 */
  basis: "displayed" | "session" | "summary";
};

/** 去重身份键:与 discoveryItemsFromSession 同口径,保证顶部分布和墙上条数对得上。 */
function originIdentity(item: unknown, index: number): string {
  const row = asRecord(item);
  const nested = { ...asRecord(row.payload), ...asRecord(row.source_fields) };
  const handle = cleanText(row.handle ?? nested.handle).toLowerCase().replace(/^@/, "");
  const platform = cleanText(row.platform ?? nested.platform).toLowerCase();
  if (handle && handle !== "unknown") return `h:${platform}:${handle}`;
  const url = cleanText(
    row.profile_url ?? row.source_url ?? nested.profile_url ?? nested.source_url ?? nested.channel_url,
  ).toLowerCase();
  if (url) return `u:${url}`;
  const poolId = Number(row.kol_pool_id ?? nested.kol_pool_id ?? 0);
  if (Number.isFinite(poolId) && poolId > 0) return `p:${poolId}`;
  return `i:${index}`;
}

function countByOrigin(items: readonly unknown[], basis: ResultOriginCounts["basis"]): ResultOriginCounts {
  // 同一个人可能同时出现在几个列表里(库内召回 + 现场又搜到)。合并时取「更强的事实」:
  // 人已经在库里 > 是你贴进来的 > 这次新找到的,所以 local > provided > online。
  const byIdentity = new Map<string, ResultOriginKind>();
  items.forEach((item, index) => {
    const identity = originIdentity(item, index);
    const kind = resultOriginOf(item).kind;
    const kept = byIdentity.get(identity);
    if (kept == null || RESULT_ORIGIN_RANK[kind] > RESULT_ORIGIN_RANK[kept]) byIdentity.set(identity, kind);
  });
  const counts: ResultOriginCounts = { total: byIdentity.size, local: 0, online: 0, provided: 0, unknown: 0, basis };
  byIdentity.forEach((kind) => { counts[kind] += 1; });
  return counts;
}

/** 按本页已显示的结果统计(几段列表拼一起,同一人只计一次)。 */
export function resultOriginCounts(...groups: ReadonlyArray<readonly unknown[] | null | undefined>): ResultOriginCounts {
  return countByOrigin(groups.flatMap((group) => (Array.isArray(group) ? group : [])), "displayed");
}

/** B1:来源标注不再被条目类型卡住 —— 一场搜索里五种类型(含搜索主力 recall_candidate)全部走同一口径。 */
export function sessionResultOriginCounts(session: VkpiKolSearchHistoryItem | null): ResultOriginCounts {
  return countByOrigin(session ? sessionItems(session) : [], "session");
}

/** result_summary 里放来源分布的键。第一个 origin_breakdown 是后端真正在写的那个
 *  (search_sessions_items._update_session 每次持久化会话汇总都重算一遍,落 schema
 *  session_item_origin_v1);其余是历史/兼容形状。 */
const RESULT_ORIGIN_COUNT_KEYS = ["origin_breakdown", "result_origin_counts", "origin_counts", "origins", "origin_distribution"];
const RESULT_ORIGIN_COUNT_ALIASES: Readonly<Record<Exclude<ResultOriginKind, "unknown">, string[]>> = Object.freeze({
  local: ["local", "local_pool", "pool", "recall", "library", "existing"],
  // online_new = 后端落库字面量;漏了它 origin_breakdown 会永远配不上,整块退回前端现数。
  online: ["online", "online_new", "new", "net_new", "discovery", "platform_discovery"],
  provided: ["provided", "url", "operator_url", "manual"],
});

function nonNegative(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.trunc(parsed) : null;
}

/** 服务端已经算好的分布(result_summary 里)。缺 local/online 两项就当没有,不半信半疑地拼数。 */
export function summaryResultOriginCounts(summary: unknown): ResultOriginCounts | null {
  const root = asRecord(summary);
  for (const key of RESULT_ORIGIN_COUNT_KEYS) {
    const node = asRecord(root[key]);
    // 后端的 origin_breakdown 把数字放在嵌套的 counts 里(同层还有 schema/labels/by_item_type);
    // 老的扁平形状直接就是数字。两种都吃,取到哪个用哪个。
    const nested = asRecord(node.counts);
    const record = Object.keys(nested).length ? nested : node;
    if (!Object.keys(record).length) continue;
    const picked = { local: null as number | null, online: null as number | null, provided: null as number | null };
    (Object.keys(RESULT_ORIGIN_COUNT_ALIASES) as Array<keyof typeof picked>).forEach((kind) => {
      for (const alias of RESULT_ORIGIN_COUNT_ALIASES[kind]) {
        const value = nonNegative(record[alias]);
        if (value != null) { picked[kind] = value; return; }
      }
    });
    if (picked.local == null || picked.online == null) continue;
    const provided = picked.provided ?? 0;
    // unknown(后端看过证据仍判不出)与 unlabeled(迁移 301 之前的老行,还没回填)是两件事,
    // 但对看结果的人是同一句话:「这些还没标上来源」。相加而不是二选一 —— 只取其一会在回填
    // 跑完前把 3806 条未标注行报成 0,那是骗人。
    const unknown = (nonNegative(record.unknown) ?? 0) + (nonNegative(record.unlabeled) ?? 0);
    const total = nonNegative(record.total) ?? nonNegative(node.total) ?? picked.local + picked.online + provided + unknown;
    return { total, local: picked.local, online: picked.online, provided, unknown, basis: "summary" };
  }
  return null;
}

/** 本地召回接口只读我们自己的达人库,所以它返回的每一条都是「库内」——这是接口的结构性事实,不是猜测。
 *  只补给还判不出来源的行;已经有明确来源的行原样不动。 */
export function withLocalRecallOrigin<T>(items: readonly T[]): T[] {
  return items.map((item) => (
    resultOriginOf(item).kind === "unknown"
      ? { ...(item as unknown as Row), result_origin: "local" } as unknown as T
      : item
  ));
}

/** 投影时把来源写进结果行;判不出就一个键都不写(下游据此不摆徽标)。 */
function originField(rawItem: Row): Row {
  const { kind, basis } = resultOriginOf(rawItem);
  return kind === "unknown" ? {} : { result_origin: kind, result_origin_basis: basis };
}

export function sessionItems(session: VkpiKolSearchHistoryItem): Row[] {
  const items = Array.isArray(session.items) && session.items.length
    ? session.items
    : Array.isArray(session.active_items) && session.active_items.length
      ? session.active_items
      : Array.isArray(session.items_preview)
        ? session.items_preview
        : [];
  return items.map((item) => asRecord(item));
}

export function recallResultFromSession(session: VkpiKolSearchHistoryItem): VkpiKolRecallResponse {
  const ranked: Array<{ rank: number; item: VkpiKolRecallItem }> = [];
  sessionItems(session).forEach((item) => {
    if (cleanText(item.item_type) !== "recall_candidate") return;
    const payload = asRecord(item.payload);
    const gateFollowers = asRecord(asRecord(payload.qualification_evidence).followers);
    const bucket: "creator" | "reviewer" = cleanText(payload.bucket) === "reviewer" ? "reviewer" : "creator";
    const matchEvidence = recallMatchEvidence(payload.match_evidence);
    const row = {
      bucket,
      kol_pool_id: Number(item.kol_pool_id || payload.kol_pool_id || 0),
      handle: display(payload.handle || payload.display_name || payload.channel_name, "unknown"),
      display_name: cleanText(payload.display_name || payload.channel_name || payload.handle),
      platform: cleanText(payload.platform),
      profile_type: display(payload.profile_type || item.item_type, "creator"),
      followers: Number(payload.followers ?? gateFollowers.value ?? 0) || null,
      avatar_url: cleanText(payload.avatar_url),
      profile_url: cleanText(item.source_url || payload.profile_url || payload.source_url || payload.channel_url),
      recall_rank_score: Number(item.score ?? payload.recall_rank_score ?? payload.vector_score ?? 0),
      vector_score: Number(payload.vector_score ?? item.score ?? 0),
      display_rank_score: Number(payload.display_rank_score ?? item.score ?? payload.recall_rank_score ?? 0),
      display_relevance_adjust: Number(payload.display_relevance_adjust ?? 0),
      relevance_flags: Array.isArray(payload.relevance_flags)
        ? (payload.relevance_flags as unknown[]).map(cleanText).filter(Boolean)
        : [],
      relevance_tier_hint: cleanText(payload.relevance_tier_hint),
      match_evidence: matchEvidence,
      candidate_facets: recallCandidateFacets(payload.candidate_facets),
      qualification_evidence: asRecord(payload.qualification_evidence),
      server_rank: Number(payload.server_rank ?? payload.global_rank ?? item.rank ?? 0) || undefined,
      global_rank: Number(payload.global_rank ?? payload.server_rank ?? item.rank ?? 0) || undefined,
      type_label: bucket === "reviewer" ? "测评号" : "创作者",
      creator_type_score: bucket === "creator" ? 1 : 0,
      reviewer_type_score: bucket === "reviewer" ? 1 : 0,
      recall_reason: matchEvidence.length ? cleanText(payload.evidence || payload.sample_title) : "",
      why_fit: matchEvidence.length ? cleanText(payload.why_fit) : "",
      fit_verdict: cleanText(payload.fit_verdict),
      creator_type: cleanText(payload.creator_type),
      exposure_potential: Number(payload.exposure_potential ?? payload.avg_views ?? 0) || null,
      source_fields: payload,
      // B1:来源标注跟着投影一起走。判据取自原始会话条目(带 item_type),判不出就不写这个键,
      // 门面看到没有键就不摆徽标 —— 绝不在投影里编一个来源出来。
      ...originField(item),
    } as VkpiKolRecallItem;
    ranked.push({ rank: Number(payload.server_rank ?? payload.global_rank ?? item.rank ?? 0) || Number.MAX_SAFE_INTEGER, item: row });
  });
  ranked.sort((a, b) => a.rank - b.rank);
  const ordered = ranked.map((entry) => entry.item);
  const creator = ordered.filter((item) => item.bucket !== "reviewer");
  const reviewer = ordered.filter((item) => item.bucket === "reviewer");
  const summary = asRecord(session.result_summary);
  const querySummary = asRecord(summary.query);
  const diagnostics = asRecord(summary.diagnostics);
  const llmQueryPlan = asRecord(summary.llm_query_plan);
  return {
    method: "search_session_history",
    query: { query_text: display(querySummary.query_text || summary.query || session.query_text, "") },
    ratio: {
      creator_quota: creator.length,
      reviewer_quota: reviewer.length,
      policy: "history",
      mixed_policy: "history",
      dedupe: true,
    },
    items: ordered,
    buckets: { creator, reviewer },
    diagnostics: {
      ...diagnostics,
      candidate_count: Number(diagnostics.candidate_count ?? session.item_count ?? creator.length + reviewer.length),
      creator_returned: Number(diagnostics.creator_returned ?? creator.length),
      reviewer_returned: Number(diagnostics.reviewer_returned ?? reviewer.length),
      returned_count: creator.length + reviewer.length,
    },
    match_status: cleanText(summary.match_status),
    candidate_set_distribution: recallCandidateDistribution(summary.candidate_set_distribution),
    ...(Object.keys(asRecord(summary.local_qualification)).length
      ? { local_qualification: asRecord(summary.local_qualification) }
      : {}),
    ...(Object.keys(llmQueryPlan).length ? { llm_query_plan: llmQueryPlan } : {}),
    snapshot_complete:
      (session as unknown as Row).recall_snapshot_complete === true
      || summary.recall_snapshot_complete === true,
  } satisfies VkpiKolRecallResponse;
}

export function discoveryItemsFromSession(session: VkpiKolSearchHistoryItem | null): VkpiKolRecallItem[] {
  const out: VkpiKolRecallItem[] = [];
  const indexByIdentity = new Map<string, number>();
  discoveryItemsFromSessionRaw(session).forEach((item) => {
    const handle = cleanText(item.handle).toLowerCase().replace(/^@/, "");
    const platform = cleanText(item.platform).toLowerCase();
    const identity = handle && handle !== "unknown" ? `${platform}:${handle}` : cleanText(item.profile_url).toLowerCase();
    const existingIndex = identity ? indexByIdentity.get(identity) : undefined;
    if (existingIndex == null) {
      if (identity) indexByIdentity.set(identity, out.length);
      out.push(item);
      return;
    }
    const kept = out[existingIndex];
    if (!Number(kept.kol_pool_id) && Number(item.kol_pool_id)) {
      out[existingIndex] = { ...kept, kol_pool_id: item.kol_pool_id };
    }
  });
  return out;
}

function discoveryItemsFromSessionRaw(session: VkpiKolSearchHistoryItem | null): VkpiKolRecallItem[] {
  if (!session) return [];
  const out: VkpiKolRecallItem[] = [];
  sessionItems(session).forEach((item) => {
    const itemType = cleanText(item.item_type);
    // 这道类型闸只管「哪些人摆进全网发现墙」,不再管来源标注:
    // 来源走 resultOriginOf/sessionResultOriginCounts,五种类型(含 recall_candidate)一视同仁。
    // 别把这行删掉当成「解除限制」—— 删了会把 1401 条本地召回和 952 条贴链接结果全灌进发现墙。
    if (itemType !== "new_creator" && itemType !== "existing_kol") return;
    const payload = asRecord(item.payload);
    out.push({
      bucket: "creator",
      kol_pool_id: Number(item.kol_pool_id || payload.kol_pool_id || 0),
      handle: display(payload.handle || payload.display_name || payload.channel_name, "unknown"),
      display_name: cleanText(payload.display_name || payload.channel_name || payload.handle),
      platform: cleanText(payload.platform),
      profile_type: display(payload.profile_type || "creator", "creator"),
      followers: Number(payload.followers || payload.follower_count || payload.subscriber_count || payload.subscribers || payload.avg_views || payload.views || 0) || null,
      avatar_url: cleanText(payload.avatar_url),
      profile_url: cleanText(item.source_url || payload.profile_url || payload.source_url || payload.channel_url),
      recall_rank_score: Number(item.score ?? payload.score ?? 0),
      vector_score: Number(payload.vector_score ?? item.score ?? 0),
      type_label: itemType === "existing_kol" ? "库内已有" : "全网发现",
      creator_type_score: 1,
      reviewer_type_score: 0,
      recall_reason: cleanText(payload.sample_title || payload.evidence),
      why_fit: cleanText(payload.why_fit || payload.sample_title),
      fit_verdict: cleanText(payload.fit_verdict),
      creator_type: cleanText(payload.creator_type),
      exposure_potential: Number(payload.exposure_potential ?? payload.avg_views ?? payload.views ?? 0) || null,
      source_fields: payload,
      ...originField(item),
    } as VkpiKolRecallItem);
  });
  return out;
}
