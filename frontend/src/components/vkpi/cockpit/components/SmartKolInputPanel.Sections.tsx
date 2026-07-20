// SmartKolInputPanel 展示型子组件 + 会话/召回派生器(从 SmartKolInputPanel.tsx 抽出,行为不变)。
// 容器组件本体(useState/useEffect/run 等)仍留 SmartKolInputPanel.tsx,这里只放纯派生函数 +
// 仅吃 props 的展示组件(各自局部 useState 不破坏容器 hooks 顺序)。容器 import 回去,调用点不变。
// 红线:纯展示/派生,只读 final_v1/QA 缓存,绝不写任何 viltrox_fit_score。
import { useRef, useState } from "react";
import { AlertTriangle, Clock3, Mail, RotateCcw, Search, Trash2, TrendingUp, UserPlus, Users } from "lucide-react";

import {
  type VkpiKolRecallItem,
  type VkpiKolSearchHistoryItem,
} from "../../../../domains/kol";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import { translateBio } from "../../../../services/vkpi/kolPool-api";

import {
  cleanText,
  display,
  numberLabel,
  type Row,
} from "./SmartKolInputPanel.helpers";

// 纯派生器 / 常量 / 类型已再抽到 SmartKolInputPanel.derivers.ts(行为不变;展示子组件留此文件)。
// 容器仍从本文件 import 这些名字,故此处 re-export 维持调用面不变。
import {
  PENDING_SEARCH_SESSION_KEY,
  PROFILE_REP_VIDEO_LIMIT,
  contentFitBadge,
  discoveryAutoEnrolledFromSession,
  discoveryItemsFromSession,
  exposureLabel,
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
  relevanceTier,
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
}: {
  item: VkpiKolRecallItem;
  index: number;
  onOpen?: (item: VkpiKolRecallItem) => void;
  // 发现网格用:右上角有绝对定位勾选框时传 pr-6 留槽,徽章不再被压在勾选框下(UI 红圈①)。
  className?: string;
}) {
  const [imgError, setImgError] = useState(false);
  const avatar = proxiedImageUrl(item.avatar_url);
  const name = display(readableCreatorName(item) || `KOL #${item.kol_pool_id}`);
  const followers = numberLabel(item.followers);
  const score = Number(item.recall_rank_score ?? item.vector_score ?? 0);
  const relevanceFlags = Array.isArray(item.relevance_flags) ? item.relevance_flags.map(cleanText).filter(Boolean) : [];
  const tierDemoted = cleanText(item.relevance_tier_hint) === "demote";
  const tier = relevanceTier(score, tierDemoted);
  const showImg = Boolean(avatar) && !imgError;
  const whyFit = cleanText(item.why_fit);
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
  const audienceReady = cleanText(audiencePreview.status) === "ready";
  const fitBadge = contentFitBadge(itemRow.fit_verdict ?? fitSrc.fit_verdict);
  const creatorType = cleanText(itemRow.creator_type ?? fitSrc.creator_type);
  const exposure = exposureLabel(item);
  const marks = freshnessMarks(item);
  return (
    <button
      type="button"
      onClick={() => onOpen?.(item)}
      className={`group flex h-full min-w-0 items-start gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.015] px-2.5 py-2 text-left transition-all hover:border-cyan-300/25 hover:bg-cyan-400/[0.04] focus:outline-none focus:ring-1 focus:ring-cyan-300/30 ${className}`}
      title={whyFit ? `${whyFit} · 相关度 ${score.toFixed(3)}` : `打开 KOL 详情 · 相关度 ${score.toFixed(3)}`}
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
            onError={() => setImgError(true)}
          />
        ) : (
          name.slice(0, 1).toUpperCase()
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-[11.5px] font-medium text-slate-100 group-hover:text-white">{name}</span>
          {followers ? (
            <span className="shrink-0 rounded bg-amber-400/[0.10] px-1 text-[9px] font-semibold text-amber-200/90">{followers}</span>
          ) : null}
        </span>
        <span className="mt-0.5 block truncate text-[9.5px] text-slate-500">
          {display(item.platform, "unknown")} · {item.type_label || item.profile_type || "profile"}{followers ? ` · ${followers} 粉/播` : ""}
        </span>
        {(contactReady || audienceReady) ? (
          <span className="mt-1 flex min-w-0 flex-wrap items-center gap-1">
            {contactReady ? (
              <span
                className="inline-flex max-w-full items-center gap-1 rounded border border-emerald-300/25 bg-emerald-400/[0.08] px-1 text-[8.5px] font-medium text-emerald-100/90"
                title={contactEmail ? `公开联系方式: ${contactEmail}` : "已找到公开联系方式"}
              >
                <Mail size={8} /> <span className="max-w-[150px] truncate">{contactEmail || "联系方式已找到"}</span>
              </span>
            ) : null}
            {audienceReady ? (
              <span
                className="inline-flex items-center gap-1 rounded border border-violet-300/25 bg-violet-400/[0.08] px-1 text-[8.5px] font-medium text-violet-100/90"
                title={`受众估算 · ${cleanText(audiencePreview.method) || "来源待标注"}${audiencePreview.confidence != null ? ` · 置信度 ${audiencePreview.confidence}` : ""}`}
              >
                <Users size={8} /> 受众可看
              </span>
            ) : null}
          </span>
        ) : null}
        {/* 三引擎徽章行:内容契合判定(已深析)+ 预估曝光 + 新人/新鲜/低合作 */}
        {(fitBadge || exposure || marks.newcomer || marks.fresh || marks.lowCollab) ? (
          <span className="mt-1 flex flex-wrap items-center gap-1">
            {fitBadge ? (
              <span className={`rounded-full border px-1.5 py-0.5 text-[8.5px] font-medium ${fitBadge.cls}`} title={creatorType ? `内容契合:${fitBadge.label} · ${creatorType}` : `内容契合判定:${fitBadge.label}`}>
                契合·{fitBadge.label}
              </span>
            ) : null}
            {exposure ? (
              <span className="inline-flex items-center gap-0.5 rounded border border-sky-300/25 bg-sky-400/[0.08] px-1 text-[8.5px] font-medium text-sky-100/90" title="预估曝光/触达潜力(均播放量,纯展示)">
                <TrendingUp size={8} /> {exposure} 曝光
              </span>
            ) : null}
            {marks.newcomer ? (
              <span className="inline-flex items-center gap-0.5 rounded border border-emerald-300/30 bg-emerald-400/[0.10] px-1 text-[8.5px] font-medium text-emerald-100" title="全网新发现、库内尚无 · 优先新人">
                <UserPlus size={8} /> 新人
              </span>
            ) : null}
            {marks.fresh ? (
              <span className="rounded border border-cyan-300/25 bg-cyan-400/[0.08] px-1 text-[8.5px] font-medium text-cyan-100/90" title="近 90 天有新作">新鲜</span>
            ) : null}
            {marks.lowCollab && !marks.newcomer ? (
              <span className="rounded border border-violet-300/25 bg-violet-400/[0.08] px-1 text-[8.5px] font-medium text-violet-100/90" title="低合作 = 历史合作少(库内无合作记录),越新鲜越好 · 成长空间大">低合作</span>
            ) : null}
          </span>
        ) : null}
        {whyFit ? (
          <span className="mt-1 line-clamp-2 block text-[10px] leading-snug text-cyan-200/85">{whyFit}</span>
        ) : null}
        {Array.isArray(fitSrc.relevance_hits) && (fitSrc.relevance_hits as unknown[]).length ? (
          <span className="mt-1 inline-flex flex-wrap items-center gap-1" title="persona 相关度命中词(为何契合)">
            <span className="text-[8.5px] text-slate-500">契合命中</span>
            {/* 【K2】chip 可点:点击把命中词写入 KOL Pool 本地筛选(卡片本体是 button,故用 span+role 阻断冒泡) */}
            {(fitSrc.relevance_hits as unknown[]).slice(0, 4).map((h, i) => (
              <span
                key={`${cleanText(h)}-${i}`}
                role="button"
                tabIndex={0}
                onClick={(ev) => applyPoolLocalFilter(cleanText(h), ev)}
                onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); applyPoolLocalFilter(cleanText(h), ev); } }}
                title={`点击 → 以「${cleanText(h)}」筛选本地 KOL Pool 列表`}
                className="cursor-pointer rounded border border-sky-300/25 bg-sky-400/[0.08] px-1 text-[8.5px] font-medium text-sky-100/90 transition-colors hover:border-sky-300/50 hover:bg-sky-400/[0.18]"
              >
                {zhTag(cleanText(h))}
              </span>
            ))}
          </span>
        ) : null}
        {relevanceFlags.length ? (
          <span className="mt-1 flex flex-wrap gap-1">
            {relevanceFlags.map((flag) => (
              <span key={flag} className="rounded border border-amber-300/25 bg-amber-400/[0.08] px-1 text-[8.5px] font-medium text-amber-200/85">
                {zhTag(flag)}
              </span>
            ))}
          </span>
        ) : null}
      </span>
      {/* 【K2】徽章解释:title 说明分档口径(高相关=向量相似度头部),附本条裸相似度供细看;
          demote 降档时(相似度达高档但后端标记降位)如实说明,不套用「中段 0.3–0.6」口径。 */}
      <span
        className={`mt-1 flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9px] font-medium ${tier.cls}`}
        title={
          tier.label === "高相关"
            ? `高相关 = 向量相似度头部(≥0.6)· 本条相似度 ${score.toFixed(3)}`
            : tier.label === "中相关"
              ? (tierDemoted && score >= 0.6
                  ? `中相关(降档):相似度 ${score.toFixed(3)} 达高档,但后端按内容形态错配标记降位,封顶中相关`
                  : `中相关 = 向量相似度中段(0.3–0.6)· 本条相似度 ${score.toFixed(3)}`)
              : `相关 = 向量相似度 <0.3 · 本条相似度 ${score.toFixed(3)}`
        }
      >
        <span className="h-1 w-1 rounded-full" style={{ background: tier.dot }} />
        {tier.label}
      </span>
    </button>
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
  const handle = cleanText(data.handle);
  const platform = cleanText(data.platform);
  const name = display(handle || platform || "账户");
  const followers = numberLabel(data.followers);
  const posts = numberLabel(data.posts_count);
  const bio = cleanText(data.bio);
  const profileUrl = cleanText(data.profile_url);
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
            className="mt-1 inline-block truncate text-[10px] text-cyan-300/80 hover:text-cyan-200 hover:underline"
          >
            {profileUrl}
          </a>
        ) : null}
      </div>
      {clickable ? (
        <span className="shrink-0 self-center text-[9px] text-cyan-300/70">查看详情 →</span>
      ) : null}
    </div>
  );
}
