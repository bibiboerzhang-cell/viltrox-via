// 以视频找相似:「还有谁在拍同款内容」增益块(给一条已深析视频找全库相似视频+背后 KOL)。
// 数据:GET /api/admin/vkpi/video/{evidenceId}/similar —— 决定性降级检索
// (final_v1 数值维余弦 + 词表标签 Jaccard 混合,method=dimensions_cosine_v0),
// 零 LLM/零新采集,后端默认排除同 KOL 自己的视频。
// 诚实态:status==="empty" 时如实展示后端 reason(种子未深析/语料不足);接口失败或
// status==="error" 整块安静缺席(非阻塞增益块);相似原因只展示后端给的真实共同标签。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { Radar } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";
import { kolHumanDisplayName } from "../lib/kolIdentity";

const e = React.createElement;

type SimilarKol = { id?: number | null; handle?: string; display_name?: string };
type SimilarItem = {
  evidence_id?: number; title?: string; url?: string; platform?: string;
  view_count?: number | null; posted_at?: string | null;
  kol?: SimilarKol;
  similarity?: number;
  score_parts?: { cosine?: number | null; tag_jaccard?: number | null; basis?: string };
  shared_tags?: string[];
};
type SimilarResp = {
  status?: string;
  reason?: string;
  method?: string;
  corpus_size?: number;
  excluded_same_kol?: number;
  seed?: {
    evidence_id?: number; title?: string; kol_handle?: string; kol_display_name?: string;
    vector_dims?: number; tags?: string[];
  };
  items?: SimilarItem[];
};

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}

function fmtSim(s: number | null | undefined): string {
  return typeof s === "number" && Number.isFinite(s) ? (Math.round(s * 1000) / 10).toFixed(1) + "%" : "—";
}

// 单条相似视频行:排名 + 相似度条 + 标题链接 + 所属 KOL + 共同标签(=相似原因)。
function SimilarRow(v: SimilarItem, i: number) {
  const sim = typeof v.similarity === "number" ? Math.max(0, Math.min(1, v.similarity)) : 0;
  const kol = v.kol || {};
  const kolName = kolHumanDisplayName(kol as unknown as Record<string, unknown>);
  const tags = Array.isArray(v.shared_tags) ? v.shared_tags : [];
  return e("div", { key: v.evidence_id ?? i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
    e("div", { className: "flex items-center gap-1.5" },
      e("span", { className: "shrink-0 text-[9px] font-bold tabular-nums " + (i === 0 ? "text-cyan-300" : "text-slate-500") }, "#" + (i + 1)),
      v.url
        ? e("a", {
            href: v.url, target: "_blank", rel: "noreferrer",
            className: "truncate text-[10.5px] text-slate-200 hover:text-cyan-200 hover:underline",
            title: v.title || v.url,
          }, v.title || v.url)
        : e("span", { className: "truncate text-[10.5px] text-slate-200" }, v.title || "(无标题)"),
      e("span", { className: "ml-auto shrink-0 tabular-nums text-[10px] font-semibold text-cyan-200" }, fmtSim(v.similarity)),
    ),
    // 相似度条(混合分 0-1 → 宽度);决定性分数,同请求可复算
    e("div", { className: "mt-1 relative h-[5px] overflow-hidden rounded-full bg-white/[0.05]" },
      e("div", {
        className: "absolute inset-y-0 left-0 rounded-full",
        style: { width: Math.max(3, Math.round(sim * 100)) + "%", background: "linear-gradient(90deg, rgba(34,211,238,0.35), rgba(34,211,238,0.75))" },
      }),
    ),
    e("div", { className: "mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9px] tabular-nums text-slate-500" },
      kolName && e("span", { className: "rounded bg-purple-500/10 px-1.5 py-0.5 text-purple-200", title: "背后 KOL" },
        "@" + kolName + (kol.id != null ? " · #" + kol.id : "")),
      e("span", null, "播放 ", e("span", { className: "text-slate-300 font-medium" }, fmtNum(v.view_count))),
      v.platform && e("span", { className: "text-slate-600" }, v.platform),
      v.posted_at && e("span", { className: "text-slate-600" }, String(v.posted_at).slice(0, 10)),
    ),
    // 相似原因 = 后端算出的真实共同标签;没有共同标签就如实说(纯数值维相似)
    tags.length > 0
      ? e("div", { className: "mt-1 flex flex-wrap items-center gap-1" },
          tags.slice(0, 8).map((t, j) => e("span", {
            key: j,
            className: "rounded border border-white/[0.06] bg-black/20 px-1.5 py-0.5 text-[9px] text-slate-300",
          }, t)),
        )
      : e("div", { className: "mt-1 text-[9px] text-slate-600" }, "无共同内容标签(仅数值维相似)"),
  );
}

export function SimilarVideosPanel({ apiToken, evidenceId }: any) {
  const [data, setData] = React.useState<SimilarResp | null>(null);

  // 换种子视频只读拉取(后端读端聚合 ≈500 行语料,读得起);失败静默不渲染。
  React.useEffect(() => {
    setData(null);
    if (!apiToken || !evidenceId) return;
    let cancelled = false;
    void apiFetch<SimilarResp>(
      `/api/admin/vkpi/video/${encodeURIComponent(String(evidenceId))}/similar?limit=10`,
      {},
      apiToken,
    )
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken, evidenceId]);

  if (!apiToken || !evidenceId || !data) return null;
  if (String(data.status || "") === "error") return null; // 聚合失败:安静缺席,不甩后端报错

  const items = Array.isArray(data.items) ? data.items : [];
  const seed = data.seed || {};
  const corpus = Number(data.corpus_size) || 0;

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "similar-videos",
      header: e(React.Fragment, null,
        e(Radar, { size: 11, className: "text-cyan-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "同款雷达 · 还有谁在拍同款内容"),
        corpus > 0 && e("span", { className: "rounded bg-cyan-500/10 px-1.5 py-0.5 text-[9px] text-cyan-200" }, `语料 ${corpus} 条深析`),
      ),
    },
      // 空态:种子未深析 / 语料不足,如实展示后端 reason(不本地编造)
      String(data.status || "") === "empty"
        ? e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" }, String(data.reason || "暂无数据"))
        : e(React.Fragment, null,
            seed.title && e("div", { className: "mb-1.5 text-[9px] text-slate-500" },
              "种子:", e("span", { className: "text-slate-300" }, String(seed.title).slice(0, 60)),
              typeof seed.vector_dims === "number" ? ` · ${seed.vector_dims} 数值维` : "",
              (Number(data.excluded_same_kol) || 0) > 0 ? ` · 已排除同 KOL ${data.excluded_same_kol} 条` : "",
            ),
            e("div", { className: "space-y-1.5" }, items.map((v, i) => SimilarRow(v, i))),
          ),
      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        `口径:决定性检索(${String(data.method || "dimensions_cosine_v0")})= final_v1 数值维余弦 + 词表标签 Jaccard;零 LLM,独立展示信号,不参与 V6 Fit 评分。`),
    ),
  );
}
