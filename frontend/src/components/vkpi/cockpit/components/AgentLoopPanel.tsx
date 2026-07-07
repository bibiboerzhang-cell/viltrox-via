// B4 件⑤ Agent 闭环串跑面板:六步链路图(建议→驾照→批准→执行→登记→记忆)+ 每步真实落点行 id。
// 数据:GET  /api/admin/vkpi/agents/loop/trace          最近串跑记录(步⑥记忆行回读)
//      POST /api/admin/vkpi/agents/loop/run-demo?dry_run=true  一键 dry-run 串跑(零执行零业务写)
// 诚实态:无串跑记录 / 无建议可串如实展示 reason;接口失败整块安静缺席(非阻塞增益块)。
// 红线:纯展示 + 只发 dry_run=true 的演示串跑;绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { GitCommitHorizontal, Play } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type LoopStep = {
  step?: number;
  key?: string;
  title?: string;
  table?: string;
  op?: string; // read | write
  row_id?: number | null;
  ok?: boolean;
  summary?: string;
};
type LoopRun = {
  status?: string;
  reason?: string;
  dry_run?: boolean;
  mode?: string; // simulated | real_whitelisted
  chain_ok?: boolean;
  trace_id?: number | null;
  action?: { id?: number; category?: string; title?: string; priority?: string };
  steps?: LoopStep[];
  generated_at?: string;
  note?: string;
};
type TraceItem = {
  trace_id?: number;
  action_id?: string;
  dry_run?: boolean;
  mode?: string;
  chain_ok?: boolean;
  action?: { id?: number; category?: string; title?: string };
  steps?: LoopStep[];
  created_at?: string;
};
type TraceResp = { status?: string; reason?: string; items?: TraceItem[] };

// 六步骨架(后端 steps 为准;这里只做缺席时的占位标签,不臆造落点)。
const STEP_LABELS = ["取建议", "验驾照", "批准", "执行留痕", "结果登记", "入记忆"];

function stepColor(s: LoopStep | undefined): string {
  if (!s) return "border-white/[0.08] bg-white/[0.02] text-slate-600";
  return s.ok
    ? "border-emerald-300/25 bg-emerald-500/[0.07] text-emerald-200"
    : "border-rose-300/25 bg-rose-500/[0.07] text-rose-200";
}

// 单步节点:序号+名称+落点表+行 id(读/写各自标注;row_id 缺席如实显示 —)。
function StepNode(s: LoopStep | undefined, i: number) {
  const label = (s && s.title) || STEP_LABELS[i] || `步${i + 1}`;
  return e("div", { key: i, className: "flex items-center gap-1 min-w-0" },
    e("div", { className: `rounded border px-1.5 py-1 min-w-0 ${stepColor(s)}` },
      e("div", { className: "flex items-center gap-1" },
        e("span", { className: "text-[8.5px] font-bold tabular-nums opacity-70" }, String(i + 1)),
        e("span", { className: "text-[9.5px] font-medium whitespace-nowrap" }, label),
        s && e("span", { className: "text-[8px] opacity-60" }, s.op === "read" ? "读" : "写"),
      ),
      s
        ? e("div", { className: "mt-0.5 text-[8px] leading-tight opacity-80 truncate", title: `${s.table || ""} — ${s.summary || ""}` },
            e("span", { className: "font-mono" }, String(s.table || "").replace("vkpi_", "")),
            e("span", { className: "tabular-nums" }, " #", s.row_id == null ? "—" : String(s.row_id)),
          )
        : e("div", { className: "mt-0.5 text-[8px] opacity-50" }, "未跑"),
    ),
    i < 5 && e("span", { className: "shrink-0 text-slate-600 text-[9px]" }, "→"),
  );
}

// 一次串跑的六步链路图 + 元信息行。
function RunChain(run: { steps?: LoopStep[]; chain_ok?: boolean; dry_run?: boolean; mode?: string; action?: { category?: string; title?: string }; created_at?: string; generated_at?: string; trace_id?: number | null }) {
  const steps = Array.isArray(run.steps) ? run.steps : [];
  const byIndex: (LoopStep | undefined)[] = [];
  for (let i = 0; i < 6; i += 1) byIndex.push(steps.find((s) => Number(s.step) === i + 1));
  const when = String(run.created_at || run.generated_at || "").slice(0, 16).replace("T", " ");
  return e("div", { className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
    e("div", { className: "flex flex-wrap items-center gap-x-2 gap-y-0.5 mb-1" },
      e("span", {
        className: "rounded px-1.5 py-0.5 text-[8.5px] font-medium " +
          (run.chain_ok ? "bg-emerald-500/15 text-emerald-200" : "bg-rose-500/15 text-rose-200"),
      }, run.chain_ok ? "六步全通" : "有步未通"),
      e("span", { className: "rounded bg-sky-500/10 px-1.5 py-0.5 text-[8.5px] text-sky-200" },
        run.dry_run === false ? "白名单真跑" : "dry-run 模拟"),
      run.trace_id != null && e("span", { className: "text-[8.5px] tabular-nums text-slate-500" }, `trace #${run.trace_id}`),
      run.action && run.action.category && e("span", { className: "text-[8.5px] text-slate-400 truncate", title: run.action.title },
        `${run.action.category}:${String(run.action.title || "").slice(0, 40)}`),
      when && e("span", { className: "ml-auto text-[8px] text-slate-600 tabular-nums" }, when),
    ),
    e("div", { className: "flex items-center gap-1 overflow-x-auto pb-0.5" },
      byIndex.map((s, i) => StepNode(s, i)),
    ),
  );
}

export function AgentLoopPanel({ apiToken }: { apiToken: string }) {
  const [trace, setTrace] = React.useState<TraceResp | null>(null);
  const [lastRun, setLastRun] = React.useState<LoopRun | null>(null);
  const [running, setRunning] = React.useState(false);
  const [runError, setRunError] = React.useState("");

  const loadTrace = React.useCallback(() => {
    if (!apiToken) return;
    void apiFetch<TraceResp>("/api/admin/vkpi/agents/loop/trace?limit=5", {}, apiToken)
      .then((payload) => setTrace(payload && typeof payload === "object" ? payload : null))
      .catch(() => setTrace(null));
  }, [apiToken]);

  React.useEffect(() => { loadTrace(); }, [loadTrace]);

  // 一键 dry-run 串跑:零执行零业务写(后端红线),跑完刷新 trace。
  const runDemo = React.useCallback(() => {
    if (!apiToken || running) return;
    setRunning(true);
    setRunError("");
    void apiFetch<LoopRun>("/api/admin/vkpi/agents/loop/run-demo?dry_run=true", { method: "POST" }, apiToken)
      .then((payload) => {
        setLastRun(payload && typeof payload === "object" ? payload : null);
        loadTrace();
      })
      .catch((err) => setRunError(err instanceof Error ? err.message : "串跑失败"))
      .finally(() => setRunning(false));
  }, [apiToken, running, loadTrace]);

  if (!apiToken) return null;
  if (trace && String(trace.status || "") === "error") return null; // 接口失败:安静缺席

  const items = Array.isArray(trace?.items) ? trace!.items! : [];
  const showRun = lastRun && String(lastRun.status || "") === "ready" ? lastRun : null;
  const latestFromTrace = !showRun && items.length > 0 ? items[0] : null;

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "agent-loop",
      header: e(React.Fragment, null,
        e(GitCommitHorizontal, { size: 11, className: "text-emerald-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "智能体闭环 · 整链串跑"),
        items.length > 0 && e("span", { className: "rounded bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-200" }, `已串 ×${items.length}`),
      ),
    },
      // 操作行:dry-run 串跑按钮 + 口径说明
      e("div", { className: "flex items-center gap-2 mt-1.5 mb-2" },
        e("button", {
          type: "button",
          disabled: running,
          onClick: runDemo,
          className: "flex items-center gap-1 rounded border border-emerald-300/25 bg-emerald-500/10 px-2 py-1 text-[9.5px] text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-50",
        },
          e(Play, { size: 9 }),
          running ? "串跑中…" : "串跑一遍(dry-run)",
        ),
        e("span", { className: "text-[8.5px] text-slate-600" }, "取真实建议→驾照判权→模拟批准→ledger 留痕→登记→入记忆;零执行零业务写"),
      ),
      runError && e("div", { className: "mb-1.5 text-[9px] text-rose-300/90" }, runError),

      // 本次串跑结果(POST 返回)优先;否则展示最近 trace 首条
      showRun && e(React.Fragment, null,
        e(RunChain, { ...showRun }),
        showRun.note && e("div", { className: "mt-1 text-[8.5px] leading-relaxed text-slate-600" }, showRun.note),
      ),
      // POST 回了 empty/blocked:如实展示原因(不编造链路)
      lastRun && String(lastRun.status || "") !== "ready" && e("div", { className: "mt-1 text-[9.5px] leading-relaxed text-amber-300/90" },
        String(lastRun.reason || "本次串跑未完成")),
      !showRun && latestFromTrace && e(RunChain, { ...latestFromTrace }),

      // 最近串跑记录列表(首条已作主图时列其余)
      items.length > (showRun ? 0 : 1) && e(React.Fragment, null,
        e("div", { className: "mt-2 mb-1 text-[9px] text-slate-500" }, "最近串跑"),
        e("div", { className: "space-y-1.5" },
          (showRun ? items : items.slice(1)).slice(0, 4).map((it, i) => e(RunChain, { key: it.trace_id ?? i, ...it })),
        ),
      ),

      // 诚实空态:一次都没串过
      !showRun && !latestFromTrace && !lastRun && e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" },
        trace && String(trace.status || "") === "empty"
          ? String(trace.reason || "尚无串跑记录")
          : "加载中…"),

      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        "口径:六步各自落真实表并回行 id(取建议 vkpi_action_inbox / 驾照 vkpi_autonomy_licenses / 批准与记忆 vkpi_agent_actions / 执行 vkpi_action_execution_ledger / 登记 vkpi_agent_outcome_evaluations);dry-run 绝不执行外部动作,不参与 V6 Fit 评分。"),
    ),
  );
}
