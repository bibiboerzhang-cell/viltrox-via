// KOL 质量 × 合规面板:一个面板两块(深度分析 tab 增益块)。
// 数据:GET /api/admin/vkpi/kol-pool/{id}/quality-score + /ftc-scan —— 纯聚合已有数据
// (final_v1 深析六维读数 + 标题/描述词表扫描),零新采集/零 LLM。
// 块一 📊 综合质量分:0-100 综合分 + 分项条 + 样本数/置信度诚实标注(素材复用分项
//      方向未确认,不进综合分,如实标注)。
// 块二 🛡️ FTC 披露扫描:已披露 / 疑似未披露 / 无合作迹象 三组 + risk 条目
//      (info/warn,措辞「疑似未披露」,词表启发式,绝不下法律结论)。
// 诚实态:每块 status==="empty" 时如实展示 reason;接口失败该块安静缺席(非阻塞增益块)。
// 红线:独立展示读数,绝不参与任何选人评分;后端绝不写库。
import React from "react";
import { Gauge, ShieldAlert } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type DimItem = {
  key?: string; label?: string; avg?: number; min?: number; max?: number;
  n?: number; in_composite?: boolean; note?: string;
};
type QualityResp = {
  status?: string; reason?: string; sample_size?: number;
  coverage?: { evidence_count?: number; deep_analyzed_count?: number };
  composite?: { score?: number | null; basis?: string };
  dimensions?: DimItem[];
  confidence?: { label?: string; sample_size?: number; llm_confidence_avg?: number | null };
  best_videos?: { title?: string; content_url?: string; composite?: number | null }[];
};
type Marker = { key?: string; label?: string };
type Signal = { key?: string; label?: string; source?: string };
type ScanVideo = {
  evidence_id?: number; title?: string; content_url?: string; platform?: string;
  view_count?: number | null; level?: string; scanned_sources?: string[];
  disclosure_markers?: Marker[]; cooperation_signals?: Signal[];
};
type FtcResp = {
  status?: string; reason?: string;
  coverage?: { evidence_count?: number; with_description?: number; with_deep_analysis?: number };
  summary?: { disclosed?: number; undisclosed_suspect?: number; clean?: number; warn_count?: number; risk_level?: string };
  groups?: { disclosed?: ScanVideo[]; undisclosed_suspect?: ScanVideo[]; clean?: ScanVideo[] };
  coverage_note?: string;
};

const CONFIDENCE_ZH: Record<string, string> = { high: "高", medium: "中", low: "低" };

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
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

// 分项条:均值宽度 + 区间/样本;不进综合分的分项如实标注(不装均匀)。
function DimRow(d: DimItem, i: number) {
  const avg = Number(d.avg);
  const width = Number.isFinite(avg) ? Math.max(4, Math.min(100, Math.round(avg))) : 0;
  const inComposite = d.in_composite !== false;
  return e("div", { key: d.key || i, className: "flex items-center gap-2 text-[10px]" },
    e("span", { className: "w-[118px] shrink-0 truncate text-slate-300", title: d.note ? `${d.label}:${d.note}` : d.label }, d.label || d.key || "—"),
    e("div", { className: "relative h-[7px] flex-1 overflow-hidden rounded-full bg-white/[0.05]" },
      e("div", {
        className: "absolute inset-y-0 left-0 rounded-full",
        style: {
          width: width + "%",
          background: inComposite
            ? "linear-gradient(90deg, rgba(52,211,153,0.35), rgba(52,211,153,0.75))"
            : "linear-gradient(90deg, rgba(148,163,184,0.25), rgba(148,163,184,0.5))",
        },
      }),
    ),
    e("span", { className: "w-[34px] shrink-0 text-right tabular-nums text-slate-200 font-medium" },
      Number.isFinite(avg) ? avg.toFixed(0) : "—"),
    e("span", { className: "w-[64px] shrink-0 text-right tabular-nums text-slate-600", title: "区间(最低-最高)· 样本" },
      `${Number(d.min) ?? "—"}-${Number(d.max) ?? "—"} ×${Number(d.n) || 0}`),
    !inComposite && e("span", { className: "shrink-0 rounded bg-slate-500/10 px-1 py-0.5 text-[8.5px] text-slate-500", title: d.note || "" }, "不计入综合"),
  );
}

// 扫描视频行(疑似/已披露共用):级别徽章 + 标题链接 + 命中词表标签。
function ScanRow(v: ScanVideo, i: number, kind: "suspect" | "disclosed") {
  const markers = Array.isArray(v.disclosure_markers) ? v.disclosure_markers : [];
  const signals = Array.isArray(v.cooperation_signals) ? v.cooperation_signals : [];
  const level = String(v.level || "");
  return e("div", { key: v.evidence_id || i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
    e("div", { className: "flex items-center gap-1.5" },
      kind === "suspect"
        ? e("span", {
            className: "shrink-0 rounded px-1 py-0.5 text-[8.5px] font-medium " +
              (level === "warn" ? "bg-amber-500/15 text-amber-200" : "bg-sky-500/10 text-sky-200"),
          }, level === "warn" ? "疑似未披露" : "疑似未披露·弱")
        : e("span", { className: "shrink-0 rounded bg-emerald-500/10 px-1 py-0.5 text-[8.5px] text-emerald-200" }, "已披露"),
      v.content_url
        ? e("a", {
            href: v.content_url, target: "_blank", rel: "noreferrer",
            className: "truncate text-[10.5px] text-slate-200 hover:text-cyan-200 hover:underline",
            title: v.title || v.content_url,
          }, v.title || v.content_url)
        : e("span", { className: "truncate text-[10.5px] text-slate-200" }, v.title || "(无标题)"),
      v.view_count != null && e("span", { className: "ml-auto shrink-0 text-[9px] tabular-nums text-slate-600" }, "播放 " + fmtNum(v.view_count)),
    ),
    e("div", { className: "mt-1 flex flex-wrap items-center gap-1" },
      markers.map((m, j) => e("span", { key: "m" + j, className: "rounded bg-emerald-500/10 px-1.5 py-0.5 text-[8.5px] text-emerald-200" }, m.label || m.key)),
      kind === "suspect" && signals.map((s, j) => e("span", { key: "s" + j, className: "rounded bg-slate-500/10 px-1.5 py-0.5 text-[8.5px] text-slate-400" }, s.label || s.key)),
      Array.isArray(v.scanned_sources) && !v.scanned_sources.includes("description") &&
        e("span", { className: "text-[8.5px] text-slate-600", title: "该视频描述未采集,只扫了标题与深析文本" }, "描述未采集"),
    ),
  );
}

export function QualityCompliancePanel({ apiToken, kolPoolId }: any) {
  const [quality, setQuality] = React.useState<QualityResp | null>(null);
  const [scan, setScan] = React.useState<FtcResp | null>(null);

  // 开抽屉/换 KOL 只读拉取(后端纯聚合已有数据,读得起);单块失败安静缺席。
  React.useEffect(() => {
    setQuality(null);
    setScan(null);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    const base = `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}`;
    void apiFetch<QualityResp>(`${base}/quality-score`, {}, apiToken)
      .then((payload) => { if (!cancelled) setQuality(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setQuality(null); });
    void apiFetch<FtcResp>(`${base}/ftc-scan`, {}, apiToken)
      .then((payload) => { if (!cancelled) setScan(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setScan(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  const q = quality && String(quality.status || "") !== "error" ? quality : null;
  const f = scan && String(scan.status || "") !== "error" ? scan : null;
  if (!apiToken || !kolPoolId || (!q && !f)) return null; // 两块都缺席:整面板安静缺席

  const dims = Array.isArray(q?.dimensions) ? q!.dimensions! : [];
  const compositeScore = q?.composite?.score;
  const confLabel = CONFIDENCE_ZH[String(q?.confidence?.label || "")] || null;
  const summary = f?.summary || {};
  const suspects = Array.isArray(f?.groups?.undisclosed_suspect) ? f!.groups!.undisclosed_suspect! : [];
  const disclosed = Array.isArray(f?.groups?.disclosed) ? f!.groups!.disclosed! : [];
  const suspectCount = Number(summary.undisclosed_suspect) || 0;

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "quality_compliance",
      header: e(React.Fragment, null,
        e(Gauge, { size: 11, className: "text-emerald-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "质量 × 合规 · 质量分与披露扫描"),
        q?.status === "ready" && compositeScore != null &&
          e("span", { className: "rounded bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-200" }, `质量 ${compositeScore}`),
        f?.status === "ready" && suspectCount > 0 &&
          e("span", { className: "rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-200" }, `疑似未披露 ×${suspectCount}`),
      ),
    },
      // ── 📊 块一:综合质量分(final_v1 六维聚合)──
      q && BlockTitle(e("span", { className: "text-[10px]" }, "📊"), "综合质量分",
        q.status === "ready" && e("span", { className: "text-[8.5px] text-slate-600" }, `样本 ${Number(q.sample_size) || 0} 条深析 · 独立展示分,不写回任何表`)),
      q && (q.status === "empty"
        ? EmptyLine(String(q.reason || ""))
        : e(React.Fragment, null,
            e("div", { className: "flex items-baseline gap-2 mb-1.5" },
              e("span", { className: "text-[22px] font-bold tabular-nums text-emerald-200" },
                compositeScore != null ? String(compositeScore) : "—"),
              e("span", { className: "text-[10px] text-slate-500" }, "/ 100"),
              confLabel && e("span", {
                className: "rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] text-slate-400",
                title: "置信度按深析样本量标注:≥10 高 / 3-9 中 / <3 低",
              }, `置信度 ${confLabel}`),
              q.confidence?.llm_confidence_avg != null &&
                e("span", { className: "text-[8.5px] text-slate-600" }, `模型自报置信均值 ${q.confidence.llm_confidence_avg}`),
            ),
            e("div", { className: "space-y-1" }, dims.map((d, i) => DimRow(d, i))),
          )),

      // ── 🛡️ 块二:FTC 披露扫描(词表法,非法律结论)──
      f && BlockTitle(e(ShieldAlert, { size: 10, className: "text-amber-300" }), "FTC 披露扫描",
        f.status === "ready" && e("span", { className: "text-[8.5px] text-slate-600" },
          `扫描 ${Number(f.coverage?.evidence_count) || 0} 条 · 描述覆盖 ${Number(f.coverage?.with_description) || 0} · 深析覆盖 ${Number(f.coverage?.with_deep_analysis) || 0}`)),
      f && (f.status === "empty"
        ? EmptyLine(String(f.reason || ""))
        : e(React.Fragment, null,
            e("div", { className: "mb-1.5 flex flex-wrap items-center gap-1 text-[9px]" },
              e("span", { className: "rounded bg-emerald-500/10 px-1.5 py-0.5 text-emerald-200" }, `已披露 ${Number(summary.disclosed) || 0}`),
              e("span", { className: "rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-200" }, `疑似未披露 ${suspectCount}`),
              e("span", { className: "rounded bg-slate-500/10 px-1.5 py-0.5 text-slate-400" }, `无合作迹象 ${Number(summary.clean) || 0}`),
            ),
            suspects.length > 0 && e("div", { className: "space-y-1.5" },
              suspects.slice(0, 5).map((v, i) => ScanRow(v, i, "suspect")),
              suspects.length > 5 && e("div", { className: "text-[9px] text-slate-600" }, `另有 ${suspects.length - 5} 条疑似未披露(见后端清单)`),
            ),
            disclosed.length > 0 && e("div", { className: "mt-1.5 space-y-1.5" },
              disclosed.slice(0, 3).map((v, i) => ScanRow(v, i, "disclosed")),
              disclosed.length > 3 && e("div", { className: "text-[9px] text-slate-600" }, `另有 ${disclosed.length - 3} 条已披露`),
            ),
            f.coverage_note && e("div", { className: "mt-1 text-[9px] text-slate-600" }, String(f.coverage_note)),
          )),

      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        "口径:纯聚合已有数据(final_v1 深析读数 + 标题/描述词表扫描,否定感知);词表启发式,非法律结论;独立展示读数,不参与任何选人评分。"),
    ),
  );
}
