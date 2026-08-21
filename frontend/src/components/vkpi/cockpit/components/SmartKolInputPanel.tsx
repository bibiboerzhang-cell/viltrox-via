import { useCallback, useEffect, useMemo, useState } from "react";
import { BadgeCheck, Link2, Loader2, Search, Sparkles, Video } from "lucide-react";

import {
  deepCrawlKolUrl,
  getKolSearchSession,
  listKolSearchHistory,
  smartKolSearchProfileAdvanceJob,
  smartKolSearch,
  type VkpiKolRecallResponse,
  type VkpiKolSearchHistoryItem,
  type VkpiKolSmartSearchProfileAdvanceResponse,
  type VkpiKolUrlDeepCrawlResponse,
} from "../../../../domains/kol";
import {
  archiveAllKolSearchHistory,
  archiveKolSearchHistorySession,
  restoreKolSearchHistorySession,
} from "../../../../services/vkpi/kolPool-api";

import {
  asRecord,
  cleanText,
  detectMode,
  sessionIdFrom,
  type Mode,
  type Row,
} from "./SmartKolInputPanel.helpers";
import {
  HistoryStrip,
  PENDING_SEARCH_SESSION_KEY,
  PROFILE_REP_VIDEO_LIMIT,
  UrlSummary,
  discoveryAutoEnrolledFromSession, discoveryBrandExcludedFromSession,
  discoveryItemsFromSession,
  historySessionId,
  isSearchSessionTerminal,
  looksLikeRetailer,
  mergeKolRecallSnapshots,
  mergeKolSearchSessionSnapshots,
  reachFloorDisplayFromSession,
  readPersistedSearchDisplay,
  recallResultFromSession,
  recallTopItems,
  sessionAdvanceCounts,
  sessionItems,
  sessionStatusBanner,
  searchSessionProgress,
  urlResultFromSession,
  writePersistedSearchDisplay,
} from "./SmartKolInputPanel.Sections";
import { TextResultSection } from "./SmartKolInputPanel.TextResult";
import {
  canExecuteUrlResult,
  extractUrls,
  URL_BATCH_MAX,
  type SmartKolInputPanelProps,
} from "./SmartKolInputPanel.runtime";
import {
  EMPTY_KOL_SEARCH_FILTERS,
  KOL_SEARCH_RESULT_LIMIT,
  KOL_SEARCH_STRATEGIES,
  KolSearchPolicyPanel,
  strategyFromLegacyMode,
  toKolSearchApiFilters,
  type KolSearchFilterState,
  type KolSearchStrategy,
} from "./SmartKolInputPanel.SearchPolicy";
import { LOCAL_QUALIFICATION_SPEC } from "./SmartKolInputPanel.LocalQualified";
import { ONLINE_QUALIFICATION_SPEC, strictOnlineDiscoveryPlatforms } from "./SmartKolInputPanel.OnlineQualified";
import {
  type SearchRequestEpoch,
  useLatestSearchRequestEpoch,
} from "./SmartKolInputPanel.sessionEpoch";
import { useSmartKolSelection } from "./SmartKolInputPanel.selection";
import { sessionDisplayState, smartKolSearchFingerprint } from "./SmartKolInputPanel.searchState";
type State = "idle" | "loading" | "ready" | "executing" | "error";
export function SmartKolInputPanel({
  apiToken = "",
  searchMode = "balanced",
  onSearchModeChange,
  onRecallItems,
  onOpenRecallItem,
  onOpenProfile,
}: SmartKolInputPanelProps) {
  // 挂载时回填上次激活搜索的展示态(sessionStorage),让 90s/10min 父刷新若偶发重挂本面板时
  // ①②③ 结果与轮询不凭空消失;无持久化则回到正常初始态。
  const persistedDisplay = useMemo(() => readPersistedSearchDisplay(), []);
  const [input, setInput] = useState(() => persistedDisplay?.input ?? "");
  const [state, setState] = useState<State>(() => (persistedDisplay?.recallResult || persistedDisplay?.urlResult ? "ready" : "idle"));
  const [mode, setMode] = useState<Mode>(() => persistedDisplay?.mode ?? "idle");
  const [urlResult, setUrlResult] = useState<VkpiKolUrlDeepCrawlResponse | null>(() => persistedDisplay?.urlResult ?? null);
  const [recallResult, setRecallResult] = useState<VkpiKolRecallResponse | null>(() => persistedDisplay?.recallResult ?? null);
  const [advanceResult, setAdvanceResult] = useState<VkpiKolSmartSearchProfileAdvanceResponse | null>(null);
  const [displayedSearchSessionId, setDisplayedSearchSessionId] = useState<number | null>(() => persistedDisplay?.activeSearchSessionId ?? null);
  const [pollingSearchSessionId, setPollingSearchSessionId] = useState<number | null>(() => {
    const persistedId = persistedDisplay?.activeSearchSessionId ?? null;
    return persistedId && persistedDisplay?.activeSearchSession && !isSearchSessionTerminal(persistedDisplay.activeSearchSession)
      ? persistedId
      : null;
  });
  const [activeSearchSession, setActiveSearchSession] = useState<VkpiKolSearchHistoryItem | null>(() => persistedDisplay?.activeSearchSession ?? null);
  const [sessionPollNotice, setSessionPollNotice] = useState("");
  const [historyItems, setHistoryItems] = useState<VkpiKolSearchHistoryItem[]>([]);
  const [archivedHistoryItems, setArchivedHistoryItems] = useState<VkpiKolSearchHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyActionBusy, setHistoryActionBusy] = useState("");
  const [historyNotice, setHistoryNotice] = useState("");
  const [error, setError] = useState("");
  // 新搜索工作台仍兼容旧页 balanced/precision/discovery 状态；UI 用业务语言展示为
  // 平衡/垂直优先/拓展，并把选择同步回旧 FilterBar，避免同页出现两套互相冲突的模式。
  const [localSearchMode, setLocalSearchMode] = useState(searchMode);
  const searchStrategy = strategyFromLegacyMode(localSearchMode);
  const searchPolicy = KOL_SEARCH_STRATEGIES[searchStrategy];
  const [searchFiltersOpen, setSearchFiltersOpen] = useState(false);
  const [searchFilters, setSearchFilters] = useState<KolSearchFilterState>(EMPTY_KOL_SEARCH_FILTERS);
  // 问题2 平台选择器:默认全选已落地的 YT/IG/TikTok(FB 待 provider 落地,UI 置灰)。
  const [discoveryPlatforms, setDiscoveryPlatforms] = useState<string[]>(["youtube", "instagram", "tiktok"]);
  // 国家/地区只有一个真状态：搜索前筛选与结果区「重新查找」共用，避免 UI 看见 A、请求却发 B。
  const discoveryRegion = searchFilters.country;
  const setDiscoveryRegion = (value: string) => setSearchFilters((current) => ({ ...current, country: value }));
  const [contentLanguages, setContentLanguages] = useState<string[]>([]);
  const [kolProfileTypes, setKolProfileTypes] = useState<string[]>([]);
  // 刀1·流3 恒开(2026-06-16):全网发现不再挂开关,任何文字搜索都自动触发(见 run() 的 queueTextAdvance)。
  // P0-6 地区口径:默认开,排除 CN/HK/TW 三地区(country/market 判据),海外中文博主放行;后端参数名保留 exclude_chinese。
  const [excludeChinese, setExcludeChinese] = useState(true);
  const { beginSearchRequest, currentSearchRequest, isCurrentSearchRequest } = useLatestSearchRequestEpoch();
  // 【K7】URL 多行批量:输入含 ≥2 个 http(s) URL 时逐条排队分析(串行,间隔 500ms,上限 10)。
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchNote, setBatchNote] = useState("");
  useEffect(() => {
    setLocalSearchMode(searchMode);
  }, [searchMode]);

  const setSearchStrategy = (strategy: KolSearchStrategy) => {
    const nextMode = KOL_SEARCH_STRATEGIES[strategy].legacyMode;
    setLocalSearchMode(nextMode);
    onSearchModeChange?.(nextMode);
  };
  const localQualificationSpec = useMemo(
    () => ({
      ...LOCAL_QUALIFICATION_SPEC,
      languages: contentLanguages,
      profile_types: kolProfileTypes,
    }),
    [contentLanguages, kolProfileTypes],
  );
  const currentSearchFingerprint = useMemo(() => smartKolSearchFingerprint({
    query: input,
    market: discoveryRegion,
    platforms: discoveryPlatforms,
    languages: contentLanguages,
    profileTypes: kolProfileTypes,
    excludeChinese,
    searchMode,
  }), [contentLanguages, discoveryPlatforms, discoveryRegion, excludeChinese, input, kolProfileTypes, searchMode]);
  // Filter state is not cached: restored lists stay read-only until reopened or rerun.
  const [recallFingerprint, setRecallFingerprint] = useState("");
  const recallIsStale = Boolean(recallResult && recallFingerprint !== currentSearchFingerprint);
  const displayedSessionTerminal = Boolean(activeSearchSession && isSearchSessionTerminal(activeSearchSession));
  const approvalReady = Boolean(displayedSearchSessionId && displayedSessionTerminal && !pollingSearchSessionId && recallResult && !recallIsStale && state === "ready");
  const {
    pickedIds, setPickedIds, clearPickedIds, addingFav, favNote,
    favoriteIds, favoriteBusyIds, favoriteResults, favoriteErrors, favoritesSyncing, favoritesLoadError,
    draftBusy, draftNote, outreachBusy, outreachNote, outreachResult,
    discoveryKey, favoriteOne, addPickedToMyKol, approveAndCreateDraft, generateOutreachForPicked,
  } = useSmartKolSelection({ apiToken, displayedSearchSessionId, canApprove: approvalReady, canFavorite: !recallIsStale, currentSearchRequest, isCurrentSearchRequest });
  const inferredMode = useMemo(() => detectMode(input), [input]);
  const isBusy = state === "loading" || state === "executing" || batchBusy;
  const urlCanExecute = canExecuteUrlResult(apiToken, urlResult, isBusy);
  const recallItems = useMemo(() => recallTopItems(recallResult), [recallResult]);
  const llmPlan = asRecord((recallResult as Row | null)?.llm_query_plan);
  // 三框·框3:全网发现项(new_creator)从在役 advance 会话抽取,与框2 库内召回分开展示。
  const discoveryItems = useMemo(() => {
    const all = discoveryItemsFromSession(activeSearchSession);
    // 平台筛选即时生效:按当前勾选的 discoveryPlatforms 过滤已展示的发现结果(不必重搜)。
    // platform 字段缺失的未分类项放行,避免因字段缺失误藏。全不勾选时仅余未分类项。
    return all.filter((it) => {
      if (looksLikeRetailer(it)) return false;  // 滤掉 B&H 等零售/经销实体,非真创作者
      const p = String(it.platform || "").toLowerCase();
      return !p || discoveryPlatforms.includes(p);
    });
  }, [activeSearchSession, discoveryPlatforms]);
  // 【K3】入库反馈:本次会话全网新发现总数(未经平台筛选)——后端 _auto_enroll_discoveries 已把
  // new_creator 自动落 Pool(会话项 kol_pool_id 保持 NULL 是不变式),故「发现数=已自动入库数」。
  const discoveryTotal = useMemo(() => discoveryItemsFromSession(activeSearchSession).length, [activeSearchSession]);
  // 【K3 正账】真实自动入库数(result_summary.new_discovery.counts.auto_enrolled);旧会话无该键 → null,
  // TextResultSection 回退到概述文案,不编数字。
  const discoveryAutoEnrolled = useMemo(() => discoveryAutoEnrolledFromSession(activeSearchSession), [activeSearchSession]);
  // 触达展示闸折叠计数(2026-07-12 第二道闸「分析后再 po」):后端按 pool 现值隐藏低触达/
  // 补全中的候选,前端只render诚实计数行;旧后端无该键 → null 静默不渲染。
  const reachFloorDisplay = useMemo(() => reachFloorDisplayFromSession(activeSearchSession), [activeSearchSession]);
  // 三框·框1:LLM 人群理解可编辑(防 LLM 理解偏)——编辑后「用此重搜」。
  const [personaEditing, setPersonaEditing] = useState(false);
  const [personaDraft, setPersonaDraft] = useState("");
  const activeSessionCounts = sessionAdvanceCounts(activeSearchSession);
  const activeSessionSummary = asRecord(activeSearchSession?.result_summary);
  const activeSmartJob = asRecord(activeSessionSummary.smart_search_profile_advance_job);
  const activeSessionStatus = cleanText(activeSmartJob.advance_status || activeSmartJob.status || activeSearchSession?.status);
  // F-display:AI 规划退回基础检索的诚实提示。读 result_summary.smart_search_profile_advance_job
  // .query_plan_source ∈ {llm_plan, rule_v0_fallback};仅当存在且等于 rule_v0_fallback 才提示,
  // 字段缺失/为 llm_plan 时静默不渲染(graceful absence,不编造)。
  const plannerFellBack = cleanText(activeSmartJob.query_plan_source) === "rule_v0_fallback";
  const activeSessionProgress = useMemo(
    () => searchSessionProgress(activeSearchSession),
    [activeSearchSession],
  );
  // 诚实会话横幅(排队/查找中/已完成/部分完成/未完成)——只读后端真有字段,见 sessionStatusBanner。
  // advanceResult?.status:queueTextAdvance 刚返回、尚未首拍轮询时的即时状态兜底(queued/...)。
  const sessionBanner = useMemo(
    () => sessionStatusBanner(activeSearchSession, activeSessionStatus || cleanText(advanceResult?.status), activeSessionCounts, Boolean(displayedSearchSessionId)),
    [activeSearchSession, activeSessionStatus, advanceResult, activeSessionCounts, displayedSearchSessionId],
  );

  useEffect(() => {
    if (recallItems.length) onRecallItems?.(recallItems);
  }, [recallItems, onRecallItems]);

  // 搜索展示态持久化:任一 ①②③ 相关态变化就写回 sessionStorage(兜底重挂恢复);
  // 全空(无召回/无 URL 结果/无激活会话)时清掉,避免回填到一个空壳搜索框。
  useEffect(() => {
    if (!recallResult && !urlResult && !activeSearchSession && !displayedSearchSessionId) {
      writePersistedSearchDisplay(null);
      return;
    }
    writePersistedSearchDisplay({ input, mode, recallResult, urlResult, activeSearchSession, activeSearchSessionId: displayedSearchSessionId });
  }, [input, mode, recallResult, urlResult, activeSearchSession, displayedSearchSessionId]);

  const refreshHistory = useCallback(async () => {
    if (!apiToken) {
      setHistoryItems([]);
      setArchivedHistoryItems([]);
      return;
    }
    setHistoryLoading(true);
    try {
      const [active, archived] = await Promise.allSettled([
        listKolSearchHistory(apiToken, { limit: 50, itemLimit: 5, archived: false }),
        listKolSearchHistory(apiToken, { limit: 50, itemLimit: 0, archived: true }),
      ]);
      if (active.status === "fulfilled") {
        setHistoryItems(Array.isArray(active.value.items) ? active.value.items : []);
      }
      if (archived.status === "fulfilled") {
        setArchivedHistoryItems(Array.isArray(archived.value.items) ? archived.value.items : []);
      }
      if (active.status === "rejected" && archived.status === "rejected") {
        setHistoryNotice("历史记录暂时无法同步，主搜索功能不受影响");
      }
    } catch {
      setHistoryNotice("历史记录暂时无法同步，主搜索功能不受影响");
    } finally {
      setHistoryLoading(false);
    }
  }, [apiToken]);

  const archiveHistoryEntry = useCallback(async (session: VkpiKolSearchHistoryItem) => {
    const sessionId = historySessionId(session);
    if (!apiToken || !sessionId) return;
    setHistoryActionBusy(`active-${sessionId}`);
    setHistoryNotice("");
    try {
      await archiveKolSearchHistorySession(apiToken, sessionId);
      setHistoryNotice("已从最近历史移除；搜索结果和任务数据仍保留，可在“已移除”中恢复");
      await refreshHistory();
    } catch (err) {
      setHistoryNotice(err instanceof Error ? err.message : "移除失败，请稍后重试");
    } finally {
      setHistoryActionBusy("");
    }
  }, [apiToken, refreshHistory]);

  const restoreHistoryEntry = useCallback(async (session: VkpiKolSearchHistoryItem) => {
    const sessionId = historySessionId(session);
    if (!apiToken || !sessionId) return;
    setHistoryActionBusy(`archived-${sessionId}`);
    setHistoryNotice("");
    try {
      await restoreKolSearchHistorySession(apiToken, sessionId);
      setHistoryNotice("历史记录已恢复");
      await refreshHistory();
    } catch (err) {
      setHistoryNotice(err instanceof Error ? err.message : "恢复失败，请稍后重试");
    } finally {
      setHistoryActionBusy("");
    }
  }, [apiToken, refreshHistory]);

  const archiveCompletedHistory = useCallback(async () => {
    if (!apiToken) return;
    setHistoryActionBusy("all");
    setHistoryNotice("");
    try {
      const response = await archiveAllKolSearchHistory(apiToken);
      const archivedCount = Math.max(0, Number(response.archived_count) || 0);
      const skippedCount = Math.max(0, Number(response.skipped_active_count) || 0);
      setHistoryNotice(`已移除 ${archivedCount} 条已完成记录${skippedCount ? `；${skippedCount} 条进行中任务已保留` : ""}`);
      await refreshHistory();
    } catch (err) {
      setHistoryNotice(err instanceof Error ? err.message : "清理失败，请稍后重试");
    } finally {
      setHistoryActionBusy("");
    }
  }, [apiToken, refreshHistory]);

  const restoreSession = useCallback((session: VkpiKolSearchHistoryItem) => {
    const query = cleanText(session.query_text);
    if (query) setInput(query);
    setAdvanceResult(null);
    setActiveSearchSession(session);
    setSessionPollNotice("");
    setError("");
    const queryType = cleanText(session.query_type);
    if (queryType === "url_video" || queryType === "url_profile") {
      const nextUrlResult = urlResultFromSession(session);
      setMode("url");
      setUrlResult(nextUrlResult);
      setRecallResult(null);
    } else {
      setMode("text");
      // 框2(库内召回)+ 框3(全网发现,派生自 activeSearchSession)立即由完整会话回填,不再只填 query。
      setRecallResult(recallResultFromSession(session));
      // Historical sessions do not yet persist an authoritative filter fingerprint. Keep every
      // restored text recall view-only until the current chips are explicitly re-run.
      setRecallFingerprint("");
      setUrlResult(null);
    }
    // 重开的会话若仍未终态(running/排队),续接轮询让后到的发现/分析项继续回填 ①②③;
    // 已终态则不再起轮询(避免空转),展示态已由上面完整回填。
    const sessionId = historySessionId(session);
    const sessionState = sessionDisplayState(sessionId, isSearchSessionTerminal(session));
    setDisplayedSearchSessionId(sessionState.displayedSessionId);
    setPollingSearchSessionId(sessionState.pollingSessionId);
    if (sessionState.pollingSessionId) {
      setSessionPollNotice("正在续接后台查找…");
    }
    setState("ready");
  }, []);

  const applyPolledSession = useCallback((session: VkpiKolSearchHistoryItem) => {
    const queryType = cleanText(session.query_type);
    if (queryType === "url_video" || queryType === "url_profile") {
      setActiveSearchSession(session);
      setMode("url");
      setUrlResult(urlResultFromSession(session));
      setRecallResult(null);
      return;
    }
    setMode("text");
    // 轮询快照会因异步写入时序出现「这一拍字段更少」。按 pool id(无 id 时 platform+handle)
    // 做逐项、逐字段 keep-richer merge:ready/已显示字段不会被后到的稀疏快照刷掉。
    setActiveSearchSession((prev) => mergeKolSearchSessionSnapshots(prev, session));
    // 框2(库内召回)非破坏式覆盖:run() 触发的全网发现会建一个 advance 会话并启动轮询,其 recall
    // items 由后台 worker 异步写入,轮询头几拍常拿到 running/空会话。若无条件覆盖,会把 run() 首屏已
    // 渲染的库内召回刷成空 → 看起来「用户列表整块消失」。故:空轮询不得覆盖已有的非空召回(保住首屏);
    // 但当前无召回(null/空)时仍允许用轮询结果点亮结果区,以便框3 全网发现能显示。
    // 框1(产品人群分析)保活:recallResultFromSession 不带 llm_query_plan(那只在实时 smartKolSearch
    // 响应里有),若直接替换会把已渲染的 ① PlanPills 刷没 → 单独把上次的 llm_query_plan 透传进来。
    const polledRecall = recallResultFromSession(session);
    setRecallResult((prev) => mergeKolRecallSnapshots(prev, polledRecall));
    setUrlResult(null);
  }, []);

  const openHistorySession = useCallback(async (sessionOrId: VkpiKolSearchHistoryItem | number | string) => {
    if (!apiToken) return;
    const requestEpoch = beginSearchRequest();
    clearPickedIds();
    setState("loading");
    setRecallResult(null);
    setRecallFingerprint("");
    setDisplayedSearchSessionId(null);
    setPollingSearchSessionId(null);
    setActiveSearchSession(null);
    const knownSession = typeof sessionOrId === "object" ? sessionOrId : null;
    const sessionId = knownSession ? historySessionId(knownSession) : Number(sessionOrId);
    if (!sessionId) {
      if (knownSession) restoreSession(knownSession);
      return;
    }
    setHistoryLoading(true);
    try {
      const session = await getKolSearchSession(apiToken, sessionId);
      if (!isCurrentSearchRequest(requestEpoch)) return;
      restoreSession(session);
      void refreshHistory();
    } catch (err) {
      if (!isCurrentSearchRequest(requestEpoch)) return;
      if (knownSession) {
        restoreSession(knownSession);
      } else {
        setError(err instanceof Error ? err.message : "历史记录读取失败");
      }
    } finally {
      if (isCurrentSearchRequest(requestEpoch)) {
        setHistoryLoading(false);
        setState((current) => current === "loading" ? "ready" : current);
      }
    }
  }, [apiToken, beginSearchRequest, clearPickedIds, isCurrentSearchRequest, refreshHistory, restoreSession]);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  useEffect(() => {
    if (!apiToken || typeof window === "undefined") return undefined;
    const openPending = (sessionId: unknown) => {
      const parsed = Number(sessionId);
      if (Number.isFinite(parsed) && parsed > 0) void openHistorySession(parsed);
    };
    const fromStorage = window.localStorage.getItem(PENDING_SEARCH_SESSION_KEY);
    if (fromStorage) {
      window.localStorage.removeItem(PENDING_SEARCH_SESSION_KEY);
      openPending(fromStorage);
    }
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail || {};
      openPending(detail.sessionId || detail.session_id);
    };
    window.addEventListener("vkpi:open-kol-search-session", handler);
    return () => window.removeEventListener("vkpi:open-kol-search-session", handler);
  }, [apiToken, openHistorySession]);

  useEffect(() => {
    if (!apiToken || !pollingSearchSessionId || typeof window === "undefined") return undefined;
    let cancelled = false;
    let inFlight = false;
    let terminalSince: number | null = null;  // 必需任务完成/终态后的稳定宽限起点(闭包,随会话重置)
    const startedAt = Date.now();
    const maxPollMs = 12 * 60 * 1000;
    const poll = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        const session = await getKolSearchSession(apiToken, pollingSearchSessionId);
        if (cancelled) return;
        applyPolledSession(session);
        const progress = searchSessionProgress(session);
        const stageText = (label: string, stage: typeof progress.video) => {
          const suffix = [
            stage.active > 0 ? `进行 ${stage.active}` : "",
            stage.failed > 0 ? `失败 ${stage.failed}` : "",
            stage.notRequested > 0 ? `未请求 ${stage.notRequested}` : "",
          ].filter(Boolean).join("/");
          return `${label} ${stage.ready}/${progress.target}${suffix ? `（${suffix}）` : ""}`;
        };
        const progressNote = progress.target > 0
          ? progress.downstreamTracked
            ? `阶段：${progress.phaseLabel} · ①基础 ${progress.basicVisible}/${progress.target} · ②档案 ${progress.profileReady}/${progress.target} · ③${stageText("视频", progress.video)} · ④${stageText("评论", progress.comments)} / ${stageText("受众", progress.audience)}`
            : `阶段：${progress.phaseLabel} · 基础结果 ${progress.basicVisible}/${progress.target} · 档案补全 ${progress.profileReady}/${progress.target} · 完整分析 ${progress.deepReady}/${progress.target}${progress.deepPartial > 0 ? ` · 部分 ${progress.deepPartial}` : ""}`
          : `阶段：${progress.phaseLabel}`;
        setSessionPollNotice(progressNote);
        // discovery 先到不等于整批完成。只按后端 phase/counts/必需任务终态推进；若旧后端只有
        // session 终态，则进入 30s 宽限。这样尾随写入仍能逐卡补齐，且不会无限轮询。
        const timedOut = Date.now() - startedAt > maxPollMs;
        if (progress.requiredTasksComplete) {
          if (terminalSince == null) terminalSince = Date.now();
          const graceUsedUp = Date.now() - terminalSince >= 30000;
          if (graceUsedUp || timedOut) {
            setPollingSearchSessionId(null);
            setSessionPollNotice(`${progressNote} · 结果已更新`);
            void refreshHistory();
            return;
          }
          // 必需任务已结束但仍在宽限期 → 继续接收尾随字段/卡片。
        } else {
          terminalSince = null;
          if (timedOut) {
            setPollingSearchSessionId(null);
            setSessionPollNotice(`${progressNote} · 仍在后台补全，可从历史或任务里继续查看`);
            void refreshHistory();
          }
        }
      } catch (err) {
        if (cancelled) return;
        setSessionPollNotice(err instanceof Error ? err.message : "同步失败，稍后会自动重试");
      } finally {
        inFlight = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      void poll();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pollingSearchSessionId, apiToken, applyPolledSession, refreshHistory]);

  const run = async (overrideQuery?: string) => {
    const query = cleanText(overrideQuery ?? input);
    if (!apiToken) {
      setState("error");
      setError("未登录 / 无 token");
      return;
    }
    if (!query) {
      setState("error");
      setError("输入为空");
      return;
    }
    const requestFingerprint = smartKolSearchFingerprint({
      query,
      market: discoveryRegion,
      platforms: discoveryPlatforms,
      languages: contentLanguages,
      profileTypes: kolProfileTypes,
      excludeChinese,
      searchMode,
    });
    const requestEpoch = beginSearchRequest();
    clearPickedIds();
    const nextMode = detectMode(query);
    setMode(nextMode);
    setState("loading");
    setError("");
    setUrlResult(null);
    setRecallResult(null);
    setRecallFingerprint("");
    setAdvanceResult(null);
    setDisplayedSearchSessionId(null);
    setPollingSearchSessionId(null);
    setActiveSearchSession(null);
    setSessionPollNotice("");
    try {
      const apiFilters = toKolSearchApiFilters(searchFilters, discoveryPlatforms);
      const response = await smartKolSearch(apiToken, query, {
        mode: "auto",
        maxPosts: 3,
        // 30 是「筛选后目标」而非抓取前上限：先过采样，再由后端执行硬筛选、业务分桶与诚实补位。
        // 同时保留 limit/creator/reviewer 兼容旧服务；新服务以 result_limit/filters/bucket_policy 为准。
        candidateLimit: 500,
        limit: KOL_SEARCH_RESULT_LIMIT,
        resultLimit: KOL_SEARCH_RESULT_LIMIT,
        creatorQuota: searchPolicy.creatorQuota,
        reviewerQuota: searchPolicy.reviewerQuota,
        searchStrategy,
        filters: apiFilters,
        bucketPolicy: searchPolicy.bucketPolicy,
        market: discoveryRegion,
        platforms: discoveryPlatforms,
        languages: contentLanguages,
        profileTypes: kolProfileTypes,
        localQualificationSpec,
        // createSession:true 回滚——false 会让前端 activeSearchSession 拿不到 advance 会话的全网发现项,
        // 整组「全网新发现」消失(550pro2 监视器搜出 15 个却 0 显示的真因)。宁可历史多一条空会话,也要保显示。
        createSession: true,
        excludeChinese,
        timeoutMs: 60000,
      });
      if (!isCurrentSearchRequest(requestEpoch)) return;
      const responseMode = cleanText(response.mode);
      const isText = !(responseMode === "url" || cleanText(response.query_type).startsWith("url_"));
      const responseRow = asRecord(response);
      const responseResult = asRecord(response.result);
      const responsePlan = asRecord(responseRow.llm_query_plan || responseResult.llm_query_plan);
      const needsProductClarification = cleanText(response.status || responsePlan.status) === "needs_clarification";
      let autoProfile: VkpiKolUrlDeepCrawlResponse | null = null;
      let autoVideo: VkpiKolUrlDeepCrawlResponse | null = null;
      if (!isText) {
        setMode("url");
        const urlPayload = response.result as VkpiKolUrlDeepCrawlResponse;
        setUrlResult(urlPayload);
        // 账号 URL 自动入库:识别为 profile 且后端 dry-run 就绪(dry_run_ready)→ 直接自动 execute
        // (mode profile_basics:只抓基础资料 + 入库,不触发昂贵视频深析),前端随后展示 ProfileInfoCard。
        if (
          urlPayload.url_type === "profile" &&
          !urlPayload.execute &&
          cleanText(asRecord(urlPayload.profile_flow).status) === "dry_run_ready"
        ) {
          autoProfile = urlPayload;
        }
        // video URL 自动 execute(urlCanExecute 同门槛):evidence+final_v1 幂等,轮询回填,失败可手动重试。
        if (!autoProfile && urlPayload.url_type === "video" && !urlPayload.execute) {
          const vFlow = asRecord(urlPayload.video_flow);
          const vCreator = asRecord(urlPayload.creator_identity || vFlow.creator_identity);
          const vStatus = cleanText(asRecord(urlPayload.profile_flow).status || vFlow.status);
          const pOp = cleanText(asRecord(urlPayload.profile_flow).operation);
          const rawVOp = cleanText(vFlow.operation);
          const vOp = ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(pOp)
            ? pOp
            : rawVOp || pOp;
          const vCreatorResolved = Boolean(
            cleanText(vFlow.creator_resolution_status) === "resolved" ||
            cleanText(
              vCreator.handle ||
                vCreator.channel_id ||
                vCreator.profile_url ||
                urlPayload.handle ||
                urlPayload.channel_id,
            ),
          );
          // 后台视频解析队列(2026-07)已接管创作者识别:dry-run 阶段拿不到创作者
          // (供应商抓取延迟到 worker,video_flow.status=provider_refresh_pending)也照样
          // 自动 execute——后端会把这类 URL 排进专用解析队列,分阶段回填创作者/媒体/分析。
          // 旧门槛(必须先解析出创作者)是队列上线前的遗留,会让新鲜视频 URL 永远停在空壳会话。
          const vDeferredResolution = cleanText(vFlow.status) === "provider_refresh_pending";
          // 中国平台视频(B站/抖音/小红书):仅内容分析不建档,同样自动 execute 进专用队列。
          const vCnPlatform = Boolean(
            vFlow.cn_platform_video === true || cleanText(vFlow.status) === "cn_platform_video_planned",
          );
          if (
            vDeferredResolution ||
            vCnPlatform ||
            (["dry_run_ready", "ready_to_execute"].includes(vStatus) &&
              vCreatorResolved &&
              ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(vOp))
          ) {
            autoVideo = urlPayload;
          }
        }
      } else {
        setMode("text");
        setRecallResult(response.result as VkpiKolRecallResponse);
        setRecallFingerprint(requestFingerprint);
        if (needsProductClarification) {
          const clarification = asRecord(responsePlan.clarification);
          setSessionPollNotice(cleanText(clarification.message) || "未匹配到明确产品，请先选择正确产品。");
        }
      }
      setState("ready");
      void refreshHistory();
      // 刀1·流3(2026-06-16)恒开:任何文字搜索都自动触发全网发现(advance-job 全量,含所选平台),
      // 不再挂在「深度查找」开关上 →「先库内召回 → 再全网发现」一步到位,本地+线上首屏同呈。
      // 预算护栏 enforce 兜底超支(已确认放行)。
      if (isText && !needsProductClarification) {
        const previewSessionId = sessionIdFrom(response.search_session);
        void queueTextAdvance(overrideQuery, requestEpoch, previewSessionId);
      }
      // 账号 URL 自动抓资料 + 入库(不再弹「抓基础资料」二次确认)。
      if (autoProfile) void runUrlExecute(autoProfile, { auto: true, requestEpoch });
      // 刀1·流1:video URL 自动入 evidence + 排 final_v1(不再弹「只分析此视频」二次确认)。
      if (autoVideo) void runUrlExecute(autoVideo, { auto: true, requestEpoch });
    } catch (err) {
      if (!isCurrentSearchRequest(requestEpoch)) return;
      setState("error");
      setError(err instanceof Error ? err.message : "请求失败，请重试");
    }
  };

  // 【K7】URL 多行批量:复用现有单 URL 通道(run → smartKolSearch → 自动 execute),串行 await
  // 保证面板状态不打架;每条之间停 500ms 不打爆后端。面板结果区显示的是最后一条的详情,
  // 批量进度/入队数以 batchNote 为准;各条的分析进度照常进「最近历史 / 任务板」。上限 10 条防误粘。
  const runUrlBatch = async (urls: string[]) => {
    if (!apiToken || batchBusy) return;
    const capped = urls.slice(0, URL_BATCH_MAX);
    setBatchBusy(true);
    setBatchNote(`已入队 ${capped.length} 条${urls.length > capped.length ? `(共检测到 ${urls.length} 条,超出上限 ${URL_BATCH_MAX} 的已忽略)` : ""} · 逐条排队分析中…`);
    try {
      for (let i = 0; i < capped.length; i += 1) {
        setBatchNote(`批量分析中 ${i + 1}/${capped.length} · ${capped[i].slice(0, 64)}`);
        // eslint-disable-next-line no-await-in-loop
        await run(capped[i]);
        // eslint-disable-next-line no-await-in-loop
        await new Promise((resolve) => window.setTimeout(resolve, 500));
      }
      setBatchNote(`已入队 ${capped.length} 条 · 后台逐条分析,进度见「最近历史」或左侧任务板`);
    } finally {
      setBatchBusy(false);
    }
  };

  // URL 执行核心:source 显式传当次结果(避免 setUrlResult 后读到旧 state),auto=true 为自动跑。
  // 刀2·流2 路A(2026-06-16):profile 改用 mode "profile_with_video"(原 profile_basics)——抓基础资料 +
  // 入库 + 自动跑 PROFILE_REP_VIDEO_LIMIT 条代表视频 final_v1,dossier 才出真 LLM 账号分(原 profile_basics
  // 不分析视频→llm_v6_fit=None,只有空 dossier)。后端 _profile_should_enqueue_representative_videos 认
  // profile_with_video;TikTok 代表视频暂被后端 skip(resolver 未修)。video 仍走 video_deep。
  // V6 Fit 由 write_kol_profile_basics 白名单兜底不触碰 viltrox_fit_score。
  const runUrlExecute = async (
    source: VkpiKolUrlDeepCrawlResponse,
    opts: { auto?: boolean; localEvaluation?: boolean; requestEpoch?: SearchRequestEpoch } = {},
  ) => {
    const query = cleanText(source.url?.input || input);
    if (!apiToken || !query) return;
    const requestEpoch = opts.requestEpoch ?? beginSearchRequest();
    if (opts.requestEpoch == null) clearPickedIds();
    const sourceProfileFlow = asRecord(source.profile_flow);
    setState("executing");
    setError("");
    try {
      const isVideo = source.url_type === "video";
      const executeMode = isVideo ? "video_deep" : "profile_with_video";
      const sessionId = sessionIdFrom(source.search_session);
      const response = await deepCrawlKolUrl(apiToken, query, true, {
        maxPosts: typeof sourceProfileFlow.max_posts === "number" ? sourceProfileFlow.max_posts : 3,
        mode: executeMode,
        ...(isVideo ? {} : { representativeVideoLimit: PROFILE_REP_VIDEO_LIMIT }),
        deferToQueue: !isVideo,
        sessionId,
        createSession: !sessionId,
        source: opts.auto ? "smart_kol_input_auto" : "smart_kol_input",
        localEvaluation: opts.localEvaluation === true,
        timeoutMs: isVideo ? 300000 : 30000,
      });
      if (!isCurrentSearchRequest(requestEpoch)) return;
      setUrlResult(response);
      const nextSessionId = sessionIdFrom(response.search_session) || sessionId;
      if (nextSessionId) {
        setDisplayedSearchSessionId(nextSessionId);
        setPollingSearchSessionId(nextSessionId);
        setSessionPollNotice(response.url_type === "video" ? "视频分析状态同步中..." : "账号资料抓取状态同步中...");
      }
      setState("ready");
      void refreshHistory();
    } catch (err) {
      if (!isCurrentSearchRequest(requestEpoch)) return;
      setState("ready");
      setError(err instanceof Error ? err.message : "URL 执行失败");
    }
  };

  // 手动执行(视频区「只分析此视频」/「建档并分析」按钮 + profile 重试兜底):沿用受控 canExecute 门槛。
  const executeUrlAction = async () => {
    if (!urlResult || !urlCanExecute) return;
    await runUrlExecute(urlResult);
  };

  const executeLocalEvaluationAction = async () => {
    if (!urlResult || !urlCanExecute || !import.meta.env.DEV) return;
    await runUrlExecute(urlResult, { localEvaluation: true });
  };

  const queueTextAdvance = async (overrideQuery?: string, parentEpoch?: SearchRequestEpoch, reuseSessionId?: number) => {
    const query = cleanText(overrideQuery ?? input);
    if (!apiToken || !query || state === "executing") return;
    const requestFingerprint = smartKolSearchFingerprint({
      query,
      market: discoveryRegion,
      platforms: discoveryPlatforms,
      languages: contentLanguages,
      profileTypes: kolProfileTypes,
      excludeChinese,
      searchMode,
    });
    const requestEpoch = parentEpoch ?? beginSearchRequest();
    if (parentEpoch == null) {
      clearPickedIds();
      setRecallResult(null);
      setAdvanceResult(null);
      setActiveSearchSession(null);
      setDisplayedSearchSessionId(null);
      setPollingSearchSessionId(null);
    }
    setRecallFingerprint(requestFingerprint);
    setState("executing");
    setError("");
    try {
      // 库内推荐与全网候选分开控量。全网先放宽候选,后端再按相关性/触达/账号类型过滤,
      // 避免原始 25 个里补全后只剩 4 个可展示。地区由 discoveryRegion 控制。
      // 新合同把「筛选后 30 人」与底层 creator/reviewer 兼容配额分开；显式硬筛选不得为凑数放松。
      const apiFilters = toKolSearchApiFilters(searchFilters, discoveryPlatforms);
      const response = await smartKolSearchProfileAdvanceJob(apiToken, query, {
        candidateLimit: 500,
        limit: KOL_SEARCH_RESULT_LIMIT,
        resultLimit: KOL_SEARCH_RESULT_LIMIT,
        creatorQuota: searchPolicy.creatorQuota,
        reviewerQuota: searchPolicy.reviewerQuota,
        advanceLimit: KOL_SEARCH_RESULT_LIMIT,
        searchStrategy,
        filters: apiFilters,
        bucketPolicy: searchPolicy.bucketPolicy,
        maxPosts: 12,
        representativeVideoLimit: 1,
        includeNewDiscovery: true,
        newDiscoveryLimit: searchPolicy.newDiscoveryLimit,
        newDiscoveryPerPlatformLimit: searchPolicy.perPlatformLimit,
        newDiscoveryPlatforms: strictOnlineDiscoveryPlatforms(discoveryPlatforms),
        excludeChinese,
        market: discoveryRegion,
        languages: contentLanguages,
        profileTypes: kolProfileTypes,
        localQualificationSpec,
        onlineQualificationSpec: ONLINE_QUALIFICATION_SPEC,
        ...(reuseSessionId ? { sessionId: reuseSessionId } : {}),
        timeoutMs: 300000,
      });
      if (!isCurrentSearchRequest(requestEpoch)) return;
      setAdvanceResult(response);
      const queuedSession = response.search_session && typeof response.search_session === "object"
        ? response.search_session as VkpiKolSearchHistoryItem
        : null;
      const sessionId = sessionIdFrom(response.search_session) || sessionIdFrom(response.advance_job) || sessionIdFrom(queuedSession);
      if (queuedSession && sessionItems(queuedSession).length) applyPolledSession(queuedSession);
      if (sessionId) {
        setDisplayedSearchSessionId(sessionId);
        setPollingSearchSessionId(sessionId);
        setSessionPollNotice("后台查找中...");
      }
      setState("ready");
      void refreshHistory();
    } catch (err) {
      if (!isCurrentSearchRequest(requestEpoch)) return;
      setState("ready");
      setError(err instanceof Error ? err.message : "全网查找启动失败，请重试");
    }
  };

  // 失败/未完成会话重试:重新入队该搜索(用当前会话/输入的查询语再跑一遍全网查找管线)。
  // 走 queueTextAdvance → smartKolSearchProfileAdvanceJob 重新排队,并续接轮询回填 ①②③。
  const retrySearchSession = async () => {
    const query = cleanText(activeSearchSession?.query_text || input);
    if (!apiToken || !query || state === "executing") return;
    setSessionPollNotice("正在重新入队该搜索…");
    await queueTextAdvance(query);
  };

  // 搜索可能创建会话、抓取和分析任务，只允许显式点击按钮触发，避免输入时误按回车重复排队。
  const runCurrentInput = () => {
    if (isBusy || !apiToken || !cleanText(input)) return;
    const urls = extractUrls(input);
    if (urls.length >= 2) {
      void runUrlBatch(urls);
      return;
    }
    void run();
  };

  return (
    <section
      data-testid="smart-kol-input-panel"
      className="rounded-lg border border-white/[0.065] bg-black/[0.14] p-2.5"
    >
      <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-cyan-300/15 bg-cyan-400/[0.08] text-cyan-100">
            <Sparkles size={12} />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <h2 className="text-[12px] font-semibold text-white">找达人</h2>
            </div>
            <div className="mt-0.5 truncate text-[10px] text-slate-600">
              贴主页/视频链接看资料，或描述产品需求找人。
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5 text-[9px] text-slate-500">
          <span className="rounded border border-cyan-300/10 bg-cyan-400/[0.035] px-1.5 py-0.5 text-cyan-100">Video</span>
          <span className="rounded border border-violet-300/10 bg-violet-400/[0.035] px-1.5 py-0.5 text-violet-100">Profile</span>
          <span className="rounded border border-emerald-300/10 bg-emerald-400/[0.035] px-1.5 py-0.5 text-emerald-100">查找</span>
        </div>
      </div>

      <div
        className="mt-2 grid gap-2 lg:grid-cols-[minmax(0,1fr)_112px]"
      >
        <input
          data-testid="smart-kol-input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.preventDefault();
          }}
          placeholder="粘贴 KOL 主页 / 视频 URL，或输入产品需求，例如: 35mm 低光人像 YouTube 摄影师"
          className="min-h-[40px] rounded-md border border-white/[0.075] bg-black/30 px-3 py-2 text-[11.5px] text-slate-200 outline-none placeholder-slate-600 focus:border-cyan-300/45"
        />
        <button
          data-testid="smart-kol-run"
          type="button"
          onClick={runCurrentInput}
          disabled={isBusy || !apiToken || !cleanText(input)}
          className="inline-flex min-h-[40px] items-center justify-center gap-1.5 rounded-md border border-cyan-300/18 bg-cyan-500/[0.14] px-3 text-[11px] font-medium text-cyan-100 transition-colors hover:bg-cyan-500/[0.22] disabled:cursor-not-allowed disabled:opacity-55"
        >
          {isBusy ? <Loader2 size={13} className="animate-spin" /> : inferredMode === "url" ? <Link2 size={13} /> : <Search size={13} />}
          {inferredMode === "url" ? "查看" : "查找"}
        </button>
      </div>

      <KolSearchPolicyPanel
        open={searchFiltersOpen}
        onToggleOpen={() => setSearchFiltersOpen((open) => !open)}
        strategy={searchStrategy}
        onStrategyChange={setSearchStrategy}
        platforms={discoveryPlatforms}
        onPlatformsChange={setDiscoveryPlatforms}
        filters={searchFilters}
        onFiltersChange={setSearchFilters}
      />

      {batchNote ? (
        <div className="mt-1.5 flex items-center gap-1.5 rounded-md border border-cyan-300/20 bg-cyan-400/[0.06] px-2.5 py-1.5 text-[10px] text-cyan-100">
          {batchBusy ? <Loader2 size={11} className="animate-spin" /> : null}
          <span>{batchNote}</span>
          {!batchBusy ? (
            <button type="button" onClick={() => setBatchNote("")} className="ml-auto text-slate-500 hover:text-slate-300">收起</button>
          ) : null}
        </div>
      ) : null}

      {state === "idle" && !input ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[9.5px] text-slate-600">
          <span className="inline-flex items-center gap-1 text-cyan-100"><Video size={9} /> 视频 URL</span>
          <span className="text-slate-700">/</span>
          <span className="inline-flex items-center gap-1 text-violet-100"><BadgeCheck size={9} /> 账号 URL</span>
          <span className="text-slate-700">/</span>
          <span className="inline-flex items-center gap-1 text-emerald-100"><Search size={9} /> 产品需求</span>
        </div>
      ) : null}

      <HistoryStrip
        items={historyItems}
        archivedItems={archivedHistoryItems}
        loading={historyLoading}
        actionBusy={historyActionBusy}
        notice={historyNotice}
        onOpen={(session) => void openHistorySession(session)}
        onArchive={(session) => void archiveHistoryEntry(session)}
        onRestore={(session) => void restoreHistoryEntry(session)}
        onArchiveAll={() => void archiveCompletedHistory()}
      />

      {error ? (
        <div className="mt-3 rounded-lg border border-rose-300/20 bg-rose-500/[0.08] px-3 py-2 text-[11px] text-rose-200">{error}</div>
      ) : null}

      {mode === "url" && urlResult ? (
        <UrlSummary
          result={urlResult}
          apiToken={apiToken}
          canExecute={urlCanExecute}
          isExecuting={state === "executing"}
          onExecute={() => void executeUrlAction()}
          onLocalEvaluation={() => void executeLocalEvaluationAction()}
          onOpenProfile={onOpenProfile}
        />
      ) : null}

      {mode === "text" && recallResult ? (
        <TextResultSection
          recallResult={recallResult}
          searchSession={activeSearchSession}
          llmPlan={llmPlan}
          discoveryItems={discoveryItems}
          discoveryTotal={discoveryTotal}
          discoveryAutoEnrolled={discoveryAutoEnrolled} discoveryBrandExcluded={discoveryBrandExcludedFromSession(activeSearchSession)}
          reachFloorDisplay={reachFloorDisplay}
          input={input}
          apiToken={apiToken}
          isBusy={isBusy}
          state={state}
          plannerFellBack={plannerFellBack}
          personaEditing={personaEditing}
          personaDraft={personaDraft}
          setPersonaEditing={setPersonaEditing}
          setPersonaDraft={setPersonaDraft}
          setInput={setInput}
          run={run}
          discoveryPlatforms={discoveryPlatforms}
          setDiscoveryPlatforms={setDiscoveryPlatforms}
          discoveryRegion={discoveryRegion}
          setDiscoveryRegion={setDiscoveryRegion}
          contentLanguages={contentLanguages}
          setContentLanguages={setContentLanguages}
          kolProfileTypes={kolProfileTypes}
          setKolProfileTypes={setKolProfileTypes}
          excludeChinese={excludeChinese}
          setExcludeChinese={setExcludeChinese}
          queueTextAdvance={queueTextAdvance}
          pickedIds={pickedIds}
          setPickedIds={setPickedIds}
          favNote={favNote}
          favoriteIds={favoriteIds}
          favoriteBusyIds={favoriteBusyIds}
          favoriteResults={favoriteResults}
          favoriteErrors={favoriteErrors}
          favoritesSyncing={favoritesSyncing}
          favoritesLoadError={favoritesLoadError}
          draftNote={draftNote}
          outreachNote={outreachNote}
          outreachResult={outreachResult}
          addingFav={addingFav}
          draftBusy={draftBusy}
          outreachBusy={outreachBusy}
          displayedSearchSessionId={displayedSearchSessionId}
          isSessionPolling={Boolean(pollingSearchSessionId)}
          resultsStale={recallIsStale}
          approvalReady={approvalReady}
          favoriteOne={favoriteOne}
          addPickedToMyKol={addPickedToMyKol}
          approveAndCreateDraft={approveAndCreateDraft}
          generateOutreachForPicked={generateOutreachForPicked}
          discoveryKey={discoveryKey}
          onOpenRecallItem={onOpenRecallItem}
          sessionBanner={sessionBanner}
          sessionProgress={activeSessionProgress}
          activeSessionCounts={activeSessionCounts}
          sessionPollNotice={sessionPollNotice}
          retrySearchSession={retrySearchSession}
        />
      ) : null}

    </section>
  );
}
