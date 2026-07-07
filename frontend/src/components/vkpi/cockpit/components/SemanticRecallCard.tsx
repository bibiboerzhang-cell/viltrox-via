// 语义召回卡:三段式语义找人(词表召回漏语义相近创作者的补刀入口)。
// 数据:GET /api/admin/vkpi/recall/semantic?query=&limit=20 —— 三段式管线:
//   ①召回(embedding 优先;不可用决定性降级 3-gram+词表,method=ngram_fallback_v0)
//   ②final_v1 十一维余弦粗排 ③LLM top-20 重排逐人「为何合适」(预算闸+当日缓存)。
// 展示:stages 三段追溯条(召回方式/候选数/粗排 n/重排状态+成本注记)+ 候选列表
//   (why_fit 一句话优先,无 LLM 时如实显示召回/粗排依据)。
// 诚实态:降级醒目标注「降级召回」;rerank=skipped 如实标注原因;接口失败一行灰字;
//   空结果如实说,绝不编人。红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { AlertTriangle, ExternalLink, Search, Sparkles } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

type RecallItem = {
  rank?: number; kol_pool_id?: number; handle?: string; display_name?: string;
  platform?: string; profile_url?: string; avatar_url?: string;
  followers?: number | null; country?: string; bio?: string; profile_type?: string;
  recall_score?: number; recall_basis?: string;
  coarse_score?: number; coarse_basis?: string; dims_cosine?: number | null; dims_count?: number;
  rerank_score?: number; llm_relevance?: number; why_fit?: string;
};
type Stages = {
  recall?: { method?: string; candidates?: number; degraded_reason?: string };
  coarse?: { n?: number; with_dims_cosine?: number };
  rerank?: { status?: string; cost_note?: string; top_n?: number; applied?: number };
};
type RecallResp = {
  status?: string; reason?: string; method?: string;
  query?: { query_text?: string; limit?: number };
  items?: RecallItem[]; stages?: Stages;
};

function fmtFollowers(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}

// 三段追溯条:召回方式 / 粗排 n / 重排状态,各一枚 chip,降级醒目。
function StageChips(stages: Stages) {
  const recall = stages.recall || {};
  const coarse = stages.coarse || {};
  const rerank = stages.rerank || {};
  const degraded = String(recall.method || "") === "ngram_fallback_v0";
  const rerankStatus = String(rerank.status || "");
  const rerankTone =
    rerankStatus === "ok" ? "bg-emerald-400/10 text-emerald-300"
    : rerankStatus === "cached" ? "bg-cyan-400/10 text-cyan-200"
    : "bg-white/[0.05] text-slate-500";
  return e("div", { className: "flex flex-wrap items-center gap-1.5 px-3 py-1.5 border-b border-white/[0.05]" },
    e("span", {
      className: "rounded px-1.5 py-0.5 text-[9px] " +
        (degraded ? "bg-amber-400/10 text-amber-300" : "bg-cyan-400/10 text-cyan-200"),
      title: degraded
        ? "embedding 不可用(无网/无 key/预算闸),已决定性降级:字符 3-gram + 词表标签混合召回" +
          (recall.degraded_reason ? ` — ${recall.degraded_reason}` : "")
        : "OpenAI embedding × Qdrant 档案索引召回",
    },
      degraded ? "① 降级召回 ngram" : "① 向量召回",
      ` · ${Number(recall.candidates) || 0}人`,
    ),
    e("span", {
      className: "rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] text-slate-400",
      title: "final_v1 十一维余弦粗排(video_similarity 向量口径)" +
        (coarse.with_dims_cosine != null ? ` — ${coarse.with_dims_cosine} 人有十一维向量` : ""),
    }, `② 粗排 ${Number(coarse.n) || 0}`),
    e("span", {
      className: "rounded px-1.5 py-0.5 text-[9px] " + rerankTone,
      title: `LLM top-${Number(rerank.top_n) || 20} 重排 · ${rerank.cost_note || ""}`,
    },
      rerankStatus === "ok" ? "③ 重排 ok"
      : rerankStatus === "cached" ? "③ 重排 缓存"
      : "③ 重排 跳过",
    ),
    degraded && e(AlertTriangle, { size: 11, className: "text-amber-400/80", strokeWidth: 2 }),
  );
}

// 单候选行:名字/平台/粉丝 + 「为何合适」一句话(无 LLM 时如实显示召回依据)。
function CandidateRow(it: RecallItem, i: number) {
  const name = it.display_name || it.handle || `KOL #${it.kol_pool_id || "?"}`;
  const why = it.why_fit
    || (it.coarse_basis === "recall+dims_cosine"
        ? `召回 ${((Number(it.recall_score) || 0)).toFixed(3)} + 十一维余弦 ${it.dims_cosine != null ? Number(it.dims_cosine).toFixed(3) : "—"}`
        : `召回依据:${it.recall_basis || "—"} ${((Number(it.recall_score) || 0)).toFixed(3)}`);
  return e("div", { key: it.kol_pool_id || i, className: "flex items-start gap-2 px-3 py-1.5" },
    e("span", { className: "mt-0.5 w-[18px] shrink-0 text-right text-[9px] tabular-nums text-slate-600" }, String(it.rank ?? i + 1)),
    it.avatar_url
      ? e("img", { src: it.avatar_url, className: "mt-0.5 h-5 w-5 shrink-0 rounded-full object-cover", alt: "" })
      : e("div", { className: "mt-0.5 h-5 w-5 shrink-0 rounded-full bg-white/[0.06]" }),
    e("div", { className: "min-w-0 flex-1" },
      e("div", { className: "flex items-center gap-1.5" },
        e("span", { className: "truncate text-[11px] font-medium text-slate-200" }, name),
        it.platform && e("span", { className: "rounded bg-white/[0.05] px-1 py-px text-[8px] uppercase text-slate-500" }, it.platform),
        e("span", { className: "text-[9px] tabular-nums text-slate-500" }, fmtFollowers(it.followers) + " 粉"),
        it.llm_relevance != null && e("span", {
          className: "rounded bg-violet-400/10 px-1 py-px text-[8.5px] tabular-nums text-violet-300",
          title: "LLM 相关度(仅展示排序,不参与任何评分列)",
        }, "LLM " + Math.round(Number(it.llm_relevance))),
        it.profile_url && e("a", {
          href: it.profile_url, target: "_blank", rel: "noreferrer",
          className: "text-slate-600 hover:text-cyan-300",
        }, e(ExternalLink, { size: 10, strokeWidth: 2 })),
      ),
      e("div", { className: "mt-0.5 truncate text-[9.5px] leading-relaxed " + (it.why_fit ? "text-cyan-200/80" : "text-slate-500"), title: why }, why),
    ),
  );
}

export function SemanticRecallCard({ apiToken }: any) {
  const [query, setQuery] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [data, setData] = React.useState<RecallResp | null>(null);
  const [error, setError] = React.useState("");

  const run = React.useCallback(() => {
    const q = query.trim();
    if (!q || !apiToken || loading) return;
    setLoading(true);
    setError("");
    setData(null);
    void apiFetch<RecallResp>(
      `/api/admin/vkpi/recall/semantic?query=${encodeURIComponent(q)}&limit=20`,
      { timeoutMs: 60000 },
      apiToken,
    )
      .then((payload) => setData(payload && typeof payload === "object" ? payload : null))
      .catch(() => setError("召回接口暂不可用"))
      .finally(() => setLoading(false));
  }, [query, apiToken, loading]);

  if (!apiToken) return null;
  const items = Array.isArray(data?.items) ? (data?.items as RecallItem[]) : [];
  const stages = data?.stages || {};

  return e("div", { className: "mb-4 rounded-xl border border-white/[0.06] bg-white/[0.012] overflow-hidden" },
    e("div", { className: "flex items-center gap-2 px-3 py-1.5 border-b border-white/[0.05]" },
      e(Sparkles, { size: 13, className: "text-violet-400", strokeWidth: 2 }),
      e("span", { className: "text-[11px] font-medium text-slate-300" }, "语义找人 · 三段式召回"),
      e("span", { className: "text-[8.5px] text-slate-600" }, "embedding→十一维粗排→LLM重排 · 不参与 Fit 评分"),
    ),
    e("div", { className: "flex items-center gap-1.5 px-3 py-2" },
      e("input", {
        value: query,
        onChange: (ev: any) => setQuery(ev.target.value),
        onKeyDown: (ev: any) => { if (ev.key === "Enter") run(); },
        placeholder: "描述你要找的创作者,如 cinematic wedding videographer",
        className: "min-w-0 flex-1 rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[11px] text-slate-200 placeholder:text-slate-600 outline-none focus:border-cyan-400/40",
      }),
      e("button", {
        onClick: run,
        disabled: loading || !query.trim(),
        className: "flex shrink-0 items-center gap-1 rounded-md bg-cyan-400/15 px-2 py-1 text-[10px] text-cyan-200 hover:bg-cyan-400/25 disabled:opacity-40",
      }, e(Search, { size: 11, strokeWidth: 2 }), loading ? "召回中…" : "找人"),
    ),
    error && e("div", { className: "px-3 pb-2 text-[10px] text-slate-500" }, error),
    data && String(data.status || "") === "error" &&
      e("div", { className: "px-3 pb-2 text-[10px] text-slate-500" }, "召回失败:" + (data.reason || "未知原因")),
    data && String(data.status || "") === "empty" &&
      e(React.Fragment, null,
        StageChips(stages),
        e("div", { className: "px-3 py-2 text-[10px] text-slate-500" }, data.reason || "没有命中的候选"),
      ),
    data && String(data.status || "") === "ready" &&
      e(React.Fragment, null,
        StageChips(stages),
        e("div", { className: "divide-y divide-white/[0.04]" }, items.map((it, i) => CandidateRow(it, i))),
        e("div", { className: "px-3 pb-1.5 pt-1 text-[8.5px] leading-relaxed text-slate-600" },
          `召回 ${Number(stages.recall?.candidates) || 0} 人 → 粗排 ${Number(stages.coarse?.n) || 0} → ` +
          (String(stages.rerank?.status) === "ok" ? "LLM 重排完成"
            : String(stages.rerank?.status) === "cached" ? "LLM 重排(当日缓存)"
            : `重排跳过(${stages.rerank?.cost_note || "预算/网络"})`) +
          " · 纯读展示信号 · 不参与 Fit 评分"),
      ),
  );
}
