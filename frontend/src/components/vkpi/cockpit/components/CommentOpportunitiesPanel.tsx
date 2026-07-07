// G3 评论区营销机会 Top5 面板:「官号值得去哪些高热视频下评论」(Dashboard 右栏卡片)。
// 数据:GET /api/admin/vkpi/market/comment-opportunities?days=30 —— 纯读端聚合已有数据
// (证据标题词表打分:热度×主题相关×竞品语境×新鲜度),零新采集/零 LLM/零写库。
// 展示:Top5 机会卡(标题链接+KOL+播放/互动+分数)+ 为何值得去 + 建议评论角度(词表模板)。
// 红线:只展示机会清单,绝不提供任何「一键发评论」入口 —— 评论由人工撰写、人工发布;
// 纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
// 诚实态:接口失败整块安静缺席;空窗口如实展示后端 reason(含库内最新发布日),绝不编数。
import React from "react";
import { MessageCircle, Swords } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

// 默认窗口:本地数据有断更期,30 天窗比 7 天更常有料;后端 days 护栏 1..180。
const DEFAULT_DAYS = 30;

type TopicHit = { key?: string; label?: string; angle?: string };
type Signals = {
  focal_hits?: string[]; topic_hits?: TopicHit[];
  competitor_hits?: string[]; viltrox_mentioned?: boolean;
};
type ScoreParts = {
  heat?: number; relevance?: number; competitor?: number; freshness?: number;
  engagement_rate?: number | null; age_days?: number;
};
type OppItem = {
  evidence_id?: number; title?: string; content_url?: string; platform?: string;
  kol_pool_id?: number | null; kol_name?: string; kol_handle?: string;
  published_day?: string; view_count?: number | null; like_count?: number | null;
  comment_count?: number | null; score?: number; score_parts?: ScoreParts;
  signals?: Signals; why?: string; comment_angles?: string[];
};
type OppResp = {
  status?: string; reason?: string; days?: number; redline?: string;
  coverage?: { videos_scanned?: number; scored?: number; skipped_own_brand?: number };
  items?: OppItem[];
};

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}

// 单条机会卡:标题链接 + KOL/播放/互动 + 命中标签 + 建议评论角度(词表模板,人工参考)。
function OppRow(it: OppItem, i: number) {
  const signals = it.signals || {};
  const focals = Array.isArray(signals.focal_hits) ? signals.focal_hits : [];
  const brands = Array.isArray(signals.competitor_hits) ? signals.competitor_hits : [];
  const angles = Array.isArray(it.comment_angles) ? it.comment_angles : [];
  const er = it.score_parts?.engagement_rate;
  return e("div", { key: it.evidence_id || i, className: "px-3 py-2" + (i > 0 ? " border-t border-white/[0.04]" : ""), title: it.why || "" },
    e("div", { className: "flex items-start gap-1.5" },
      e("span", { className: "shrink-0 text-[9px] font-bold tabular-nums mt-[1px] " + (i === 0 ? "text-amber-300" : "text-slate-500") }, "#" + (i + 1)),
      e("div", { className: "min-w-0 flex-1" },
        it.content_url
          ? e("a", {
              href: it.content_url, target: "_blank", rel: "noreferrer",
              className: "block truncate text-[10.5px] text-slate-200 hover:text-cyan-200 hover:underline",
            }, it.title || it.content_url)
          : e("span", { className: "block truncate text-[10.5px] text-slate-200" }, it.title || "(无标题)"),
        e("div", { className: "mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[9px] tabular-nums text-slate-500" },
          (it.kol_name || it.kol_handle) && e("span", { className: "truncate max-w-[110px] text-slate-400" }, it.kol_name || "@" + it.kol_handle),
          e("span", null, String(it.platform || "")),
          e("span", null, "播放 ", e("span", { className: "text-slate-300 font-medium" }, fmtNum(it.view_count))),
          typeof er === "number" && e("span", null, "互动 " + (Math.round(er * 1000) / 10).toFixed(1) + "%"),
          it.published_day && e("span", { className: "text-slate-600" }, String(it.published_day).slice(5)),
        ),
      ),
      e("span", {
        className: "shrink-0 rounded bg-amber-400/10 px-1.5 py-0.5 text-[9px] font-semibold tabular-nums text-amber-200",
        title: "综合分 = 热度×0.35 + 主题相关×0.25 + 竞品语境×0.20 + 新鲜度×0.20(悬停整卡看「为何值得去」)",
      }, String(it.score ?? "—")),
    ),
    (focals.length > 0 || brands.length > 0 || signals.viltrox_mentioned) && e("div", { className: "mt-1 flex flex-wrap items-center gap-1" },
      focals.slice(0, 3).map((f, j) => e("span", { key: "f" + j, className: "rounded bg-cyan-400/10 px-1.5 py-0.5 text-[8.5px] text-cyan-200" }, f)),
      brands.slice(0, 3).map((b, j) => e("span", { key: "b" + j, className: "flex items-center gap-0.5 rounded bg-rose-400/10 px-1.5 py-0.5 text-[8.5px] text-rose-200" },
        e(Swords, { size: 8, strokeWidth: 2 }), b)),
      signals.viltrox_mentioned && e("span", { className: "rounded bg-emerald-400/10 px-1.5 py-0.5 text-[8.5px] text-emerald-200" }, "已提 Viltrox"),
    ),
    // 建议评论角度(词表模板非 LLM):给人工撰写参考,最多两条防溢出。
    angles.length > 0 && e("div", { className: "mt-1 space-y-0.5" },
      angles.slice(0, 2).map((a, j) => e("div", { key: "a" + j, className: "rounded bg-white/[0.02] border border-white/[0.04] px-1.5 py-1 text-[9px] leading-relaxed text-slate-400" }, "💬 " + a)),
      angles.length > 2 && e("div", { className: "text-[8.5px] text-slate-600" }, `另有 ${angles.length - 2} 条角度(接口 comment_angles 全量)`),
    ),
  );
}

export function CommentOpportunitiesPanel({ apiToken }: any) {
  const [data, setData] = React.useState<OppResp | null>(null);

  // 挂载后只读拉取一次(后端纯聚合词表打分,数据日更粒度,无需轮询)。
  React.useEffect(() => {
    setData(null);
    if (!apiToken) return;
    let cancelled = false;
    void apiFetch<OppResp>(
      `/api/admin/vkpi/market/comment-opportunities?days=${DEFAULT_DAYS}`,
      { timeoutMs: 15000 },
      apiToken,
    )
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

  if (!apiToken || !data) return null;
  if (String(data.status || "") === "error") return null; // 聚合失败:安静缺席,不甩后端报错

  const items = Array.isArray(data.items) ? data.items : [];
  const coverage = data.coverage || {};
  const isEmpty = data.status === "empty" || items.length === 0;

  return e("div", { className: "mb-4 rounded-xl border border-white/[0.06] bg-white/[0.012] overflow-hidden" },
    e("div", { className: "flex items-center justify-between gap-2 px-3 py-1.5 border-b border-white/[0.05]" },
      e("div", { className: "flex items-center gap-2" },
        e(MessageCircle, { size: 13, className: "text-amber-300", strokeWidth: 2 }),
        e("span", { className: "text-[11px] font-medium text-slate-300" }, `评论区机会 Top5 · ${Number(data.days) || DEFAULT_DAYS}天`),
      ),
      !isEmpty && e("span", { className: "text-[9px] tabular-nums text-slate-500", title: "窗口内扫描/可打分的证据视频数" },
        `${Number(coverage.scored) || 0}/${Number(coverage.videos_scanned) || 0} 条参评`),
    ),
    isEmpty
      // 诚实空态:后端 reason 原样展示(含库内最新发布日提示),绝不编机会。
      ? e("div", { className: "px-3 py-2 text-[10px] leading-relaxed text-slate-500" }, String(data.reason || "窗口内暂无可打分的视频证据"))
      : e(React.Fragment, null,
          e("div", null, items.slice(0, 5).map((it, i) => OppRow(it, i))),
          e("div", { className: "border-t border-white/[0.05] px-3 pb-1.5 pt-1 text-[8.5px] leading-relaxed text-slate-600" },
            "热度×主题×竞品语境×新鲜度 词表打分(非 LLM)· 只出清单,评论由人工撰写发布 · 不参与 Fit 评分"),
        ),
  );
}
