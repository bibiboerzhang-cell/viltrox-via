// 影子评测面板(L 轨道):「挑战者赢旧版才上线」—— 评测列表 + 跑一轮 + verdict 徽章。
// 数据:GET  /api/admin/vkpi/learning/shadow-evals(注册表元数据)
//      POST /api/admin/vkpi/learning/shadow-evals/{name}/run(纯读端留一回测,零写库/零 LLM)。
// 展示:challenger vs baseline 同指标对比(带内率/中位相对误差)+ 双赢判定徽章
//      + 样本口径 + 分 KOL 明细(前 8 行);两次运行 fingerprint 一致(决定性)。
// 诚实态:status==="empty"/"unavailable"/"error" 如实展示 reason;接口失败整块安静缺席。
// 红线:纯展示,影子结论只建议、绝不自动切换线上规则;绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { FlaskConical, Loader2, Play } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { kolHumanDisplayName } from "../lib/kolIdentity";

const e = React.createElement;

type EvalMeta = {
  name?: string; label?: string; description?: string;
  challenger?: string; baseline?: string;
};
type MetricBlock = {
  name?: string; band_hit_rate?: number | null; hits?: number;
  evaluated?: number; median_rel_error?: number | null;
};
type PerKolRow = {
  kol_pool_id?: number; handle?: string | null; sample_count?: number;
  challenger_band_hit_rate?: number | null; baseline_band_hit_rate?: number | null;
  challenger_median_rel_error?: number | null; baseline_median_rel_error?: number | null;
};
type RunResult = {
  status?: string; reason?: string; verdict?: string; recommendation?: string;
  challenger?: MetricBlock; baseline?: MetricBlock;
  samples?: {
    candidates_scanned?: number; eligible?: number; evaluated?: number;
    skipped_insufficient_history?: number; kol_count?: number; cap?: number;
    min_history?: number; ordering?: string;
  };
  per_kol?: PerKolRow[]; fingerprint?: string; note?: string;
};
type ListResp = { status?: string; evals?: EvalMeta[] };

function fmtPct(r: number | null | undefined): string {
  return typeof r === "number" && Number.isFinite(r) ? (Math.round(r * 1000) / 10).toFixed(1) + "%" : "—";
}

// verdict 徽章:只有 challenger_wins 才是「建议上线」,其余一律「维持旧版」。
function VerdictBadge(verdict: string | undefined) {
  const v = String(verdict || "");
  if (v === "challenger_wins") {
    return e("span", { className: "rounded bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-medium text-emerald-200" }, "双赢 · 建议上线");
  }
  if (v === "baseline_wins") {
    return e("span", { className: "rounded bg-rose-500/15 px-1.5 py-0.5 text-[9px] font-medium text-rose-200" }, "旧版胜 · 维持旧版");
  }
  if (v === "mixed") {
    return e("span", { className: "rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-medium text-amber-200" }, "各有胜负 · 维持旧版");
  }
  return null;
}

// challenger vs baseline 同指标对比行(带内率 / 中位相对误差,谁更优谁亮色)。
function MetricRow(label: string, c: number | null | undefined, b: number | null | undefined, higherBetter: boolean) {
  const cv = typeof c === "number" ? c : null;
  const bv = typeof b === "number" ? b : null;
  const cWins = cv != null && bv != null && (higherBetter ? cv > bv : cv < bv);
  const bWins = cv != null && bv != null && (higherBetter ? bv > cv : bv < cv);
  return e("div", { className: "flex items-center gap-2 text-[10px] tabular-nums" },
    e("span", { className: "w-[92px] shrink-0 text-slate-500" }, label),
    e("span", { className: "w-[64px] text-right " + (cWins ? "font-semibold text-emerald-300" : "text-slate-300") }, fmtPct(cv)),
    e("span", { className: "w-[64px] text-right " + (bWins ? "font-semibold text-emerald-300" : "text-slate-400") }, fmtPct(bv)),
  );
}

function PerKolTable(rows: PerKolRow[]) {
  if (rows.length === 0) return null;
  const shown = rows.slice(0, 8);
  return e("div", { className: "mt-1.5" },
    e("div", { className: "flex items-center gap-2 text-[8.5px] uppercase tracking-wider text-slate-600" },
      e("span", { className: "w-[128px] shrink-0" }, "分 KOL 明细"),
      e("span", { className: "w-[30px] text-right" }, "样本"),
      e("span", { className: "w-[74px] text-right" }, "带内 挑/基"),
      e("span", { className: "w-[84px] text-right" }, "误差 挑/基"),
    ),
    shown.map((row, i) => e("div", { key: row.kol_pool_id ?? i, className: "flex items-center gap-2 text-[9.5px] tabular-nums text-slate-400" },
      e("span", { className: "w-[128px] shrink-0 truncate text-slate-300", title: kolHumanDisplayName(row as unknown as Record<string, unknown>, `#${row.kol_pool_id ?? "—"}`) },
        kolHumanDisplayName(row as unknown as Record<string, unknown>, `#${row.kol_pool_id ?? "—"}`)),
      e("span", { className: "w-[30px] text-right" }, String(row.sample_count ?? 0)),
      e("span", { className: "w-[74px] text-right" }, fmtPct(row.challenger_band_hit_rate) + " / " + fmtPct(row.baseline_band_hit_rate)),
      e("span", { className: "w-[84px] text-right" }, fmtPct(row.challenger_median_rel_error) + " / " + fmtPct(row.baseline_median_rel_error)),
    )),
    rows.length > shown.length && e("div", { className: "mt-0.5 text-[8.5px] text-slate-600" }, `另有 ${rows.length - shown.length} 个 KOL 未展开`),
  );
}

// 单条评测行:元数据 + 跑一轮按钮 + 本轮结果(verdict 徽章 + 指标对比 + 样本口径)。
function EvalRow(
  meta: EvalMeta,
  result: RunResult | undefined,
  running: boolean,
  onRun: () => void,
) {
  const status = String(result?.status || "");
  const samples = result?.samples;
  return e("div", { className: "rounded border border-white/[0.05] bg-black/20 px-2.5 py-2" },
    e("div", { className: "flex items-center gap-2" },
      e("div", { className: "min-w-0 flex-1" },
        e("div", { className: "flex items-center gap-1.5" },
          e("span", { className: "truncate text-[11px] font-medium text-slate-200" }, meta.label || meta.name || "—"),
          result && status === "ready" && VerdictBadge(result.verdict),
        ),
        e("div", { className: "mt-0.5 text-[9px] leading-relaxed text-slate-500" }, meta.description || ""),
        (meta.challenger || meta.baseline) && e("div", { className: "mt-0.5 text-[8.5px] text-slate-600" },
          `挑战者 ${meta.challenger || "—"} vs 对照组 ${meta.baseline || "—"}`),
      ),
      e("button", {
        type: "button",
        disabled: running,
        onClick: onRun,
        className: "flex shrink-0 items-center gap-1 rounded border border-cyan-300/20 bg-cyan-400/10 px-2 py-1 text-[9.5px] text-cyan-200 hover:bg-cyan-400/20 disabled:cursor-not-allowed disabled:opacity-50",
      },
        running ? e(Loader2, { size: 10, className: "animate-spin" }) : e(Play, { size: 10 }),
        running ? "回测中…" : "跑一轮",
      ),
    ),

    // ── 本轮结果:诚实态优先(empty/unavailable/error 如实说 reason)──
    result && status !== "ready" && e("div", { className: "mt-1.5 text-[9.5px] leading-relaxed text-amber-300/90" },
      String(result.reason || "本轮评测未出结论")),
    result && status === "ready" && e(React.Fragment, null,
      e("div", { className: "mt-1.5 flex items-center gap-2 text-[8.5px] uppercase tracking-wider text-slate-600" },
        e("span", { className: "w-[92px] shrink-0" }, "指标"),
        e("span", { className: "w-[64px] text-right" }, "挑战者"),
        e("span", { className: "w-[64px] text-right" }, "对照组"),
      ),
      MetricRow("带内率 p10~p90", result.challenger?.band_hit_rate, result.baseline?.band_hit_rate, true),
      MetricRow("中位相对误差", result.challenger?.median_rel_error, result.baseline?.median_rel_error, false),
      result.recommendation && e("div", {
        className: "mt-1.5 rounded border px-1.5 py-1 text-[9.5px] leading-relaxed " + (
          result.verdict === "challenger_wins"
            ? "border-emerald-300/15 bg-emerald-400/[0.06] text-emerald-100/90"
            : "border-white/[0.06] bg-white/[0.03] text-slate-300"
        ),
      }, result.recommendation),
      samples && e("div", { className: "mt-1 text-[8.5px] leading-relaxed text-slate-600" },
        `样本:合格 ${Number(samples.eligible) || 0} / 实评 ${Number(samples.evaluated) || 0}` +
        `(历史不足跳过 ${Number(samples.skipped_insufficient_history) || 0},覆盖 ${Number(samples.kol_count) || 0} 个 KOL,上限 ${Number(samples.cap) || 0});` +
        String(samples.ordering || "")),
      PerKolTable(Array.isArray(result.per_kol) ? result.per_kol : []),
      result.fingerprint && e("div", { className: "mt-1 truncate text-[8px] text-slate-700", title: result.fingerprint },
        "fingerprint " + String(result.fingerprint).slice(0, 16) + "…(决定性:两次运行必一致)"),
    ),
  );
}

export function ShadowEvalPanel({ apiToken }: { apiToken: string }) {
  const [evals, setEvals] = React.useState<EvalMeta[] | null>(null);
  const [results, setResults] = React.useState<Record<string, RunResult>>({});
  const [runningName, setRunningName] = React.useState<string | null>(null);

  // 挂载拉取注册表(元数据不触发计算,读得起);失败静默不渲染。
  React.useEffect(() => {
    setEvals(null);
    if (!apiToken) return;
    let cancelled = false;
    void apiFetch<ListResp>("/api/admin/vkpi/learning/shadow-evals", {}, apiToken)
      .then((payload) => {
        if (!cancelled) setEvals(Array.isArray(payload?.evals) ? payload.evals : []);
      })
      .catch(() => { if (!cancelled) setEvals(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

  const runOne = React.useCallback((name: string) => {
    if (!apiToken || !name) return;
    setRunningName(name);
    void apiFetch<RunResult>(
      `/api/admin/vkpi/learning/shadow-evals/${encodeURIComponent(name)}/run`,
      { method: "POST", timeoutMs: 120_000 },
      apiToken,
    )
      .then((payload) => {
        setResults((prev) => ({ ...prev, [name]: payload && typeof payload === "object" ? payload : { status: "error", reason: "响应异常" } }));
      })
      .catch((err: unknown) => {
        setResults((prev) => ({ ...prev, [name]: { status: "error", reason: err instanceof Error ? err.message : "评测请求失败" } }));
      })
      .finally(() => { setRunningName((cur) => (cur === name ? null : cur)); });
  }, [apiToken]);

  if (!apiToken || !evals || evals.length === 0) return null;

  return e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
    e("div", { className: "flex items-center gap-1.5" },
      e(FlaskConical, { size: 11, className: "text-purple-300" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "影子评测 · 赢旧版才上线"),
      e("span", { className: "rounded bg-purple-500/10 px-1.5 py-0.5 text-[9px] text-purple-200" }, `${evals.length} 个评测`),
    ),
    e("div", { className: "mt-2 space-y-2" },
      evals.map((meta, i) => {
        const name = String(meta.name || "");
        return e(React.Fragment, { key: name || i },
          EvalRow(meta, results[name], runningName === name, () => runOne(name)),
        );
      }),
    ),
    e("div", { className: "mt-1.5 text-[8.5px] leading-relaxed text-slate-600" },
      "口径:留一回测纯读端计算(零写库/零 LLM,决定性可复算);影子结论只建议,绝不自动切换线上规则,不参与 V6 Fit 评分。"),
  );
}
