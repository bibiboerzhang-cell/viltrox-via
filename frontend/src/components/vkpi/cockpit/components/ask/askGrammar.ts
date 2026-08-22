// 顶栏 Ask ⌘K 前缀语法 + 候选契约(纯函数,零 React / 零网络;表驱动测试见 askGrammar.test.ts)。
//
// 语法:`@`=KOL  `#`=项目/活动  `$`=SKU/镜头  `/`=板块跳转  无前缀=混合;全角 ＠＃＄／ 同样识别。
// 候选契约(P1 只做 navigate / open_entity;ask 是问 AI 的本地动作,不是后端 mutate):
//   { kind, id, label, detail, action:{ type, route?, entity? } }
// mutate 类动作留 P2:这里没有任何会写库的动作类型,UI 也不渲染占位。

import type { GlobalSearchEvent, GlobalSearchKol, GlobalSearchProject, GlobalSearchResult } from "../../../../../services/vkpi/globalSearch-api";
import { kolHumanDisplayName, kolHumanPublicHandle } from "../../lib/kolIdentity";

export type AskPrefix = "kol" | "project" | "sku" | "nav";

export interface AskParsedQuery {
  raw: string;
  prefix: AskPrefix | null;
  /** 去掉前缀与首尾空白后的检索词。 */
  term: string;
}

export type AskCandidateKind = "kol" | "project" | "event" | "sku" | "nav" | "recent" | "job" | "suggestion";
export type AskEntityType = "kol" | "project" | "event" | "sku" | "search_session";

export type AskCandidateAction =
  | { type: "navigate"; route: string; params?: Record<string, string> }
  | { type: "open_entity"; entity: { type: AskEntityType; id: string | number }; route?: string }
  | { type: "ask"; query: string };

export interface AskCandidate {
  kind: AskCandidateKind;
  id: string;
  label: string;
  detail: string;
  action: AskCandidateAction;
  /** 最近区里保留原始类型,供图标/再执行使用。 */
  origin?: AskCandidateKind;
}

export interface AskNavItemLike {
  key: string;
  label: string;
  v2?: boolean;
  ops?: boolean;
}

export type AskEmptyKind = "none" | "unavailable" | "scope";

const PREFIX_MAP: Record<string, AskPrefix> = {
  "@": "kol", "＠": "kol",
  "#": "project", "＃": "project",
  "$": "sku", "＄": "sku",
  "/": "nav", "／": "nav",
};

export const ASK_PREFIX_HINTS: Array<{ prefix: string; kind: AskPrefix }> = [
  { prefix: "@", kind: "kol" },
  { prefix: "#", kind: "project" },
  { prefix: "$", kind: "sku" },
  { prefix: "/", kind: "nav" },
];

export function parseAskQuery(raw: string): AskParsedQuery {
  const trimmed = String(raw ?? "").trimStart();
  const head = trimmed.charAt(0);
  const prefix = PREFIX_MAP[head] ?? null;
  const body = prefix ? trimmed.slice(1) : trimmed;
  return { raw: String(raw ?? ""), prefix, term: body.trim() };
}

// 板块别名表:中英 label + 口语别名;匹配时全部小写、去空白。v2 占位板块不进候选。
export const NAV_ALIASES: Record<string, string[]> = {
  dashboard: ["仪表盘", "总览", "首页", "home", "overview"],
  "my-kol": ["我的 KOL", "我的kol", "my kol", "mykol", "收藏", "跟进"],
  "kol-pool": ["KOL Pool", "人才库", "KOL 人才库", "找达人", "达人库", "pool", "发现", "discover"],
  kolProfile: ["KOL 档案", "档案", "profile", "kol profile"],
  projects: ["项目", "project", "projects", "合作"],
  events: ["活动", "event", "events", "展会"],
  shopify: ["店铺", "订单", "gmv", "shop"],
  dealers: ["经销商", "经销商地图", "dealer", "dealer map", "地图", "门店", "map"],
  intelligent: ["智能问答", "问答", "ask", "q&a", "qa"],
  marketVoice: ["市场之声", "市场", "voice", "voice of market", "舆情", "评论"],
  sku360: ["SKU 360", "sku", "sku360", "产品", "镜头", "product"],
  creativeLibrary: ["创意资产库", "创意", "素材", "creative", "library", "creative library"],
  replyQueue: ["回复队列", "回复", "reply", "reply queue"],
  launchpad: ["发射台", "launch", "上市", "发布"],
  autonomy: ["自治驾照", "驾照", "自治", "autonomy license"],
  strategyBoard: ["战略台", "战略", "strategy", "strategy desk"],
  gtmCommand: ["gtm", "GTM 指挥台", "指挥台"],
  triage: ["运维 Triage", "运维", "分诊", "ops", "ops triage"],
  dataQuery: ["问数", "数据问答", "data q&a", "data query", "查数"],
  marketTrends: ["市场趋势", "趋势", "trends", "market trends"],
  skillStudio: ["技能工作室", "skill", "技能"],
};

function fold(value: string): string {
  return String(value ?? "").toLowerCase().replace(/\s+/g, "");
}

function matchTier(term: string, haystack: string): number | null {
  if (!haystack) return null;
  if (haystack === term) return 0;
  if (haystack.startsWith(term)) return 1;
  if (haystack.includes(term)) return 2;
  return null;
}

export interface NavMatch {
  item: AskNavItemLike;
  tier: number;
}

/** 板块本地匹配:空词返回全部可达板块(导航顺序),有词按 相等<前缀<子串 排序。 */
export function matchNavItems(
  term: string,
  items: readonly AskNavItemLike[],
  translate: (text: string) => string = (text) => text,
  limit = 8,
): NavMatch[] {
  const visible = items.filter((item) => !item.v2);
  const needle = fold(term);
  if (!needle) return visible.slice(0, limit).map((item) => ({ item, tier: 3 }));
  const matches: Array<NavMatch & { order: number }> = [];
  visible.forEach((item, order) => {
    const haystacks = [item.key, item.label, translate(item.label), ...(NAV_ALIASES[item.key] || [])].map(fold);
    let best: number | null = null;
    for (const hay of haystacks) {
      const tier = matchTier(needle, hay);
      if (tier !== null && (best === null || tier < best)) best = tier;
    }
    if (best !== null) matches.push({ item, tier: best, order });
  });
  return matches
    .sort((a, b) => a.tier - b.tier || a.order - b.order)
    .slice(0, limit)
    .map(({ item, tier }) => ({ item, tier }));
}

export function navCandidate(item: AskNavItemLike, translate: (text: string) => string): AskCandidate {
  const label = translate(item.label);
  return {
    kind: "nav",
    id: `nav:${item.key}`,
    label,
    detail: item.ops ? translate("智能运维") : translate("板块"),
    action: { type: "navigate", route: item.key },
  };
}

export function kolCandidate(item: GlobalSearchKol): AskCandidate {
  const row = item as unknown as Record<string, unknown>;
  return {
    kind: "kol",
    id: `kol:${item.id}`,
    label: kolHumanDisplayName(row),
    detail: [item.platform, kolHumanPublicHandle(row)].filter(Boolean).join(" · "),
    action: { type: "open_entity", entity: { type: "kol", id: item.id }, route: "kol-pool" },
  };
}

export function projectCandidate(item: GlobalSearchProject, translate: (text: string) => string): AskCandidate {
  return {
    kind: "project",
    id: `project:${item.id}`,
    label: item.project_name || item.project_uid || `${translate("项目")} #${item.id}`,
    detail: item.stage || translate("项目"),
    action: { type: "open_entity", entity: { type: "project", id: item.id }, route: "projects" },
  };
}

export function eventCandidate(item: GlobalSearchEvent, translate: (text: string) => string): AskCandidate {
  return {
    kind: "event",
    id: `event:${item.id}`,
    label: item.title || `${translate("活动")} #${item.id}`,
    detail: item.start_date || translate("活动"),
    action: { type: "open_entity", entity: { type: "event", id: item.id }, route: "events" },
  };
}

export interface CatalogSuggestItemLike {
  sku: string;
  display_name: string;
  lens_key: string;
}

export function skuCandidate(item: CatalogSuggestItemLike, translate: (text: string) => string): AskCandidate {
  const sku = String(item.sku || "").trim();
  const name = String(item.display_name || "").trim() || sku;
  return {
    kind: "sku",
    id: `sku:${sku || item.lens_key || name}`,
    label: name,
    detail: sku || translate("镜头系列"),
    // 家族名没有单一 SKU:跳 SKU 360° 并预填搜索词(复用市场之声→SKU 360° 的既有桥)。
    action: sku
      ? { type: "open_entity", entity: { type: "sku", id: sku }, route: "sku360" }
      : { type: "navigate", route: "sku360", params: { search: name } },
  };
}

export function suggestionCandidate(question: string, index: number): AskCandidate {
  return {
    kind: "suggestion",
    id: `suggestion:${index}:${question}`,
    label: question,
    detail: "",
    action: { type: "ask", query: question },
  };
}

/** 把 /global-search 结果按前缀切成候选:@ 只留 KOL,# 留项目+活动,无前缀全留。 */
export function globalSearchCandidates(
  result: GlobalSearchResult,
  prefix: AskPrefix | null,
  translate: (text: string) => string,
  perGroup = 4,
): AskCandidate[] {
  const kols = prefix === null || prefix === "kol" ? result.kols.slice(0, prefix === "kol" ? perGroup * 2 : perGroup).map(kolCandidate) : [];
  const projects = prefix === null || prefix === "project"
    ? result.projects.slice(0, perGroup).map((item) => projectCandidate(item, translate))
    : [];
  const events = prefix === null || prefix === "project"
    ? result.events.slice(0, perGroup).map((item) => eventCandidate(item, translate))
    : [];
  return [...kols, ...projects, ...events];
}

/** 本地即时过滤:用于 @ 的 LRU 与最近区的文本匹配。 */
export function filterCandidatesByTerm(candidates: readonly AskCandidate[], term: string, limit = 4): AskCandidate[] {
  const needle = fold(term);
  if (!needle) return [];
  const scored: Array<{ candidate: AskCandidate; tier: number }> = [];
  for (const candidate of candidates) {
    const tier = [candidate.label, candidate.detail].map(fold).reduce<number | null>((best, hay) => {
      const current = matchTier(needle, hay);
      return current !== null && (best === null || current < best) ? current : best;
    }, null);
    if (tier !== null) scored.push({ candidate, tier });
  }
  return scored.sort((a, b) => a.tier - b.tier).slice(0, limit).map((entry) => entry.candidate);
}

/** 诚实空态三态:回填/降级永不计入命中。 */
export function resolveEmptyKind(
  states: readonly string[],
  resultCount: number,
): AskEmptyKind | null {
  if (resultCount > 0) return null;
  const known = states.filter(Boolean);
  if (known.some((state) => state !== "ready" && state !== "blocked")) return "unavailable";
  if (known.some((state) => state === "blocked")) return "scope";
  return "none";
}

/** 去重合并:同 id 只保留首次出现。 */
export function dedupeCandidates(lists: readonly (readonly AskCandidate[])[]): AskCandidate[] {
  const seen = new Set<string>();
  const merged: AskCandidate[] = [];
  for (const list of lists) {
    for (const candidate of list) {
      if (seen.has(candidate.id)) continue;
      seen.add(candidate.id);
      merged.push(candidate);
    }
  }
  return merged;
}
