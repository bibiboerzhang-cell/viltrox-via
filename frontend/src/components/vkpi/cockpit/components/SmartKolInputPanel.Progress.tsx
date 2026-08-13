import { CheckCircle2, Clock3, Cloud, Laptop, Loader2, Server, ShieldCheck, TriangleAlert, WifiOff } from "lucide-react";

import { API_BASE } from "../../../../services/http";
import type {
  SearchProgressContractStage,
  SearchProgressContractView,
  SearchSessionProgress,
  SearchStageProgress,
} from "./SmartKolInputPanel.derivers";
import { kolHumanDisplayName } from "../lib/kolIdentity";

function targetLabel(value: number, target: number): string {
  return target > 0 ? `${value}/${target}` : String(value);
}

function stageParts(stage: SearchStageProgress): string[] {
  return [
    stage.ready > 0 ? `完成 ${stage.ready}` : "",
    (stage.blocked ?? 0) > 0 ? `阻塞 ${stage.blocked}` : "",
    stage.active > 0 ? `进行中 ${stage.active}` : "",
    stage.failed > 0 ? `失败 ${stage.failed}` : "",
    stage.notRequested > 0 ? `未请求 ${stage.notRequested}` : "",
  ].filter(Boolean);
}

export type RuntimeSurface = {
  kind: "local" | "cloud" | "unknown";
  label: string;
  host: string;
  basis: "api_origin" | "unavailable";
};

function isLoopbackHost(hostname: string): boolean {
  const normalized = hostname.trim().toLowerCase().replace(/^\[|\]$/g, "");
  return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "::1";
}

/** Classifies only the effective API address; it never upgrades this into a deployment claim. */
export function runtimeSurfaceFromLocation(pageHref: string, apiBase = ""): RuntimeSurface {
  try {
    const page = new URL(pageHref);
    if (!/^https?:$/.test(page.protocol)) throw new Error("unsupported protocol");
    const api = apiBase ? new URL(apiBase, page) : page;
    if (!/^https?:$/.test(api.protocol)) throw new Error("unsupported api protocol");
    const local = isLoopbackHost(api.hostname);
    return {
      kind: local ? "local" : "cloud",
      label: local ? "本地后端" : "云端后端",
      host: api.host,
      basis: "api_origin",
    };
  } catch {
    return { kind: "unknown", label: "后端位置未知", host: "", basis: "unavailable" };
  }
}

function compactPercent(value: number): string {
  return `${value.toFixed(1).replace(/\.0$/, "")}%`;
}

function contractCount(value: number | null, label: string): string {
  return value != null && value > 0 ? `${label} ${value}` : "";
}

function contractStageParts(stage: SearchProgressContractStage, blockedByWorker: boolean): string[] {
  const active = (stage.counts.queued ?? 0) + (stage.counts.running ?? 0) + (stage.counts.active ?? 0);
  return [
    contractCount(stage.counts.ready, "完成"),
    blockedByWorker && active > 0 ? `Worker 阻塞 ${active}` : "",
    !blockedByWorker ? contractCount(stage.counts.queued, "排队") : "",
    !blockedByWorker ? contractCount(stage.counts.running, "运行") : "",
    !blockedByWorker ? contractCount(stage.counts.active, "处理中/状态待确认") : "",
    contractCount((stage.counts.failed ?? 0) + (stage.counts.partial ?? 0), "失败/不完整"),
    contractCount(stage.counts.skipped, "跳过"),
    contractCount(stage.counts.notRequested, "未请求"),
  ].filter(Boolean);
}

function contractStageLabel(stage: SearchProgressContractStage, blockedByWorker: boolean): string {
  const active = (stage.counts.queued ?? 0) + (stage.counts.running ?? 0) + (stage.counts.active ?? 0);
  if (!stage.tracked) return "状态未返回";
  if (blockedByWorker && active > 0) return "Worker 阻塞";
  if (stage.state === "not_requested" || stage.requested === 0) return "未请求";
  if (stage.state === "ready") return "完成";
  if (stage.state === "running") return "运行中";
  if (stage.state === "active") return "处理中";
  if (stage.state === "queued") return "排队";
  if (stage.state === "partial") return "部分完成";
  return stage.state === "pending" ? "待处理" : "状态已记录";
}

function ContractStageTile({
  stageKey,
  label,
  stage,
  blockedByWorker,
}: {
  stageKey: string;
  label: string;
  stage: SearchProgressContractStage;
  blockedByWorker: boolean;
}) {
  const parts = contractStageParts(stage, blockedByWorker);
  const active = (stage.counts.queued ?? 0) + (stage.counts.running ?? 0) + (stage.counts.active ?? 0);
  const blocked = blockedByWorker && active > 0;
  const main = stage.requested === 0 || stage.state === "not_requested"
    ? "未请求"
    : stage.successful != null && stage.requested != null
      ? `${stage.successful}/${stage.requested} 成功`
      : contractStageLabel(stage, blockedByWorker);
  return (
    <div
      data-testid={`kol-truth-progress-${stageKey}`}
      className={`rounded-md border px-2 py-1.5 ${blocked
        ? "border-rose-300/20 bg-rose-400/[0.055]"
        : "border-white/[0.07] bg-black/15"}`}
    >
      <div className="flex items-center justify-between gap-1.5">
        <span className="text-[9px] text-slate-500">{label}</span>
        <span className={`text-[8.5px] ${blocked ? "text-rose-200" : stage.state === "ready" ? "text-emerald-200" : "text-slate-400"}`}>
          {contractStageLabel(stage, blockedByWorker)}
        </span>
      </div>
      <div className="mt-0.5 text-[10.5px] font-medium text-slate-100">{main}</div>
      {parts.length ? <div className="mt-0.5 text-[8.5px] leading-relaxed text-slate-400">{parts.join(" · ")}</div> : null}
      {stage.dataReady != null ? (
        <div className="mt-0.5 text-[8.5px] text-cyan-100/70">可用数据 {stage.dataReady}</div>
      ) : null}
    </div>
  );
}

function workerCopy(contract: SearchProgressContractView): { label: string; tone: string; title: string } {
  const worker = contract.worker;
  const count = worker.onlineCount != null
    ? `${worker.onlineCount}${worker.expectedCount != null ? `/${worker.expectedCount}` : ""}`
    : "";
  const title = [
    worker.latestHeartbeatAt ? `最近心跳 ${worker.latestHeartbeatAt}` : "未返回心跳时间",
    worker.shaAligned === false ? "Worker 与当前发布版本不一致" : "",
  ].filter(Boolean).join(" · ");
  if (!worker.observed) return { label: "Worker 未观测", tone: "text-slate-400 border-white/[0.09]", title };
  if (worker.state === "release_mismatch" || worker.shaAligned === false) {
    return { label: `Worker${count ? ` ${count}` : ""} · 版本不一致`, tone: "text-rose-200 border-rose-300/25", title };
  }
  if (worker.online === false || worker.state === "offline") {
    return { label: `Worker${count ? ` ${count}` : ""} · 离线`, tone: "text-rose-200 border-rose-300/25", title };
  }
  if (worker.state === "under_capacity" || worker.capacityReady === false) {
    return { label: `Worker${count ? ` ${count}` : ""} · 容量不足`, tone: "text-amber-200 border-amber-300/25", title };
  }
  if (worker.online === true) return { label: `Worker${count ? ` ${count}` : ""} · 在线`, tone: "text-emerald-200 border-emerald-300/25", title };
  return { label: "Worker 状态未知", tone: "text-slate-400 border-white/[0.09]", title };
}

function ContractProgressCard({ progress }: { progress: SearchSessionProgress }) {
  const contract = progress.contract!;
  const runtime = runtimeSurfaceFromLocation(typeof window !== "undefined" ? window.location.href : "", API_BASE);
  const worker = workerCopy(contract);
  const search = contract.stages.search;
  const candidateMain = search.dataReady != null && search.population != null
    ? `${search.dataReady}/${search.population} 已返回`
    : search.dataReady != null
      ? `已返回 ${search.dataReady}`
      : "返回数量未确认";
  const overallState = contract.blockedByWorker
    ? "Worker 阻塞"
    : contract.fullAnalysisComplete
      ? "完整数据可用"
      : progress.phaseLabel;
  const runtimeIcon = runtime.kind === "local" ? Laptop : runtime.kind === "cloud" ? Cloud : Server;
  const RuntimeIcon = runtimeIcon;

  return (
    <div
      data-testid="kol-progressive-stage-card"
      data-progress-contract={contract.schema}
      className={`rounded-lg border px-3 py-2.5 ${contract.blockedByWorker
        ? "border-rose-300/20 bg-rose-950/[0.10]"
        : "border-cyan-300/15 bg-cyan-950/[0.10]"}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5 text-[10.5px] font-medium text-cyan-50">
          {contract.blockedByWorker
            ? <WifiOff size={12} className="text-rose-300" />
            : contract.fullAnalysisComplete
              ? <CheckCircle2 size={12} className="text-emerald-300" />
              : <Loader2 size={12} className="animate-spin text-cyan-200" />}
          找达人真实进度
          <span
            data-testid="kol-progress-runtime"
            title={`依据当前 API 请求地址判定${runtime.host ? `：${runtime.host}` : ""}；不代表另一套环境状态`}
            className="inline-flex items-center gap-1 rounded-full border border-white/[0.09] px-1.5 py-0.5 text-[8.5px] font-normal text-slate-300"
          >
            <RuntimeIcon size={9} /> {runtime.label}
          </span>
          <span
            data-testid="kol-progress-worker"
            title={worker.title}
            className={`rounded-full border px-1.5 py-0.5 text-[8.5px] font-normal ${worker.tone}`}
          >{worker.label}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {contract.progressPct != null ? (
            <span data-testid="kol-progress-success-pct" title="只计已持久化成功结果；排队、运行、失败均不计完成" className="rounded-full border border-emerald-300/20 bg-emerald-400/[0.07] px-2 py-0.5 text-[9px] text-emerald-100">
              已请求成功 {compactPercent(contract.progressPct)}
            </span>
          ) : null}
          {contract.terminalPct != null ? (
            <span data-testid="kol-progress-terminal-pct" title="已结束包含成功、部分完成、失败和跳过；不等于成功完成" className="rounded-full border border-white/[0.09] px-2 py-0.5 text-[9px] text-slate-400">
              已结束 {compactPercent(contract.terminalPct)}
            </span>
          ) : null}
          <span data-testid="kol-progress-strict-status" className={`rounded-full border px-2 py-0.5 text-[9px] ${contract.blockedByWorker ? "border-rose-300/25 text-rose-200" : "border-cyan-300/20 text-cyan-100"}`}>
            {overallState}
          </span>
        </div>
      </div>

      <div className="mt-2 grid gap-1.5 sm:grid-cols-2 xl:grid-cols-5">
        <div data-testid="kol-progress-base" className="rounded-md border border-white/[0.07] bg-black/15 px-2 py-1.5">
          <div className="flex items-center justify-between gap-1.5">
            <span className="text-[9px] text-slate-500">候选返回</span>
            <span className={`text-[8.5px] ${search.state === "ready" ? "text-emerald-200" : "text-slate-400"}`}>{contractStageLabel(search, false)}</span>
          </div>
          <div className="mt-0.5 text-[10.5px] font-medium text-slate-100">{candidateMain}</div>
          <div className="mt-0.5 text-[8.5px] text-slate-400">只报当前会话已持久化候选</div>
        </div>
        <ContractStageTile stageKey="profile" label="档案" stage={contract.stages.profile} blockedByWorker={contract.blockedByWorker} />
        <ContractStageTile stageKey="video" label="视频" stage={contract.stages.video} blockedByWorker={contract.blockedByWorker} />
        <ContractStageTile stageKey="comments" label="评论" stage={contract.stages.comments} blockedByWorker={contract.blockedByWorker} />
        <ContractStageTile stageKey="audience" label="受众" stage={contract.stages.audience} blockedByWorker={contract.blockedByWorker} />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-slate-500">
        <span>完成口径：持久化成功 {contract.successfulUnits ?? "未返回"}/{contract.requestedUnits ?? "未返回"} 单元</span>
        {(contract.queuedUnits ?? 0) > 0 ? <span>排队 {contract.queuedUnits} · 不计完成</span> : null}
        {(contract.runningUnits ?? 0) > 0 ? <span>运行 {contract.runningUnits} · 不计完成</span> : null}
        {(contract.activeUnits ?? 0) > 0 ? <span>处理中/状态待确认 {contract.activeUnits} · 不计完成</span> : null}
        {(contract.failedUnits ?? 0) > 0 ? <span className="text-rose-200/80">失败/不完整 {contract.failedUnits} · 不计完成</span> : null}
        {contract.fullAnalysisExecutionComplete && !contract.fullAnalysisObservable ? (
          <span className="text-amber-200/80">执行已结束，但可用数据尚未完全可观测</span>
        ) : null}
        {contract.observedAt ? <span title={contract.observedAt}>会话状态已观测</span> : null}
      </div>
    </div>
  );
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
    || Boolean(progress.currentItem)
    || Boolean(progress.contract);
  if (!hasEvidence) return null;
  if (progress.contract) return <ContractProgressCard progress={progress} />;

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
    progress.currentItem ? kolHumanDisplayName(progress.currentItem, "创作者") : "",
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
