import React from "react";
import { useT } from "../cockpit/lib/i18n";

type ReadinessStatus = "needs_inputs" | "needs_evidence" | "draft_for_review";
type Step = { code: string; title: string; reason: string };
type Gap = { code: string; message: string };

const record = (value: unknown): Record<string, unknown> | null => (
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null
);
const text = (value: unknown): value is string => typeof value === "string" && Boolean(value.trim());

function readinessFrom(output: unknown) {
  const raw = record(record(output)?.planning_readiness);
  if (!raw || raw.executable !== false || raw.approval_status !== "not_requested"
    || raw.claim_status !== "descriptive_only"
    || !["needs_inputs", "needs_evidence", "draft_for_review"].includes(String(raw.status))) return null;
  if (!Array.isArray(raw.gaps) || !Array.isArray(raw.next_steps) || raw.next_steps.length === 0) return null;
  const gaps = raw.gaps as Gap[];
  const steps = raw.next_steps as Step[];
  if (gaps.some((gap) => !record(gap) || !text(gap.code) || !text(gap.message))
    || steps.some((step) => !record(step) || !text(step.code) || !text(step.title) || !text(step.reason))) return null;
  if ((raw.status === "draft_for_review") !== (gaps.length === 0)) return null;
  return { status: raw.status as ReadinessStatus, gaps, steps };
}

/** Read-only guidance. These flags never grant approval or call an executor. */
export function CampaignPlanReadiness({ output }: { output: unknown }) {
  const { t } = useT();
  const readiness = readinessFrom(output);
  const titles: Record<ReadinessStatus, string> = {
    needs_inputs: "先补齐规划条件",
    needs_evidence: "先补齐判断依据",
    draft_for_review: "草稿可供人工审阅",
  };
  return (
    <section aria-label={t("营销计划准备检查")} className="mb-3 rounded border border-sky-500/20 bg-sky-500/5 p-3 text-[11px] text-slate-200">
      <h3 className="font-semibold">{t("营销计划准备检查")}</h3>
      <p role="status" className="mt-1 text-sky-200">
        {t(readiness ? titles[readiness.status] : "准备状态待核验")}
      </p>
      <p className="mt-1 text-slate-400">{t("生成草稿不等于项目创建、批准或对外执行。")}</p>
      {readiness ? <>
        {readiness.gaps.length > 0 ? <div className="mt-3">
          <h4 className="font-medium">{t("当前缺口")}</h4>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {readiness.gaps.map((gap, index) => <li key={`${gap.code}-${index}`}>{t(gap.message)}</li>)}
          </ul>
        </div> : <p className="mt-2 text-slate-400">{t("已有材料仍需负责人核对适用范围、资源和交付条件。")}</p>}
        <h4 className="mt-3 font-medium">{t("下一步")}</h4>
        <ol className="mt-1 list-decimal space-y-2 pl-4">
          {readiness.steps.map((step, index) => <li key={`${step.code}-${index}`}>
            <span className="font-medium">{t(step.title)}</span>
            <p className="text-slate-400">{t(step.reason)}</p>
          </li>)}
        </ol>
      </> : <p className="mt-2 text-amber-200">{t("准备状态缺失或不一致，请先核对本次规划结果，不要据此执行。")}</p>}
    </section>
  );
}
