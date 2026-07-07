// A1 KOL Pool 收口:默认视图 = 推荐卡片流(表格收进「表格视图」折叠)。
// 每卡:头像 / handle / 招牌拍法一行(复用既有 GET /api/admin/vkpi/kol-pool/{id}/signature,
// 懒加载 + 会话内缓存 + 并发限流)/ V6 Fit 徽章 / 粉丝数 / 点卡直接开详情抽屉。
// 红线:纯读展示,零新端点、零 LLM、零触 viltrox_fit_score / rule_v0;
// signature 拉取失败安静缺席(卡片如实显示「招牌拍法待深析」,绝不编造)。
import React from "react";
import { Star } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { CandidateKindChip } from "./CandidateKindChip";
import { KPAvatar } from "./KPAvatar";
import { PlatformPill } from "./PlatformPill";
import { V6FitBar } from "./V6FitBar";
import { formatNumber, formatPercent } from "../lib/format";
import { getCountryInfo } from "../data/countryInfo";

const e = React.createElement;

const PAGE_SIZE = 12;

// ── 会话级缓存 + 并发限流 ──
// 同一 KOL 的招牌一行整个会话只拉一次(翻页/切筛选不重复请求);失败也缓存 null
// (安静缺席,抽屉里的 SignaturePanel 仍会独立重新拉取,不受此缓存影响)。
// 并发上限 3:首屏 12 卡不齐射后端聚合端点。
const signatureLineCache = new Map<number, string | null>();
const signatureLineInflight = new Map<number, Promise<string | null>>();
const MAX_CONCURRENT_SIGNATURE_FETCHES = 3;
let activeSignatureFetches = 0;
const signatureWaiters: Array<() => void> = [];

function acquireSignatureSlot(): Promise<void> {
  if (activeSignatureFetches < MAX_CONCURRENT_SIGNATURE_FETCHES) {
    activeSignatureFetches += 1;
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    signatureWaiters.push(() => {
      activeSignatureFetches += 1;
      resolve();
    });
  });
}

function releaseSignatureSlot() {
  activeSignatureFetches = Math.max(0, activeSignatureFetches - 1);
  const next = signatureWaiters.shift();
  if (next) next();
}

// 招牌一行提取(三档诚实回退,字段口径与 SignaturePanel 完全一致,不另造):
// ① 🎬 TOP 拍摄模式(条数+均播放,需深析 ready)→ ② 💡 代表作 final_v1 一句话点评
// → ③ 🏆 最爆代表作标题+播放数(纯 evidence 聚合,真库当前主态)→ 皆无 = null(诚实空态)。
export function extractSignatureLine(payload: any): string | null {
  if (!payload || typeof payload !== "object") return null;
  if (String(payload.status || "") === "error") return null;
  const styles = payload.shooting_styles || {};
  const modes = Array.isArray(styles.modes) ? styles.modes : [];
  if (String(styles.status || "") === "ready" && modes.length) {
    const top = modes[0] || {};
    const label = String(top.label || top.key || "").trim();
    if (label) {
      const count = Number(top.video_count) || 0;
      const avgViews = top.avg_views != null && Number.isFinite(Number(top.avg_views))
        ? "均" + formatNumber(top.avg_views) + "播放"
        : "";
      return "🎬 " + label + (count ? ` ×${count}` : "") + (avgViews ? " · " + avgViews : "");
    }
  }
  const topVideos = payload.top_videos || {};
  const items = Array.isArray(topVideos.items) ? topVideos.items : [];
  const verdict = items.map((v: any) => String(v?.verdict || "").trim()).find(Boolean);
  if (verdict) return "💡 " + verdict;
  const best = items.find((v: any) => String(v?.title || "").trim());
  if (best) {
    const views = best.view_count != null && Number.isFinite(Number(best.view_count))
      ? " · " + formatNumber(best.view_count) + "播放"
      : "";
    return "🏆 代表作:" + String(best.title).trim() + views;
  }
  return null;
}

function fetchSignatureLine(apiToken: string, kolPoolId: number): Promise<string | null> {
  if (signatureLineCache.has(kolPoolId)) {
    return Promise.resolve(signatureLineCache.get(kolPoolId) ?? null);
  }
  const inflight = signatureLineInflight.get(kolPoolId);
  if (inflight) return inflight;
  const promise = acquireSignatureSlot()
    .then(() => apiFetch<any>(
      `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/signature`,
      {},
      apiToken,
    ))
    .then((payload) => {
      const line = extractSignatureLine(payload);
      signatureLineCache.set(kolPoolId, line);
      return line;
    })
    .catch(() => {
      signatureLineCache.set(kolPoolId, null);
      return null;
    })
    .finally(() => {
      releaseSignatureSlot();
      signatureLineInflight.delete(kolPoolId);
    });
  signatureLineInflight.set(kolPoolId, promise);
  return promise;
}

function realKolId(item: any): number | null {
  const raw = item?.id ?? item?.kol_pool_id;
  const id = Number(raw);
  return Number.isFinite(id) && id > 0 ? id : null;
}

// 招牌一行(懒加载):undefined=加载中骨架,null=诚实空态,string=真数据。
function SignatureLine({ apiToken, kolPoolId }: { apiToken?: string; kolPoolId: number | null }) {
  const [line, setLine] = React.useState<string | null | undefined>(() =>
    kolPoolId != null && signatureLineCache.has(kolPoolId) ? (signatureLineCache.get(kolPoolId) ?? null) : undefined,
  );
  React.useEffect(() => {
    if (!apiToken || kolPoolId == null) {
      setLine(null);
      return;
    }
    if (signatureLineCache.has(kolPoolId)) {
      setLine(signatureLineCache.get(kolPoolId) ?? null);
      return;
    }
    setLine(undefined);
    let cancelled = false;
    void fetchSignatureLine(apiToken, kolPoolId).then((value) => {
      if (!cancelled) setLine(value);
    });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  if (line === undefined) {
    return e("div", { className: "h-[13px] w-3/4 animate-pulse rounded bg-white/[0.05]" });
  }
  if (!line) {
    return e("div", { className: "truncate text-[10px] text-slate-600" }, "招牌拍法待深析(暂无深析样本)");
  }
  return e("div", { className: "truncate text-[10px] text-cyan-100/90", title: line }, line);
}

// 头像:有真实头像 URL 用 img(加载失败回退首字母),无则首字母色块(KPAvatar 同款)。
function CardAvatar({ item, avatarUrl }: { item: any; avatarUrl?: string }) {
  const [broken, setBroken] = React.useState(false);
  const url = String(avatarUrl || "").trim();
  if (url && !broken) {
    return e("img", {
      src: url,
      alt: item.handle || item.display_name || "KOL",
      referrerPolicy: "no-referrer",
      className: "h-9 w-9 shrink-0 rounded-full border border-white/10 object-cover",
      onError: () => setBroken(true),
    });
  }
  return e(KPAvatar, { name: item.display_name || item.handle, color: item.avatar_color, size: 36 });
}

function RecommendationCard({ item, apiToken, inMyList, onOpen, avatarUrl }: any) {
  const kolPoolId = realKolId(item);
  const flag = item.country ? (getCountryInfo(item.country)?.flag || "") : "";
  return e("div", {
    role: "button",
    tabIndex: 0,
    onClick: () => onOpen(item),
    onKeyDown: (ev: any) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        onOpen(item);
      }
    },
    title: "查看 " + (item.handle || item.display_name || "KOL") + " 详情",
    className: "group cursor-pointer rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 transition-colors hover:border-purple-400/30 hover:bg-purple-500/[0.05] focus:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/50",
  },
    // ── 头行:头像 + handle/平台 + Fit 徽章 ──
    e("div", { className: "flex items-start gap-2.5" },
      e(CardAvatar, { item, avatarUrl }),
      e("div", { className: "min-w-0 flex-1" },
        e("div", { className: "flex items-center gap-1.5" },
          e("span", { className: "truncate text-[12px] font-medium text-white" }, item.handle || item.display_name || "—"),
          inMyList && e(Star, { size: 10, className: "shrink-0 text-amber-400", style: { fill: "#fbbf24" } } as any),
        ),
        e("div", { className: "mt-0.5 flex flex-wrap items-center gap-1.5" },
          e(PlatformPill, { platform: item.platform }),
          e(CandidateKindChip, { kind: item.candidate_kind, size: "xs" }),
        ),
      ),
      e("div", { className: "shrink-0" }, e(V6FitBar, { score: item.v6_fit, kind: item.candidate_kind })),
    ),
    // ── 招牌拍法一行(既有 signature 聚合端点,失败安静缺席)──
    e("div", { className: "mt-2" }, e(SignatureLine, { apiToken, kolPoolId })),
    // ── 底行:粉丝 / Real ER / 国家 ──
    e("div", { className: "mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 border-t border-white/[0.045] pt-1.5 text-[10px] tabular-nums text-slate-500" },
      e("span", null, "粉丝 ", e("span", { className: "font-medium text-slate-200" }, formatNumber(item.followers))),
      item.real_er_pct != null && e("span", null, "Real ER ", e("span", { className: "font-medium text-slate-300" }, formatPercent(item.real_er_pct, 2))),
      (flag || item.country) && e("span", null, `${flag ? flag + " " : ""}${item.country}`),
      e("span", { className: "ml-auto text-slate-600 opacity-0 transition-opacity group-hover:opacity-100" }, "点开详情 →"),
    ),
  );
}

// 推荐卡片流:items = 页面既有筛选+排序后的行(与表格视图同一数据源,筛选/排序对两个视图同时生效)。
export function KolRecommendationCards({ items, apiToken, myList, onOpenItem, avatarFor, emptyHint }: any) {
  const [visibleCount, setVisibleCount] = React.useState(PAGE_SIZE);
  const list = Array.isArray(items) ? items : [];
  // 筛选/排序变化 → 回到第一页(避免翻到深处后切筛选残留大页码)。
  React.useEffect(() => { setVisibleCount(PAGE_SIZE); }, [list.length]);
  if (!list.length) {
    return e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.015] px-4 py-6 text-center text-[11px] text-slate-500" },
      emptyHint || "当前筛选下暂无推荐 KOL — 试试放宽筛选,或用上方智能入口找新达人");
  }
  const shown = list.slice(0, visibleCount);
  return e(React.Fragment, null,
    e("div", { className: "grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4" },
      shown.map((item: any, index: number) => e(RecommendationCard, {
        key: [item.id, item.platform, item.handle, index].filter(Boolean).join(":"),
        item,
        apiToken,
        inMyList: Boolean(myList && myList.has && myList.has(item.id)),
        onOpen: onOpenItem,
        avatarUrl: typeof avatarFor === "function" ? avatarFor(item) : "",
      })),
    ),
    list.length > visibleCount && e("div", { className: "mt-2.5 flex justify-center" },
      e("button", {
        type: "button",
        onClick: () => setVisibleCount((count: number) => count + PAGE_SIZE),
        className: "rounded-md border border-white/[0.08] bg-white/[0.02] px-3 py-1.5 text-[10.5px] text-slate-400 transition-colors hover:border-white/[0.16] hover:text-white",
      }, `加载更多(还有 ${list.length - visibleCount} 位)`),
    ),
  );
}
