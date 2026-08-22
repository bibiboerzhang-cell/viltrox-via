// Ask ⌘K 候选引擎:前缀分流 / 220ms 防抖 / generation 防串 / @ 本地 LRU / 三区空态首屏。
// 零 LLM:这里只有本地匹配 + 两个轻端点(/global-search、/catalog/suggest)+ 首屏三只读端点。

import React from "react";
import { catalogSuggest } from "../../../../../services/vkpi/catalogSuggest-api";
import { globalSearch, type GlobalSearchResult } from "../../../../../services/vkpi/globalSearch-api";
import { fetchSuggestions } from "../../../../../services/vkpi/intelligent-api";
import { listKolSearchHistory } from "../../../../../services/vkpi/kolPool-api.search";
import { fetchProgressCenter, type ProgressTask } from "../../../../../services/vkpi/progressCenter-api";
import {
  dedupeCandidates,
  filterCandidatesByTerm,
  globalSearchCandidates,
  matchNavItems,
  navCandidate,
  parseAskQuery,
  resolveEmptyKind,
  skuCandidate,
  suggestionCandidate,
  type AskCandidate,
  type AskEmptyKind,
  type AskNavItemLike,
  type AskParsedQuery,
} from "./askGrammar";
import { readAskRecent, recentCandidate } from "./askRecent";

export const ASK_DEBOUNCE_MS = 220;
const KOL_LRU_LIMIT = 60;
const HOME_JOBS_LIMIT = 3;
const HOME_RECENT_SERVER_LIMIT = 5;

// 与 SmartKolInputPanel / TaskProgressBoard 同值的待打开会话键(不 import 以免把工作台 chunk 拉进顶栏)。
export const PENDING_SEARCH_SESSION_KEY = "vkpi:pendingKolSearchSessionId";

export const OFFLINE_SUGGESTIONS = {
  zh: ["目前 KOL 数量是多少？", "多少 KOL 做过 26mm EVO 视频？", "搜索 26mm EVO 项目", "总结本周市场对于 Viltrox 的评价"],
  en: ["How many KOLs are in the pool?", "How many KOLs reviewed 26mm EVO?", "Find 26mm EVO projects", "Summarize this week's market feedback on Viltrox"],
} as const;

export type AskSourceState = "loading" | "ready" | "unavailable";

export interface AskGroup {
  key: "jobs" | "recent" | "suggestions" | "nav" | "kol" | "project" | "sku";
  title: string;
  candidates: AskCandidate[];
  /** 来源备注(例:离线建议 / 暂不可用),门面不露内部术语。 */
  note?: string;
}

export interface AskCandidatesState {
  parsed: AskParsedQuery;
  groups: AskGroup[];
  flat: AskCandidate[];
  loading: boolean;
  /** 仅在有检索词且远端已回(或纯本地)时给出;null = 有命中或仍在加载。 */
  emptyKind: AskEmptyKind | null;
  home: { jobs: AskSourceState; recent: AskSourceState; suggestions: AskSourceState; suggestionsOffline: boolean };
}

interface UseAskCandidatesOptions {
  open: boolean;
  query: string;
  apiToken: string;
  lang: string;
  navItems: readonly AskNavItemLike[];
  t: (text: string) => string;
}

type RemoteSnapshot = {
  candidates: AskCandidate[];
  states: string[];
};

function failureState(error: unknown): string {
  const status = Number((error as { status?: unknown } | null)?.status);
  return status === 401 || status === 403 ? "blocked" : "error";
}

function jobCandidate(task: ProgressTask, t: (text: string) => string): AskCandidate {
  const label = String(task.label || task.kind || task.job_type || t("进行中任务")).trim();
  const progress = typeof task.progress_pct === "number" && Number.isFinite(task.progress_pct) && !task.progress_overdue
    ? `${Math.round(task.progress_pct)}%`
    : "";
  const stage = String(task.stage_label || task.status || "").trim();
  const detail = [stage, progress].filter(Boolean).join(" · ") || t("进行中");
  const poolId = Number(task.kol_pool_id);
  return {
    kind: "job",
    id: `job:${task.id}`,
    label,
    detail,
    action: Number.isFinite(poolId) && poolId > 0
      ? { type: "open_entity", entity: { type: "kol", id: poolId }, route: "kol-pool" }
      : { type: "navigate", route: "dashboard" },
  };
}

export function useAskCandidates({ open, query, apiToken, lang, navItems, t }: UseAskCandidatesOptions): AskCandidatesState {
  const parsed = React.useMemo(() => parseAskQuery(query), [query]);
  const [remote, setRemote] = React.useState<RemoteSnapshot | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [jobs, setJobs] = React.useState<AskCandidate[]>([]);
  const [jobsState, setJobsState] = React.useState<AskSourceState>("loading");
  const [serverRecent, setServerRecent] = React.useState<AskCandidate[]>([]);
  const [recentState, setRecentState] = React.useState<AskSourceState>("loading");
  const [localRecent, setLocalRecent] = React.useState<AskCandidate[]>([]);
  const [suggestions, setSuggestions] = React.useState<AskCandidate[]>([]);
  const [suggestionsState, setSuggestionsState] = React.useState<AskSourceState>("loading");
  const [suggestionsOffline, setSuggestionsOffline] = React.useState(false);
  const generationRef = React.useRef(0);
  const kolLruRef = React.useRef<Map<string, AskCandidate>>(new Map());

  const rememberKols = React.useCallback((candidates: AskCandidate[]) => {
    const lru = kolLruRef.current;
    for (const candidate of candidates) {
      if (candidate.kind !== "kol") continue;
      lru.delete(candidate.id);
      lru.set(candidate.id, candidate);
    }
    while (lru.size > KOL_LRU_LIMIT) {
      const oldest = lru.keys().next().value;
      if (oldest === undefined) break;
      lru.delete(oldest);
    }
  }, []);

  // ── 首屏三区:每次打开拉一次;失败各自诚实降级,互不拖累。 ──
  React.useEffect(() => {
    if (!open) return;
    setLocalRecent(readAskRecent().map(recentCandidate));
    const offline = OFFLINE_SUGGESTIONS[lang === "en" ? "en" : "zh"].map(suggestionCandidate);
    if (!apiToken) {
      setJobs([]); setJobsState("unavailable");
      setServerRecent([]); setRecentState("ready");
      setSuggestions(offline); setSuggestionsState("ready"); setSuggestionsOffline(true);
      return;
    }
    const controller = new AbortController();
    setJobsState("loading"); setRecentState("loading"); setSuggestionsState("loading");
    void fetchProgressCenter({ token: apiToken, signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return;
        const active = [...data.running, ...data.queued].filter((task) => !task.masked).slice(0, HOME_JOBS_LIMIT);
        setJobs(active.map((task) => jobCandidate(task, t)));
        setJobsState("ready");
      })
      .catch(() => { if (!controller.signal.aborted) { setJobs([]); setJobsState("unavailable"); } });
    void listKolSearchHistory(apiToken, { limit: HOME_RECENT_SERVER_LIMIT, itemLimit: 0 })
      .then((data) => {
        if (controller.signal.aborted) return;
        const items = Array.isArray(data?.items) ? data.items : [];
        // 同一检索词反复跑过多次会话:只留最新一条(后端按时间倒序),免得「最近」被同名刷屏。
        const seenLabels = new Set<string>();
        setServerRecent(items.flatMap((item) => {
          const id = Number(item.id);
          const label = String(item.query_text || "").trim();
          if (!Number.isFinite(id) || id <= 0 || !label || seenLabels.has(label)) return [];
          seenLabels.add(label);
          return [{
            kind: "recent" as const,
            origin: "kol" as const,
            id: `recent:session:${id}`,
            label,
            detail: t("找达人记录"),
            action: { type: "open_entity" as const, entity: { type: "search_session" as const, id }, route: "kol-pool" },
          }];
        }));
        setRecentState("ready");
      })
      .catch(() => { if (!controller.signal.aborted) { setServerRecent([]); setRecentState("unavailable"); } });
    void fetchSuggestions(apiToken)
      .then((list) => {
        if (controller.signal.aborted) return;
        const cleaned = list.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 6);
        if (cleaned.length === 0) throw new Error("empty_suggestions");
        setSuggestions(cleaned.map(suggestionCandidate));
        setSuggestionsOffline(false);
        setSuggestionsState("ready");
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setSuggestions(offline);
        setSuggestionsOffline(true);
        setSuggestionsState("ready");
      });
    return () => controller.abort();
  }, [apiToken, lang, open, t]);

  // ── 检索:前缀分流 + 防抖 + generation。 ──
  React.useEffect(() => {
    const generation = ++generationRef.current;
    setRemote(null);
    if (!open || !parsed.term || parsed.prefix === "nav") {
      setLoading(false);
      return;
    }
    setLoading(true);
    const controller = new AbortController();
    const term = parsed.term;
    const prefix = parsed.prefix;
    const timer = window.setTimeout(() => {
      const tasks: Array<Promise<RemoteSnapshot>> = [];
      if (prefix === null || prefix === "kol" || prefix === "project") {
        tasks.push(globalSearch(term, { token: apiToken, signal: controller.signal })
          .then((result: GlobalSearchResult) => {
            const candidates = globalSearchCandidates(result, prefix, t);
            rememberKols(candidates);
            const relevant = prefix === "kol" ? ["kols"] : prefix === "project" ? ["projects", "events"] : ["kols", "projects", "events"];
            const states = relevant.map((key) => result.source_status?.[key as "kols"]?.status || "ready");
            return { candidates, states };
          })
          .catch((error: unknown) => ({ candidates: [], states: [failureState(error)] })));
      }
      if (prefix === null || prefix === "sku") {
        tasks.push(catalogSuggest(term, { token: apiToken, signal: controller.signal, limit: prefix === "sku" ? 20 : 6 })
          .then((result) => ({
            candidates: result.items.map((item) => skuCandidate(item, t)),
            states: Object.values(result.source_status).map((entry) => entry.status),
          }))
          .catch((error: unknown) => ({ candidates: [], states: [failureState(error)] })));
      }
      void Promise.all(tasks).then((snapshots) => {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        setRemote({
          candidates: dedupeCandidates(snapshots.map((snapshot) => snapshot.candidates)),
          states: snapshots.flatMap((snapshot) => snapshot.states),
        });
        setLoading(false);
      });
    }, ASK_DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [apiToken, open, parsed.prefix, parsed.term, rememberKols, t]);

  return React.useMemo<AskCandidatesState>(() => {
    const groups: AskGroup[] = [];
    const { prefix, term } = parsed;
    if (!term && !prefix) {
      groups.push({ key: "jobs", title: t("进行中"), candidates: jobs, note: jobsState === "unavailable" ? t("该来源暂不可用") : undefined });
      const recent = dedupeCandidates([localRecent, serverRecent]).slice(0, 10);
      groups.push({ key: "recent", title: t("最近"), candidates: recent, note: recentState === "unavailable" ? t("该来源暂不可用") : undefined });
      groups.push({ key: "suggestions", title: t("建议"), candidates: suggestions, note: suggestionsOffline ? t("离线建议") : undefined });
      return {
        parsed,
        groups,
        flat: groups.flatMap((group) => group.candidates),
        loading: false,
        emptyKind: null,
        home: { jobs: jobsState, recent: recentState, suggestions: suggestionsState, suggestionsOffline },
      };
    }
    const home = { jobs: jobsState, recent: recentState, suggestions: suggestionsState, suggestionsOffline };
    if (prefix === "nav") {
      const navs = matchNavItems(term, navItems, t).map((match) => navCandidate(match.item, t));
      groups.push({ key: "nav", title: t("板块"), candidates: navs });
      return { parsed, groups, flat: navs, loading: false, emptyKind: navs.length ? null : "none", home };
    }
    const local: AskCandidate[] = [];
    if (prefix === null) {
      local.push(...matchNavItems(term, navItems, t, 3).filter((match) => match.tier <= 1).map((match) => navCandidate(match.item, t)));
      local.push(...filterCandidatesByTerm(dedupeCandidates([localRecent, serverRecent]), term, 3));
    }
    if (prefix === null || prefix === "kol") {
      local.push(...filterCandidatesByTerm(Array.from(kolLruRef.current.values()), term, prefix === "kol" ? 6 : 3));
    }
    const all = dedupeCandidates([local, remote?.candidates ?? []]);
    const byKind = (kinds: AskCandidate["kind"][]) => all.filter((candidate) => kinds.includes(candidate.kind));
    const navGroup = byKind(["nav", "recent"]);
    const kolGroup = byKind(["kol"]);
    const projectGroup = byKind(["project", "event"]);
    const skuGroup = byKind(["sku"]);
    if (navGroup.length) groups.push({ key: "nav", title: t("直达"), candidates: navGroup });
    if (kolGroup.length) groups.push({ key: "kol", title: "KOL", candidates: kolGroup });
    if (projectGroup.length) groups.push({ key: "project", title: t("项目与活动"), candidates: projectGroup });
    if (skuGroup.length) groups.push({ key: "sku", title: t("SKU 与镜头"), candidates: skuGroup });
    const flat = groups.flatMap((group) => group.candidates);
    const emptyKind = loading || !remote ? null : resolveEmptyKind(remote.states, flat.length);
    return { parsed, groups, flat, loading, emptyKind, home };
  }, [jobs, jobsState, loading, localRecent, navItems, parsed, recentState, remote, serverRecent, suggestions, suggestionsOffline, suggestionsState, t]);
}
