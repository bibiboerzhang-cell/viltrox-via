// E2 内容记分卡面板:官号内容按三平台北极星轴判档的分布卡(GTM-2)。
// 数据:GET /api/admin/vkpi/content/scorecard/channel/{channelId} —— 纯读聚合
// (每帖最新快照 + growth_playbook 规则库代理判档),零采集/零 LLM/零重析/零写库。
// 展示:档位分布条(A/B/C/淘汰/不可判)+ 各档 top 例帖 + proxy 诚实标注 +
//       rule_refs 规则引用脚注(每个判档可溯源到规则册条目)。
// 诚实态:北极星真数据(完播/sends/CTR)拿不到时后端如实 unknown/unavailable,
// 本面板原样展示 proxy_notes,绝不本地编数;接口失败整块安静缺席(非阻塞增益块)。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { ClipboardList } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { FreshnessDot } from "./ui/FreshnessDot";
import { SkeletonBlock } from "./ui/SkeletonBlock";

const e = React.createElement;

type RuleRef = {
  rule_id?: string; role?: string; note?: string; statement?: string;
  confidence?: string; source?: string;
};
type TierBucket = { tier?: string; tier_label?: string; count?: number; share?: number };
type ExampleItem = {
  post_uid?: string; title?: string | null; post_url?: string | null; posted_at?: string | null;
  views?: number | null; likes?: number | null; comments?: number | null;
  engagement_rate?: number | null; account_percentile?: number | null;
  tier?: string; tier_basis?: string;
};
type ScorecardResp = {
  status?: string; reason?: string; method?: string;
  channel?: { id?: number; platform?: string; handle?: string; display_name?: string; url?: string | null };
  posts_total?: number; posts_judged?: number;
  distribution?: TierBucket[];
  examples?: Record<string, ExampleItem[]>;
  proxy_notes?: string[];
  rule_refs?: RuleRef[];
  latest_snapshot_date?: string | null;
  latest_captured_at?: string | null;
  note?: string;
};

// 档位视觉体系(判档徽章):A=emerald / B=cyan / C=slate / 淘汰=rose / 不可判=amber
const TIER_STYLE: Record<string, { bar: string; badge: string }> = {
  A: { bar: "rgba(52,211,153,0.75)", badge: "bg-emerald-500/15 text-emerald-200 border-emerald-300/20" },
  B: { bar: "rgba(34,211,238,0.65)", badge: "bg-cyan-500/15 text-cyan-200 border-cyan-300/20" },
  C: { bar: "rgba(148,163,184,0.45)", badge: "bg-slate-500/15 text-slate-300 border-slate-300/15" },
  eliminate: { bar: "rgba(251,113,133,0.6)", badge: "bg-rose-500/15 text-rose-200 border-rose-300/20" },
  unrated: { bar: "rgba(251,191,36,0.35)", badge: "bg-amber-500/10 text-amber-200/90 border-amber-300/15" },
};
const ROLE_LABEL: Record<string, string> = {
  applied: "已应用",
  axis_unavailable: "轴数据缺失",
  gate: "统计闸",
};

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}

function fmtRate(r: number | null | undefined): string {
  return typeof r === "number" ? (Math.round(r * 1000) / 10).toFixed(1) + "%" : "—";
}

// 判档徽章(全面板统一;title 带判档依据,判档可解释)
function TierBadge(tier: string, label: string, basis?: string) {
  const style = TIER_STYLE[tier] || TIER_STYLE.C;
  return e("span", {
    className: "shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-medium " + style.badge,
    title: basis || undefined,
  }, label || tier);
}

// 例帖行:标题链接 + 播放/互动率 + 判档徽章
function ExampleRow(item: ExampleItem, i: number, labelByTier: Record<string, string>) {
  const tier = String(item.tier || "");
  return e("div", { key: (item.post_uid || "") + i, className: "flex items-center gap-2 rounded border border-white/[0.05] bg-black/20 px-2 py-1" },
    TierBadge(tier, labelByTier[tier] || tier, item.tier_basis),
    item.post_url
      ? e("a", {
          href: item.post_url, target: "_blank", rel: "noreferrer",
          className: "min-w-0 flex-1 truncate text-[10px] text-slate-200 hover:text-cyan-200 hover:underline",
          title: item.title || item.post_url,
        }, item.title || item.post_uid || "(无标题)")
      : e("span", { className: "min-w-0 flex-1 truncate text-[10px] text-slate-200", title: item.title || "" }, item.title || item.post_uid || "(无标题)"),
    e("span", { className: "shrink-0 text-[9px] tabular-nums text-slate-500" }, "播放 ", e("span", { className: "text-slate-300" }, fmtNum(item.views))),
    e("span", { className: "shrink-0 text-[9px] tabular-nums text-slate-500" }, "互动率 " + fmtRate(item.engagement_rate)),
  );
}

export function ContentScorecardPanel({ apiToken, channelId }: { apiToken?: string; channelId?: number | string }) {
  const [data, setData] = React.useState<ScorecardResp | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [showRules, setShowRules] = React.useState(false);

  // 换官号只读拉取(后端纯聚合,读得起);失败静默缺席。
  React.useEffect(() => {
    setData(null);
    if (!apiToken || !channelId) return;
    let cancelled = false;
    setLoading(true);
    void apiFetch<ScorecardResp>(
      `/api/admin/vkpi/content/scorecard/channel/${encodeURIComponent(String(channelId))}`,
      {},
      apiToken,
    )
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apiToken, channelId]);

  if (!apiToken || !channelId) return null;
  if (loading && !data) {
    return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" }, e(SkeletonBlock, { lines: 3 }));
  }
  if (!data || String(data.status || "") === "error") return null; // 聚合失败:安静缺席,不甩后端报错

  if (data.status === "empty") {
    return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
      e("div", { className: "flex items-center gap-1.5" },
        e(ClipboardList, { size: 11, className: "text-cyan-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "内容记分卡"),
      ),
      e("div", { className: "mt-1 text-[10px] text-amber-300/90" }, String(data.reason || "该官号暂无帖子快照")),
    );
  }

  const dist = Array.isArray(data.distribution) ? data.distribution : [];
  const total = Number(data.posts_total) || 0;
  const labelByTier: Record<string, string> = {};
  for (const b of dist) labelByTier[String(b.tier)] = String(b.tier_label || b.tier);
  const examples = data.examples || {};
  const exampleTiers = ["A", "B", "eliminate"].filter((t) => (examples[t] || []).length > 0);
  const refs = Array.isArray(data.rule_refs) ? data.rule_refs : [];
  const notes = Array.isArray(data.proxy_notes) ? data.proxy_notes : [];
  const ch = data.channel || {};

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    // ── 卡头:标题 + 平台 + 新鲜度 + 样本量 ──
    e("div", { className: "flex items-center gap-1.5" },
      e(ClipboardList, { size: 11, className: "text-cyan-300" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "内容记分卡 · 北极星判档"),
      ch.platform && e("span", { className: "rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] text-slate-300" }, String(ch.platform)),
      e(FreshnessDot, { ts: data.latest_captured_at || data.latest_snapshot_date, label: "最新快照" }),
      e("span", { className: "ml-auto text-[9px] tabular-nums text-slate-500" }, `${total} 帖 · 判档 ${Number(data.posts_judged) || 0}`),
    ),

    // ── 档位分布条(A/B/C/淘汰/不可判)──
    total > 0 && e("div", { className: "mt-2" },
      e("div", { className: "flex h-[8px] w-full overflow-hidden rounded-full bg-white/[0.04]" },
        dist.filter((b) => (Number(b.count) || 0) > 0).map((b) => e("div", {
          key: b.tier,
          className: "h-full",
          style: { width: Math.max(1.5, (Number(b.share) || 0) * 100) + "%", background: (TIER_STYLE[String(b.tier)] || TIER_STYLE.C).bar },
          title: `${b.tier_label} ${b.count} 帖(${(Math.round((Number(b.share) || 0) * 1000) / 10).toFixed(1)}%)`,
        })),
      ),
      e("div", { className: "mt-1.5 flex flex-wrap items-center gap-1.5" },
        dist.map((b) => e("span", { key: b.tier, className: "inline-flex items-center gap-1" },
          TierBadge(String(b.tier), String(b.tier_label || b.tier)),
          e("span", { className: "text-[9px] tabular-nums text-slate-500" }, `×${Number(b.count) || 0}`),
        )),
      ),
    ),

    // ── 各档例帖(A / B / 淘汰;每档 top3 按播放)──
    exampleTiers.length > 0 && e("div", { className: "mt-2 space-y-1" },
      exampleTiers.flatMap((t) => (examples[t] || []).slice(0, t === "B" ? 2 : 3).map((item, i) => ExampleRow(item, i, labelByTier))),
    ),

    // ── proxy 诚实标注(来自后端,不本地编)──
    notes.length > 0 && e("div", { className: "mt-1.5 text-[9px] leading-relaxed text-amber-300/80" },
      "代理口径:" + notes.join(";")),

    // ── 规则引用脚注(判档溯源到 growth_playbook 条目)──
    refs.length > 0 && e("div", { className: "mt-1.5" },
      e("button", {
        type: "button",
        onClick: () => setShowRules((v) => !v),
        className: "text-[9px] text-slate-500 hover:text-slate-300 underline decoration-dotted underline-offset-2",
      }, (showRules ? "收起" : "展开") + ` 规则引用 ×${refs.length}(growth_playbook)`),
      showRules && e("div", { className: "mt-1 space-y-1" },
        refs.map((r, i) => e("div", { key: r.rule_id || i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1" },
          e("div", { className: "flex flex-wrap items-center gap-1.5" },
            e("span", { className: "font-mono text-[9px] text-cyan-200/90" }, String(r.rule_id || "")),
            e("span", { className: "rounded bg-white/[0.05] px-1 py-0.5 text-[8.5px] text-slate-400" }, ROLE_LABEL[String(r.role)] || String(r.role || "")),
            r.confidence && e("span", { className: "text-[8.5px] text-slate-600" }, `confidence=${r.confidence}`),
          ),
          r.statement && e("div", { className: "mt-0.5 text-[9px] leading-relaxed text-slate-400" }, String(r.statement)),
          r.note && e("div", { className: "mt-0.5 text-[8.5px] text-slate-600" }, String(r.note)),
        )),
      ),
    ),

    e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
      "口径:每帖最新快照按规则库代理判档(北极星真数据缺失如实标注);纯读展示,不参与 V6 Fit 评分,不触发任何重析。"),
  );
}
