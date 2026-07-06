// 件① KOL 招牌面板:这个 KOL「最擅长什么」三块卡片(深度分析 tab 增益块)。
// 数据:GET /api/admin/vkpi/kol-pool/{id}/signature —— 纯聚合已有数据
// (final_v1 深析 layer1 + evidence 播放/互动 + 已入库评论词表分布),零新采集/零 LLM。
// 三块:🎬 擅长拍法(TOP 拍摄模式+均播放) / 🏆 最爆代表作 TOP3(+final_v1 一句话点评)
//      / 💬 最引反馈 TOP3(评论数/互动率 + 意向/情感分布)。
// 诚实态:每块 status==="empty" 时如实展示 reason;接口失败整块安静缺席(非阻塞增益块)。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { Clapperboard, MessageSquare, Trophy } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type ModeItem = {
  key?: string; label?: string; video_count?: number; avg_views?: number | null;
  views_sample?: number; top_example?: { title?: string; view_count?: number | null };
};
type OpeningItem = { key?: string; label?: string; count?: number };
type VideoItem = {
  evidence_id?: number; title?: string; content_url?: string; platform?: string;
  posted_at?: string | null; view_count?: number | null; like_count?: number | null;
  comment_count?: number | null; engagement_rate?: number | null; verdict?: string | null;
  has_deep_analysis?: boolean;
  comments?: {
    status?: string; reason?: string; sample_size?: number;
    intent?: { purchase_intent?: number; question?: number };
    sentiment?: { positive?: number; negative?: number; neutral?: number };
    purchase_intent_samples?: string[];
  };
};
type Block<T> = { status?: string; reason?: string } & T;
type SignatureResp = {
  status?: string;
  reason?: string;
  coverage?: { evidence_count?: number; deep_analyzed_count?: number };
  shooting_styles?: Block<{
    sample_size?: number; modes?: ModeItem[]; unclassified_count?: number;
    openings?: OpeningItem[]; professional_level?: Record<string, number>;
    brand_exposure?: { sufficient?: number; insufficient?: number; text_only?: number };
  }>;
  top_videos?: Block<{ items?: VideoItem[]; total_with_views?: number }>;
  feedback?: Block<{ items?: VideoItem[] }>;
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

// 空态行:块级诚实空态统一渲染(reason 直接来自后端,不本地编造)。
function EmptyLine(reason: string) {
  return e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" }, reason || "暂无数据");
}

function BlockTitle(icon: React.ReactNode, text: string, extra?: React.ReactNode) {
  return e("div", { className: "flex items-center gap-1.5 mt-2 mb-1" },
    icon,
    e("span", { className: "text-[10px] font-medium text-slate-300" }, text),
    extra || null,
  );
}

// 视频行(块二/块三共用):标题链接 + 数据 + 点评/评论分布。
function VideoRow(v: VideoItem, i: number, kind: "top" | "feedback") {
  const comments = v.comments;
  const commentsReady = comments && comments.status === "ready";
  const senti = (commentsReady && comments!.sentiment) || null;
  const intent = (commentsReady && comments!.intent) || null;
  return e("div", { key: i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
    e("div", { className: "flex items-center gap-1.5" },
      e("span", { className: "shrink-0 text-[9px] font-bold tabular-nums " + (i === 0 ? "text-amber-300" : "text-slate-500") }, "#" + (i + 1)),
      v.content_url
        ? e("a", {
            href: v.content_url, target: "_blank", rel: "noreferrer",
            className: "truncate text-[10.5px] text-slate-200 hover:text-cyan-200 hover:underline",
            title: v.title || v.content_url,
          }, v.title || v.content_url)
        : e("span", { className: "truncate text-[10.5px] text-slate-200" }, v.title || "(无标题)"),
    ),
    e("div", { className: "mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9px] tabular-nums text-slate-500" },
      e("span", null, "播放 ", e("span", { className: "text-slate-300 font-medium" }, fmtNum(v.view_count))),
      kind === "top" && v.like_count != null && e("span", null, "赞 " + fmtNum(v.like_count)),
      e("span", null, "评论 " + fmtNum(v.comment_count)),
      e("span", null, "互动率 " + fmtRate(v.engagement_rate)),
      v.posted_at && e("span", { className: "text-slate-600" }, String(v.posted_at).slice(0, 10)),
    ),
    // final_v1 一句话点评:有则带,无深析如实说(不杜撰点评)
    v.verdict
      ? e("div", { className: "mt-1 rounded bg-cyan-400/[0.05] border border-cyan-300/10 px-1.5 py-1 text-[9.5px] leading-relaxed text-cyan-100/90" }, "💡 " + v.verdict)
      : (v.has_deep_analysis === false && kind === "top"
          ? e("div", { className: "mt-1 text-[9px] text-slate-600" }, "该视频未深析,暂无 AI 点评")
          : null),
    // 块三专属:该视频已入库评论的意向/情感分布;未入库如实说 reason
    kind === "feedback" && comments && (commentsReady
      ? e("div", { className: "mt-1 flex flex-wrap items-center gap-1 text-[9px]" },
          e("span", { className: "text-slate-500" }, `已入库 ${Number(comments!.sample_size) || 0} 条:`),
          senti && e("span", { className: "rounded bg-emerald-500/10 px-1.5 py-0.5 text-emerald-200" }, `正面 ${Number(senti.positive) || 0}`),
          senti && e("span", { className: "rounded bg-rose-500/10 px-1.5 py-0.5 text-rose-200" }, `负面 ${Number(senti.negative) || 0}`),
          senti && e("span", { className: "rounded bg-slate-500/10 px-1.5 py-0.5 text-slate-400" }, `中性 ${Number(senti.neutral) || 0}`),
          intent && e("span", { className: "rounded bg-purple-500/10 px-1.5 py-0.5 text-purple-200" }, `购买意向 ${Number(intent.purchase_intent) || 0}`),
          intent && e("span", { className: "rounded bg-sky-500/10 px-1.5 py-0.5 text-sky-200" }, `提问 ${Number(intent.question) || 0}`),
        )
      : e("div", { className: "mt-1 text-[9px] text-slate-600" }, String(comments!.reason || "评论未入库"))),
  );
}

export function SignaturePanel({ apiToken, kolPoolId }: any) {
  const [data, setData] = React.useState<SignatureResp | null>(null);

  // 开抽屉/换 KOL 只读拉取(后端纯聚合已有数据,读得起);失败静默不渲染。
  React.useEffect(() => {
    setData(null);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    void apiFetch<SignatureResp>(
      `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/signature`,
      {},
      apiToken,
    )
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  if (!apiToken || !kolPoolId || !data) return null;
  if (String(data.status || "") === "error") return null; // 聚合失败:安静缺席,不甩后端报错

  const styles = data.shooting_styles || {};
  const top = data.top_videos || {};
  const feedback = data.feedback || {};
  const modes = Array.isArray(styles.modes) ? styles.modes : [];
  const openings = Array.isArray(styles.openings) ? styles.openings : [];
  const exposure = styles.brand_exposure || {};
  const topItems = Array.isArray(top.items) ? top.items : [];
  const fbItems = Array.isArray(feedback.items) ? feedback.items : [];
  const deepCount = Number(data.coverage?.deep_analyzed_count) || 0;
  const maxModeCount = modes.reduce((acc, m) => Math.max(acc, Number(m.video_count) || 0), 0);

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "signature",
      header: e(React.Fragment, null,
        e(Clapperboard, { size: 11, className: "text-amber-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "KOL 招牌 · 他最擅长什么"),
        deepCount > 0 && e("span", { className: "rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-200" }, `深析 ×${deepCount}`),
      ),
    },
      // ── 🎬 块一:擅长拍法(TOP 拍摄模式 + 各模式均播放)──
      BlockTitle(e("span", { className: "text-[10px]" }, "🎬"), "擅长拍法",
        styles.status === "ready" && e("span", { className: "text-[8.5px] text-slate-600" }, `样本 ${Number(styles.sample_size) || 0} 条深析 · 词表分类`)),
      styles.status === "empty"
        ? EmptyLine(String(styles.reason || ""))
        : e(React.Fragment, null,
            e("div", { className: "space-y-1" },
              modes.slice(0, 5).map((m, i) => {
                const count = Number(m.video_count) || 0;
                const widthPct = maxModeCount > 0 ? Math.max(8, Math.round((count / maxModeCount) * 100)) : 0;
                return e("div", { key: m.key || i, className: "flex items-center gap-2 text-[10px]" },
                  e("span", { className: "w-[86px] shrink-0 truncate text-slate-300", title: m.label }, m.label || m.key || "—"),
                  e("div", { className: "relative h-[7px] flex-1 overflow-hidden rounded-full bg-white/[0.05]" },
                    e("div", {
                      className: "absolute inset-y-0 left-0 rounded-full",
                      style: { width: widthPct + "%", background: "linear-gradient(90deg, rgba(251,191,36,0.35), rgba(251,191,36,0.75))" },
                    }),
                  ),
                  e("span", { className: "w-[30px] shrink-0 text-right tabular-nums text-slate-400" }, "×" + count),
                  e("span", { className: "w-[64px] shrink-0 text-right tabular-nums text-slate-500", title: "该模式均播放" },
                    m.avg_views != null ? "均" + fmtNum(m.avg_views) : "播放缺失"),
                );
              }),
              (Number(styles.unclassified_count) || 0) > 0 && e("div", { className: "text-[9px] text-slate-600" },
                `另有 ${styles.unclassified_count} 条深析词表未能归类(不硬贴标签)`),
            ),
            openings.length > 0 && e("div", { className: "mt-1.5 flex flex-wrap items-center gap-1" },
              e("span", { className: "text-[9px] text-slate-500" }, "开头习惯:"),
              openings.map((o, i) => e("span", {
                key: o.key || i,
                className: "rounded border border-white/[0.06] bg-black/20 px-1.5 py-0.5 text-[9px] text-slate-300",
              }, `${o.label} ×${Number(o.count) || 0}`)),
            ),
            (Number(exposure.sufficient) || 0) + (Number(exposure.insufficient) || 0) > 0 && e("div", { className: "mt-1 text-[9px] text-slate-500" },
              `品牌露出充分 ${Number(exposure.sufficient) || 0} 条 / 不足 ${Number(exposure.insufficient) || 0} 条(深析判定)`),
          ),

      // ── 🏆 块二:最爆代表作 TOP3(view_count 排序 + 一句话点评)──
      BlockTitle(e(Trophy, { size: 10, className: "text-amber-300" }), "最爆代表作 TOP3",
        top.status === "ready" && e("span", { className: "text-[8.5px] text-slate-600" }, `${Number(top.total_with_views) || 0} 条有播放数`)),
      top.status === "empty"
        ? EmptyLine(String(top.reason || ""))
        : e("div", { className: "space-y-1.5" }, topItems.map((v, i) => VideoRow(v, i, "top"))),

      // ── 💬 块三:最引反馈 TOP3(评论数/互动率 + 意向/情感分布)──
      BlockTitle(e(MessageSquare, { size: 10, className: "text-sky-300" }), "最引反馈 TOP3"),
      feedback.status === "empty"
        ? EmptyLine(String(feedback.reason || ""))
        : e("div", { className: "space-y-1.5" }, fbItems.map((v, i) => VideoRow(v, i, "feedback"))),

      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        "口径:纯聚合已有数据(final_v1 深析 + 视频证据 + 已入库评论);独立展示信号,不参与 V6 Fit 评分。"),
    ),
  );
}
