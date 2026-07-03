import { useCallback, useEffect, useMemo, useState } from "react";
import { BadgeCheck, Link2, Loader2, Search, Sparkles, Video } from "lucide-react";

import {
  deepCrawlKolUrl,
  getKolSearchSession,
  listKolSearchHistory,
  smartKolSearchProfileAdvanceJob,
  smartKolSearch,
  type VkpiKolRecallItem,
  type VkpiKolRecallResponse,
  type VkpiKolSearchHistoryItem,
  type VkpiKolSmartSearchProfileAdvanceResponse,
  type VkpiKolUrlDeepCrawlResponse,
} from "../../../../domains/kol";
import { approveKolSearchSession, createProjectDraftFromSession, favoriteKolPool, generateKolSearchSessionOutreach, resolveKolPool } from "../../../../services/vkpi/kolPool-api";

// 纯函数工具已抽到 SmartKolInputPanel.helpers.ts(行为不变)。
import {
  asRecord,
  cleanText,
  detectMode,
  sessionIdFrom,
  type Mode,
  type Row,
} from "./SmartKolInputPanel.helpers";

// 展示型子组件 + 会话/召回派生器已抽到 SmartKolInputPanel.Sections.tsx(行为不变;容器本体留此文件)。
import {
  HistoryStrip,
  PENDING_SEARCH_SESSION_KEY,
  PROFILE_REP_VIDEO_LIMIT,
  UrlSummary,
  discoveryItemsFromSession,
  historySessionId,
  isSearchSessionTerminal,
  looksLikeRetailer,
  readPersistedSearchDisplay,
  recallResultFromSession,
  recallTopItems,
  sessionAdvanceCounts,
  sessionItems,
  sessionStatusBanner,
  urlResultFromSession,
  writePersistedSearchDisplay,
} from "./SmartKolInputPanel.Sections";

// 文字搜索结果区(框1/框2/框3)展示 JSX 已抽到 SmartKolInputPanel.TextResult.tsx(行为不变;容器透传 props)。
import { TextResultSection } from "./SmartKolInputPanel.TextResult";

type State = "idle" | "loading" | "ready" | "executing" | "error";

// 【K1 搜索模式映射表 2026-07-02】FilterBar 三档(平衡/精准/探索)→ 全网查找请求参数。
// 只改参数映射,不动召回算法;数字与 FilterBar 的 hint/title 一致:
//   balanced  平衡: 库内召回 创作者8+测评7 / 全网发现 30 / 每平台 12(=原默认值)
//   precision 精准: 库内召回 创作者10+测评5 / 全网发现 20 / 每平台 8(收窄)
//   discovery 探索: 库内召回 创作者5+测评5  / 全网发现 40 / 每平台 15(放宽)
const SEARCH_MODE_QUOTAS: Record<string, { creatorQuota: number; reviewerQuota: number; newDiscoveryLimit: number; perPlatformLimit: number }> = {
  balanced:  { creatorQuota: 8,  reviewerQuota: 7, newDiscoveryLimit: 30, perPlatformLimit: 12 },
  precision: { creatorQuota: 10, reviewerQuota: 5, newDiscoveryLimit: 20, perPlatformLimit: 8 },
  discovery: { creatorQuota: 5,  reviewerQuota: 5, newDiscoveryLimit: 40, perPlatformLimit: 15 },
};

// 【K7】URL 多行批量:一次输入里抠出全部 http(s) URL(空格/换行分隔);上限 10 条防误粘。
// 验收补强(2026-07-02):①去掉粘贴常见的尾缀标点(逗号/分号/引号/右括号),避免「url1, url2」把
// 逗号带进后端解析;②去重(同链接粘两遍只分析一次)。社媒链接不含括号,尾缀剥离安全。
const URL_BATCH_MAX = 10;
function extractUrls(raw: string): string[] {
  const matched = String(raw || "").match(/https?:\/\/[^\s]+/g) || [];
  const cleaned = matched.map((u) => u.replace(/[),;'"\]]+$/, "")).filter(Boolean);
  return Array.from(new Set(cleaned));
}

export function SmartKolInputPanel({
  apiToken = "",
  searchMode = "balanced",
  onRecallItems,
  onOpenRecallItem,
  onOpenProfile,
}: {
  apiToken?: string;
  searchMode?: string;
  onRecallItems?: (items: VkpiKolRecallItem[]) => void;
  onOpenRecallItem?: (item: VkpiKolRecallItem) => void;
  onOpenProfile?: (result: VkpiKolUrlDeepCrawlResponse) => void;
}) {
  // 挂载时回填上次激活搜索的展示态(sessionStorage),让 90s/10min 父刷新若偶发重挂本面板时
  // ①②③ 结果与轮询不凭空消失;无持久化则回到正常初始态。
  const persistedDisplay = useMemo(() => readPersistedSearchDisplay(), []);
  const [input, setInput] = useState(() => persistedDisplay?.input ?? "");
  const [state, setState] = useState<State>(() => (persistedDisplay?.recallResult || persistedDisplay?.urlResult ? "ready" : "idle"));
  const [mode, setMode] = useState<Mode>(() => persistedDisplay?.mode ?? "idle");
  const [urlResult, setUrlResult] = useState<VkpiKolUrlDeepCrawlResponse | null>(() => persistedDisplay?.urlResult ?? null);
  const [recallResult, setRecallResult] = useState<VkpiKolRecallResponse | null>(() => persistedDisplay?.recallResult ?? null);
  const [advanceResult, setAdvanceResult] = useState<VkpiKolSmartSearchProfileAdvanceResponse | null>(null);
  const [activeSearchSessionId, setActiveSearchSessionId] = useState<number | null>(() => persistedDisplay?.activeSearchSessionId ?? null);
  const [activeSearchSession, setActiveSearchSession] = useState<VkpiKolSearchHistoryItem | null>(() => persistedDisplay?.activeSearchSession ?? null);
  const [sessionPollNotice, setSessionPollNotice] = useState("");
  const [historyItems, setHistoryItems] = useState<VkpiKolSearchHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");
  // 问题2 平台选择器:默认全选已落地的 YT/IG/TikTok(FB 待 provider 落地,UI 置灰)。
  const [discoveryPlatforms, setDiscoveryPlatforms] = useState<string[]>(["youtube", "instagram", "tiktok"]);
  // 区域语言本地化:目标市场码(空=全球英文;JP/KR/DE/… 按该区语言搜平台捞本地达人)。
  const [discoveryRegion, setDiscoveryRegion] = useState<string>("");
  // 刀1·流3 恒开(2026-06-16):全网发现不再挂开关,任何文字搜索都自动触发(见 run() 的 queueTextAdvance)。
  // P0-6 地区口径:默认开,排除 {中国大陆 CN / 香港 HK / 台湾 TW} 三地区(按 country/market 地区判据,
  // 含 ISO 码与中文地名),其余所有国家放行(含海外中文博主)。后端参数名保留 exclude_chinese。
  const [excludeChinese, setExcludeChinese] = useState(true);
  // 手动收藏:搜到≠自动归我。从「全网新发现」勾选若干 → 一键加入我的 MY KOL(收藏),由你挑。
  const [pickedIds, setPickedIds] = useState<Set<number>>(() => new Set());
  const [addingFav, setAddingFav] = useState(false);
  const [favNote, setFavNote] = useState("");
  // 新发现已被后端 _auto_enroll_discoveries 入池,但会话项 kol_pool_id 保持 NULL(不变式:
  // 否则会话项交集会误杀这些真候选)。所以勾选时按 handle resolve 出真池 id 再收藏 —— 不回戳、无副作用。
  const [resolvedPids, setResolvedPids] = useState<Map<string, number>>(() => new Map());
  const [resolvingKeys, setResolvingKeys] = useState<Set<string>>(() => new Set());
  // 【K7】URL 多行批量:输入含 ≥2 个 http(s) URL 时逐条排队分析(串行,间隔 500ms,上限 10)。
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchNote, setBatchNote] = useState("");
  // R4 找人闭合:批准锁定 → 建项目草案(带预算/风险)→ 话术草案。仅草案,绝不外发/承诺价格。
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftNote, setDraftNote] = useState("");
  const [outreachBusy, setOutreachBusy] = useState(false);
  const [outreachNote, setOutreachNote] = useState("");
  const [outreachResult, setOutreachResult] = useState<Record<string, any> | null>(null);

  function togglePick(id: number) {
    setPickedIds((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function discoveryKey(item: any): string {
    return `${cleanText(item?.platform).toLowerCase()}:${cleanText(item?.handle).toLowerCase().replace(/^@/, "")}`;
  }
  // 新发现勾选:已带 pool id 直接 toggle;否则按 handle resolve 出真池 id 再 toggle(入池记录已存在,只读不写)。
  async function pickDiscovery(item: any) {
    const direct = Number(item?.kol_pool_id) || 0;
    if (direct > 0) { togglePick(direct); return; }
    const key = discoveryKey(item);
    const cached = resolvedPids.get(key);
    if (cached) { togglePick(cached); return; }
    if (resolvingKeys.has(key) || !apiToken) return;
    const handle = cleanText(item?.handle).replace(/^@/, "");
    if (!handle) { setFavNote("该新发现缺 handle,无法定位入库记录"); return; }
    setResolvingKeys((cur) => new Set(cur).add(key));
    try {
      const resp: any = await resolveKolPool(apiToken, handle, cleanText(item?.platform));
      const pid = Number(resp?.kol_pool_id || resp?.matched_kol_pool_id) || 0;
      if (pid > 0) {
        setResolvedPids((cur) => new Map(cur).set(key, pid));
        togglePick(pid);
      } else {
        setFavNote(`「${handle}」尚未入库,请稍后重试或刷新发现列表`);
      }
    } catch {
      setFavNote(`「${handle}」定位失败,请重试`);
    } finally {
      setResolvingKeys((cur) => { const next = new Set(cur); next.delete(key); return next; });
    }
  }
  async function addPickedToMyKol() {
    if (!apiToken || !pickedIds.size) return;
    setAddingFav(true);
    setFavNote("");
    const ids = [...pickedIds];
    const results = await Promise.allSettled(ids.map((id) => favoriteKolPool(apiToken, id)));
    const ok = results.filter((r) => r.status === "fulfilled").length;
    setFavNote(ok === ids.length ? `已加入我的 MY KOL · ${ok} 人` : `加入 ${ok}/${ids.length}(其余失败,可重试)`);
    setPickedIds(new Set());
    setAddingFav(false);
  }

  // R4:批准锁定选中候选 → 一键建项目草案(草案带成本估算 + 风险;占用冲突降级为提示)。
  async function approveAndCreateDraft() {
    if (!apiToken || !pickedIds.size || !activeSearchSessionId) return;
    setDraftBusy(true);
    setDraftNote("");
    const ids = [...pickedIds];
    try {
      await approveKolSearchSession(apiToken, activeSearchSessionId, ids);
      const draft: any = await createProjectDraftFromSession(apiToken, activeSearchSessionId, { kolPoolIds: ids });
      const ce = (draft && draft.cost_estimate) || {};
      const total = ce.total_cents || {};
      const lowUsd = Math.round((total.low || 0) / 100);
      const highUsd = Math.round((total.high || 0) / 100);
      const risk = (ce.risk && ce.risk.level) || "—";
      const budgetStr =
        total.low || total.high
          ? ` · 预算 ~$${lowUsd.toLocaleString()}–$${highUsd.toLocaleString()} · 风险 ${risk}`
          : "";
      const warn = draft && draft.kol_attach_warning ? ` · ⚠ ${String(draft.kol_attach_warning).slice(0, 60)}` : "";
      setDraftNote(`已建草案 ${draft?.project_uid || ""}(挂 ${draft?.attached_kol_count ?? 0}/${ids.length} 人)${budgetStr}${warn}`);
    } catch (err: any) {
      setDraftNote(`建草案失败 · ${err?.message || "请重试"}`);
    } finally {
      setDraftBusy(false);
    }
  }

  // R4:为选中候选生成合作话术 + SOW 草案(LLM,预算闸;仅草案,人审后手动外发)。
  async function generateOutreachForPicked() {
    if (!apiToken || !pickedIds.size || !activeSearchSessionId) return;
    setOutreachBusy(true);
    setOutreachNote("");
    setOutreachResult(null);
    const ids = [...pickedIds];
    try {
      const res: any = await generateKolSearchSessionOutreach(apiToken, activeSearchSessionId, { kolPoolIds: ids });
      setOutreachResult(res || null);
      const n = Array.isArray(res?.messages) ? res.messages.length : 0;
      const src = res?.llm_used ? "LLM" : "确定性模板(LLM 未启用/预算关)";
      setOutreachNote(`已生成 ${n} 封话术草案 · ${src}${res?.truncated ? " · 已截断至上限" : ""}`);
    } catch (err: any) {
      setOutreachNote(`生成话术失败 · ${err?.message || "请重试"}`);
    } finally {
      setOutreachBusy(false);
    }
  }

  const inferredMode = useMemo(() => detectMode(input), [input]);
  const isBusy = state === "loading" || state === "executing" || batchBusy;
  const profileFlow = asRecord(urlResult?.profile_flow);
  const videoFlow = asRecord(urlResult?.video_flow);
  const videoCreator = asRecord(urlResult?.creator_identity || videoFlow.creator_identity);
  const videoStatus = cleanText(profileFlow.status || videoFlow.status);
  const videoJobStatus = cleanText(videoFlow.job_status || videoStatus);
  const videoJobLastError = cleanText(videoFlow.job_last_error);
  const profileOperation = cleanText(profileFlow.operation);
  const rawVideoOperation = cleanText(videoFlow.operation);
  const videoOperation = ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(profileOperation)
    ? profileOperation
    : rawVideoOperation || profileOperation;
  const videoCreatorResolved = Boolean(
    cleanText(videoFlow.creator_resolution_status) === "resolved" ||
    cleanText(videoCreator.handle || videoCreator.channel_id || videoCreator.profile_url || urlResult?.handle || urlResult?.channel_id),
  );
  const urlCanExecute = Boolean(
    apiToken &&
    urlResult &&
    (!urlResult.execute || Boolean(videoJobLastError)) &&
    !isBusy &&
    (
      (urlResult.url_type === "profile" && cleanText(profileFlow.status) === "dry_run_ready") ||
      (urlResult.url_type === "video" && Boolean(videoJobLastError) && ["failed", "blocked"].includes(videoJobStatus)) ||
      (
        urlResult.url_type === "video" &&
        ["dry_run_ready", "ready_to_execute"].includes(videoStatus) &&
        videoCreatorResolved &&
        ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(videoOperation)
      )
    )
  );
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
  // 诚实会话横幅(排队/查找中/已完成/部分完成/未完成)——只读后端真有字段,见 sessionStatusBanner。
  // advanceResult?.status:queueTextAdvance 刚返回、尚未首拍轮询时的即时状态兜底(queued/...)。
  const sessionBanner = useMemo(
    () => sessionStatusBanner(activeSearchSession, activeSessionStatus || cleanText(advanceResult?.status), activeSessionCounts, Boolean(activeSearchSessionId)),
    [activeSearchSession, activeSessionStatus, advanceResult, activeSessionCounts, activeSearchSessionId],
  );

  useEffect(() => {
    if (recallItems.length) onRecallItems?.(recallItems);
  }, [recallItems, onRecallItems]);

  // 搜索展示态持久化:任一 ①②③ 相关态变化就写回 sessionStorage(兜底重挂恢复);
  // 全空(无召回/无 URL 结果/无激活会话)时清掉,避免回填到一个空壳搜索框。
  useEffect(() => {
    if (!recallResult && !urlResult && !activeSearchSession && !activeSearchSessionId) {
      writePersistedSearchDisplay(null);
      return;
    }
    writePersistedSearchDisplay({ input, mode, recallResult, urlResult, activeSearchSession, activeSearchSessionId });
  }, [input, mode, recallResult, urlResult, activeSearchSession, activeSearchSessionId]);

  const refreshHistory = useCallback(async () => {
    if (!apiToken) {
      setHistoryItems([]);
      return;
    }
    setHistoryLoading(true);
    try {
      const response = await listKolSearchHistory(apiToken, { limit: 20, itemLimit: 5 });
      setHistoryItems(Array.isArray(response.items) ? response.items : []);
    } catch {
      // History is a convenience surface; do not interrupt the main search flow.
    } finally {
      setHistoryLoading(false);
    }
  }, [apiToken]);

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
      setUrlResult(null);
    }
    // 重开的会话若仍未终态(running/排队),续接轮询让后到的发现/分析项继续回填 ①②③;
    // 已终态则不再起轮询(避免空转),展示态已由上面完整回填。
    const sessionId = historySessionId(session);
    if (sessionId && !isSearchSessionTerminal(session)) {
      setActiveSearchSessionId(sessionId);
      setSessionPollNotice("正在续接后台查找…");
    } else {
      setActiveSearchSessionId(null);
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
    // 框3(全网发现)非破坏式覆盖:discoveryItems 派生自 activeSearchSession 的 new_creator 项;
    // 轮询头几拍常拿到尚无发现项的会话快照(running/部分写入)。若无条件覆盖,会把已点亮的框3
    // 刷成空 → 看起来「全网发现整块消失」。故:已有发现项时,空发现的轮询快照不覆盖会话(保住框3);
    // 但仍合并后端最新 result_summary/status,让横幅与计数继续推进。
    setActiveSearchSession((prev) => {
      const prevDiscovery = discoveryItemsFromSession(prev).length;
      const nextDiscovery = discoveryItemsFromSession(session).length;
      // 保住已点亮的框3:新快照发现项更少时(轮询时序/异步落库导致),合并后端最新 status/summary
      // 但保留 prev 更全的 items,绝不让更稀的快照把已显示的发现项刷没。
      if (prevDiscovery > nextDiscovery) {
        return { ...prev, status: session.status, result_summary: session.result_summary };
      }
      return session;
    });
    // 框2(库内召回)非破坏式覆盖:run() 触发的全网发现会建一个 advance 会话并启动轮询,其 recall
    // items 由后台 worker 异步写入,轮询头几拍常拿到 running/空会话。若无条件覆盖,会把 run() 首屏已
    // 渲染的库内召回刷成空 → 看起来「用户列表整块消失」。故:空轮询不得覆盖已有的非空召回(保住首屏);
    // 但当前无召回(null/空)时仍允许用轮询结果点亮结果区,以便框3 全网发现能显示。
    // 框1(产品人群分析)保活:recallResultFromSession 不带 llm_query_plan(那只在实时 smartKolSearch
    // 响应里有),若直接替换会把已渲染的 ① PlanPills 刷没 → 单独把上次的 llm_query_plan 透传进来。
    const polledRecall = recallResultFromSession(session);
    const polledRecallCount =
      (polledRecall.buckets?.creator?.length || 0) + (polledRecall.buckets?.reviewer?.length || 0);
    setRecallResult((prev) => {
      const prevCount =
        (prev?.buckets?.creator?.length || 0) + (prev?.buckets?.reviewer?.length || 0);
      const prevPlan = (prev as Row | null)?.llm_query_plan;
      const merged = prevPlan && !(polledRecall as unknown as Row).llm_query_plan
        ? ({ ...polledRecall, llm_query_plan: prevPlan } as VkpiKolRecallResponse)
        : polledRecall;
      if (polledRecallCount === 0 && prevCount > 0) return prev;
      return merged;
    });
    setUrlResult(null);
  }, []);

  const openHistorySession = useCallback(async (sessionOrId: VkpiKolSearchHistoryItem | number | string) => {
    if (!apiToken) return;
    const knownSession = typeof sessionOrId === "object" ? sessionOrId : null;
    const sessionId = knownSession ? historySessionId(knownSession) : Number(sessionOrId);
    if (!sessionId) {
      if (knownSession) restoreSession(knownSession);
      return;
    }
    setHistoryLoading(true);
    try {
      const session = await getKolSearchSession(apiToken, sessionId);
      restoreSession(session);
      void refreshHistory();
    } catch (err) {
      if (knownSession) {
        restoreSession(knownSession);
      } else {
        setError(err instanceof Error ? err.message : "历史记录读取失败");
      }
    } finally {
      setHistoryLoading(false);
    }
  }, [apiToken, refreshHistory, restoreSession]);

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
    if (!apiToken || !activeSearchSessionId || typeof window === "undefined") return undefined;
    let cancelled = false;
    let terminalSince: number | null = null;  // 本会话内「首次见终态」时间(闭包,随会话重置)
    const startedAt = Date.now();
    const maxPollMs = 12 * 60 * 1000;
    const poll = async () => {
      try {
        const session = await getKolSearchSession(apiToken, activeSearchSessionId);
        if (cancelled) return;
        applyPolledSession(session);
        // 发现项常在 session 状态置「终态」之后才异步落库 → 不能一见终态就停(否则框3 卡 0,正是你看到的)。
        // 改:终态后宽限继续轮询,直到发现项真的到 / 宽限 30s 用尽 / 总超时,才真正宣告已找完。
        const haveDiscovery = discoveryItemsFromSession(session).length > 0;
        const timedOut = Date.now() - startedAt > maxPollMs;
        if (isSearchSessionTerminal(session)) {
          if (terminalSince == null) terminalSince = Date.now();
          const graceUsedUp = Date.now() - terminalSince >= 30000;
          if (haveDiscovery || graceUsedUp || timedOut) {
            setActiveSearchSessionId(null);
            setSessionPollNotice("已找完，结果已更新");
            void refreshHistory();
            return;
          }
          // 终态但发现项未到 + 宽限期内 → 继续轮询
        } else if (timedOut) {
          setActiveSearchSessionId(null);
          setSessionPollNotice("仍在后台查找，可从最近历史或任务里继续查看");
          void refreshHistory();
        }
      } catch (err) {
        if (cancelled) return;
        setSessionPollNotice(err instanceof Error ? err.message : "同步失败，稍后会自动重试");
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
  }, [activeSearchSessionId, apiToken, applyPolledSession, refreshHistory]);

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
    const nextMode = detectMode(query);
    setMode(nextMode);
    setState("loading");
    setError("");
    setUrlResult(null);
    setRecallResult(null);
    setAdvanceResult(null);
    setActiveSearchSessionId(null);
    setActiveSearchSession(null);
    setSessionPollNotice("");
    try {
      const response = await smartKolSearch(apiToken, query, {
        mode: "auto",
        maxPosts: 3,
        candidateLimit: 50,
        limit: 10,
        creatorQuota: 7,
        reviewerQuota: 3,
        // createSession:true 回滚——false 会让前端 activeSearchSession 拿不到 advance 会话的全网发现项,
        // 整组「全网新发现」消失(550pro2 监视器搜出 15 个却 0 显示的真因)。宁可历史多一条空会话,也要保显示。
        createSession: true,
        excludeChinese,
        timeoutMs: 60000,
      });
      const responseMode = cleanText(response.mode);
      const isText = !(responseMode === "url" || cleanText(response.query_type).startsWith("url_"));
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
        // 刀1·流1(2026-06-16):video URL 也自动 execute,贴视频链接不必再点「只分析此视频/建档并分析」。
        // 门槛与 urlCanExecute 的 video 正常分支一致:创作者已解析 + 后端就绪 + 合法操作。runUrlExecute 对
        // video 走 video_deep,入 evidence + 排 final_v1(幂等 already_analyzed/already_queued);视频/分镜随
        // worker 完成经会话轮询自动内联回填。失败时 videoJobLastError 置位,手动重试按钮按 urlCanExecute 自然复现。
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
          if (
            ["dry_run_ready", "ready_to_execute"].includes(vStatus) &&
            vCreatorResolved &&
            ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(vOp)
          ) {
            autoVideo = urlPayload;
          }
        }
      } else {
        setMode("text");
        setRecallResult(response.result as VkpiKolRecallResponse);
      }
      setState("ready");
      void refreshHistory();
      // 刀1·流3(2026-06-16)恒开:任何文字搜索都自动触发全网发现(advance-job 全量,含所选平台),
      // 不再挂在「深度查找」开关上 →「先库内召回 → 再全网发现」一步到位,本地+线上首屏同呈。
      // 预算护栏 enforce 兜底超支(已确认放行)。
      if (isText) void queueTextAdvance(overrideQuery);
      // 账号 URL 自动抓资料 + 入库(不再弹「抓基础资料」二次确认)。
      if (autoProfile) void runUrlExecute(autoProfile, { auto: true });
      // 刀1·流1:video URL 自动入 evidence + 排 final_v1(不再弹「只分析此视频」二次确认)。
      if (autoVideo) void runUrlExecute(autoVideo, { auto: true });
    } catch (err) {
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
  const runUrlExecute = async (source: VkpiKolUrlDeepCrawlResponse, opts: { auto?: boolean } = {}) => {
    const query = cleanText(source.url?.input || input);
    if (!apiToken || !query) return;
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
        sessionId,
        createSession: !sessionId,
        source: opts.auto ? "smart_kol_input_auto" : "smart_kol_input",
        timeoutMs: 300000,
      });
      setUrlResult(response);
      const nextSessionId = sessionIdFrom(response.search_session) || sessionId;
      if (nextSessionId) {
        setActiveSearchSessionId(nextSessionId);
        setSessionPollNotice(response.url_type === "video" ? "视频分析状态同步中..." : "账号资料抓取状态同步中...");
      }
      setState("ready");
      void refreshHistory();
    } catch (err) {
      setState("ready");
      setError(err instanceof Error ? err.message : "URL 执行失败");
    }
  };

  // 手动执行(视频区「只分析此视频」/「建档并分析」按钮 + profile 重试兜底):沿用受控 canExecute 门槛。
  const executeUrlAction = async () => {
    if (!urlResult || !urlCanExecute) return;
    await runUrlExecute(urlResult);
  };

  const queueTextAdvance = async (overrideQuery?: string) => {
    const query = cleanText(overrideQuery ?? input);
    if (!apiToken || !query || state === "executing") return;
    setState("executing");
    setError("");
    try {
      // 2026-07-02 用户令:库内推荐 15(创作者8+测评7)/ 云端新发现 30(三平台轮转均匀,
      // 每平台候选上限 12 留补位余量)。地区由 discoveryRegion 控制(非英语区按当地语言搜)。
      // 【K1 接真】上述数字即「平衡」档;精准/探索按 SEARCH_MODE_QUOTAS 映射表收窄/放宽,
      // 只改参数不改算法。limit/advanceLimit = 创作者+测评之和,保持召回总量与配额一致。
      const quotas = SEARCH_MODE_QUOTAS[searchMode] || SEARCH_MODE_QUOTAS.balanced;
      const recallLimit = quotas.creatorQuota + quotas.reviewerQuota;
      const response = await smartKolSearchProfileAdvanceJob(apiToken, query, {
        candidateLimit: 100,
        limit: recallLimit,
        creatorQuota: quotas.creatorQuota,
        reviewerQuota: quotas.reviewerQuota,
        advanceLimit: recallLimit,
        maxPosts: 12,
        representativeVideoLimit: 1,
        includeNewDiscovery: true,
        newDiscoveryLimit: quotas.newDiscoveryLimit,
        newDiscoveryPerPlatformLimit: quotas.perPlatformLimit,
        newDiscoveryPlatforms: discoveryPlatforms,
        excludeChinese,
        market: discoveryRegion,
        timeoutMs: 300000,
      });
      setAdvanceResult(response);
      const queuedSession = response.search_session && typeof response.search_session === "object"
        ? response.search_session as VkpiKolSearchHistoryItem
        : null;
      const sessionId = sessionIdFrom(response.search_session) || sessionIdFrom(response.advance_job) || sessionIdFrom(queuedSession);
      if (queuedSession && sessionItems(queuedSession).length) applyPolledSession(queuedSession);
      if (sessionId) {
        setActiveSearchSessionId(sessionId);
        setSessionPollNotice("后台查找中...");
      }
      setState("ready");
      void refreshHistory();
    } catch (err) {
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

      <form
        className="mt-2 grid gap-2 lg:grid-cols-[minmax(0,1fr)_112px]"
        onSubmit={(event) => {
          event.preventDefault();
          if (isBusy || !apiToken || !cleanText(input)) return;
          // 【K7】多行/多 URL(≥2 个 http)→ 逐条排队分析;单条照旧走 run()。
          const urls = extractUrls(input);
          if (urls.length >= 2) { void runUrlBatch(urls); return; }
          void run();
        }}
      >
        <input
          data-testid="smart-kol-input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !isBusy) {
              const urls = extractUrls(input);
              if (urls.length >= 2) { void runUrlBatch(urls); return; }
              void run();
            }
          }}
          placeholder="粘贴 KOL 主页 / 视频 URL，或输入产品需求，例如: 35mm 低光人像 YouTube 摄影师"
          className="min-h-[40px] rounded-md border border-white/[0.075] bg-black/30 px-3 py-2 text-[11.5px] text-slate-200 outline-none placeholder-slate-600 focus:border-cyan-300/45"
        />
        <button
          data-testid="smart-kol-run"
          type="submit"
          disabled={isBusy || !apiToken || !cleanText(input)}
          className="inline-flex min-h-[40px] items-center justify-center gap-1.5 rounded-md border border-cyan-300/18 bg-cyan-500/[0.14] px-3 text-[11px] font-medium text-cyan-100 transition-colors hover:bg-cyan-500/[0.22] disabled:cursor-not-allowed disabled:opacity-55"
        >
          {isBusy ? <Loader2 size={13} className="animate-spin" /> : inferredMode === "url" ? <Link2 size={13} /> : <Search size={13} />}
          {inferredMode === "url" ? "查看" : "查找"}
        </button>
      </form>

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
        loading={historyLoading}
        onOpen={(session) => void openHistorySession(session)}
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
          onOpenProfile={onOpenProfile}
        />
      ) : null}

      {mode === "text" && recallResult ? (
        <TextResultSection
          recallResult={recallResult}
          llmPlan={llmPlan}
          recallItems={recallItems}
          discoveryItems={discoveryItems}
          discoveryTotal={discoveryTotal}
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
          excludeChinese={excludeChinese}
          setExcludeChinese={setExcludeChinese}
          queueTextAdvance={queueTextAdvance}
          pickedIds={pickedIds}
          setPickedIds={setPickedIds}
          favNote={favNote}
          draftNote={draftNote}
          outreachNote={outreachNote}
          outreachResult={outreachResult}
          addingFav={addingFav}
          draftBusy={draftBusy}
          outreachBusy={outreachBusy}
          activeSearchSessionId={activeSearchSessionId}
          addPickedToMyKol={addPickedToMyKol}
          approveAndCreateDraft={approveAndCreateDraft}
          generateOutreachForPicked={generateOutreachForPicked}
          resolvedPids={resolvedPids}
          resolvingKeys={resolvingKeys}
          discoveryKey={discoveryKey}
          pickDiscovery={pickDiscovery}
          onOpenRecallItem={onOpenRecallItem}
          sessionBanner={sessionBanner}
          activeSessionCounts={activeSessionCounts}
          sessionPollNotice={sessionPollNotice}
          retrySearchSession={retrySearchSession}
        />
      ) : null}

    </section>
  );
}
