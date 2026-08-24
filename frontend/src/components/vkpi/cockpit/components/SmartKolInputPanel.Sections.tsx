// SmartKolInputPanel 展示型子组件 + 会话/召回派生器(从 SmartKolInputPanel.tsx 抽出,行为不变)。
// 容器组件本体(useState/useEffect/run 等)仍留 SmartKolInputPanel.tsx,这里只放纯派生函数 +
// 仅吃 props 的展示组件(各自局部 useState 不破坏容器 hooks 顺序)。容器 import 回去,调用点不变。
// 红线:纯展示/派生,只读 final_v1/QA 缓存,绝不写任何 viltrox_fit_score。
import { useRef, useState } from "react";
import { AlertTriangle, Clock3, Info, Mail, RotateCcw, Search, Trash2, UserPlus, Users } from "lucide-react";

import {
  type VkpiKolRecallItem,
  type VkpiKolSearchHistoryItem,
} from "../../../../domains/kol";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import { translateBio } from "../../../../services/vkpi/kolPool-api";
import type { SearchFeedbackSource } from "../../../../services/vkpi/searchFeedback-api";
import { SearchFeedbackControl } from "./SearchFeedbackControl";

import {
  cleanText,
  display,
  numberLabel,
  type Row,
} from "./SmartKolInputPanel.helpers";
import { candidateEvidenceSummary, candidateRankSummary } from "./SmartKolInputPanel.CandidateEvidence";
import { kolHumanDisplayName, kolHumanProfileLinkLabel, kolHumanPublicHandle } from "../lib/kolIdentity";

// 纯派生器 / 常量 / 类型已再抽到 SmartKolInputPanel.derivers.ts(行为不变;展示子组件留此文件)。
// 容器仍从本文件 import 这些名字,故此处 re-export 维持调用面不变。
import {
  PENDING_SEARCH_SESSION_KEY,
  PROFILE_REP_VIDEO_LIMIT,
  contentFitBadge,
  discoveryAutoEnrolledFromSession,
  discoveryBrandExcludedFromSession,
  discoveryItemsFromSession,
  freshnessMarks,
  historyKindMeta,
  historyLabel,
  historySessionId,
  historySessionStatusMeta,
  isSearchSessionTerminal,
  looksLikeRetailer,
  mergeKolRecallSnapshots,
  mergeKolSearchSessionSnapshots,
  reachFloorDisplayFromSession,
  readPersistedSearchDisplay,
  readableCreatorName,
  recallResultFromSession,
  recallTopItems,
  relativeTime,
  sessionAdvanceCounts,
  sessionItems,
  sessionStatusBanner,
  searchSessionProgress,
  urlResultFromSession,
  writePersistedSearchDisplay,
  zhTag,
  type PersistedSearchDisplay,
} from "./SmartKolInputPanel.derivers";

export {
  PENDING_SEARCH_SESSION_KEY,
  PROFILE_REP_VIDEO_LIMIT,
  discoveryAutoEnrolledFromSession,
  discoveryBrandExcludedFromSession,
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
};
export type { PersistedSearchDisplay };

// URL 深析展示子组件已抽到 SmartKolInputPanel.UrlSummary.tsx(行为不变);此处 re-export 维持容器调用面不变。
export { UrlSummary } from "./SmartKolInputPanel.UrlSummary";

type State = "idle" | "loading" | "ready" | "executing" | "error";

const EMPTY_CANDIDATE_TEXT = new Set(["", "-", "--", "unknown", "n/a", "na", "null", "none", "未知", "未提供"]);

function candidateText(value: unknown): string {
  const normalized = cleanText(value);
  return EMPTY_CANDIDATE_TEXT.has(normalized.toLowerCase()) ? "" : normalized;
}

function positiveMetric(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function rateLabel(value: unknown): string {
  const parsed = positiveMetric(value);
  if (parsed == null) return "";
  const percent = parsed <= 1 ? parsed * 100 : parsed;
  if (percent > 100) return "";
  return `${percent.toFixed(percent < 10 ? 1 : 0).replace(/\.0$/, "")}%`;
}

function compactDate(value: unknown): string {
  const raw = candidateText(value);
  if (!raw) return "";
  const parsed = Date.parse(raw);
  if (!Number.isFinite(parsed)) return "";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(parsed));
}

function candidateHttpUrl(value: unknown): string {
  const raw = candidateText(value);
  return /^https?:\/\//i.test(raw) ? raw : "";
}

// 【K2】契合命中 chip 点击 → 写入 KOL Pool 本地筛选词。复用顶栏全局搜索同款 localStorage+event
// 管道(KOLPoolPage.consumeSearch 监听 vkpi:open-kol-pool-search → setSearch + 展开表格视图)。
// 筛选词用原文(英文命中词)而非中文映射——本地筛选 hay(handle/bio/设备)是英文,原文才命中。
function applyPoolLocalFilter(tag: string, ev?: { stopPropagation?: () => void }) {
  if (ev && typeof ev.stopPropagation === "function") ev.stopPropagation();
  const q = String(tag || "").trim();
  if (!q) return;
  try {
    window.localStorage.setItem("vkpi:pending-kolpool-search", q);
    window.dispatchEvent(new Event("vkpi:open-kol-pool-search"));
  } catch { /* localStorage 不可用忽略 */ }
}

export function HistoryStrip({
  items,
  archivedItems,
  loading,
  actionBusy,
  notice,
  onOpen,
  onArchive,
  onRestore,
  onArchiveAll,
}: {
  items: VkpiKolSearchHistoryItem[];
  archivedItems: VkpiKolSearchHistoryItem[];
  loading: boolean;
  actionBusy?: string;
  notice?: string;
  onOpen: (session: VkpiKolSearchHistoryItem) => void;
  onArchive: (session: VkpiKolSearchHistoryItem) => void;
  onRestore: (session: VkpiKolSearchHistoryItem) => void;
  onArchiveAll: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<"active" | "archived">("active");
  const [filter, setFilter] = useState("");
  const [clearArmed, setClearArmed] = useState(false);
  const source = tab === "active" ? items : archivedItems;
  const normalizedFilter = filter.trim().toLowerCase();
  const shown = expanded
    ? source.filter((item) => {
        if (!normalizedFilter) return true;
        return [historyLabel(item), historyKindMeta(item).label, historySessionStatusMeta(item).label]
          .join(" ")
          .toLowerCase()
          .includes(normalizedFilter);
      })
    : [];
  const terminalCount = items.filter((item) => ["ready", "partial", "failed", "cancelled"].includes(String(item.status || ""))).length;
  const busy = Boolean(actionBusy);
  return (
    <div className="mt-2 rounded-lg border border-white/[0.065] bg-black/15 px-2.5 py-2" data-testid="kol-search-history">
      <div className={`flex items-center justify-between gap-2${expanded ? " mb-1.5" : ""}`}>
        <button
          type="button"
          onClick={() => {
            setExpanded((x) => !x);
            setClearArmed(false);
          }}
          className="inline-flex items-center gap-1.5 text-[10px] font-medium text-slate-300 hover:text-cyan-100"
          aria-expanded={expanded}
        >
          <Clock3 size={11} className="text-slate-500" />
          历史记录
          <span className="text-[9px] text-slate-600">
            · 最近 {items.length}{items.length >= 50 ? "+" : ""} · 已移除 {archivedItems.length}{archivedItems.length >= 50 ? "+" : ""}
          </span>
        </button>
        <div className="inline-flex items-center gap-2">
          {loading ? <span className="text-[9.5px] text-slate-600">同步中</span> : null}
          {!loading && notice ? <span className="text-[9.5px] text-amber-300/80">同步异常</span> : null}
          {!loading ? (
            <button
              type="button"
              onClick={() => {
                setExpanded((x) => !x);
                setClearArmed(false);
              }}
              className="text-[9.5px] font-medium text-slate-500 hover:text-cyan-200"
            >
              {expanded ? "收起" : "查看"}
            </button>
          ) : null}
        </div>
      </div>
      {expanded ? (
        <div className="mb-2 flex flex-wrap items-center gap-1.5 border-b border-white/[0.055] pb-2">
          <div className="inline-flex rounded-md border border-white/[0.065] bg-black/15 p-0.5" aria-label="历史记录范围">
            <button
              type="button"
              onClick={() => { setTab("active"); setClearArmed(false); }}
              className={`rounded px-2 py-1 text-[9.5px] transition-colors ${tab === "active" ? "bg-white/[0.08] text-cyan-100" : "text-slate-500 hover:text-slate-300"}`}
            >
              最近 {items.length}{items.length >= 50 ? "+" : ""}
            </button>
            <button
              type="button"
              onClick={() => { setTab("archived"); setClearArmed(false); }}
              className={`rounded px-2 py-1 text-[9.5px] transition-colors ${tab === "archived" ? "bg-white/[0.08] text-cyan-100" : "text-slate-500 hover:text-slate-300"}`}
            >
              已移除 {archivedItems.length}{archivedItems.length >= 50 ? "+" : ""}
            </button>
          </div>
          <label className="flex min-w-[150px] flex-1 items-center gap-1.5 rounded-md border border-white/[0.065] bg-black/15 px-2 py-1">
            <Search size={10} className="shrink-0 text-slate-600" />
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="筛选历史"
              className="min-w-0 flex-1 bg-transparent text-[10px] text-slate-300 outline-none placeholder:text-slate-700"
              aria-label="筛选历史记录"
            />
          </label>
          {tab === "active" && terminalCount > 0 ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (clearArmed) {
                  onArchiveAll();
                  setClearArmed(false);
                } else {
                  setClearArmed(true);
                }
              }}
              className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[9px] transition-colors disabled:cursor-wait disabled:opacity-50 ${clearArmed ? "border-rose-300/30 bg-rose-500/[0.08] text-rose-200" : "border-white/[0.065] text-slate-500 hover:border-rose-300/20 hover:text-rose-200"}`}
              title="只移除已完成记录；运行中任务会保留"
            >
              <Trash2 size={10} />
              {clearArmed ? "确认清理全部已完成" : "清理已完成"}
            </button>
          ) : null}
        </div>
      ) : null}
      {expanded && notice ? <div className="mb-1.5 rounded-md border border-cyan-300/10 bg-cyan-400/[0.035] px-2 py-1.5 text-[9.5px] text-cyan-100/80">{notice}</div> : null}
      <div className={expanded ? "max-h-64 space-y-1 overflow-y-auto pr-0.5" : ""}>
        {expanded && !shown.length && !loading ? (
          <div className="rounded-md border border-dashed border-white/[0.065] px-3 py-4 text-center text-[10px] text-slate-600">
            {normalizedFilter
              ? "没有匹配的历史记录"
              : tab === "archived"
                ? "当前登录账号没有已移除记录"
                : archivedItems.length
                  ? "最近记录为空；可切换到“已移除”查看和恢复"
                  : "当前登录账号暂无搜索历史"}
          </div>
        ) : null}
        {shown.map((item) => {
          const sessionId = historySessionId(item);
          const label = historyLabel(item);
          const kind = historyKindMeta(item);
          const st = historySessionStatusMeta(item);
          const searchTimeValue = item.created_at || item.updated_at;
          const archiveTimeValue = item.archived_at;
          const relativeValue = tab === "archived" ? archiveTimeValue || searchTimeValue : searchTimeValue;
          const when = relativeTime(relativeValue);
          const whenLabel = tab === "archived" && archiveTimeValue && when ? `${when}移除` : when;
          const exactWhen = searchTimeValue && Number.isFinite(Date.parse(String(searchTimeValue)))
            ? new Date(String(searchTimeValue)).toLocaleString()
            : "";
          const exactArchiveWhen = archiveTimeValue && Number.isFinite(Date.parse(String(archiveTimeValue)))
            ? new Date(String(archiveTimeValue)).toLocaleString()
            : "";
          const resultCount = Math.max(0, Number(item.item_count) || 0);
          const terminal = ["ready", "partial", "failed", "cancelled"].includes(String(item.status || ""));
          const actionKey = `${tab}-${sessionId || label}`;
          return (
            <div
              key={`${sessionId || label}-${item.updated_at || item.created_at || ""}`}
              className="group flex w-full items-stretch rounded-md border border-white/[0.05] bg-white/[0.015] transition-colors hover:border-cyan-300/25 hover:bg-cyan-400/[0.04]"
              title={`${kind.label} · ${label} · ${st.label}${sessionId ? ` · 会话 #${sessionId}` : ""}${exactWhen ? ` · 搜索于 ${exactWhen}` : ""}${exactArchiveWhen ? ` · 移除于 ${exactArchiveWhen}` : ""}`}
            >
              <button
                type="button"
                onClick={() => onOpen(item)}
                className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left"
              >
                <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[8.5px] font-semibold ${kind.cls}`}>{kind.label}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11px] text-slate-300 group-hover:text-cyan-100">{label}</span>
                  <span className="mt-0.5 block truncate text-[8.5px] text-slate-600">
                    {sessionId ? `会话 #${sessionId}` : "历史会话"} · {resultCount} 个结果{exactWhen ? ` · ${exactWhen}` : ""}
                  </span>
                </span>
                {whenLabel ? <span className="shrink-0 text-[9px] text-slate-600">{whenLabel}</span> : null}
                <span className={`inline-flex shrink-0 items-center gap-1 text-[9.5px] font-medium ${st.cls}`}>
                  <span className="h-1 w-1 rounded-full" style={{ background: st.dot }} />
                  {st.label}
                </span>
              </button>
              <button
                type="button"
                disabled={busy || !sessionId || (tab === "active" && !terminal)}
                onClick={() => tab === "active" ? onArchive(item) : onRestore(item)}
                className="mx-1 my-1 inline-flex w-7 shrink-0 items-center justify-center rounded border border-transparent text-slate-600 transition-colors hover:border-white/[0.065] hover:bg-black/20 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-30"
                aria-label={tab === "active" ? `移除历史：${label}` : `恢复历史：${label}`}
                title={tab === "active" ? (terminal ? "从最近历史移除（数据仍保留）" : "任务进行中，完成后可移除") : "恢复到最近历史"}
              >
                {actionBusy === actionKey ? <span className="text-[9px]">…</span> : tab === "active" ? <Trash2 size={11} /> : <RotateCcw size={11} />}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function RecallMiniItem({
  item,
  index,
  onOpen,
  className = "",
  feedbackSource,
  feedbackToken = "",
}: {
  item: VkpiKolRecallItem;
  index: number;
  onOpen?: (item: VkpiKolRecallItem) => void;
  // 发现网格用:右上角有绝对定位勾选框时传 pr-6 留槽,徽章不再被压在勾选框下(UI 红圈①)。
  className?: string;
  /** F4 最小标注:传 source 才渲染 👍/👎(发现墙=discovery_wall);需登录 token。 */
  feedbackSource?: SearchFeedbackSource;
  feedbackToken?: string;
}) {
  const [failedAvatar, setFailedAvatar] = useState("");
  const avatar = proxiedImageUrl(item.avatar_url);
  const name = candidateText(readableCreatorName(item)) || (item.kol_pool_id ? `KOL #${item.kol_pool_id}` : "候选达人");
  const rank = candidateRankSummary(item);
  const relevanceFlags = Array.isArray(item.relevance_flags) ? item.relevance_flags.map(cleanText).filter(Boolean) : [];
  // Bind failure to the URL, not the card instance: polling can replace an
  // expired signed avatar with a refreshed URL without remounting the card.
  const showImg = Boolean(avatar) && failedAvatar !== avatar;
  const evidence = candidateEvidenceSummary(item);
  // 三引擎产出·候选卡展示信号(全部纯只读透传,绝不触评分):
  const itemRow = item as unknown as Row;
  const fitSrc = (item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {}) as Row;
  const profileExecute = (fitSrc.profile_execute && typeof fitSrc.profile_execute === "object" ? fitSrc.profile_execute : {}) as Row;
  const contactPreview = (fitSrc.contact_preview && typeof fitSrc.contact_preview === "object"
    ? fitSrc.contact_preview
    : profileExecute.contact_enrichment && typeof profileExecute.contact_enrichment === "object"
      ? profileExecute.contact_enrichment
      : {}) as Row;
  const audiencePreview = (fitSrc.audience_preview && typeof fitSrc.audience_preview === "object" ? fitSrc.audience_preview : {}) as Row;
  const contactEmail = cleanText(contactPreview.email);
  const contactReady = cleanText(contactPreview.status) === "ready" || Boolean(contactEmail) || Number(contactPreview.found) > 0;
  const audienceMethod = candidateText(audiencePreview.method);
  const audienceReady = cleanText(audiencePreview.status) === "ready" && Boolean(audienceMethod);
  const fitBadge = contentFitBadge(itemRow.fit_verdict ?? fitSrc.fit_verdict);
  const creatorType = cleanText(itemRow.creator_type ?? fitSrc.creator_type);
  const marks = freshnessMarks(item);
  const platform = candidateText(item.platform);
  const country = candidateText(itemRow.country ?? fitSrc.country);
  const language = candidateText(itemRow.language ?? fitSrc.language);
  const profileType = candidateText(item.type_label || item.profile_type);
  const identityMeta = [platform, country, language, profileType].filter(Boolean);
  // 粉丝数待核(2026-08-22):发现面 followers 未知照常上墙,诚实标注、不藏、不假排队。
  // 判据=后端读端标 reach_status=analyzing,或发现项(platform_discovery)快照缺粉丝数。
  const followersPending = !numberLabel(item.followers)
    && (cleanText(fitSrc.reach_status) === "analyzing" || cleanText(fitSrc.source) === "platform_discovery");
  const observedMetrics = [
    { key: "followers", label: "粉丝", value: numberLabel(item.followers) },
    { key: "views", label: "均播", value: numberLabel(itemRow.avg_views ?? fitSrc.avg_views) },
    { key: "likes", label: "均赞", value: numberLabel(itemRow.avg_likes ?? fitSrc.avg_likes) },
    { key: "comments", label: "均评", value: numberLabel(itemRow.avg_comments ?? fitSrc.avg_comments) },
    { key: "engagement", label: "互动", value: rateLabel(itemRow.engagement_rate ?? fitSrc.engagement_rate) },
  ].filter((metric) => metric.value);
  const representativeEvidence = (Array.isArray(item.representative_evidence) ? item.representative_evidence : [])
    .map((entry, evidenceIndex) => {
      const url = candidateHttpUrl(entry?.content_url);
      const views = positiveMetric(entry?.view_count);
      const likes = positiveMetric(entry?.like_count);
      if (!url || (views == null && likes == null)) return null;
      return {
        key: `${url}-${evidenceIndex}`,
        url,
        title: candidateText(entry?.title) || `代表内容 ${evidenceIndex + 1}`,
        metricLabel: [
          views != null ? `${numberLabel(views)}播` : "",
          likes != null ? `${numberLabel(likes)}赞` : "",
        ].filter(Boolean).join(" · "),
      };
    })
    .filter((entry): entry is { key: string; url: string; title: string; metricLabel: string } => Boolean(entry))
    .slice(0, 3);
  const sourceLabel = candidateText(
    itemRow.source_label
      ?? itemRow.source_type
      ?? fitSrc.source_label
      ?? fitSrc.source_type
      ?? fitSrc.source,
  );
  const updatedLabel = compactDate(
    itemRow.updated_at
      ?? itemRow.last_seen_at
      ?? fitSrc.updated_at
      ?? fitSrc.last_seen_at
      ?? fitSrc.published_at,
  );
  const candidateBucket = cleanText(item.candidate_bucket ?? item.business_lane ?? item.candidate_lane);
  const candidateBucketReason = cleanText(item.candidate_bucket_reason);
  const laneLabel = candidateBucket === "core_vertical"
    ? "核心垂直"
    : candidateBucket === "expansion"
      ? "拓展型"
      : "";
  const matchTier = cleanText(item.match_tier);
  const missingLabels = evidence.missingLabels;
  const relaxedFilters = (Array.isArray(item.relaxed_filters) ? item.relaxed_filters : [])
    .map(cleanText)
    .filter(Boolean);
  const relevanceHits = Array.isArray(fitSrc.relevance_hits)
    ? (fitSrc.relevance_hits as unknown[]).map(cleanText).filter(Boolean).slice(0, 4)
    : [];
  const hasOptionalDetails = evidence.grade !== "missing"
    || rank.score != null
    || Boolean(sourceLabel)
    || Boolean(updatedLabel)
    || representativeEvidence.length > 0
    || Boolean(fitBadge)
    || relevanceHits.length > 0
    || relevanceFlags.length > 0;
  return (
    <div
      data-testid="kol-recall-card"
      data-kol-pool-id={item.kol_pool_id || undefined}
      data-candidate-bucket={candidateBucket || "legacy"}
      data-match-tier={matchTier || "unknown"}
      className={`group relative h-full min-w-0 overflow-hidden rounded-lg border border-white/[0.06] bg-white/[0.015] text-left transition-all hover:border-cyan-300/25 hover:bg-cyan-400/[0.04] ${className}`}
    >
      {/* F4 最小标注:主体是 <button>,控件不能嵌套其中 → 绝对定位在右上角(未入库项无 kol_pool_id 自动不渲染)。 */}
      {feedbackSource && feedbackToken && item.kol_pool_id ? (
        <SearchFeedbackControl
          source={feedbackSource}
          kolPoolId={item.kol_pool_id}
          sessionItemId={item.session_item_id ?? null}
          apiToken={feedbackToken}
          className="absolute right-1.5 top-1.5"
        />
      ) : null}
      <button
        type="button"
        onClick={() => onOpen?.(item)}
        className={`flex w-full min-w-0 items-start gap-2.5 px-2.5 py-2 text-left focus:outline-none focus:ring-1 focus:ring-inset focus:ring-cyan-300/30${feedbackSource && feedbackToken && item.kol_pool_id ? " pr-14" : ""}`}
        title={`${evidence.reasonLabel}：${evidence.reason}`}
      >
        <span className="mt-1 w-3.5 shrink-0 text-center text-[9px] font-medium tabular-nums text-slate-600">{index}</span>
        <span
          className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full text-[12px] font-bold text-white"
          style={{ background: "linear-gradient(135deg,#7c3aed,#06b6d4)" }}
        >
          {showImg ? (
            <img
              src={avatar}
              alt=""
              className="h-full w-full rounded-full object-cover"
              referrerPolicy="no-referrer"
              onError={() => setFailedAvatar(avatar)}
              onLoad={(event) => {
                const img = event.currentTarget;
                if (img.naturalWidth <= 2 && img.naturalHeight <= 2) setFailedAvatar(avatar);
              }}
            />
          ) : (
            name.slice(0, 1).toUpperCase()
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate text-[11.5px] font-medium text-slate-100 group-hover:text-white">{name}</span>
            {marks.newcomer ? (
              <span className="shrink-0 rounded border border-emerald-300/25 bg-emerald-400/[0.08] px-1 text-[8px] font-medium text-emerald-100">新人</span>
            ) : null}
            {marks.fresh ? (
              <span className="shrink-0 rounded border border-cyan-300/20 bg-cyan-400/[0.06] px-1 text-[8px] text-cyan-100/85">近期活跃</span>
            ) : null}
          </span>
          {identityMeta.length ? (
            <span data-testid="candidate-identity-meta" className="mt-0.5 block truncate text-[9.5px] text-slate-500">
              {identityMeta.join(" · ")}
            </span>
          ) : null}
          {observedMetrics.length || followersPending ? (
            <span data-testid="candidate-observed-metrics" className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[9px] text-slate-300" title="仅展示后端已返回的正值；缺失指标不占位">
              {followersPending ? (
                <span data-testid="candidate-followers-pending" className="rounded border border-amber-300/25 bg-amber-400/[0.08] px-1 text-amber-200/90" title="粉丝数尚未核实:已自动入库并排队补全,核实后自动更新">粉丝数待核</span>
              ) : null}
              {observedMetrics.map((metric) => (
                <span key={metric.key}><span className="text-slate-600">{metric.label}</span> {metric.value}</span>
              ))}
            </span>
          ) : null}
          {representativeEvidence.length ? (
            <span data-testid="candidate-representative-evidence-summary" className="mt-1 block truncate text-[9px] text-cyan-100/75" title="点击下方数据依据可展开原内容证据">
              代表内容：{representativeEvidence[0].metricLabel}
            </span>
          ) : null}
          {(laneLabel || matchTier === "backfill" || relaxedFilters.length || contactReady || audienceReady || missingLabels.length) ? (
            <span className="mt-1 flex min-w-0 flex-wrap items-center gap-1">
              {laneLabel ? (
                <span title={candidateBucketReason || undefined} className={`rounded-full border px-1.5 py-0.5 text-[8.5px] font-medium ${candidateBucket === "core_vertical"
                  ? "border-violet-300/30 bg-violet-400/[0.10] text-violet-100"
                  : "border-cyan-300/30 bg-cyan-400/[0.10] text-cyan-100"}`}>{laneLabel}</span>
              ) : null}
              {matchTier === "backfill" || relaxedFilters.length ? (
                <span className="rounded border border-amber-300/30 bg-amber-400/[0.09] px-1 text-[8.5px] font-medium text-amber-100" title={relaxedFilters.length ? `仅放宽相关性补位：${relaxedFilters.join("、")}` : "严格相关结果不足后的补位；显式硬筛选仍须满足"}>补位</span>
              ) : null}
              {contactReady ? (
                <span className="inline-flex max-w-full items-center gap-1 rounded border border-emerald-300/25 bg-emerald-400/[0.08] px-1 text-[8.5px] font-medium text-emerald-100/90" title={contactEmail ? `公开联系方式：${contactEmail}` : "已找到公开联系方式"}>
                  <Mail size={8} /> <span className="max-w-[120px] truncate">{contactEmail || "联系方式"}</span>
                </span>
              ) : null}
              {audienceReady ? (
                <span className="inline-flex items-center gap-1 rounded border border-violet-300/25 bg-violet-400/[0.08] px-1 text-[8.5px] font-medium text-violet-100/90" title={`受众估算已返回 · 方法 ${audienceMethod}${audiencePreview.confidence != null ? ` · 置信值 ${audiencePreview.confidence}` : ""}`}>
                  <Users size={8} /> 受众估算
                </span>
              ) : null}
              {missingLabels.length ? (
                <span data-testid="candidate-completion-action" className="rounded border border-slate-300/15 bg-white/[0.025] px-1 text-[8.5px] text-slate-400" title={`建议补全：${missingLabels.join("、")}`}>
                  补全关键资料 · {missingLabels.length} 项
                </span>
              ) : null}
            </span>
          ) : null}
          <span data-testid="candidate-recommendation-reason" className={`mt-1 line-clamp-2 block text-[10px] leading-snug ${evidence.onlyCandidate ? "text-amber-100/85" : "text-cyan-200/85"}`}>
            <span className="font-semibold">{evidence.reasonLabel}：</span>{evidence.reason}
          </span>
        </span>
      </button>
      {hasOptionalDetails ? (
        <details data-testid="candidate-secondary-details" className="border-t border-white/[0.045] px-2.5 py-1.5 text-[8.5px] text-slate-500">
          <summary className="flex cursor-pointer list-none items-center gap-1 select-none hover:text-slate-300">
            <Info size={9} /> 数据依据
            {sourceLabel ? <span>· {sourceLabel}</span> : null}
            {updatedLabel ? <span>· 更新 {updatedLabel}</span> : null}
          </summary>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {evidence.grade !== "missing" ? (
              <span data-testid="candidate-evidence-grade" title={`${evidence.gradeDetail}；这是证据覆盖说明，不代表业务结果`} className="rounded border border-sky-300/20 bg-sky-400/[0.06] px-1 py-0.5 text-sky-100/85">{evidence.gradeLabel}</span>
            ) : null}
            {rank.score != null ? (
              <span data-testid="candidate-rank-signal" title={rank.detail} className="rounded border border-white/[0.07] bg-white/[0.02] px-1 py-0.5 text-slate-400">{rank.scoreLabel} {rank.score.toFixed(2)}</span>
            ) : null}
            {fitBadge ? (
              <span className={`rounded-full border px-1.5 py-0.5 font-medium ${fitBadge.cls}`} title={creatorType ? `内容契合：${fitBadge.label} · ${creatorType}` : `内容契合判定：${fitBadge.label}`}>契合·{fitBadge.label}</span>
            ) : null}
            {relevanceHits.map((hit, hitIndex) => (
              <button key={`${hit}-${hitIndex}`} type="button" onClick={(event) => applyPoolLocalFilter(hit, event)} title={`以“${hit}”筛选 KOL Pool`} className="rounded border border-sky-300/20 bg-sky-400/[0.05] px-1 py-0.5 text-sky-100/80 hover:border-sky-300/45">{zhTag(hit)}</button>
            ))}
            {relevanceFlags.map((flag) => (
              <span key={flag} className="rounded border border-amber-300/20 bg-amber-400/[0.05] px-1 py-0.5 text-amber-200/80">{zhTag(flag)}</span>
            ))}
          </div>
          {representativeEvidence.length ? (
            <div data-testid="candidate-representative-evidence-links" className="mt-1.5 space-y-1 border-t border-white/[0.04] pt-1.5">
              {representativeEvidence.map((entry) => (
                <a key={entry.key} href={entry.url} target="_blank" rel="noreferrer noopener" className="flex min-w-0 items-center justify-between gap-2 rounded px-1 py-0.5 text-slate-400 hover:bg-white/[0.03] hover:text-cyan-100">
                  <span className="truncate">{entry.title}</span>
                  <span className="shrink-0 tabular-nums">{entry.metricLabel}</span>
                </a>
              ))}
            </div>
          ) : null}
        </details>
      ) : null}
    </div>
  );
}

export function PlanPills({ plan }: { plan: Row }) {
  const searchQuery = display(plan.search_query);
  const clarification = plan.clarification && typeof plan.clarification === "object" ? plan.clarification as Row : {};
  const suggestions = Array.isArray(clarification.suggestions) ? clarification.suggestions.slice(0, 6) as Row[] : [];
  if (cleanText(plan.status) === "needs_clarification") {
    return (
      <div className="mb-2 rounded-md border border-amber-300/25 bg-amber-400/[0.07] px-2.5 py-2 text-amber-50">
        <div className="flex items-start gap-2 text-[10.5px] leading-relaxed">
          <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-300" />
          <span>{display(clarification.message, "没有在产品目录中找到这个明确型号，请先确认产品名称。")}</span>
        </div>
        {suggestions.length ? (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {suggestions.map((item) => (
              <span key={display(item.sku)} className="rounded border border-amber-200/15 bg-black/10 px-1.5 py-0.5 text-[9.5px] text-amber-100/80">
                {display(item.name || item.sku)}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    );
  }
  // 产品定位(说人话):优先 LLM product_positioning,缺则用 persona。
  const positioning = display(plan.product_positioning, "") || display(plan.target_persona, "");
  const persona = display(plan.target_persona, "");
  const focus = Array.isArray(plan.product_focus) ? plan.product_focus.map(cleanText).filter(Boolean).slice(0, 4) : [];
  const avoid = Array.isArray(plan.avoid_types) ? plan.avoid_types.map(cleanText).filter(Boolean).slice(0, 4) : [];
  return (
    <div className="mb-2 rounded-md border border-cyan-300/12 bg-cyan-400/[0.045] px-2.5 py-2">
      {/* 产品定位:这是什么产品、价位、给谁的(说人话,不暴露 SKU 技术腔) */}
      {positioning ? (
        <div className="text-[10.5px] leading-relaxed text-slate-200">{positioning}</div>
      ) : null}
      {searchQuery ? <div className="mt-1 truncate text-[10px] text-slate-500">检索词:{searchQuery}</div> : null}
      {persona && persona !== positioning ? (
        <div className="mt-0.5 truncate text-[9.5px] text-slate-600">{persona}</div>
      ) : null}
      {focus.length ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          <span className="self-center text-[9px] text-emerald-300/70">理想</span>
          {focus.map((item) => (
            <span key={item} className="rounded border border-emerald-300/15 bg-emerald-400/[0.07] px-1.5 py-0.5 text-[9.5px] text-emerald-100/80">
              {item}
            </span>
          ))}
        </div>
      ) : null}
      {avoid.length ? (
        <div className="mt-1 flex flex-wrap gap-1">
          <span className="self-center text-[9px] text-rose-300/70">规避</span>
          {avoid.map((item) => (
            <span key={item} className="rounded border border-rose-300/15 bg-rose-400/[0.07] px-1.5 py-0.5 text-[9.5px] text-rose-100/75 line-through decoration-rose-300/40">
              {item}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// #25 bio 行:英文 bio 显「译中文」按钮 → translateBio(后端预算闸+按原文缓存);译空回退原文,中文 bio 不显按钮。
function BioLine({ bio, apiToken }: { bio: string; apiToken?: string }) {
  const stateKey = `${apiToken || ""}\u0000${bio}`;
  const requestGeneration = useRef(0);
  const currentKey = useRef(stateKey);
  if (currentKey.current !== stateKey) {
    currentKey.current = stateKey;
    requestGeneration.current += 1;
  }
  const [stored, setStored] = useState({ key: stateKey, zh: "", busy: false, tried: false });
  // 渲染时先按 bio/token 隔离，A→B 的首帧不会短暂显示 A 的译文；generation 再拦截旧 Promise 回写。
  const current = stored.key === stateKey ? stored : { key: stateKey, zh: "", busy: false, tried: false };
  const { zh, busy, tried } = current;
  const translate = async (ev: any) => {
    if (ev && ev.stopPropagation) ev.stopPropagation();
    if (busy || tried || !apiToken || !bio) return;
    const requestId = ++requestGeneration.current;
    const requestKey = stateKey;
    setStored({ key: requestKey, zh: "", busy: true, tried: false });
    try {
      const res: any = await translateBio(apiToken, bio);
      if (
        currentKey.current === requestKey &&
        requestGeneration.current === requestId &&
        res && res.translated
      ) {
        setStored({ key: requestKey, zh: res.translated, busy: false, tried: true });
        return;
      }
    } catch { /* 回退原文 */ }
    if (currentKey.current === requestKey && requestGeneration.current === requestId) {
      setStored({ key: requestKey, zh: "", busy: false, tried: true });
    }
  };
  return (
    <div className="mt-1">
      <p className="line-clamp-2 text-[10.5px] leading-relaxed text-slate-400">{zh || bio}</p>
      {!zh && /[A-Za-z]/.test(bio) ? (
        <button
          type="button"
          onClick={translate}
          disabled={busy || tried}
          className="mt-0.5 text-[9px] text-cyan-300/70 hover:text-cyan-200 disabled:text-slate-600"
        >
          {busy ? "翻译中…" : tried ? "(暂不可译)" : "译中文"}
        </button>
      ) : null}
    </div>
  );
}

export function ProfileInfoCard({ data, onOpen, apiToken }: { data: Row; onOpen?: () => void; apiToken?: string }) {
  const [failedAvatar, setFailedAvatar] = useState("");
  const avatar = proxiedImageUrl(cleanText(data.avatar_url));
  const handle = kolHumanPublicHandle(data);
  const platform = cleanText(data.platform);
  const name = kolHumanDisplayName(data, platform || "创作者");
  const followers = numberLabel(data.followers);
  const posts = numberLabel(data.posts_count);
  const bio = cleanText(data.bio);
  const profileUrl = cleanText(data.profile_url);
  const profileLinkLabel = kolHumanProfileLinkLabel(data);
  const showImg = Boolean(avatar) && failedAvatar !== avatar;
  const clickable = Boolean(onOpen);
  // P7:可点卡片打开右侧 KOL 详情抽屉。用 div+role 而非 <button>,避免把内部的真链接 <a> 嵌进按钮(非法嵌套)。
  return (
    <div
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable ? onOpen : undefined}
      onKeyDown={clickable ? (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen?.(); } } : undefined}
      title={clickable ? "打开 KOL 详情" : undefined}
      className={`mt-2 flex items-start gap-3 rounded-md border border-white/[0.07] bg-black/20 px-2.5 py-2${clickable ? " cursor-pointer transition-colors hover:border-cyan-300/30 hover:bg-cyan-400/[0.04] focus:outline-none focus:ring-1 focus:ring-cyan-300/30" : ""}`}
    >
      <span
        className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full text-[14px] font-bold text-white"
        style={{ background: "linear-gradient(135deg,#7c3aed,#06b6d4)" }}
      >
        {showImg ? (
          <img
            src={avatar}
            alt=""
            className="h-full w-full rounded-full object-cover"
            referrerPolicy="no-referrer"
            onError={() => setFailedAvatar(avatar)}
            // 同 RecallMiniItem:识破 1×1 透明失败占位,诚实退回首字母(不摆假头像)。
            onLoad={(event) => {
              const img = event.currentTarget;
              if (img.naturalWidth <= 2 && img.naturalHeight <= 2) setFailedAvatar(avatar);
            }}
          />
        ) : (
          name.slice(0, 1).toUpperCase()
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate text-[12px] font-medium text-slate-100">{name}</span>
          {platform ? (
            <span className="shrink-0 rounded border border-white/[0.08] px-1 text-[9px] text-slate-400">{platform}</span>
          ) : null}
          {followers ? (
            <span className="shrink-0 rounded bg-amber-400/[0.10] px-1 text-[9px] font-semibold text-amber-200/90">{followers} 粉</span>
          ) : null}
          {posts ? (
            <span className="shrink-0 rounded bg-cyan-400/[0.10] px-1 text-[9px] font-semibold text-cyan-200/90">{posts} 帖</span>
          ) : null}
        </div>
        {bio ? <BioLine bio={bio} apiToken={apiToken} /> : null}
        {profileUrl ? (
          <a
            href={profileUrl}
            target="_blank"
            rel="noreferrer noopener"
            onClick={(event) => event.stopPropagation()}
            title={profileLinkLabel}
            className="mt-1 inline-block truncate text-[10px] text-cyan-300/80 hover:text-cyan-200 hover:underline"
          >
            {profileLinkLabel}
          </a>
        ) : null}
      </div>
      {clickable ? (
        <span className="shrink-0 self-center text-[9px] text-cyan-300/70">查看详情 →</span>
      ) : null}
    </div>
  );
}
