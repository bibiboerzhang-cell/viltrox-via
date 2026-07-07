// 闭环波 L4 裁决一屏(Action Inbox 内嵌):当时预判/预期 vs 三窗实际 → 一键 decision + lesson。
// 数据:GET /api/admin/vkpi/gtm/verdicts/{id}/context?id_type=inbox|outcome(L4 只读路由)。
// 裁决:POST /api/admin/vkpi/gtm/verdicts/{id}/decide(L2 端点)body {decision, lesson, id_type}。
// id 口径与 L2 对齐:inbox=bet 的 action_inbox id(vkpi_gtm_outcomes.action_inbox_id 桥)/
//                    outcome=结果行 id 直查。
// 红线:裁决只能人工点按(绝无自动裁决);lesson 一句话必填(强制学习记录);
//       decided 即 finalized,已裁决行只读展示不再给按钮。零触 viltrox_fit_score。
// 诚实态:迁移 217 未上线 / context 读取失败 → 用 Inbox payload 快照兜底渲染并如实标注。
import React from "react";
import { CheckCircle2, Gavel, Loader2 } from "lucide-react";
import { apiFetch, jsonBody } from "../../../../services/http";

const e = React.createElement;

type Dict = Record<string, unknown>;

type VerdictOutcome = {
  id?: number;
  gtm_plan_id?: string | null;
  product_sku?: string | null;
  market?: string | null;
  segment?: string | null;
  channel?: string | null;
  action_type?: string | null;
  content_angle?: string | null;
  expected_result?: Dict;
  actual_result?: Dict;
  window_7d?: Dict;
  window_14d?: Dict;
  window_28d?: Dict;
  decision?: string | null;
  lesson?: string | null;
  review_at?: string | null;
  created_at?: string | null;
  decided_at?: string | null;
};

type VerdictContext = {
  available?: boolean;
  reason?: string;
  outcome?: VerdictOutcome;
  weight_preview?: {
    counts?: { total?: number; actionable?: number; held?: number; recorded_only?: number };
    min_sample?: number;
  } | null;
};

// 六个 decision(规格第四章:验证成立/证伪/部分/重试/加码/撤退)。
export const VERDICT_DECISIONS: Array<{ key: string; label: string; cls: string }> = [
  { key: "validated", label: "验证成立", cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20" },
  { key: "failed", label: "证伪", cls: "border-rose-500/30 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20" },
  { key: "partial", label: "部分成立", cls: "border-amber-500/30 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20" },
  { key: "retry", label: "重试", cls: "border-sky-500/30 bg-sky-500/10 text-sky-300 hover:bg-sky-500/20" },
  { key: "escalate", label: "加码", cls: "border-violet-500/30 bg-violet-500/10 text-violet-300 hover:bg-violet-500/20" },
  { key: "retreat", label: "撤退", cls: "border-orange-500/30 bg-orange-500/10 text-orange-300 hover:bg-orange-500/20" },
];

const DECIDED_SET = new Set(VERDICT_DECISIONS.map((d) => d.key));
const DECISION_LABEL: Record<string, string> = Object.fromEntries(
  VERDICT_DECISIONS.map((d) => [d.key, d.label]),
);

function asDict(value: unknown): Dict {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Dict) : {};
}

// kv 展平:对象 → 最多 8 行 [key, 短字符串](裁决读数一屏能看完,不塞原始 JSON 墙)。
function kvLines(value: unknown): Array<[string, string]> {
  const obj = asDict(value);
  return Object.entries(obj)
    .slice(0, 8)
    .map(([k, v]) => [
      k,
      typeof v === "object" && v !== null ? JSON.stringify(v).slice(0, 80) : String(v ?? "—").slice(0, 80),
    ]);
}

function KvBlock(title: string, value: unknown, emptyNote: string, keyPrefix: string) {
  const lines = kvLines(value);
  return e(
    "div",
    { key: keyPrefix, className: "rounded border border-white/[0.06] bg-black/20 px-2 py-1.5" },
    e("div", { className: "text-[9px] font-medium uppercase tracking-wider text-slate-500" }, title),
    lines.length === 0
      ? e("div", { className: "mt-0.5 text-[9px] text-slate-600" }, emptyNote)
      : e(
          "div",
          { className: "mt-0.5 space-y-0.5" },
          lines.map(([k, v], i) =>
            e(
              "div",
              { key: `${keyPrefix}-${i}`, className: "flex gap-1.5 text-[9.5px] leading-relaxed" },
              e("span", { className: "shrink-0 text-slate-500" }, `${k}:`),
              e("span", { className: "min-w-0 break-all text-slate-200" }, v),
            ),
          ),
        ),
  );
}

export function VerdictPanel({
  apiToken = "",
  verdictId = 0,
  idType = "inbox",
  fallback = null,
  onDecided,
}: {
  apiToken?: string;
  // 与 L2 decide 端点同口径:idType=inbox 时 verdictId 是 bet 的 action_inbox id;
  // idType=outcome 时是 vkpi_gtm_outcomes.id。
  verdictId?: number;
  idType?: "inbox" | "outcome" | string;
  // Inbox 条目 payload_json 快照(context 接口不可用时的兜底读数)。
  fallback?: Dict | null;
  onDecided?: (decision: string) => void;
}) {
  const [ctx, setCtx] = React.useState<VerdictContext | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [ctxNote, setCtxNote] = React.useState("");
  const [lesson, setLesson] = React.useState("");
  const [submitting, setSubmitting] = React.useState("");
  const [submitError, setSubmitError] = React.useState("");
  const [decidedAs, setDecidedAs] = React.useState("");

  const oid = Number(verdictId) || 0;
  const idt = idType === "outcome" ? "outcome" : "inbox";

  React.useEffect(() => {
    setCtx(null);
    setCtxNote("");
    if (!apiToken || !oid) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void apiFetch<VerdictContext>(
      `/api/admin/vkpi/gtm/verdicts/${encodeURIComponent(String(oid))}/context?id_type=${idt}`,
      { cache: "no-store" },
      apiToken,
    )
      .then((payload) => {
        if (cancelled) return;
        if (payload && payload.available !== false && payload.outcome) {
          setCtx(payload);
        } else {
          setCtx(null);
          setCtxNote(String(payload?.reason || "账本读数不可用,以下为任务快照。"));
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setCtx(null);
          setCtxNote(String(err?.message || "账本读数读取失败,以下为任务快照。"));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, oid, idt]);

  if (!oid) {
    return e(
      "div",
      { className: "mt-2 rounded border border-amber-500/20 bg-amber-500/[0.05] px-2 py-1.5 text-[9.5px] text-amber-300/90" },
      "缺 gtm_outcome_id / verdict_for_inbox_id,无法定位结果账本行(裁决任务 payload 应带其一)。",
    );
  }

  const outcome: VerdictOutcome = ctx?.outcome || {};
  // 兜底读数:payload.bet 七要素(L1 合约)或 payload 顶层 expected/why。
  const fb = asDict(fallback);
  const fbBet = asDict(fb.bet);
  const expected =
    kvLines(outcome.expected_result).length > 0
      ? outcome.expected_result
      : (fbBet.expected ?? fb.expected ?? null);
  const why = String((fbBet.why ?? fb.why ?? "") || "");
  const alreadyDecided =
    Boolean(outcome.decided_at) || DECIDED_SET.has(String(outcome.decision || "").toLowerCase());
  const finalDecision = decidedAs || String(outcome.decision || "");
  const wp = ctx?.weight_preview || null;
  const wpCounts = wp?.counts || null;

  const metaChips = [
    outcome.product_sku ? `SKU ${outcome.product_sku}` : "",
    outcome.market || "",
    outcome.channel || "",
    outcome.action_type || "",
  ].filter(Boolean);

  const decide = (decision: string) => {
    if (!apiToken || submitting || decidedAs) return;
    const cleanLesson = lesson.trim();
    if (!cleanLesson) {
      setSubmitError("lesson 一句话必填:这次学到什么,下次怎么改。");
      return;
    }
    setSubmitting(decision);
    setSubmitError("");
    void apiFetch<{ ok?: boolean; reason?: string }>(
      `/api/admin/vkpi/gtm/verdicts/${encodeURIComponent(String(oid))}/decide`,
      {
        method: "POST",
        body: jsonBody({ decision, lesson: cleanLesson, id_type: idt }),
        cache: "no-store",
      },
      apiToken,
    )
      .then((res) => {
        if (res && res.ok === false) {
          setSubmitError(String(res.reason || "裁决未生效"));
          return;
        }
        setDecidedAs(decision);
        if (onDecided) onDecided(decision);
      })
      .catch((err) => {
        setSubmitError(String(err?.message || "裁决提交失败(decide 端点可能未上线)"));
      })
      .finally(() => setSubmitting(""));
  };

  return e(
    "div",
    { className: "mt-2 rounded-md border border-fuchsia-500/20 bg-black/30 p-2.5" },
    // ── 头:裁决一屏 + bet 元信息 ──
    e(
      "div",
      { className: "flex flex-wrap items-center gap-1.5" },
      e(Gavel, { size: 11, className: "shrink-0 text-fuchsia-300" }),
      e("span", { className: "text-[10px] font-semibold text-white" }, "裁决一屏 · 对答案"),
      e("span", { className: "text-[8.5px] text-slate-500" }, `${idt}#${oid}`),
      metaChips.map((chip, i) =>
        e(
          "span",
          { key: `m-${i}`, className: "rounded border border-white/[0.08] bg-white/[0.03] px-1 py-0.5 text-[8.5px] text-slate-300" },
          chip,
        ),
      ),
      loading ? e(Loader2, { size: 10, className: "animate-spin text-slate-500" }) : null,
    ),
    // 诚实态:context 不可用 → 快照兜底标注
    ctxNote
      ? e("div", { className: "mt-1 text-[9px] text-amber-300/80" }, `账本读数不可用 · ${ctxNote}`)
      : null,
    // ── 当时预判(why)──
    why
      ? e(
          "div",
          { className: "mt-1.5 rounded border border-white/[0.06] bg-white/[0.02] px-2 py-1 text-[9.5px] leading-relaxed text-slate-300" },
          e("span", { className: "text-slate-500" }, "当时预判:"),
          why.slice(0, 200),
        )
      : null,
    // ── 预期 vs 三窗实际 ──
    e(
      "div",
      { className: "mt-1.5 grid grid-cols-1 gap-1.5 sm:grid-cols-2" },
      KvBlock("预期结果(bet)", expected, "预期结果缺失(旧 bet 未带 expected)", "exp"),
      KvBlock("实际结果(裁决时点)", outcome.actual_result, "actual_result 未回填", "act"),
    ),
    e(
      "div",
      { className: "mt-1.5 grid grid-cols-1 gap-1.5 sm:grid-cols-3" },
      KvBlock("7d 执行", outcome.window_7d, "未回填(等每日 job)", "w7"),
      KvBlock("14d 内容", outcome.window_14d, "未回填(等每日 job)", "w14"),
      KvBlock("28d 商业", outcome.window_28d, "未回填(Shopify 归因 pending)", "w28"),
    ),
    // ── 权重回流预览(纯读;真回流有样本闸)──
    wpCounts && Number(wpCounts.total) > 0
      ? e(
          "div",
          { className: "mt-1.5 text-[9px] text-slate-500" },
          `权重回流预览:${Number(wpCounts.total) || 0} 条 weight_change · 可回流 ${Number(wpCounts.actionable) || 0}` +
            ` · hold ${Number(wpCounts.held) || 0}(样本闸 <${Number(wp?.min_sample) || 5} 不改权重)`,
        )
      : null,
    // ── 裁决区:已裁决只读 / 未裁决给六键 + lesson ──
    alreadyDecided || decidedAs
      ? e(
          "div",
          { className: "mt-2 flex items-start gap-1.5 rounded border border-emerald-500/25 bg-emerald-500/[0.06] px-2 py-1.5" },
          e(CheckCircle2, { size: 11, className: "mt-0.5 shrink-0 text-emerald-300" }),
          e(
            "div",
            { className: "text-[9.5px] leading-relaxed text-emerald-200/90" },
            `已裁决:${DECISION_LABEL[finalDecision] || finalDecision || "—"} · finalized`,
            (decidedAs ? lesson.trim() : String(outcome.lesson || ""))
              ? e("div", { className: "text-emerald-200/70" }, `lesson:${decidedAs ? lesson.trim() : String(outcome.lesson || "")}`)
              : null,
          ),
        )
      : e(
          React.Fragment,
          null,
          e("input", {
            type: "text",
            value: lesson,
            onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setLesson(ev.target.value),
            placeholder: "lesson 一句话(必填):这次学到什么,下次怎么改",
            maxLength: 300,
            className:
              "mt-2 w-full rounded border border-white/[0.08] bg-black/30 px-2 py-1 text-[10px] text-slate-200 placeholder:text-slate-600 focus:border-fuchsia-400/40 focus:outline-none",
          }),
          e(
            "div",
            { className: "mt-1.5 flex flex-wrap items-center gap-1" },
            VERDICT_DECISIONS.map((d) =>
              e(
                "button",
                {
                  key: d.key,
                  type: "button",
                  disabled: Boolean(submitting) || !lesson.trim(),
                  onClick: () => decide(d.key),
                  title: lesson.trim() ? `裁决为「${d.label}」并写入 lesson` : "先写一句 lesson 才能裁决",
                  className: `flex items-center gap-1 rounded border px-1.5 py-0.5 text-[9px] transition-colors disabled:opacity-40 ${d.cls}`,
                },
                submitting === d.key ? e(Loader2, { size: 9, className: "animate-spin" }) : null,
                d.label,
              ),
            ),
          ),
          e(
            "div",
            { className: "mt-1 text-[8.5px] text-slate-600" },
            "裁决只能人工完成,decided 即 finalized;写 gtm_outcomes.decision + lesson,权重回流走既有生效链(样本<5 不改权重)。",
          ),
        ),
    submitError
      ? e(
          "div",
          { className: "mt-1.5 rounded border border-red-500/20 bg-red-500/[0.06] px-2 py-1 text-[9px] text-red-300/85" },
          `裁决未生效 · ${submitError}`,
        )
      : null,
  );
}
