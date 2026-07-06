// SmartKolInputPanel 展示型子组件 + 会话/召回派生器(从 SmartKolInputPanel.tsx 抽出,行为不变)。
// 容器组件本体(useState/useEffect/run 等)仍留 SmartKolInputPanel.tsx,这里只放纯派生函数 +
// 仅吃 props 的展示组件(各自局部 useState 不破坏容器 hooks 顺序)。容器 import 回去,调用点不变。
// 红线:纯展示/派生,只读 final_v1/QA 缓存,绝不写任何 viltrox_fit_score。
import { useState } from "react";
import { Clock3, TrendingUp, UserPlus } from "lucide-react";

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
  historyStatusMeta,
  isSearchSessionTerminal,
  looksLikeRetailer,
  readPersistedSearchDisplay,
  readableCreatorName,
  recallResultFromSession,
  recallTopItems,
  relativeTime,
  relevanceTier,
  sessionAdvanceCounts,
  sessionItems,
  sessionStatusBanner,
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
  readPersistedSearchDisplay,
  recallResultFromSession,
  recallTopItems,
  sessionAdvanceCounts,
  sessionItems,
  sessionStatusBanner,
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
  loading,
  onOpen,
}: {
  items: VkpiKolSearchHistoryItem[];
  loading: boolean;
  onOpen: (session: VkpiKolSearchHistoryItem) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!items.length && !loading) return null;
  // 收缩态:只显示「最近历史」标题(不渲染记录);展开才出列表。
  const shown = expanded ? items : [];
  return (
    <div className="mt-2 rounded-lg border border-white/[0.055] bg-black/15 px-2.5 py-2">
      <div className={`flex items-center justify-between gap-2${expanded ? " mb-1.5" : ""}`}>
        <button
          type="button"
          onClick={() => setExpanded((x) => !x)}
          className="inline-flex items-center gap-1.5 text-[10px] font-medium text-slate-300 hover:text-cyan-100"
        >
          <Clock3 size={11} className="text-slate-500" />
          最近历史
          {items.length ? <span className="text-[9px] text-slate-600">· {items.length}</span> : null}
        </button>
        <div className="inline-flex items-center gap-2">
          {loading ? <span className="text-[9.5px] text-slate-600">同步中</span> : null}
          {items.length ? (
            <button
              type="button"
              onClick={() => setExpanded((x) => !x)}
              className="text-[9.5px] font-medium text-slate-500 hover:text-cyan-200"
            >
              {expanded ? "收起" : `展开 ${items.length} 条`}
            </button>
          ) : null}
        </div>
      </div>
      <div className={expanded ? "space-y-1" : ""}>
        {shown.map((item) => {
          const sessionId = historySessionId(item);
          const label = historyLabel(item);
          const kind = historyKindMeta(item);
          const st = historyStatusMeta(item.status || "ready");
          const when = relativeTime(item.updated_at || item.created_at);
          return (
            <button
              key={`${sessionId || label}-${item.updated_at || item.created_at || ""}`}
              type="button"
              onClick={() => onOpen(item)}
              className="group flex w-full items-center gap-2 rounded-md border border-white/[0.05] bg-white/[0.015] px-2 py-1.5 text-left transition-colors hover:border-cyan-300/25 hover:bg-cyan-400/[0.04]"
              title={`${kind.label} · ${label} · ${st.label}`}
            >
              <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[8.5px] font-semibold ${kind.cls}`}>{kind.label}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-slate-300 group-hover:text-cyan-100">{label}</span>
              {when ? <span className="shrink-0 text-[9px] text-slate-600">{when}</span> : null}
              <span className={`inline-flex shrink-0 items-center gap-1 text-[9.5px] font-medium ${st.cls}`}>
                <span className="h-1 w-1 rounded-full" style={{ background: st.dot }} />
                {st.label}
              </span>
            </button>
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
      <div className="mt-1 truncate text-[10px] text-slate-500">检索词:{searchQuery}</div>
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
  const [zh, setZh] = useState("");
  const [busy, setBusy] = useState(false);
  const [tried, setTried] = useState(false);
  const translate = async (ev: any) => {
    if (ev && ev.stopPropagation) ev.stopPropagation();
    if (busy || tried || !apiToken || !bio) return;
    setBusy(true);
    try {
      const res: any = await translateBio(apiToken, bio);
      if (res && res.translated) setZh(res.translated);
    } catch { /* 回退原文 */ }
    finally { setBusy(false); setTried(true); }
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
  const [imgError, setImgError] = useState(false);
  const avatar = proxiedImageUrl(cleanText(data.avatar_url));
  const handle = cleanText(data.handle);
  const platform = cleanText(data.platform);
  const name = display(handle || platform || "账户");
  const followers = numberLabel(data.followers);
  const posts = numberLabel(data.posts_count);
  const bio = cleanText(data.bio);
  const profileUrl = cleanText(data.profile_url);
  const showImg = Boolean(avatar) && !imgError;
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
            onError={() => setImgError(true)}
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
