import { CheckCircle2, Clock3, Loader2, ShieldCheck, TriangleAlert } from "lucide-react";

import type { SearchSessionProgress, SearchStageProgress } from "./SmartKolInputPanel.derivers";

function targetLabel(value: number, target: number): string {
  return target > 0 ? `${value}/${target}` : String(value);
}

function stageParts(stage: SearchStageProgress): string[] {
  return [
    stage.ready > 0 ? `完成 ${stage.ready}` : "",
    stage.active > 0 ? `进行中 ${stage.active}` : "",
    stage.failed > 0 ? `失败 ${stage.failed}` : "",
    stage.notRequested > 0 ? `未请求 ${stage.notRequested}` : "",
  ].filter(Boolean);
}

function StageLine({ stageKey, label, stage, tracked }: { stageKey: string; label: string; stage: SearchStageProgress; tracked: boolean }) {
  const parts = stageParts(stage);
  return (
    <div className="flex items-start justify-between gap-2 text-[9.5px]" data-testid={`kol-progress-stage-${stageKey}`}>
      <span className="shrink-0 text-slate-500">{label}</span>
      <span className={`text-right ${stage.failed > 0 ? "text-rose-200" : stage.active > 0 ? "text-cyan-100" : "text-slate-300"}`}>
        {tracked ? (parts.length ? parts.join(" · ") : "等待登记") : "尚未登记"}
      </span>
    </div>
  );
}

/**
 * Search-session progress is evidence, not an optimistic percentage. It renders only counters and
 * strict booleans emitted by the backend. In particular, `not_requested` never becomes "complete".
 */
export function ProgressiveSearchStageCard({ progress }: { progress: SearchSessionProgress }) {
  const hasEvidence = progress.target > 0
    || progress.completionContractExplicit
    || progress.downstreamTracked
    || Boolean(progress.currentItem);
  if (!hasEvidence) return null;

  const notRequested = progress.video.notRequested + progress.comments.notRequested + progress.audience.notRequested;
  const decisionCopy = progress.decisionEligible
    ? "决策证据已就绪"
    : progress.fullAnalysisComplete
      ? "完整分析已完成 · 暂未满足决策条件"
      : progress.requestedTasksTerminal
        ? "已请求阶段结束 · 尚非完整分析"
        : "基础结果先展示，完整分析继续后台补全";
  const active = ["queued", "discovering", "profiling", "enriching"].includes(progress.phase);
  const currentLabel = progress.requestedTasksTerminal ? "最近处理" : "当前处理";
  const currentName = [
    progress.currentItem?.rank ? `#${progress.currentItem.rank}` : "",
    progress.currentItem?.handle || "",
  ].filter(Boolean).join(" · ");
  const currentStatus = progress.currentItem?.profileStatus || progress.currentItem?.status;

  return (
    <div
      data-testid="kol-progressive-stage-card"
      className="rounded-lg border border-cyan-300/15 bg-cyan-950/[0.10] px-3 py-2.5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[10.5px] font-medium text-cyan-50">
          {progress.decisionEligible || progress.fullAnalysisComplete
            ? <CheckCircle2 size={12} className="text-emerald-300" />
            : active
              ? <Loader2 size={12} className="animate-spin text-cyan-200" />
              : <Clock3 size={12} className="text-amber-200" />}
          分析进度
        </div>
        <span
          data-testid="kol-progress-strict-status"
          className={`rounded-full border px-2 py-0.5 text-[9px] ${
            progress.decisionEligible
              ? "border-emerald-300/30 bg-emerald-400/[0.10] text-emerald-100"
              : progress.requestedTasksTerminal
                ? "border-amber-300/25 bg-amber-400/[0.08] text-amber-100"
                : "border-cyan-300/20 bg-cyan-400/[0.06] text-cyan-100"
          }`}
        >
          {progress.decisionEligible ? "可进入决策" : progress.phaseLabel}
        </span>
      </div>

      <div className="mt-2 grid gap-1.5 sm:grid-cols-2 xl:grid-cols-4">
        <div data-testid="kol-progress-base" className="rounded-md border border-white/[0.07] bg-black/15 px-2 py-1.5">
          <div className="text-[9px] text-slate-500">① 基础资料</div>
          <div className="mt-0.5 text-[10.5px] font-medium text-slate-100">
            {targetLabel(progress.basicVisible, progress.target)} 可查看
          </div>
          <div className={`text-[9px] ${progress.baseComplete ? "text-emerald-200" : "text-cyan-200/80"}`}>
            {progress.baseComplete ? "基础资料已到齐" : "已有结果先行展示"}
          </div>
        </div>

        <div data-testid="kol-progress-profile" className="rounded-md border border-white/[0.07] bg-black/15 px-2 py-1.5">
          <div className="text-[9px] text-slate-500">② 档案补全</div>
          <div className="mt-0.5 text-[10.5px] font-medium text-slate-100">
            {targetLabel(progress.profileCompleted, progress.target)} 已处理
          </div>
          <div className="text-[9px] text-slate-400">
            成功 {progress.profileSucceeded}
            {progress.profileFailed > 0 ? <span className="text-rose-200"> · 失败 {progress.profileFailed}</span> : null}
            {progress.profileRemaining > 0 ? ` · 待处理 ${progress.profileRemaining}` : ""}
          </div>
        </div>

        <div data-testid="kol-progress-video" className="rounded-md border border-white/[0.07] bg-black/15 px-2 py-1.5">
          <div className="text-[9px] text-slate-500">③ 视频分析</div>
          <div className="mt-1">
            <StageLine stageKey="video" label="视频" stage={progress.video} tracked={progress.downstreamTracked} />
          </div>
        </div>

        <div data-testid="kol-progress-comments-audience" className="rounded-md border border-white/[0.07] bg-black/15 px-2 py-1.5">
          <div className="text-[9px] text-slate-500">④ 评论 / 受众</div>
          <div className="mt-1 space-y-0.5">
            <StageLine stageKey="comments" label="评论" stage={progress.comments} tracked={progress.downstreamTracked} />
            <StageLine stageKey="audience" label="受众" stage={progress.audience} tracked={progress.downstreamTracked} />
          </div>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9.5px]">
        {progress.currentItem && (currentName || currentStatus) ? (
          <span data-testid="kol-progress-current-item" className="text-slate-300">
            {currentLabel}{currentName ? ` · ${currentName}` : ""}{currentStatus ? ` · ${currentStatus}` : ""}
          </span>
        ) : null}
        <span className={progress.decisionEligible ? "text-emerald-200" : "text-slate-400"}>
          {progress.decisionEligible ? <ShieldCheck size={10} className="mr-1 inline" /> : null}
          {decisionCopy}
        </span>
        {notRequested > 0 ? (
          <span data-testid="kol-progress-not-requested-warning" className="inline-flex items-center gap-1 text-amber-200/90">
            <TriangleAlert size={10} /> 未请求 {notRequested} 项不计入完整分析
          </span>
        ) : null}
        {!progress.completionContractExplicit ? (
          <span data-testid="kol-progress-legacy-contract" className="text-slate-500">旧会话未提供严格完成口径</span>
        ) : null}
      </div>
    </div>
  );
}
