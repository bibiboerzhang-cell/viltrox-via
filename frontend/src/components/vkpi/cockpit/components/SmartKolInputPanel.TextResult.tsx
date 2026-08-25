// SmartKolInputPanel 文字搜索结果区(框1 产品人群分析 / 框2 库内召回 / 框3 全网发现)展示型子组件。
// 从 SmartKolInputPanel.tsx 抽出,行为不变:JSX 逐字保留,容器本体 + 全部 hooks 仍留 SmartKolInputPanel.tsx,
// 这里只是「仅吃 props」的展示组件(无自身 hooks),容器把 state/派生值/回调透传进来,调用点不变。
// 红线:纯展示,绝不写任何 viltrox_fit_score。
import { FolderPlus, Info, Loader2, MessageSquare, RefreshCw, Sparkles, UserPlus } from "lucide-react";

import type { VkpiKolRecallItem, VkpiKolRecallResponse, VkpiKolSearchHistoryItem } from "../../../../domains/kol";

import { asRecord, cleanText, display, type Row } from "./SmartKolInputPanel.helpers";
import { recallDistributionView } from "./SmartKolInputPanel.evidence";
import { localQualifiedSummary } from "./SmartKolInputPanel.LocalQualified";
import { LocalQualifiedList, StrictQualifiedList } from "./SmartKolInputPanel.LocalQualifiedList";
import { onlineQualifiedSummaryFromSession } from "./SmartKolInputPanel.OnlineQualified";
import {
  resultOriginBadgeOfKind,
  resultOriginCounts,
  summaryResultOriginCounts,
  withLocalRecallOrigin,
  type ResultOriginCounts,
} from "./SmartKolInputPanel.sessionProjection";
import { SmartKolQualityFilters } from "./SmartKolInputPanel.QualityFilters";
import { PlanPills, RecallMiniItem } from "./SmartKolInputPanel.Sections";
import { recallTopItems, type SearchSessionProgress } from "./SmartKolInputPanel.derivers";
import { ProgressiveSearchStageCard } from "./SmartKolInputPanel.Progress";
import { kolHumanDisplayName } from "../lib/kolIdentity";
import { useSearchFeedbackLabeledCount } from "../../../../services/vkpi/searchFeedback-api";

type SessionBanner = {
  tone: string;
  label: string;
  note: string;
} | null;

export type CandidateBusinessLane = "core" | "expansion" | "exploration";

export function candidateBusinessLane(item: VkpiKolRecallItem): CandidateBusinessLane {
  if (cleanText(item.match_tier) === "backfill") return "exploration";
  const explicit = cleanText(item.candidate_bucket ?? item.business_lane ?? item.candidate_lane);
  if (explicit === "core_vertical") return "core";
  if (explicit === "expansion") return "expansion";
  // 滚动升级兼容：旧服务只有 reviewer/creator。这个映射只负责摆放，不声称已执行新业务算法。
  return cleanText(item.bucket) === "reviewer" ? "core" : "expansion";
}

export function hasExplicitBusinessLanes(items: VkpiKolRecallItem[]): boolean {
  return items.some((item) => Boolean(cleanText(item.candidate_bucket ?? item.business_lane ?? item.candidate_lane ?? item.match_tier)));
}

function CandidateLaneGroups({
  items,
  renderItem,
}: {
  items: VkpiKolRecallItem[];
  renderItem: (item: VkpiKolRecallItem, sourceIndex: number) => JSX.Element;
}) {
  const groups: Array<{
    key: CandidateBusinessLane;
    title: string;
    note: string;
    cls: string;
  }> = [
    { key: "core", title: "核心垂直", note: "产品和垂类证据优先", cls: "border-violet-300/18 bg-violet-400/[0.025] text-violet-100" },
    { key: "expansion", title: "拓展型", note: "相邻内容与跨圈层机会", cls: "border-cyan-300/18 bg-cyan-400/[0.025] text-cyan-100" },
    { key: "exploration", title: "探索 / 补位", note: "严格相关候选不足时单独列出", cls: "border-amber-300/18 bg-amber-400/[0.025] text-amber-100" },
  ];
  const explicit = hasExplicitBusinessLanes(items);
  return (
    <div className="space-y-2">
      {!explicit && items.length ? (
        <div className="rounded-md border border-amber-300/18 bg-amber-400/[0.055] px-2.5 py-1.5 text-[9.5px] leading-relaxed text-amber-100/85">
          旧服务兼容：本批尚未返回业务分桶，暂按原有“测评号 / 创作者”映射摆放；不代表新筛选和30人配额已由服务端验证。
        </div>
      ) : null}
      {groups.map((group) => {
        const groupItems = items.filter((item) => candidateBusinessLane(item) === group.key);
        if (!groupItems.length) return null;
        return (
          <div key={group.key} className={`rounded-lg border p-2 ${group.cls}`} data-candidate-lane={group.key}>
            <div className="mb-1.5 flex flex-wrap items-center justify-between gap-1.5">
              <span className="text-[10.5px] font-semibold">{group.title} · {groupItems.length}</span>
              <span className="text-[8.5px] text-slate-500">{group.note}</span>
            </div>
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {groupItems.map((item) => renderItem(item, items.indexOf(item)))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const SEARCH_FILTER_LABELS: Record<string, string> = {
  platform: "主要平台",
  platforms: "主要平台",
  country: "国家/地区",
  countries: "国家/地区",
  region: "国家/地区",
  regions: "国家/地区",
  language: "内容语言",
  languages: "内容语言",
  followers: "粉丝数",
  followers_min: "最低粉丝数",
  followers_max: "最高粉丝数",
  creator_type: "创作者类型",
  creator_types: "创作者类型",
  vertical: "垂直标签",
  verticals: "垂直标签",
  gear_content: "摄影器材内容",
  lens_content: "镜头内容",
};

function optionalCount(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function searchFilterLabel(value: unknown): string {
  const key = cleanText(value);
  return SEARCH_FILTER_LABELS[key.toLowerCase()] || key;
}

export function SearchFilterDiagnostics({ diagnostics }: { diagnostics: Row }) {
  const requested = optionalCount(diagnostics.requested_count);
  const strict = optionalCount(diagnostics.strict_count);
  const backfill = optionalCount(diagnostics.backfill_count);
  const strictAvailable = optionalCount(diagnostics.strict_available_count);
  const backfillAvailable = optionalCount(diagnostics.backfill_available_count);
  const finalCount = optionalCount(diagnostics.final_count);
  const shortfall = optionalCount(diagnostics.shortfall);
  const hardRejected = optionalCount(diagnostics.hard_filter_rejected_count);
  const contractSatisfied = typeof diagnostics.result_contract_satisfied === "boolean"
    ? diagnostics.result_contract_satisfied
    : null;
  const backfillPolicy = cleanText(diagnostics.backfill_policy);
  const hardFiltersNotRelaxed = diagnostics.hard_filters_relaxed === false
    || backfillPolicy === "query_relevance_only_hard_filters_never_relaxed";
  const unsupported = (Array.isArray(diagnostics.unsupported_filters) ? diagnostics.unsupported_filters : [])
    .map(cleanText)
    .filter(Boolean);
  const bucketCounts = asRecord(diagnostics.business_bucket_counts);
  const businessBuckets = [
    ["核心垂直", optionalCount(bucketCounts.core_vertical)],
    ["拓展", optionalCount(bucketCounts.expansion)],
    ["探索/补位", optionalCount(bucketCounts.exploration)],
  ] as const;
  const visibleBusinessBuckets = businessBuckets.flatMap(([label, value]) => value == null ? [] : [`${label} ${value}`]);
  const rejectedBy = Object.entries(asRecord(diagnostics.hard_filter_rejected_by))
    .map(([key, value]) => [searchFilterLabel(key), optionalCount(value)] as const)
    .filter((entry): entry is readonly [string, number] => entry[1] != null && entry[1] > 0);
  const hasShortfall = shortfall != null && shortfall > 0;
  const isWarning = contractSatisfied === false || hasShortfall || unsupported.length > 0;
  const hasDiagnostics = [requested, strict, backfill, strictAvailable, backfillAvailable, finalCount, shortfall, hardRejected]
    .some((value) => value != null)
    || contractSatisfied != null
    || rejectedBy.length > 0
    || unsupported.length > 0
    || visibleBusinessBuckets.length > 0;
  if (!hasDiagnostics) return null;
  return (
    <div
      data-testid="search-filter-diagnostics"
      className={`mb-2 rounded-md border px-2.5 py-1.5 text-[9.5px] leading-relaxed ${isWarning
      ? "border-amber-300/20 bg-amber-400/[0.065] text-amber-100"
      : "border-emerald-300/20 bg-emerald-400/[0.055] text-emerald-100"}`}
    >
      {hasShortfall || contractSatisfied === false ? (
        <div data-testid="search-hard-filter-shortfall" className="font-medium">
          {finalCount != null && requested != null
            ? `硬筛选后仅有 ${finalCount}/${requested}`
            : requested != null
              ? `硬筛选后的返回量未完整确认，目标 ${requested}`
              : "筛选结果合同未满足"}
          {hasShortfall ? `；短缺 ${shortfall}` : ""}
          {hardFiltersNotRelaxed ? "；显式硬筛选未放宽" : "；硬筛选是否放宽待服务端确认"}
        </div>
      ) : contractSatisfied === true && requested != null ? (
        <div data-testid="search-result-contract-satisfied" className="font-medium">已满足筛选后 {requested} 人结果合同</div>
      ) : null}
      <div className="flex flex-wrap items-center gap-1.5">
        {requested != null ? <span>筛选后目标 {requested}</span> : null}
        {strict != null || backfill != null ? (
          <span>· {strict != null ? `严格 ${strict}` : "严格数量待返回"} + {backfill != null ? `补位 ${backfill}` : "补位数量待返回"}</span>
        ) : null}
        {finalCount != null ? <span>· 最终 {finalCount}</span> : null}
        {hasShortfall ? <span>· 不以不满足硬筛选的账号凑数</span> : null}
      </div>
      {strictAvailable != null || backfillAvailable != null ? (
        <div className="mt-0.5">
          可用候选：{strictAvailable != null ? `严格 ${strictAvailable}` : "严格数量待返回"}
          {backfillAvailable != null ? ` · 相关性补位 ${backfillAvailable}` : ""}
        </div>
      ) : null}
      {hardRejected != null && hardRejected > 0 ? (
        <div className="mt-0.5">
          硬筛选排除 {hardRejected} 人{rejectedBy.length ? `：${rejectedBy.map(([label, count]) => `${label} ${count}`).join(" · ")}` : "；分项原因待返回"}
        </div>
      ) : rejectedBy.length ? (
        <div className="mt-0.5">硬筛选排除明细：{rejectedBy.map(([label, count]) => `${label} ${count}`).join(" · ")}</div>
      ) : null}
      {visibleBusinessBuckets.length ? (
        <div className="mt-0.5">业务分层：{visibleBusinessBuckets.join(" · ")}</div>
      ) : null}
      {unsupported.length ? (
        <div className="mt-0.5">未应用筛选：{unsupported.map(searchFilterLabel).join("、")} · 对应数据或服务能力待补全</div>
      ) : null}
    </div>
  );
}

type SearchEvaluationState = "not_evaluated" | "labeling" | "shareable" | "stale";

function evaluationState(value: unknown): SearchEvaluationState {
  const state = cleanText(value);
  return state === "labeling" || state === "shareable" || state === "stale"
    ? state
    : "not_evaluated";
}

function evaluationPercent(value: unknown): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "待返回";
  const percent = Math.abs(parsed) <= 1 ? parsed * 100 : parsed;
  return `${percent.toFixed(1).replace(/\.0$/, "")}%`;
}

export function SearchEvaluationStatus({ evaluation }: { evaluation: Row }) {
  const state = evaluationState(evaluation.state);
  const target = optionalCount(evaluation.target_count) ?? 180;
  // F4:本会话 👍/👎 最小标注计数(服务端回 labeled_count 优先,否则本地已保存条数)。
  const quickLabeled = useSearchFeedbackLabeledCount();
  const labeled = optionalCount(evaluation.labeled_count) ?? 0;
  const dualTarget = optionalCount(evaluation.dual_review_target);
  const dualReviewed = optionalCount(evaluation.dual_reviewed_count);
  const metrics = state === "shareable" ? asRecord(evaluation.metrics) : {};
  const version = cleanText(evaluation.gold_set_id ?? evaluation.dataset_version);
  const copy = state === "labeling"
    ? `人工标注 ${labeled}/${target}${dualTarget != null && dualReviewed != null ? ` · 双人复核 ${dualReviewed}/${dualTarget}` : ""}；完成前不发布准确率`
    : state === "shareable"
      ? `${version ? `${version} · ` : ""}真人 Gold Set 已冻结，评测结果可分享`
      : state === "stale"
        ? "算法或数据版本已变化；历史评测需重跑，当前不发布准确率"
        : quickLabeled > 0
          ? `已标注 ${quickLabeled} 条（卡片 👍/👎）；未达 Gold Set 门槛前只显示检索相关度，不发布准确率`
          : "尚无真人标注；可在结果卡上用 👍/👎 标注，当前只显示检索相关度，不发布准确率";
  const tone = state === "shareable"
    ? "border-emerald-300/20 bg-emerald-400/[0.055] text-emerald-100"
    : state === "stale"
      ? "border-rose-300/20 bg-rose-400/[0.055] text-rose-100"
      : "border-sky-300/18 bg-sky-400/[0.045] text-sky-100";
  return (
    <div data-testid="search-evaluation-status" data-evaluation-state={state} data-quick-labeled={quickLabeled} className={`mb-2 rounded-md border px-2.5 py-1.5 text-[10px] leading-relaxed ${tone}`}>
      <div className="font-medium">搜索质量：{state === "shareable" ? "可分享" : state === "labeling" ? "标注中" : state === "stale" ? "需重评" : quickLabeled > 0 ? `已标注 ${quickLabeled}` : "未评测"}</div>
      <div className="opacity-80">{copy}</div>
      {state === "shareable" && Object.keys(metrics).length ? (
        <div data-testid="search-evaluation-metrics" className="mt-0.5 flex flex-wrap gap-x-2 text-[9.5px]">
          {metrics.precision_at_30 != null ? <span>Precision@30 {evaluationPercent(metrics.precision_at_30)}</span> : null}
          {metrics.hard_filter_violation_rate != null ? <span>硬筛违规 {evaluationPercent(metrics.hard_filter_violation_rate)}</span> : null}
          {metrics.evidence_support_rate != null ? <span>证据支持 {evaluationPercent(metrics.evidence_support_rate)}</span> : null}
          {metrics.cohen_kappa != null ? <span>双审一致性 κ {Number(metrics.cohen_kappa).toFixed(2)}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

export function recallDisplayCounts(items: VkpiKolRecallItem[], diagnostics: Row) {
  const creatorVisible = items.filter((item) => cleanText(item.bucket) === "creator").length;
  const reviewerVisible = items.length - creatorVisible;
  const contractAware = ["requested_count", "final_count", "returned_count"]
    .some((key) => Number(diagnostics[key]) > 0);
  return {
    total: Math.max(
      items.length,
      Number(diagnostics.final_count) || 0,
      Number(diagnostics.returned_count) || 0,
      contractAware ? 0 : Number(diagnostics.candidate_count) || 0,
    ),
    creator: Math.max(creatorVisible, Number(diagnostics.creator_returned) || 0),
    reviewer: Math.max(reviewerVisible, Number(diagnostics.reviewer_returned) || 0),
  };
}

export function resolvedProductSkuFromPlan(plan: Row): string {
  const resolved = plan?.resolved_product && typeof plan.resolved_product === "object"
    ? plan.resolved_product as Row
    : {};
  return cleanText(resolved.sku ?? plan?.product_sku ?? plan?.productSku);
}

export function withResolvedProductSku<T extends object>(item: T, productSku: string): T & { product_sku?: string } {
  const normalized = cleanText(productSku);
  return normalized ? { ...item, product_sku: normalized } : item;
}

export function recallReturnedCount(result: VkpiKolRecallResponse, items: VkpiKolRecallItem[]): number {
  const returned = Number(result.diagnostics?.returned_count);
  return Number.isInteger(returned) && returned >= 0 ? returned : items.length;
}

/* ============ 结果来源分布:本次 N 人:库内 X · 新发现 Y ============
   口径全部来自 sessionProjection 的纯函数;这里只负责摆。
   数字优先用服务端已经算好的分布,没有就按本页已显示的结果现数,并把用的是哪一种口径写在脸上。 */
const ORIGIN_BASIS_NOTE: Readonly<Record<ResultOriginCounts["basis"], string>> = {
  summary: "本次搜索全部结果",
  session: "本次搜索全部结果",
  displayed: "本页已显示的结果",
};

export function ResultOriginSummaryBar({ counts }: { counts: ResultOriginCounts }) {
  if (counts.total <= 0) return null;
  const chips = (["local", "online", "provided"] as const)
    .map((kind) => ({ kind, badge: resultOriginBadgeOfKind(kind), value: counts[kind] }))
    // 库内 / 新发现 是用户点名要看的两个数,即使是 0 也照实摆;「你提供的」没有就不占位。
    .filter((chip) => chip.badge != null && (chip.kind !== "provided" || chip.value > 0));
  return (
    <div
      data-testid="result-origin-summary"
      data-origin-basis={counts.basis}
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-white/[0.08] bg-black/20 px-2.5 py-1.5 text-[10px] text-slate-400"
    >
      <span data-testid="result-origin-total" className="font-medium text-slate-200">本次 {counts.total} 人</span>
      {chips.map((chip) => (
        <span
          key={chip.kind}
          data-testid={`result-origin-count-${chip.kind}`}
          title={chip.badge?.title}
          className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9.5px] font-semibold ${chip.badge?.toneClassName ?? ""}`}
        >{chip.badge?.label} {chip.value}</span>
      ))}
      {counts.unknown > 0 ? (
        <span
          data-testid="result-origin-count-unknown"
          title="这些结果的来源后端还没给出判据,宁可不标也不猜"
          className="inline-flex items-center gap-1 rounded-full border border-amber-300/25 bg-amber-400/[0.08] px-1.5 py-0.5 text-[9.5px] text-amber-100/85"
        >来源待标注 {counts.unknown}</span>
      ) : null}
      <span className="ml-auto text-[9px] text-slate-600">{ORIGIN_BASIS_NOTE[counts.basis]} · 同一人只计一次</span>
    </div>
  );
}

export function nextRequiredPlatformSelection(current: readonly string[], platform: string): string[] {
  if (current.includes(platform)) return current.length > 1 ? current.filter((value) => value !== platform) : [...current];
  return [...current, platform];
}

export function TextResultSection({
  recallResult,
  searchSession,
  llmPlan,
  discoveryItems,
  discoveryTotal = 0,
  discoveryAutoEnrolled = null,
  discoveryBrandExcluded = 0,
  reachFloorDisplay = null,
  input,
  apiToken,
  isBusy,
  state,
  plannerFellBack,
  personaEditing,
  personaDraft,
  setPersonaEditing,
  setPersonaDraft,
  setInput,
  run,
  discoveryPlatforms,
  setDiscoveryPlatforms,
  discoveryRegion,
  setDiscoveryRegion,
  contentLanguages,
  setContentLanguages,
  kolProfileTypes,
  setKolProfileTypes,
  excludeChinese,
  setExcludeChinese,
  queueTextAdvance,
  pickedIds,
  setPickedIds,
  favNote,
  favoriteIds,
  favoriteBusyIds,
  favoriteResults,
  favoriteErrors,
  favoritesSyncing,
  favoritesLoadError,
  draftNote,
  outreachNote,
  outreachResult,
  addingFav,
  draftBusy,
  outreachBusy,
  displayedSearchSessionId,
  isSessionPolling,
  isSessionPollPaused,
  resultsStale,
  approvalReady,
  favoriteOne,
  addPickedToMyKol,
  approveAndCreateDraft,
  generateOutreachForPicked,
  discoveryKey,
  onOpenRecallItem,
  sessionBanner,
  sessionProgress,
  activeSessionCounts,
  sessionPollNotice,
  retrySearchSession,
  resumeSearchPolling,
}: {
  recallResult: VkpiKolRecallResponse;
  searchSession: VkpiKolSearchHistoryItem | null;
  llmPlan: Row;
  discoveryItems: any[];
  discoveryTotal?: number;
  discoveryAutoEnrolled?: number | null;
  /** 品牌官方账号排除数(诚实信息):>0 才渲染一行说明;旧后端无该键恒 0 静默。 */
  discoveryBrandExcluded?: number;
  /** 触达展示闸折叠计数(2026-07-12「分析后再 po」):lowReach=低触达不展示(已入库仅不推荐)、
   *  analyzing=档案补全中,达标后自动放出;旧后端/无隐藏 → null 不渲染。 */
  reachFloorDisplay?: {
    discovery: { lowReach: number; analyzing: number; pendingFollowers: number };
    recall: { lowReach: number; analyzing: number; pendingFollowers: number };
  } | null;
  input: string;
  apiToken: string;
  isBusy: boolean;
  state: string;
  plannerFellBack: boolean;
  personaEditing: boolean;
  personaDraft: string;
  setPersonaEditing: (v: boolean) => void;
  setPersonaDraft: (v: string) => void;
  setInput: (v: string) => void;
  run: (overrideQuery?: string) => void;
  discoveryPlatforms: string[];
  setDiscoveryPlatforms: (updater: (cur: string[]) => string[]) => void;
  discoveryRegion: string;
  setDiscoveryRegion: (v: string) => void;
  contentLanguages: string[];
  setContentLanguages: (v: string[]) => void;
  kolProfileTypes: string[];
  setKolProfileTypes: (v: string[]) => void;
  excludeChinese: boolean;
  setExcludeChinese: (v: boolean) => void;
  queueTextAdvance: (overrideQuery?: string) => void;
  pickedIds: Set<number>;
  setPickedIds: (v: Set<number>) => void;
  favNote: string;
  favoriteIds: ReadonlySet<number>;
  favoriteBusyIds: ReadonlySet<number>;
  favoriteResults: ReadonlyMap<number, string>;
  favoriteErrors: ReadonlyMap<number, string>;
  favoritesSyncing: boolean;
  favoritesLoadError: string;
  draftNote: string;
  outreachNote: string;
  outreachResult: Record<string, any> | null;
  addingFav: boolean;
  draftBusy: boolean;
  outreachBusy: boolean;
  displayedSearchSessionId: number | null;
  isSessionPolling: boolean;
  isSessionPollPaused: boolean;
  resultsStale: boolean;
  approvalReady: boolean;
  favoriteOne: (kolPoolId: number) => void;
  addPickedToMyKol: () => void;
  approveAndCreateDraft: () => void;
  generateOutreachForPicked: () => void;
  discoveryKey: (item: any) => string;
  onOpenRecallItem?: (item: VkpiKolRecallItem) => void;
  sessionBanner: SessionBanner;
  sessionProgress: SearchSessionProgress;
  activeSessionCounts: Record<string, any>;
  sessionPollNotice: string;
  retrySearchSession: () => void;
  resumeSearchPolling: () => void;
}) {
  // 发现真总数 = 可见 + 被触达闸折叠(分析中/低触达):K3 入库反馈按真总数说话,
  // 否则「发现 3 人、入库 15 人」自相矛盾(隐藏项也都入了库)。纯派生,无 hooks。
  const hiddenDiscovery = reachFloorDisplay
    ? (reachFloorDisplay.discovery.analyzing || 0) + (reachFloorDisplay.discovery.lowReach || 0)
    : 0;
  const discoveryGrandTotal = discoveryTotal + hiddenDiscovery;
  // 本地召回接口只读我们自己的达人库 → 它返回的每一条都是「库内」;已带明确来源的行原样不动。
  const recallItems = withLocalRecallOrigin(recallTopItems(recallResult));
  const distribution = recallDistributionView(recallResult.candidate_set_distribution);
  const localStrict = localQualifiedSummary(recallResult);
  const onlineStrict = onlineQualifiedSummaryFromSession(searchSession);
  const totalStrictUnique = Math.min(60, localStrict.uniqueQualified + onlineStrict.uniqueQualified);
  const onlineStats = [
    onlineStrict.contractValid ? `${onlineStrict.selectionReady ? "终态" : "增量中"} r${onlineStrict.snapshotRevision}` : "",
    onlineStrict.duplicateLocal > 0 ? `与本地重复 ${onlineStrict.duplicateLocal}` : "",
    onlineStrict.duplicateOnline > 0 ? `联网内重复 ${onlineStrict.duplicateOnline}` : "",
    onlineStrict.duplicateLocalInventory > 0 ? `池内已有 ${onlineStrict.duplicateLocalInventory}` : "",
    onlineStrict.evaluated > 0 ? `已核验 ${onlineStrict.evaluated}` : "",
    onlineStrict.providerRounds > 0 ? `来源轮次 ${onlineStrict.providerRounds}` : "",
  ].filter(Boolean);
  const resolvedProductSku = resolvedProductSkuFromPlan(llmPlan);
  const recallCounts = recallDisplayCounts(recallItems, (recallResult.diagnostics || {}) as Row);
  // 来源分布:服务端算好的优先;没有就把本页三段列表(本地严格 / 联网净新增 / 全网发现)拼起来现数。
  const originCounts = summaryResultOriginCounts(asRecord(searchSession?.result_summary))
    ?? resultOriginCounts(recallItems, onlineStrict.rows.map((row) => row.item), discoveryItems);
  const openProductScopedItem = (item: VkpiKolRecallItem) => {
    onOpenRecallItem?.(withResolvedProductSku(item, resolvedProductSku));
  };
  return (
    <div className="mt-3 space-y-2.5">
      <ProgressiveSearchStageCard progress={sessionProgress} />

      {/* 结果来源分布(用户点名要的:哪些是库里捞的、哪些是这次现场新找到的) */}
      <ResultOriginSummaryBar counts={originCounts} />

      {/* 框1 · 产品人群分析(可编辑,防 LLM 理解偏) */}
      <div className="rounded-lg border border-cyan-300/15 bg-cyan-400/[0.04] p-3">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <div className="text-[11px] font-medium text-cyan-100">① 要找什么样的人</div>
          {!personaEditing ? (
            <button
              type="button"
              onClick={() => { setPersonaDraft(display(llmPlan.search_query, cleanText(input))); setPersonaEditing(true); }}
              className="rounded border border-white/[0.1] px-2 py-0.5 text-[9.5px] text-slate-400 transition-colors hover:border-white/[0.2] hover:text-white"
            >编辑</button>
          ) : null}
        </div>
        {plannerFellBack ? (
          <div className="mb-1.5 flex items-start gap-1.5 rounded-md border border-amber-300/20 bg-amber-400/[0.07] px-2 py-1.5 text-[10px] leading-relaxed text-amber-100/90">
            <Info size={11} className="mt-0.5 shrink-0" />
            <span>AI 规划暂不可用,已用基础检索匹配。下方结果可正常查看,稍后可重试以获得更精准的人群理解。</span>
          </div>
        ) : null}
        {personaEditing ? (
          <div className="space-y-1.5">
            <textarea
              value={personaDraft}
              onChange={(event) => setPersonaDraft(event.target.value)}
              rows={2}
              className="w-full resize-none rounded-md border border-white/[0.1] bg-black/30 px-2 py-1.5 text-[11px] text-white placeholder-slate-600 focus:border-cyan-400/40 focus:outline-none"
              placeholder="描述要找什么样的人，例如:35mm 低光人像 YouTube 摄影师…"
            />
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={!cleanText(personaDraft) || isBusy}
                onClick={() => { const q = cleanText(personaDraft); setInput(q); setPersonaEditing(false); void run(q); }}
                className="rounded-md border border-cyan-300/25 bg-cyan-500/[0.14] px-2.5 py-1 text-[10px] font-medium text-cyan-100 transition-colors hover:bg-cyan-500/[0.22] disabled:cursor-not-allowed disabled:opacity-50"
              >用此重搜</button>
              <button type="button" onClick={() => setPersonaEditing(false)} className="text-[10px] text-slate-500 hover:text-slate-300">取消</button>
            </div>
          </div>
        ) : Object.keys(llmPlan).length ? (
          <PlanPills plan={llmPlan} />
        ) : (
          <div className="text-[10px] text-slate-500">点「编辑」改写要找的人群，再「用此重搜」。</div>
        )}
      </div>

      {/* 框2 · 本地 + 联网两条严格通道，共用选择与审批动作。 */}
      <div className="rounded-lg border border-violet-300/15 bg-violet-950/[0.10] p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="text-[11px] font-medium text-violet-100">② 严格合格名单 · 本地先显示，联网按排名追加</div>
          <div className="flex flex-wrap gap-1.5 text-[10px] text-slate-500">
            <span className="rounded-md border border-white/[0.07] px-2 py-1">本地创作者 {recallCounts.creator}</span>
            <span className="rounded-md border border-white/[0.07] px-2 py-1">本地测评号 {recallCounts.reviewer}</span>
          </div>
        </div>
        <div className="mb-2 flex flex-wrap items-center gap-1.5 rounded-lg border border-cyan-300/20 bg-cyan-400/[0.05] px-2.5 py-2 text-[10px]" data-testid="strict-60-counter">
          <span className="font-semibold text-violet-100">严格 60 名单</span>
          <span className="rounded border border-violet-300/20 px-2 py-0.5 text-violet-100">本地 {localStrict.uniqueQualified}/30</span>
          <span className="rounded border border-emerald-300/20 px-2 py-0.5 text-emerald-100">联网净新增 {onlineStrict.uniqueQualified}/30</span>
          <span className="rounded border border-cyan-300/25 bg-cyan-400/[0.08] px-2 py-0.5 font-medium text-cyan-100">唯一 {totalStrictUnique}/60</span>
          {!onlineStrict.contractValid ? <span className="text-amber-200/80">联网严格合同待回填，未计入</span> : null}
        </div>
        <div className="mb-2">
          <SmartKolQualityFilters
            languages={contentLanguages}
            profileTypes={kolProfileTypes}
            onLanguagesChange={setContentLanguages}
            onProfileTypesChange={setKolProfileTypes}
          />
          <div className="mt-1 text-[9px] text-slate-600">改选后点下方“重新全网查找”；本地与联网名单都会按新硬闸重算。</div>
        </div>
        {resultsStale ? (
          <div className="mb-2 rounded-md border border-amber-300/25 bg-amber-400/[0.08] px-2.5 py-1.5 text-[10px] text-amber-100">
            搜索条件已变更：下方是上一轮结果，仅供参考且不可批准。点“重新全网查找”后按新条件重算。
          </div>
        ) : isSessionPolling ? (
          <div className="mb-2 rounded-md border border-cyan-300/20 bg-cyan-400/[0.06] px-2.5 py-1.5 text-[10px] text-cyan-100/90">
            新一轮仍在补全与验收；可以先勾选已通过行，批准动作会在本轮终态后开放。
          </div>
        ) : null}
        <SearchFilterDiagnostics diagnostics={(recallResult.diagnostics || {}) as Row} />
        <SearchEvaluationStatus evaluation={asRecord(recallResult.evaluation_status)} />
        <LocalQualifiedList
          result={recallResult}
          onOpen={openProductScopedItem}
          selectedIds={pickedIds}
          onSelectionChange={setPickedIds}
          selectionDisabled={resultsStale}
          favoriteIds={favoriteIds}
          favoriteBusyIds={favoriteBusyIds}
          favoriteResults={favoriteResults}
          favoriteErrors={favoriteErrors}
          favoritesSyncing={favoritesSyncing}
          onFavorite={favoriteOne}
        />
        <div className="my-2 border-t border-emerald-300/10 pt-2 text-[10px] font-medium text-emerald-100">联网严格净新增名单</div>
        <StrictQualifiedList
          summary={onlineStrict}
          lane="online"
          extraStats={onlineStats}
          onOpen={openProductScopedItem}
          selectedIds={pickedIds}
          onSelectionChange={setPickedIds}
          selectionDisabled={resultsStale}
          selectionReady={onlineStrict.selectionReady}
          favoriteIds={favoriteIds}
          favoriteBusyIds={favoriteBusyIds}
          favoriteResults={favoriteResults}
          favoriteErrors={favoriteErrors}
          favoritesSyncing={favoritesSyncing}
          onFavorite={favoriteOne}
        />
        <div className="mt-2 rounded-md border border-emerald-300/15 bg-emerald-400/[0.04] px-2.5 py-1.5 text-[9.5px] text-emerald-100/80">
          关注 = 加入本人 MY KOL，后续可分组、认领和跟进；不等于批准项目，也不会自动外联或展示联系方式明文。
          {favoritesLoadError ? <div className="mt-0.5 text-amber-100">{favoritesLoadError}</div> : null}
        </div>
        {pickedIds.size > 0 || favNote || draftNote || outreachNote ? (
          <div className="mt-2 flex flex-col gap-1.5 rounded-md border border-emerald-300/25 bg-emerald-400/[0.08] px-2.5 py-1.5">
            <div className="flex flex-wrap items-center gap-2">
              {pickedIds.size > 0 ? (
                <>
                  <span className="text-[10.5px] font-medium text-emerald-100">严格合格已选 {pickedIds.size} 人</span>
                  <button
                    type="button"
                    onClick={() => void addPickedToMyKol()}
                    disabled={addingFav || !apiToken || resultsStale}
                    title="关注后进入本人 MY KOL；重复操作由服务端幂等处理"
                    className="inline-flex items-center gap-1 rounded border border-emerald-300/35 bg-emerald-500/[0.2] px-2 py-0.5 text-[10px] font-medium text-emerald-50 transition-colors hover:bg-emerald-500/[0.32] disabled:opacity-50"
                  >
                    {addingFav ? <Loader2 size={11} className="animate-spin" /> : <UserPlus size={11} />} 关注并加入 MY KOL
                  </button>
                  {/* R4:批准锁定 → 一键建项目草案(草案带成本估算 + 风险) */}
                  <button
                    type="button"
                    onClick={() => void approveAndCreateDraft()}
                    disabled={draftBusy || !apiToken || !approvalReady}
                    title={approvalReady ? "批准选中候选并据此建项目草案(带预算/风险)" : resultsStale ? "搜索条件已变更，需先重算" : isSessionPolling ? "本轮仍在补全，终态后可批准" : displayedSearchSessionId ? "当前结果尚不可批准" : "需先有搜索会话"}
                    className="inline-flex items-center gap-1 rounded border border-sky-300/35 bg-sky-500/[0.2] px-2 py-0.5 text-[10px] font-medium text-sky-50 transition-colors hover:bg-sky-500/[0.32] disabled:opacity-50"
                  >
                    {draftBusy ? <Loader2 size={11} className="animate-spin" /> : <FolderPlus size={11} />} 批准并建草案
                  </button>
                  {/* R4:为选中候选生成合作话术 + SOW 草案(LLM·预算闸·仅草案) */}
                  <button
                    type="button"
                    onClick={() => void generateOutreachForPicked()}
                    disabled={outreachBusy || !apiToken || !approvalReady}
                    title={approvalReady ? "为选中候选生成合作话术 + SOW 草案(人审后手动外发)" : resultsStale ? "搜索条件已变更，需先重算" : isSessionPolling ? "本轮仍在补全，终态后可生成" : displayedSearchSessionId ? "当前结果尚不可生成" : "需先有搜索会话"}
                    className="inline-flex items-center gap-1 rounded border border-violet-300/35 bg-violet-500/[0.2] px-2 py-0.5 text-[10px] font-medium text-violet-50 transition-colors hover:bg-violet-500/[0.32] disabled:opacity-50"
                  >
                    {outreachBusy ? <Loader2 size={11} className="animate-spin" /> : <MessageSquare size={11} />} 生成话术
                  </button>
                  <button type="button" onClick={() => setPickedIds(new Set())} className="text-[10px] text-slate-400 hover:text-slate-200">清空</button>
                </>
              ) : null}
              {favNote ? <span className="text-[10px] text-emerald-200/85">{favNote}</span> : null}
              {favNote && favoriteIds.size > 0 ? (
                <button
                  type="button"
                  onClick={() => window.dispatchEvent(new CustomEvent("vkpi:open-mykol-kol"))}
                  className="rounded border border-emerald-300/25 bg-black/15 px-2 py-0.5 text-[10px] text-emerald-100 hover:bg-emerald-400/[0.10]"
                >
                  打开 MY KOL 查看
                </button>
              ) : null}
            </div>
            {draftNote ? <span className="text-[10px] text-sky-100/90">{draftNote}</span> : null}
            {outreachNote ? <span className="text-[10px] text-violet-100/90">{outreachNote}</span> : null}
            {outreachResult && Array.isArray(outreachResult.messages) && outreachResult.messages.length ? (
              <details className="mt-0.5 rounded border border-violet-300/20 bg-black/20 px-2 py-1">
                <summary className="cursor-pointer text-[10px] text-violet-100/90">查看话术草案({outreachResult.messages.length} 封)· 人审后手动外发</summary>
                <div className="mt-1 flex max-h-56 flex-col gap-1.5 overflow-y-auto">
                  {outreachResult.messages.map((m: any, i: number) => (
                    <div key={`om-${m.kol_pool_id || i}`} className="rounded border border-white/[0.06] bg-white/[0.02] px-2 py-1">
                      <div className="text-[10px] font-medium text-violet-50">
                        {m.display_name || m.handle || `KOL #${m.kol_pool_id || i + 1}`}
                        {m.personalized === false ? <span className="ml-1 text-[8px] text-slate-400">· 模板</span> : null}
                      </div>
                      {m.subject ? <div className="text-[9.5px] text-slate-300">主题:{m.subject}</div> : null}
                      <div className="whitespace-pre-line text-[9.5px] leading-relaxed text-slate-200/90">{m.body}</div>
                    </div>
                  ))}
                  {outreachResult.sow_draft && outreachResult.sow_draft.scope ? (
                    <div className="rounded border border-violet-300/20 bg-violet-500/[0.06] px-2 py-1 text-[9.5px] text-violet-100/90">
                      <div className="font-medium">SOW 草案</div>
                      <div className="opacity-90">范围:{outreachResult.sow_draft.scope}</div>
                      {Array.isArray(outreachResult.sow_draft.deliverables) && outreachResult.sow_draft.deliverables.length ? (
                        <div className="opacity-90">交付:{outreachResult.sow_draft.deliverables.join(" · ")}</div>
                      ) : null}
                      <div className="opacity-75">报酬:{outreachResult.sow_draft.compensation || "待人工确定(不承诺价格)"}</div>
                    </div>
                  ) : null}
                </div>
              </details>
            ) : null}
          </div>
        ) : null}
        {distribution ? (
          <div className="mt-2 rounded-md border border-cyan-300/15 bg-cyan-400/[0.04] px-2.5 py-2">
            <div className="flex flex-wrap items-center justify-between gap-1.5 text-[9.5px] text-slate-400">
              <span className="font-medium text-cyan-100/90">本次有证据候选分布 · {distribution.denominator} 人</span>
              <span title="仅描述本次返回的去重候选，不代表市场份额或整体市场覆盖">描述性统计 · 非市场份额</span>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {distribution.chips.map((chip) => (
                <span key={`${chip.dimension}-${chip.label}`} className="rounded-full border border-white/[0.08] bg-black/20 px-2 py-0.5 text-[9px] text-slate-300">
                  {chip.label} {chip.count}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        {/* 触达展示闸折叠行(诚实信息,别删):被隐藏的候选不摆行,只报计数。 */}
        {reachFloorDisplay && (reachFloorDisplay.recall.analyzing > 0 || reachFloorDisplay.recall.lowReach > 0) ? (
          <div
            className="mt-2 rounded-md border border-white/[0.08] bg-black/20 px-2.5 py-1.5 text-[10px] text-slate-400"
            title="低触达=粉丝数低于门槛,已入库仅不推荐;分析中=粉丝数待档案补全,达标后自动出现在列表"
          >
            {[
              reachFloorDisplay.recall.analyzing > 0 ? `分析中 ×${reachFloorDisplay.recall.analyzing}(补全后自动放出)` : "",
              reachFloorDisplay.recall.lowReach > 0 ? `低触达不展示 ×${reachFloorDisplay.recall.lowReach}` : "",
            ].filter(Boolean).join(" · ")}
          </div>
        ) : null}
      </div>

      {/* 框3 · 普通 provider 候选仅供观察；未进入联网严格合同前不计数、不可选择。 */}
      <div className="rounded-lg border border-emerald-300/30 bg-emerald-950/[0.16] p-3 ring-1 ring-emerald-300/10">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-300/30 bg-emerald-400/[0.12] px-1.5 py-0.5 text-[8.5px] font-semibold text-emerald-100">
              <UserPlus size={9} /> 候选池
            </span>
            <div className="text-[11px] font-semibold text-emerald-100">③ 联网待验收候选（不计入严格 30）{discoveryItems.length ? ` · ${discoveryItems.length} 个` : ""}</div>
          </div>
          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-300/25 bg-emerald-400/[0.1] px-1.5 py-0.5 text-[9px] font-medium text-emerald-200/90" title="任何文字搜索都自动从所选平台发现新号,无需手点">
            <Sparkles size={9} /> 自动·恒开
          </span>
        </div>
        {/* 【K3 正账】入库反馈:发现即自动落 Pool(后端 _auto_enroll_discoveries)。后端现把真实入库数
            记进 result_summary.new_discovery.counts.auto_enrolled(仅计本次成功 upsert 的新行;
            已在库/缺 handle/入库失败的不计)→ 有真数就显示真数;旧会话无该键则回退到概述文案,不编数字。 */}
        {discoveryGrandTotal > 0 ? (
          <div
            className="mb-2 rounded-md border border-emerald-300/20 bg-emerald-400/[0.06] px-2.5 py-1.5 text-[10px] text-emerald-100/90"
            title="全网新发现会即时轻量入库(仅基础资料,不触评分);已在库/缺 handle/入库失败的项不计入入库数;含下方「分析中/低触达」折叠项"
          >
            {typeof discoveryAutoEnrolled === "number"
              ? `本次全网新发现 ${discoveryGrandTotal} 人,其中 ${discoveryAutoEnrolled} 人已自动入库(其余已在库或入库失败)· 下次同类搜索归「库内已有的人」`
              : `本次全网新发现 ${discoveryGrandTotal} 人,已自动登记入库(个别缺 handle/入库失败的除外)· 下次同类搜索归「库内已有的人」`}
          </div>
        ) : null}
        {/* 触达展示闸折叠行(2026-07-12「分析后再 po」):粉丝数未知的发现项已入库并自动补全,
            补全达标后自动出现在下方;低触达项不摆行,只报诚实计数。 */}
        {reachFloorDisplay && (reachFloorDisplay.discovery.analyzing > 0 || reachFloorDisplay.discovery.lowReach > 0 || reachFloorDisplay.discovery.pendingFollowers > 0) ? (
          <div
            data-testid="discovery-reach-floor-row"
            className="mb-2 flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-black/20 px-2.5 py-1.5 text-[10px] text-slate-400"
            title="粉丝数待核=已上墙、已自动入库并排队补全,核实后卡片自动更新;分析中=旧口径折叠项;低触达=粉丝数低于门槛,已入库仅不推荐"
          >
            {reachFloorDisplay.discovery.analyzing > 0 ? <Loader2 size={10} className="animate-spin text-emerald-200/70" /> : null}
            {[
              reachFloorDisplay.discovery.pendingFollowers > 0 ? `粉丝数待核 ×${reachFloorDisplay.discovery.pendingFollowers}(已上墙,核实后自动更新)` : "",
              reachFloorDisplay.discovery.analyzing > 0 ? `分析中 ×${reachFloorDisplay.discovery.analyzing}(档案补全后达标自动放出)` : "",
              reachFloorDisplay.discovery.lowReach > 0 ? `低触达不展示 ×${reachFloorDisplay.discovery.lowReach}` : "",
            ].filter(Boolean).join(" · ")}
          </div>
        ) : null}
        {/* 品牌官号排除行(诚实信息,门面不暴露判据细节):后端已把品牌官方账号挡在发现结果外。 */}
        {discoveryBrandExcluded > 0 ? (
          <div
            className="mb-2 rounded-md border border-white/[0.08] bg-black/20 px-2.5 py-1.5 text-[10px] text-slate-400"
            title="品牌官方账号不属于创作者合作对象,已从本次发现结果中排除"
          >
            品牌官方账号已排除 ×{discoveryBrandExcluded}
          </div>
        ) : null}
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] text-slate-500">发现平台</span>
          {/* 【B5】Facebook 解锁为可选平台:后端 SUPPORTED_DISCOVERY_PLATFORMS 已含 facebook
              (apify/facebook-search-scraper,discovery_filters.py);opt-in 设计——不进默认三平台兜底,
              显式勾选后请求 new_discovery_platforms 数组才带 "facebook"。 */}
          {([
            { k: "youtube", t: "YouTube" },
            { k: "instagram", t: "Instagram" },
            { k: "tiktok", t: "TikTok" },
            // F5 诚实状态:Facebook 只进候选池(③),不进严格 30;标签直说「仅候选池」。
            { k: "facebook", t: "Facebook · 仅候选池", strictDisabled: true, tip: "Facebook 发现结果只进入候选池观察，不计入严格联网 30；严格 30 当前仅支持 YouTube、Instagram、TikTok" },
          ] as { k: string; t: string; tip?: string; strictDisabled?: boolean }[]).map((p) => {
            const on = discoveryPlatforms.includes(p.k);
            const disabled = p.strictDisabled || (on && discoveryPlatforms.length === 1);
            return (
              <button
                key={p.k}
                type="button"
                disabled={disabled}
                title={p.strictDisabled ? p.tip : on && discoveryPlatforms.length === 1 ? "至少保留一个发现平台" : p.tip}
                onClick={() => setDiscoveryPlatforms((cur) => nextRequiredPlatformSelection(cur, p.k))}
                className={`rounded-full border px-2 py-0.5 text-[10px] transition-colors disabled:cursor-not-allowed disabled:opacity-55 ${on ? "border-cyan-300/40 bg-cyan-400/[0.12] text-cyan-100" : "border-white/[0.08] text-slate-500 hover:border-white/[0.16]"}`}
              >{p.t}</button>
            );
          })}
          <span className="ml-1 text-[10px] text-slate-500">目标市场</span>
          <select
            value={discoveryRegion}
            onChange={(event) => setDiscoveryRegion(event.target.value)}
            title="只限定目标市场，不会自动推断内容语言；内容语言请在上方独立选择"
            className="rounded-md border border-white/[0.1] bg-black/30 px-1.5 py-0.5 text-[10px] text-slate-200 focus:border-cyan-400/40 focus:outline-none"
          >
            {[
              { v: "", t: "全球（不限定市场）" },
              { v: "US", t: "美国" }, { v: "UK", t: "英国" }, { v: "CA", t: "加拿大" },
              { v: "AU", t: "澳大利亚" }, { v: "JP", t: "日本" }, { v: "KR", t: "韩国" },
              { v: "DE", t: "德国" }, { v: "FR", t: "法国" }, { v: "ES", t: "西班牙" },
              { v: "MX", t: "墨西哥" }, { v: "IT", t: "意大利" }, { v: "BR", t: "巴西" },
              { v: "PT", t: "葡萄牙" }, { v: "RU", t: "俄罗斯" }, { v: "TH", t: "泰国" },
              { v: "VN", t: "越南" }, { v: "ID", t: "印度尼西亚" }, { v: "TR", t: "土耳其" },
              { v: "PL", t: "波兰" }, { v: "NL", t: "荷兰" }, { v: "SA", t: "沙特阿拉伯" },
              { v: "AE", t: "阿联酋" }, { v: "IN", t: "印度" }, { v: "SG", t: "新加坡" },
              { v: "NZ", t: "新西兰" },
            ].map((o) => (
              <option key={o.v} value={o.v} className="bg-slate-900 text-slate-100">{o.t}</option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-[10px] text-slate-400" title="排除 中国大陆/香港/台湾 地区(按 country/market 地区判据,海外中文博主放行)">
            <input type="checkbox" checked={excludeChinese} onChange={(event) => setExcludeChinese(event.target.checked)} className="accent-emerald-500" />
            排除 中国/港/台 地区
          </label>
          <button
            type="button"
            onClick={() => void queueTextAdvance()}
            disabled={state === "executing" || !apiToken || !cleanText(input)}
            className="ml-auto inline-flex min-h-[28px] items-center justify-center gap-1.5 rounded-md border border-emerald-300/18 bg-emerald-500/[0.12] px-2.5 text-[10px] font-medium text-emerald-100 transition-colors hover:bg-emerald-500/[0.20] disabled:cursor-not-allowed disabled:opacity-55"
          >
            {state === "executing" ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
            重新全网查找
          </button>
        </div>
        {discoveryItems.length ? (
          <CandidateLaneGroups
            items={discoveryItems as VkpiKolRecallItem[]}
            renderItem={(item, index) => {
              const key = discoveryKey(item);
              // 重复卡修:渲染 key 用「平台:handle」身份键(pool id 回填不换 key,不再裂成两张卡)。
              return (
                <div key={`d-${key || item.kol_pool_id || index}`} className="h-full">
                  <RecallMiniItem item={item} index={index + 1} onOpen={openProductScopedItem} feedbackSource="discovery_wall" feedbackToken={apiToken} />
                </div>
              );
            }}
          />
        ) : isSessionPolling ? (
          <div className="flex items-center gap-1.5 rounded-md border border-emerald-300/15 bg-black/15 px-2.5 py-2 text-[10.5px] text-emerald-100/80">
            <Loader2 size={12} className="animate-spin" /> 正在从所选平台找新号，完成后自动显示
          </div>
        ) : isSessionPollPaused ? (
          <div className="rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-3 py-2.5 text-[10.5px] leading-relaxed text-amber-100">
            <div className="font-medium">后台任务状态待继续同步</div>
            <div className="mt-0.5 opacity-85">高频同步已到时限，但没有把任务判成失败，也没有重新入队。</div>
            <button
              type="button"
              onClick={resumeSearchPolling}
              className="mt-1.5 inline-flex min-h-[26px] items-center justify-center gap-1.5 rounded-md border border-amber-300/30 bg-amber-500/[0.14] px-2.5 text-[10px] font-medium text-amber-100 hover:bg-amber-500/[0.22]"
            >
              <RefreshCw size={11} /> 继续同步原任务
            </button>
          </div>
        ) : sessionBanner && (sessionBanner.tone === "error" || sessionBanner.tone === "warn") ? (
          // 失败/部分但无发现项:不再静默落空白占位,直接说明状态与原因(诚实兜底)。
          <div className={`rounded-md border px-3 py-2.5 text-[10.5px] leading-relaxed ${
            sessionBanner.tone === "error"
              ? "border-rose-300/20 bg-rose-500/[0.08] text-rose-100"
              : "border-amber-300/20 bg-amber-400/[0.08] text-amber-100"
          }`}>
            <div className="font-medium">{sessionBanner.label}</div>
            <div className="mt-0.5 opacity-85">{sessionBanner.note}</div>
            {/* 失败/未完成 → 「重试」(重新入队该搜索,续接轮询回填 ①②③) */}
            <button
              type="button"
              onClick={() => void retrySearchSession()}
              disabled={state === "executing" || !apiToken || !cleanText(input)}
              className={`mt-1.5 inline-flex min-h-[26px] items-center justify-center gap-1.5 rounded-md border px-2.5 text-[10px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55 ${
                sessionBanner.tone === "error"
                  ? "border-rose-300/30 bg-rose-500/[0.14] text-rose-100 hover:bg-rose-500/[0.22]"
                  : "border-amber-300/30 bg-amber-500/[0.14] text-amber-100 hover:bg-amber-500/[0.22]"
              }`}
            >
              {state === "executing" ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              重试
            </button>
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-3 text-center text-[10.5px] text-slate-500">全网发现恒开 · 搜索后自动从所选平台发现新号</div>
        )}
        {sessionBanner ? (
          // 诚实会话横幅:排队/查找中/已完成/部分完成/未完成 + 真原因;部分/已完成仍保留计数。
          <div className={`mt-2 rounded-md border px-2.5 py-2 text-[10px] leading-relaxed ${
            sessionBanner.tone === "error"
              ? "border-rose-300/20 bg-rose-500/[0.07] text-rose-100"
              : sessionBanner.tone === "warn"
                ? "border-amber-300/20 bg-amber-400/[0.07] text-amber-100"
                : sessionBanner.tone === "ok"
                  ? "border-emerald-300/20 bg-emerald-400/[0.07] text-emerald-100"
                  : "border-emerald-300/15 bg-black/15 text-emerald-100/75"
          }`}>
            <div className="flex flex-wrap items-center gap-1.5">
              {sessionBanner.tone === "info" && !isSessionPollPaused ? <Loader2 size={11} className="animate-spin" /> : null}
              <span className="font-medium">{isSessionPollPaused ? "后台状态待继续同步" : sessionBanner.label}</span>
              {Object.keys(activeSessionCounts).length ? (
                <>
                  <span className="rounded border border-white/[0.1] bg-black/15 px-1.5 py-0.5">已找到 {display(activeSessionCounts.ready, "0")}</span>
                  <span className="rounded border border-white/[0.1] bg-black/15 px-1.5 py-0.5">已入库 {display(activeSessionCounts.executed, "0")}</span>
                  {Number(activeSessionCounts.errors) > 0 || Number(activeSessionCounts.failed) > 0 ? (
                    <span className="rounded border border-rose-300/20 bg-black/15 px-1.5 py-0.5 text-rose-200/80">未完成 {display(Number(activeSessionCounts.errors || 0) + Number(activeSessionCounts.failed || 0), "0")}</span>
                  ) : null}
                </>
              ) : null}
            </div>
            <div className="mt-0.5 opacity-85">{sessionBanner.note}</div>
            {sessionPollNotice ? <div className="mt-0.5 opacity-70">{sessionPollNotice}</div> : null}
            {isSessionPollPaused ? (
              <button
                type="button"
                onClick={resumeSearchPolling}
                className="mt-1.5 inline-flex min-h-[26px] items-center justify-center gap-1.5 rounded-md border border-amber-300/25 px-2.5 text-[10px] font-medium text-amber-100 hover:bg-amber-400/[0.08]"
              >
                <RefreshCw size={11} /> 继续同步原任务
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
