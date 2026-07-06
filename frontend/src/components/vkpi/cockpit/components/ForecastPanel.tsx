// 件 A 预测战绩条(档案第八层):「找他合作,大概能跑出什么数」。
// 数据:GET /api/admin/vkpi/kol-pool/{id}/forecast?sku=(可选)—— 决定性分位数法,
// 基于历史 evidence 播放分布出 p10/p50/p90 期望播放 + 互动率中位数,零 LLM/零新采集。
// 三块:📊 三档区间条(保守p10/预期p50/乐观p90) / 置信度徽章(样本<3 显著标注「样本不足」)
//      / 🏷️ 带品实绩 track_record(历史 Viltrox 语境视频的真实表现小结)。
// 诚实态:status==="empty" 如实展示 reason(0 样本绝不画区间);basis 一句话交代口径;
// 接口失败整块安静缺席(非阻塞增益块)。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;数字全部来自后端 basis,不本地编造。
import React from "react";
import { Target, Tag } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type FactorItem = { factor?: string; value?: number; reason?: string };
type SkuAdjustment = {
  status?: string; sku?: string; product_name?: string | null; focals?: string[];
  covered_video_count?: number; coefficient?: number; factors?: FactorItem[]; reason?: string;
};
type TrackExample = {
  evidence_id?: number; title?: string; content_url?: string | null; platform?: string | null;
  posted_at?: string | null; view_count?: number | null; engagement_rate?: number | null;
};
type TrackRecord = {
  status?: string; reason?: string; method?: string; video_count?: number; with_views?: number;
  median_views?: number | null; max_views?: number | null; median_engagement_rate?: number | null;
  examples?: TrackExample[];
};
type ForecastResp = {
  status?: string;
  reason?: string;
  sku?: string | null;
  expected_views_p10?: number | null;
  expected_views_p50?: number | null;
  expected_views_p90?: number | null;
  engagement_rate?: number | null;
  confidence?: string;
  low_sample?: boolean;
  basis?: {
    method?: string; evidence_count?: number; views_sample_size?: number; er_sample_size?: number;
    window?: { from?: string | null; to?: string | null };
    baseline?: { p10?: number; p50?: number; p90?: number };
    coefficient?: number; confidence_rule?: string; summary?: string;
  };
  sku_adjustment?: SkuAdjustment | null;
  track_record?: TrackRecord;
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

// 置信度徽章:low 一律醒目标注「样本不足/低置信」,不让人误把宽区间当准数。
function ConfidenceBadge(confidence: string | undefined, lowSample: boolean | undefined) {
  const level = String(confidence || "").toLowerCase();
  if (level === "high") {
    return e("span", { className: "rounded bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-200" }, "置信度高");
  }
  if (level === "medium") {
    return e("span", { className: "rounded bg-sky-500/10 px-1.5 py-0.5 text-[9px] text-sky-200" }, "置信度中");
  }
  return e("span", { className: "rounded bg-amber-500/15 border border-amber-400/30 px-1.5 py-0.5 text-[9px] font-medium text-amber-200" },
    lowSample ? "样本不足 · 低置信" : "置信度低");
}

// 三档区间条:p10 左端、p90 右端、p50 竖线标记(线性位置,数据来自后端不再加工)。
function RangeBar(p10: number, p50: number, p90: number) {
  const span = p90 - p10;
  const pct = span > 0 ? Math.min(96, Math.max(4, Math.round(((p50 - p10) / span) * 100))) : 50;
  return e("div", { className: "mt-1" },
    e("div", { className: "relative h-[10px] overflow-hidden rounded-full bg-white/[0.05]" },
      e("div", {
        className: "absolute inset-y-0 left-0 right-0 rounded-full",
        style: { background: "linear-gradient(90deg, rgba(56,189,248,0.15), rgba(56,189,248,0.45), rgba(16,185,129,0.5))" },
      }),
      e("div", {
        className: "absolute inset-y-0 w-[2px] bg-cyan-200",
        style: { left: pct + "%" },
        title: "p50 预期",
      }),
    ),
    e("div", { className: "mt-1 flex items-baseline justify-between text-[9px] tabular-nums" },
      e("span", { className: "text-slate-500" }, "保守 p10 ", e("span", { className: "text-slate-300" }, fmtNum(p10))),
      e("span", { className: "text-cyan-200 text-[11px] font-semibold" }, "预期 ", fmtNum(p50)),
      e("span", { className: "text-slate-500" }, "乐观 p90 ", e("span", { className: "text-slate-300" }, fmtNum(p90))),
    ),
  );
}

export function ForecastPanel({ apiToken, kolPoolId, sku }: any) {
  const [data, setData] = React.useState<ForecastResp | null>(null);

  // 开抽屉/换 KOL/换 SKU 只读拉取(后端纯聚合已有数据,读得起);失败静默不渲染。
  React.useEffect(() => {
    setData(null);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    const query = sku ? `?sku=${encodeURIComponent(String(sku))}` : "";
    void apiFetch<ForecastResp>(
      `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/forecast${query}`,
      {},
      apiToken,
    )
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId, sku]);

  if (!apiToken || !kolPoolId || !data) return null;
  if (String(data.status || "") === "error") return null; // 聚合失败:安静缺席,不甩后端报错

  const isEmpty = String(data.status || "") === "empty";
  const basis = data.basis || {};
  const adj = data.sku_adjustment || null;
  const track = data.track_record || null;
  const trackReady = track && track.status === "ready";
  const trackExamples = (trackReady && Array.isArray(track!.examples)) ? track!.examples! : [];
  const p10 = Number(data.expected_views_p10);
  const p50 = Number(data.expected_views_p50);
  const p90 = Number(data.expected_views_p90);
  const hasRange = !isEmpty && Number.isFinite(p10) && Number.isFinite(p50) && Number.isFinite(p90);

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "forecast",
      header: e(React.Fragment, null,
        e(Target, { size: 11, className: "text-cyan-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "预测战绩 · 找他合作能跑出什么数"),
        ConfidenceBadge(data.confidence, data.low_sample),
      ),
    },
      // ── 📊 块一:三档期望播放区间(0 样本诚实空态,绝不画区间)──
      isEmpty
        ? EmptyLine(String(data.reason || ""))
        : e(React.Fragment, null,
            hasRange && RangeBar(p10, p50, p90),
            e("div", { className: "mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9px] tabular-nums text-slate-500" },
              e("span", null, "期望互动率 ", e("span", { className: "text-slate-300 font-medium" }, fmtRate(data.engagement_rate))),
              e("span", null, `样本 ${Number(basis.views_sample_size) || 0} 条有播放 / ${Number(basis.evidence_count) || 0} 条证据`),
              basis.coefficient != null && Number(basis.coefficient) !== 1 &&
                e("span", { className: "rounded bg-cyan-400/10 px-1.5 py-0.5 text-cyan-200" }, `SKU 系数 x${basis.coefficient}`),
            ),
            // 样本<3:后端 low_sample=true,面板显著标注(不是脚注级,是警示块)
            data.low_sample && e("div", { className: "mt-1.5 rounded border border-amber-400/25 bg-amber-500/[0.08] px-2 py-1 text-[9.5px] leading-relaxed text-amber-200" },
              "⚠ 样本不足(有播放证据 <3 条):区间仅供参考,别当准数用。"),
          ),

      // ── basis 一句话:预测口径可追溯(直接展示后端 summary,不本地改写)──
      !isEmpty && basis.summary && e("div", { className: "mt-1.5 text-[9px] leading-relaxed text-slate-500" },
        "依据:" + String(basis.summary)),

      // ── SKU 调整因子:每个系数带理由(仅带 sku 请求时出现)──
      adj && e("div", { className: "mt-1.5" },
        e("div", { className: "flex items-center gap-1.5 mb-1" },
          e(Tag, { size: 10, className: "text-cyan-300" }),
          e("span", { className: "text-[10px] font-medium text-slate-300" },
            "SKU 调整 " + String(adj.sku || "") + (adj.product_name ? ` · ${adj.product_name}` : "")),
          adj.status === "ready" && e("span", { className: "text-[8.5px] text-slate-600" }, `系数 x${Number(adj.coefficient) || 1}`),
        ),
        adj.status === "ready" && Array.isArray(adj.factors)
          ? e("div", { className: "space-y-0.5" },
              adj.factors.map((f, i) => e("div", { key: f.factor || i, className: "flex items-start gap-1.5 text-[9px] leading-relaxed" },
                e("span", { className: "shrink-0 rounded bg-white/[0.05] px-1 py-0.5 tabular-nums text-slate-300" }, "x" + (Number(f.value) || 1)),
                e("span", { className: "text-slate-500" }, String(f.reason || f.factor || "")),
              )),
            )
          : e("div", { className: "text-[9px] leading-relaxed text-slate-500" }, String(adj.reason || "SKU 调整未生效")),
      ),

      // ── 🏷️ 块二:带品实绩 track_record(历史带品视频真实表现;识别不了诚实空态)──
      e("div", { className: "flex items-center gap-1.5 mt-2 mb-1" },
        e("span", { className: "text-[10px]" }, "🏷️"),
        e("span", { className: "text-[10px] font-medium text-slate-300" }, "带品实绩"),
        trackReady && e("span", { className: "text-[8.5px] text-slate-600" },
          `${Number(track!.video_count) || 0} 条带品视频 · ${String(track!.method || "")}`),
      ),
      track && (trackReady
        ? e(React.Fragment, null,
            e("div", { className: "flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9px] tabular-nums text-slate-500" },
              e("span", null, "带品播放中位 ", e("span", { className: "text-slate-300 font-medium" }, fmtNum(track!.median_views))),
              e("span", null, "最高 " + fmtNum(track!.max_views)),
              e("span", null, "带品互动率中位 " + fmtRate(track!.median_engagement_rate)),
            ),
            trackExamples.length > 0 && e("div", { className: "mt-1 space-y-1" },
              trackExamples.map((v, i) => e("div", { key: v.evidence_id || i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1" },
                e("div", { className: "flex items-center gap-1.5" },
                  e("span", { className: "shrink-0 text-[9px] font-bold tabular-nums " + (i === 0 ? "text-cyan-300" : "text-slate-500") }, "#" + (i + 1)),
                  v.content_url
                    ? e("a", {
                        href: v.content_url, target: "_blank", rel: "noreferrer",
                        className: "truncate text-[10px] text-slate-200 hover:text-cyan-200 hover:underline",
                        title: v.title || v.content_url,
                      }, v.title || v.content_url)
                    : e("span", { className: "truncate text-[10px] text-slate-200" }, v.title || "(无标题)"),
                ),
                e("div", { className: "mt-0.5 flex flex-wrap items-center gap-x-2.5 text-[9px] tabular-nums text-slate-500" },
                  e("span", null, "播放 ", e("span", { className: "text-slate-300" }, fmtNum(v.view_count))),
                  e("span", null, "互动率 " + fmtRate(v.engagement_rate)),
                  v.posted_at && e("span", { className: "text-slate-600" }, String(v.posted_at).slice(0, 10)),
                ),
              )),
            ),
          )
        : EmptyLine(String(track.reason || ""))),

      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        "口径:历史 evidence 播放分布的分位数预测(决定性、零 LLM);独立展示信号,不参与 V6 Fit 评分。"),
    ),
  );
}
