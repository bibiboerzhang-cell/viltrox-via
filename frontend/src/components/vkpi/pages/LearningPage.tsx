import React from "react";
import { getLearningStatus, getWorkspaceDigest } from "../../../services/vkpi/agentOps-api";

// 学习仪表盘:学习闭环状态(动作沉淀/反馈/漏斗/成熟度)+ 运营每日 digest。
// 接 /agents/learning-status + /agents/workspace-digest。红线:只读;maturity 为展示信号。

const MATURITY: Record<string, { label: string; cls: string }> = {
  cold: { label: "冷启动(缺真实结果证据)", cls: "text-slate-300" },
  warming: { label: "升温中", cls: "text-amber-300" },
  learning: { label: "在学习", cls: "text-emerald-300" },
};

function Bar({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between rounded-md bg-white/[0.02] px-2 py-1 text-[11px]">
      <span className="text-slate-300">{label}</span>
      <span className="font-semibold text-white">{value}</span>
    </div>
  );
}

export function LearningPage({ apiToken = "" }: { apiToken?: string }) {
  const [ls, setLs] = React.useState<any>(null);
  const [digest, setDigest] = React.useState<any>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    if (!apiToken) { setLoading(false); return; }
    Promise.all([getLearningStatus(apiToken), getWorkspaceDigest(apiToken)])
      .then(([a, b]: any[]) => { setLs(a); setDigest(b); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [apiToken]);

  if (loading) return <div className="p-6 text-sm text-slate-400">学习状态加载中…</div>;
  if (!apiToken) return <div className="p-6 text-sm text-red-300/80">未登录 / 无 token</div>;

  const actions = ls?.agent_actions || {};
  const feedback = ls?.memory_feedback || {};
  const funnel = ls?.recommendation_funnel || {};
  const maturity = MATURITY[ls?.maturity] || MATURITY.cold;
  const pipeline = digest?.pipeline || {};
  const verified = ls?.verified_evidence || {};
  const targets = ls?.targets_4_5 || {};
  const excluded = ls?.excluded_raw_activity || {};

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-white">学习闭环 · 系统在学什么</h2>
        <span className={`text-[12px] font-medium ${maturity.cls}`}>成熟度:{maturity.label}</span>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
          <div className="mb-2 text-[11px] text-slate-400">动作沉淀 · 共 {actions.total ?? 0}</div>
          <div className="space-y-1">
            {Object.entries(actions.by_kind || {}).map(([k, v]) => <Bar key={k} label={k} value={Number(v)} />)}
            {!Object.keys(actions.by_kind || {}).length ? <div className="text-[10px] text-slate-500">暂无(随收藏/加项目累积)</div> : null}
          </div>
        </div>
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
          <div className="mb-2 text-[11px] text-slate-400">反馈信号 · 共 {feedback.total ?? 0}</div>
          <div className="space-y-1">
            {Object.entries(feedback.by_type || {}).map(([k, v]) => <Bar key={k} label={k} value={Number(v)} />)}
            {!Object.keys(feedback.by_type || {}).length ? <div className="text-[10px] text-slate-500">暂无</div> : null}
          </div>
        </div>
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
          <div className="mb-2 text-[11px] text-slate-400">推荐漏斗</div>
          <div className="space-y-1">
            <Bar label="总计" value={Number(funnel.total || 0)} />
            <Bar label="已认领" value={Number(funnel.claimed || 0)} />
            <Bar label="已达成" value={Number(funnel.agreed || 0)} />
            <Bar label="已发布" value={Number(funnel.published || 0)} />
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-amber-300/15 bg-amber-500/[0.05] p-3">
        <div className="mb-2 flex items-center justify-between text-[11px]">
          <span className="text-slate-300">真实学习证据 · 排除 test / demo / dry-run / execution ack</span>
          <span className="text-amber-300">{ls?.claim_status || "descriptive_only"}</span>
        </div>
        <div className="grid grid-cols-1 gap-1 md:grid-cols-3">
          <Bar label={`人工 finalized outcome / ${targets.finalized_outcomes ?? 100}`} value={Number(verified.finalized_outcomes || 0)} />
          <Bar label={`非演示人工反馈 / ${targets.human_feedback ?? 20}`} value={Number(verified.human_feedback || 0)} />
          <Bar label={`带 actual 的预测评估 / ${targets.prediction_actual_evals ?? 50}`} value={Number(verified.prediction_actual_evals || 0)} />
          <Bar label={`人工复核 Skill / ${targets.human_reviewed_skill_runs ?? 100}`} value={Number(verified.human_reviewed_skill_runs || 0)} />
          <Bar label={`真实 Agent tool run / ${targets.executed_agent_tool_runs ?? 20}`} value={Number(verified.executed_agent_tool_runs || 0)} />
          <Bar label="绑定动作的结果评估" value={Number(verified.linked_agent_outcomes || 0)} />
        </div>
        <div className="mt-2 text-[10px] text-slate-500">
          原始 Skill run {Number(excluded.skill_runs_total || 0)} 条、Agent eval {Number(excluded.agent_outcome_evaluations_total || 0)} 条；不满足上述证据口径的行不抬高成熟度。
        </div>
      </div>

      <div className="rounded-xl border border-sky-300/15 bg-sky-500/[0.05] p-3">
        <div className="text-[11px] text-slate-400">主链路就绪度 · 待接 {pipeline.total_ready ?? 0}</div>
        <div className="mt-1 space-y-1">
          {(pipeline.breakpoints || []).map((b: any) => (
            <div key={b.id} className="flex items-center justify-between text-[11px]">
              <span className="text-slate-300">{b.label}</span>
              <span className="text-slate-400">{b.ready_count == null ? "—" : b.ready_count}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="text-[10px] text-slate-500">{ls?.note}</div>
    </div>
  );
}
