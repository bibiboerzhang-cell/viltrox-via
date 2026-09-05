import React from "react";
import { useT } from "../cockpit/lib/i18n";
import { candidateCoverage, candidateEvidence, EVIDENCE_FIELDS, EVIDENCE_GAPS } from "./campaignPlanCandidateEvidence";

type Row = Record<string, unknown>;
const record = (value: unknown): Row => (
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : {}
);
const text = (value: unknown): string | null => typeof value === "string" && value.trim() ? value.trim() : null;
const integer = (value: unknown): number | null => (
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null
);
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(record) : [];
const ratio = (value: unknown): number | null => (
  typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null
);

// Amounts arrive in minor units. Do not infer currency or round fractional cents.
function amount(value: unknown): string | null {
  const cents = integer(value);
  if (cents === null) return null;
  return `${Math.floor(cents / 100).toLocaleString("en-US")}.${String(cents % 100).padStart(2, "0")}`;
}

function percentage(value: unknown): string | null {
  const share = ratio(value);
  return share === null ? null : `${(share * 100).toLocaleString("en-US", { maximumFractionDigits: 2 })}%`;
}

const BUCKETS: Record<string, string> = {
  creator_fees: "创作者合作费", content_boost: "内容推广", paid_amplify: "付费推广", ops_buffer: "运营预留",
};
const PHASES: Record<string, string> = { seed: "筹备", ramp: "启动", peak: "重点推进", harvest: "复盘" };
const GOALS: Record<string, string> = { awareness: "品牌认知", conversion: "转化", launch: "新品发布" };
const ALLOCATIONS: Record<string, string> = {
  rule: "规则分配草案", model_validated: "模型分配草案", rule_fallback: "已回退规则分配", unavailable: "预算待核验",
};

function budgetState(meta: Row, allocation: Row[]) {
  const budget = integer(meta.budget_cents);
  const amounts = allocation.map((row) => integer(row.amount_cents));
  const sum = amounts.reduce<number>((total, value) => total + (value ?? 0), 0);
  const allocated = allocation.length > 0 && amounts.every((value) => value !== null) && Number.isSafeInteger(sum) ? sum : null;
  const remaining = budget !== null && allocated !== null && allocated <= budget ? budget - allocated : null;
  const validation = record(meta.budget_validation);
  const reportedTotal = integer(validation.allocated_cents);
  const reportedRemaining = integer(validation.unallocated_cents);
  const reportPresent = Object.keys(validation).length > 0;
  const consistent = reportPresent && validation.input_status === "valid"
    && budget !== null && allocated !== null && remaining !== null
    && reportedTotal === allocated && reportedRemaining === remaining
    && ["rule", "model_validated", "rule_fallback"].includes(String(validation.allocation_status));
  return { allocated, remaining, consistent, validation, reportPresent };
}

function ModelCost({ value }: { value: unknown }) {
  const { t } = useT();
  const cost = record(value);
  const label = cost.status === "unknown" ? "模型调用成本未知"
    : cost.status === "not_applicable" && cost.source === "rule_only" ? "规则生成（未调用模型）"
      : "模型调用成本待核验";
  return <p className="mt-2 text-slate-400">{t(label)}</p>;
}

function Budget({ meta, value }: { meta: Row; value: unknown }) {
  const { t } = useT();
  const allocation = rows(value);
  const state = budgetState(meta, allocation);
  const allocationLabel = text(state.validation.allocation_status);
  return (
    <section aria-label={t("预算分配")} className="space-y-2 border-t border-white/10 pt-3">
      <h4 className="font-semibold">{t("预算分配")}</h4>
      <p className="text-slate-400">{t("金额按100分换算，币种未声明；规划预算不是实际模型费用，也不代表获批支出。")}</p>
      {allocation.length ? <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead className="text-slate-400"><tr>
            <th scope="col" className="py-1 pr-3">{t("用途")}</th>
            <th scope="col" className="py-1 pr-3">{t("建议占比")}</th>
            <th scope="col" className="py-1 text-right">{t("规划金额")}</th>
          </tr></thead>
          <tbody>{allocation.map((row, index) => {
            const bucket = text(row.bucket);
            return <tr key={index} className="border-t border-white/5">
              <td className="py-1.5 pr-3">{bucket ? t(BUCKETS[bucket] || bucket) : t("待核验")}</td>
              <td className="py-1.5 pr-3">{percentage(row.pct) ?? t("待核验")}</td>
              <td className="py-1.5 text-right tabular-nums">{amount(row.amount_cents) ?? t("待核验")}</td>
            </tr>;
          })}</tbody>
        </table>
      </div> : <p className="text-amber-200">{t("预算分配缺失或格式异常，待核验。")}</p>}
      <dl className="flex flex-wrap gap-x-5 gap-y-1 text-slate-300">
        <div><dt className="inline">{t("分配合计")}：</dt><dd className="inline tabular-nums">{amount(state.allocated) ?? t("待核验")}</dd></div>
        <div><dt className="inline">{t("尚未分配")}：</dt><dd className="inline tabular-nums">{amount(state.remaining) ?? t("待核验")}</dd></div>
      </dl>
      <p className={state.consistent ? "text-slate-400" : "text-amber-200"}>
        {t(state.consistent && allocationLabel ? ALLOCATIONS[allocationLabel]
          : state.reportPresent ? "预算校验信息缺失或与金额不一致，请先核对。" : "旧结果未提供预算校验记录，金额仅供核对。")}
      </p>
      {Array.isArray(state.validation.warnings) && state.validation.warnings.length > 0
        ? <p className="text-amber-200">{t("预算含校验提示，请在原始数据中核对。")}</p> : null}
      <ModelCost value={meta.model_cost} />
    </section>
  );
}

function Timeline({ value }: { value: unknown }) {
  const { t } = useT();
  const stages = rows(value);
  return (
    <section aria-label={t("建议节奏")} className="space-y-2">
      <h4 className="font-semibold">{t("建议节奏")}</h4>
      <p className="text-slate-400">{t("阶段为相对周次，不是已确认的档期。")}</p>
      {stages.length ? <ol className="space-y-2">
        {stages.map((stage, index) => {
          const phase = text(stage.phase);
          const week = integer(stage.week);
          return <li key={index} className="border-l border-sky-500/30 pl-3">
            <span className="font-medium">{week !== null && week > 0 ? t("第 {week} 周", { week }) : t("周次待核验")}
              {" · "}{phase ? t(PHASES[phase] || phase) : t("阶段待核验")}</span>
            <p className="mt-0.5 break-words text-slate-300">{text(stage.focus) ?? t("安排待核验")}</p>
          </li>;
        })}
      </ol> : <p className="text-amber-200">{t("节奏缺失或格式异常，待核验。")}</p>}
    </section>
  );
}

function CandidateEvidence({ creator }: { creator: Row }) {
  const { t } = useT();
  const evidence = candidateEvidence(creator);
  return <div className="mt-1 min-w-0 text-slate-400">
    <p>{t(evidence.state === "partial" ? "资料依据部分可审阅 · 匹配未核验" : "资料依据待核验")}</p>
    <details className="mt-1 min-w-0">
      <summary className="cursor-pointer text-sky-200">{t("查看候选依据与缺口")}</summary>
      <div className="mt-2 space-y-2 border-l border-white/10 pl-2">
        <p>{t("以下仅为候选池资料记录，不是独立核实的个人匹配理由。")}</p>
        {evidence.state === "legacy" ? <p className="text-amber-200">{t("旧结果未提供逐候选证据，无法据此确认匹配。")}</p> : null}
        {evidence.state === "invalid" || evidence.omitted ? <p className="text-amber-200">{t("部分依据格式、状态或引用异常，未作为可审阅资料展示。")}</p> : null}
        {evidence.facts.length ? <ul className="space-y-2">{evidence.facts.map(fact => <li key={fact.field} className="break-words">
          <p><span className="text-slate-300">{t(EVIDENCE_FIELDS[fact.field])}：</span>{fact.value}</p>
          <p className="break-all">{t("引用资料记录")}：{fact.sourceRef}</p>
        </li>)}</ul> : <p>{t("当前没有可展示的资料事实。")}</p>}
        {evidence.sources.map(source => <div key={source.ref} className="space-y-1">
          <p className="break-all">{t("资料记录来源")}：{source.ref}</p>
          <p className="break-all">{source.url ?? t("公开资料网址待核验")}</p>
          <p>{t("资料更新时间")}：{source.updatedAt
            ? <time dateTime={source.updatedAt}>{source.updatedAt}</time>
            : t("待核验")}</p>
          {source.updatedAt ? <p>{t("按原记录显示，未补时区或精度。")}</p> : null}
          {source.updatedAt && !/(?:Z|[+-]\d{2}:\d{2})$/.test(source.updatedAt)
            ? <p>{t("原记录未提供时区。")}</p> : null}
        </div>)}
        <p>{t("采集时间待核验；资料更新时间不能替代采集时间。")}</p>
        <div><p className="text-slate-300">{t("证据缺口与下一步")}</p>
          <ul className="mt-1 list-disc space-y-1 pl-4 text-amber-200">{evidence.gaps.map(gap => <li key={gap}>
            {t(Object.prototype.hasOwnProperty.call(EVIDENCE_GAPS, gap) ? EVIDENCE_GAPS[gap] : "其他证据缺口待核验。")}
          </li>)}</ul>
        </div>
      </div>
    </details>
  </div>;
}

function Creators({ value }: { value: unknown }) {
  const { t } = useT();
  const groups = rows(value);
  return (
    <section aria-label={t("KOL 候选与理由")} className="space-y-2">
      <h4 className="font-semibold">{t("KOL 候选与理由")}</h4>
      <p className="text-slate-400">{t("人数与梯队是规划分组，不代表已找到或验证同等数量的创作者。")}</p>
      {groups.length ? <ul className="space-y-3">
        {groups.map((group, index) => {
          const candidates = rows(group.sample_creators);
          const coverage = candidateCoverage(group);
          return <li key={index}>
            <p className="font-medium">{text(group.tier) ?? t("分组待核验")}
              {" · "}{t("规划人数")} {integer(group.count) ?? t("待核验")}
              {" · "}{t("建议占比")} {percentage(group.share) ?? t("待核验")}</p>
            <p className="mt-1 text-slate-400">{coverage ? t("当前展示样本 {samples} · 与规划人数差额 {missing}", coverage)
              : t("展示样本数量待核验，不把规划人数当作实际候选数。")}</p>
            {candidates.length ? <ul className="mt-1 space-y-1 text-slate-300">
              {candidates.map((creator, candidateIndex) => <li key={candidateIndex} className="break-words">
                <p>{text(creator.handle) ?? t("候选身份待核验")}{" · "}{text(creator.platform) ?? t("平台待核验")}
                {" · "}{integer(creator.id) !== null && Number(creator.id) > 0 ? `#${creator.id}` : t("候选编号待核验")}</p>
                <CandidateEvidence creator={creator} />
              </li>)}
            </ul> : <p className="mt-1 text-amber-200">{t("该分组未提供可核对的候选资料。")}</p>}
          </li>;
        })}
      </ul> : <p className="text-amber-200">{t("候选分组缺失或格式异常，待核验。")}</p>}
      <p className="text-amber-200">{t("逐人匹配理由未提供，需核对产品、市场、内容和档期；不从评分或梯队推定匹配。")}</p>
    </section>
  );
}

function Rationale({ anglesValue, risksValue }: { anglesValue: unknown; risksValue: unknown }) {
  const { t } = useT();
  const angles = rows(anglesValue);
  const risks = rows(risksValue);
  return (
    <section aria-label={t("方向依据与风险")} className="space-y-3 border-t border-white/10 pt-3">
      <h4 className="font-semibold">{t("方向依据与风险")}</h4>
      {angles.length ? <ul className="space-y-2">{angles.map((angle, index) => <li key={index}>
        <p className="font-medium">{text(angle.angle) ?? t("内容方向待核验")}</p>
        <p className="text-slate-300">{text(angle.why) ?? t("方向理由待核验")}</p>
        <p className="text-slate-400">{t("来源线索")}：{text(angle.market_signal) ?? t("待核验")}</p>
      </li>)}</ul> : <p className="text-amber-200">{t("方向依据缺失或格式异常，待核验。")}</p>}
      <p className="text-slate-400">{t("来源线索不等于已验证的市场证据。")}</p>
      {risks.length ? <ul className="space-y-2">{risks.map((risk, index) => <li key={index}>
        <p className="text-amber-200">{text(risk.risk) ?? t("风险待核验")}</p>
        <p className="text-slate-300">{text(risk.mitigation) ?? t("应对方式待核验")}</p>
      </li>)}</ul> : <p className="text-amber-200">{t("未提供风险资料，不代表没有风险。")}</p>}
    </section>
  );
}

/** A projection of the returned draft only: no requests, approvals, or navigation. */
export function CampaignPlanPresentation({ output }: { output: unknown }) {
  const { t } = useT();
  const data = record(output);
  const meta = record(data.meta);
  const plan = record(data.plan);
  const goal = text(meta.goal);
  return (
    <section aria-label={t("营销规划草案")} className="mb-3 space-y-4 text-[11px] text-slate-200">
      <div className="space-y-2">
        <h3 className="text-[12px] font-semibold">{t("规划摘要")}</h3>
        <p className="text-slate-400">{t("以下仅为返回的规划草案，不代表项目已创建、批准或执行。")}</p>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
          {[
            [t("产品"), text(meta.product) ?? t("待核验")],
            [t("目标市场"), text(meta.market) ?? t("待核验")],
            [t("规划目标"), goal ? t(GOALS[goal] || goal) : t("待核验")],
            [t("规划预算"), amount(meta.budget_cents) ?? t("待核验")],
          ].map(([label, value]) => <div key={label}><dt className="text-slate-400">{label}</dt><dd className="break-words font-medium tabular-nums">{value}</dd></div>)}
        </dl>
      </div>
      <Budget meta={meta} value={plan.budget_allocation} />
      <div className="grid gap-4 border-t border-white/10 pt-3 xl:grid-cols-2">
        <Timeline value={plan.timeline} />
        <Creators value={plan.creator_mix} />
      </div>
      <Rationale anglesValue={plan.content_angles} risksValue={data.risks} />
      {Array.isArray(meta.output_warnings) && meta.output_warnings.length > 0
        ? <p className="text-amber-200">{t("部分规划字段经过回退或校验，请结合原始数据核对。")}</p> : null}
    </section>
  );
}
