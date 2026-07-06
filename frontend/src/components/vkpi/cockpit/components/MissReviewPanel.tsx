// L 轨道 · 低命中复盘面板:哪里没打中、为什么,失败原因一键入记忆。
// 数据:GET /api/admin/vkpi/learning/miss-review —— 未命中/失败条目按 action_type 分组
// (alert 未闭环 / 推荐负反馈 / 任务 failed 带 last_error),词表聚类失败原因(零 LLM),
// 低命中组(hit<0.5 且样本>=5)标 needs_review。
// 写:POST /api/admin/vkpi/learning/miss-review/persist?dry_run= —— 经既有
// agent_memory_writer.record_signal 落学习留痕表;dry_run 预览;同组同日幂等跳过。
// 诚实态:每组 status==="empty"/"error" 如实展示 reason;接口失败明示,不装有数据。
// 红线:纯展示 + 只调 persist 端点;绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { AlertTriangle, Brain, Eye, RefreshCw } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

type ReasonItem = { key?: string; label?: string; count?: number; samples?: string[] };
type HitInfo = {
  hit_rate?: number | null; sample_count?: number; status?: string;
  confidence?: string; source?: string; note?: string | null;
};
type GroupItem = {
  action_type?: string; label?: string; status?: string; reason?: string;
  miss_count?: number; needs_review?: boolean; hit?: HitInfo;
  reasons?: ReasonItem[]; source?: string[];
};
type MissReviewResp = {
  status?: string; reason?: string; groups?: GroupItem[];
  totals?: { groups?: number; miss_total?: number; needs_review_groups?: number; groups_with_miss?: number };
  thresholds?: { needs_review?: string };
  generated_at?: string;
};
type PersistEntry = {
  action_type?: string; action?: string; summary?: string;
  agent_action_id?: number | null; miss_count?: number; needs_review?: boolean;
};
type PersistResp = {
  status?: string; reason?: string; dry_run?: boolean;
  written?: number; skipped_same_day?: number; would_write?: number; write_failed?: number;
  entries?: PersistEntry[];
};

function fmtRate(r: number | null | undefined): string {
  return typeof r === "number" && Number.isFinite(r) ? (Math.round(r * 1000) / 10).toFixed(1) + "%" : "判不出";
}

// 组内失败原因行:label + 数量条 + 原文样本(词表聚类,unclassified 如实标未归类)。
function ReasonRow(r: ReasonItem, i: number, maxCount: number) {
  const count = Number(r.count) || 0;
  const widthPct = maxCount > 0 ? Math.max(6, Math.round((count / maxCount) * 100)) : 0;
  const samples = Array.isArray(r.samples) ? r.samples : [];
  return e("div", { key: r.key || i, className: "space-y-0.5" },
    e("div", { className: "flex items-center gap-2 text-[10px]" },
      e("span", { className: "w-[110px] shrink-0 truncate text-slate-300", title: r.label }, r.label || r.key || "—"),
      e("div", { className: "relative h-[7px] flex-1 overflow-hidden rounded-full bg-white/[0.05]" },
        e("div", {
          className: "absolute inset-y-0 left-0 rounded-full",
          style: { width: widthPct + "%", background: "linear-gradient(90deg, rgba(244,63,94,0.35), rgba(244,63,94,0.7))" },
        }),
      ),
      e("span", { className: "w-[34px] shrink-0 text-right tabular-nums text-slate-400" }, "×" + count),
    ),
    samples.length > 0 && e("div", { className: "pl-1 text-[9px] leading-relaxed text-slate-500 truncate", title: samples.join(" | ") },
      "例:" + samples[0]),
  );
}

function GroupCard(g: GroupItem, i: number) {
  const reasons = Array.isArray(g.reasons) ? g.reasons : [];
  const maxCount = reasons.reduce((acc, r) => Math.max(acc, Number(r.count) || 0), 0);
  const hit = g.hit || {};
  return e("div", { key: g.action_type || i, className: "rounded border border-white/[0.05] bg-black/20 px-2.5 py-2" },
    e("div", { className: "flex flex-wrap items-center gap-1.5" },
      e("span", { className: "text-[10.5px] font-medium text-slate-200" }, g.label || g.action_type || "—"),
      e("span", { className: "rounded bg-white/[0.04] px-1.5 py-0.5 text-[8.5px] text-slate-500 font-mono" }, g.action_type || ""),
      g.needs_review && e("span", { className: "flex items-center gap-1 rounded bg-rose-500/15 px-1.5 py-0.5 text-[9px] text-rose-200" },
        e(AlertTriangle, { size: 9 }), "低命中需复盘"),
    ),
    e("div", { className: "mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9px] tabular-nums text-slate-500" },
      e("span", null, "失败/未命中 ", e("span", { className: "font-medium text-rose-300" }, String(Number(g.miss_count) || 0)), " 条"),
      e("span", { title: hit.note || "" }, "命中率 ",
        e("span", { className: "font-medium " + (g.needs_review ? "text-rose-300" : "text-slate-300") }, fmtRate(hit.hit_rate)),
        `(样本 ${Number(hit.sample_count) || 0})`),
      Array.isArray(g.source) && g.source.length > 0 && e("span", { className: "text-slate-600" }, "源:" + g.source.join("+")),
    ),
    g.status === "ready"
      ? e("div", { className: "mt-1.5 space-y-1" }, reasons.slice(0, 6).map((r, j) => ReasonRow(r, j, maxCount)))
      : e("div", { className: "mt-1 text-[9.5px] leading-relaxed " + (g.status === "error" ? "text-rose-300/90" : "text-amber-300/90") },
          String(g.reason || "暂无数据")),
  );
}

export function MissReviewPanel({ apiToken }: { apiToken: string }) {
  const [data, setData] = React.useState<MissReviewResp | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [persisting, setPersisting] = React.useState(false);
  const [persist, setPersist] = React.useState<PersistResp | null>(null);

  const load = React.useCallback(() => {
    if (!apiToken) return;
    setLoading(true);
    setLoadError(null);
    void apiFetch<MissReviewResp>("/api/admin/vkpi/learning/miss-review", {}, apiToken)
      .then((payload) => setData(payload && typeof payload === "object" ? payload : null))
      .catch((err) => { setData(null); setLoadError(err instanceof Error ? err.message : String(err)); })
      .finally(() => setLoading(false));
  }, [apiToken]);

  React.useEffect(() => { load(); }, [load]);

  // 「入记忆」/「预览」共用:走 persist 端点;真写后刷新结果(同日再点=幂等跳过,后端保证)。
  const runPersist = React.useCallback((dryRun: boolean) => {
    if (!apiToken || persisting) return;
    setPersisting(true);
    setPersist(null);
    void apiFetch<PersistResp>(
      `/api/admin/vkpi/learning/miss-review/persist?dry_run=${dryRun ? "true" : "false"}`,
      { method: "POST" },
      apiToken,
    )
      .then((payload) => setPersist(payload && typeof payload === "object" ? payload : null))
      .catch((err) => setPersist({ status: "error", reason: err instanceof Error ? err.message : String(err) }))
      .finally(() => setPersisting(false));
  }, [apiToken, persisting]);

  if (!apiToken) return null;

  const groups = Array.isArray(data?.groups) ? data!.groups! : [];
  const totals = data?.totals || {};
  const persistOk = persist && persist.status === "ok";

  return e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2.5" },
    // ── 头部:标题 + 统计 + 刷新 ──
    e("div", { className: "flex flex-wrap items-center gap-1.5" },
      e(Brain, { size: 12, className: "text-rose-300" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "低命中复盘 · 失败入记忆"),
      (Number(totals.miss_total) || 0) > 0 && e("span", { className: "rounded bg-rose-500/10 px-1.5 py-0.5 text-[9px] text-rose-200" },
        `失败 ${totals.miss_total} 条`),
      (Number(totals.needs_review_groups) || 0) > 0 && e("span", { className: "rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-200" },
        `${totals.needs_review_groups} 组低命中`),
      e("button", {
        type: "button",
        className: "ml-auto flex items-center gap-1 rounded border border-white/[0.08] px-1.5 py-0.5 text-[9px] text-slate-400 hover:text-slate-200 disabled:opacity-50",
        onClick: load,
        disabled: loading,
      }, e(RefreshCw, { size: 9, className: loading ? "animate-spin" : "" }), loading ? "加载中" : "刷新"),
    ),

    // ── 主体:分组卡片(诚实态:错误/空组如实说)──
    loadError
      ? e("div", { className: "mt-2 text-[10px] text-rose-300/90" }, "加载失败:" + loadError)
      : data && String(data.status || "") === "error"
        ? e("div", { className: "mt-2 text-[10px] text-rose-300/90" }, "聚合失败:" + String(data.reason || "未知原因"))
        : groups.length === 0
          ? e("div", { className: "mt-2 text-[10px] text-slate-500" }, loading ? "加载中…" : "暂无分组数据")
          : e("div", { className: "mt-2 space-y-1.5" }, groups.map((g, i) => GroupCard(g, i))),

    // ── 底部:预览 + 入记忆(走 persist 端点;dry_run 默认预览,真写同日幂等)──
    e("div", { className: "mt-2 flex flex-wrap items-center gap-1.5 border-t border-white/[0.05] pt-2" },
      e("button", {
        type: "button",
        className: "flex items-center gap-1 rounded border border-white/[0.08] px-2 py-1 text-[9.5px] text-slate-300 hover:text-slate-100 disabled:opacity-50",
        onClick: () => runPersist(true),
        disabled: persisting,
        title: "dry_run=true:只预览要写入记忆的摘要,零写库",
      }, e(Eye, { size: 10 }), "预览入记忆"),
      e("button", {
        type: "button",
        className: "flex items-center gap-1 rounded border border-rose-400/30 bg-rose-500/10 px-2 py-1 text-[9.5px] text-rose-200 hover:bg-rose-500/20 disabled:opacity-50",
        onClick: () => runPersist(false),
        disabled: persisting,
        title: "dry_run=false:经既有 record_signal 真写学习留痕表;同组同日不重复写",
      }, e(Brain, { size: 10 }), persisting ? "处理中…" : "入记忆"),
      e("span", { className: "text-[8.5px] text-slate-600" }, "写只落学习留痕表;不改任何线上规则,不触碰评分"),
    ),

    // ── persist 结果(写了几条/幂等跳过几条,逐组摘要)──
    persist && e("div", { className: "mt-1.5 rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
      persistOk
        ? e(React.Fragment, null,
            e("div", { className: "flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9.5px] tabular-nums" },
              e("span", { className: "text-slate-400" }, persist!.dry_run ? "预览(未写库):" : "写入结果:"),
              persist!.dry_run
                ? e("span", { className: "text-cyan-200" }, `将写 ${Number(persist!.would_write) || 0} 组`)
                : e("span", { className: "text-emerald-200" }, `已写 ${Number(persist!.written) || 0} 组`),
              (Number(persist!.skipped_same_day) || 0) > 0 && e("span", { className: "text-amber-200" },
                `同日已写跳过 ${persist!.skipped_same_day} 组`),
              (Number(persist!.write_failed) || 0) > 0 && e("span", { className: "text-rose-300" },
                `写失败 ${persist!.write_failed} 组`),
            ),
            e("div", { className: "mt-1 space-y-0.5" },
              (Array.isArray(persist!.entries) ? persist!.entries! : []).map((it, i) =>
                e("div", { key: i, className: "text-[9px] leading-relaxed text-slate-500 truncate", title: it.summary || "" },
                  e("span", {
                    className: it.action === "written" ? "text-emerald-300"
                      : it.action === "skip_same_day" ? "text-amber-300"
                      : it.action === "write_failed" ? "text-rose-300" : "text-cyan-300",
                  }, it.action === "written" ? "已写 " : it.action === "skip_same_day" ? "跳过 " : it.action === "write_failed" ? "失败 " : "预览 "),
                  it.summary || it.action_type || "",
                ),
              ),
            ),
          )
        : e("div", { className: "text-[9.5px] text-rose-300/90" }, "入记忆失败:" + String(persist!.reason || "未知原因")),
    ),
  );
}
