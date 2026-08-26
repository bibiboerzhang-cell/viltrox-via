// U1 顶栏全局任务进度中心(会呼吸的指挥室 · 动的是进度与新鲜度)。
// 自持 state 的自拉取组件(GlobalSearchBox 同款定位):不吃任何 props,
// 10s 轮询 GET /api/admin/vkpi/progress/center(纯读聚合,一次请求喂全部)。
//
// 形态两档:
//   忙:药丸态「N 跑中 · M 排队」+ 底部 2px 细进度条缓慢呼吸(唯一循环动画,
//       2.4s 低幅 opacity,是任务点名要的"呼吸",非闪烁;reduced-motion 降静态);
//   闲:安静收起成图标按钮(不占注意力,点开仍可看最近完成流水)。
// 点开下拉抽屉:跑中(进度%/ETA/深析·全案类显示阶段流 队列中→抓取→分析→落库)
// + 排队(位次/预计等待)+ 最近完成 5 条。抽屉入场动画一次(220ms),无循环。
// 显示层宪法:只展示任务进度与新鲜度,不透出任何内部评分。

import React from "react";
import { Activity, ChevronDown } from "lucide-react";
import { AnimatePresence, MotionConfig, m } from "framer-motion";
import {
  fetchProgressCenter,
  type ProgressCenterData,
  type ProgressRecentDone,
  type ProgressTask,
} from "../../../../services/vkpi/progressCenter-api";
import { buildApiUrl } from "../../../../services/http";
import { readStoredApiToken } from "../../../../services/vkpi/globalSearch-api";
import { relativeFromNow } from "../../lib/timeLocal";
import { etaLabel, etaSecondsOf, hasReadableFailure } from "../../../../services/vkpi/failureReason";
import { FailureGuidance } from "../lib/failureGuidance";
import { useEventStreamOrPoll } from "../useEventStreamOrPoll";
import { useT } from "../lib/i18n";
import type { Translate } from "../../../../app/providers/LocaleProvider";
import {
  analysisChannelLabel,
  analysisProviderTrace,
  analysisStageFlow,
  analysisTaskBindingLabel,
  analysisTaskBindingTrace,
  analysisTerminalCopy,
} from "./analysisTaskProgress";

const e = React.createElement;

const POLL_MS = 10000;
// A4 后端聚合事件流已上线:server 端轮询转推(每数秒重算投影,diff 才推一帧 snapshot),
// 前端自动切 SSE(近实时、少连接)。SSE 不可用 / 断线 → useEventStreamOrPoll 无感回退
// 固定间隔轮询(行为与轮询版完全一致)。
const PROGRESS_STREAM_URL: string | null = buildApiUrl("/api/admin/vkpi/progress/center/stream");

// 呼吸动画:唯一的循环动画,幅度克制(opacity .45↔.9)。reduced-motion 下降级为静态。
const BREATH_CSS = `
@keyframes tpc-breath { 0%, 100% { opacity: .45; } 50% { opacity: .9; } }
.tpc-breath { animation: tpc-breath 2.4s ease-in-out infinite; }
@media (prefers-reduced-motion: reduce) {
  .tpc-breath { animation: none; opacity: .7; }
}
`;

// 深析/全案类任务:抽屉里显示 4 步阶段流(队列中→抓取→分析→落库)。
// kind 口径与后端 queue_view._infer_kind 同源。
const DEEP_KINDS = new Set([
  "video深析",
  "账号分析",
  "内容契合",
  "报告生成",
  "复盘聚合",
  "总结沉淀",
  "智能查找",
  "KOL查找",
  "LLM分析",
  "受众分析",
  "视频QA",
  "评论分析",
  "营销顾问",
  "账号沉淀",
]);

function isDeepTask(task: ProgressTask): boolean {
  const kind = String(task.kind || "");
  if (DEEP_KINDS.has(kind)) return true;
  return /深析|全案/.test(kind) || /final_v1|deep|report/.test(String(task.job_type || ""));
}

// 人性化 ETA(TaskProgressBoard 同口径,F7 新口径 eta_seconds):非法/缺失不显示。
function etaText(seconds: unknown): string {
  return etaLabel(etaSecondsOf({ eta_seconds: seconds }));
}

// 门面显示映射:后端 kind 里带内部术语的少数几个换成业务说法(红线2)。
// 只改显示层——DEEP_KINDS 判定仍用后端原值,契约不动。
const KIND_DISPLAY: Record<string, string> = {
  "LLM分析": "智能分析",
  "video深析": "视频深度分析",
};

// kind 是后端 queue_view 产出的中文字面(同源口径),英文模式下原样透出不翻;只有兜底词走 t。
function taskTitle(task: ProgressTask | ProgressRecentDone, t: Translate): string {
  const raw = String(task.kind || "");
  const kind = raw ? (KIND_DISPLAY[raw] || raw) : t("任务");
  const label = String(task.label || "").trim();
  return label ? `${kind} · ${label}` : kind;
}

function llmPhaseLabel(task: ProgressTask | ProgressRecentDone, t: Translate): string {
  const labels: Record<string, string> = {
    dialogue: t("对话生成"),
    structured_generation: t("结构化分析"),
    provider_generation: t("模型生成"),
    evaluation: t("结果评估"),
    qa: t("证据 QA"),
  };
  const phase = String(task.phase || "").trim();
  const subphase = String(task.subphase || "").trim();
  const parts = [labels[phase] || phase, labels[subphase] || subphase].filter(Boolean);
  const attempt = Number(task.attempt_index);
  const total = Number(task.attempt_total);
  if (Number.isFinite(attempt) && attempt > 0 && Number.isFinite(total) && total > 0) {
    parts.push(t("尝试 {attempt}/{total}", { attempt, total }));
  }
  return parts.join(" · ");
}

// ── 抽屉里的小件们 ────────────────────────────────────────────────────────────

/** 4 步阶段流:当前步高亮,已过步实心,未来步暗。纯静态,无动画。 */
function StageFlow({ task, flow }: { task: ProgressTask; flow: Array<{ stage: string; label: string }> }) {
  const current = Math.max(0, flow.findIndex((s) => s.stage === String(task.stage || "")));
  return e("div", { className: "mt-1 flex items-center gap-1 text-[10px]" },
    ...flow.map((step, i) => e(React.Fragment, { key: step.stage },
      i > 0 && e("span", { className: "text-muted" }, "→"),
      e("span", {
        className: i === current
          ? "font-medium text-accent"
          : i < current
            ? "text-ink-2"
            : "text-muted",
      }, step.label)
    ))
  );
}

/** 跑中一行:标题 + 进度条(%已知走宽度,未知走整条低幅呼吸)+ ETA/阶段。 */
function RunningRow({ task, flow }: { task: ProgressTask; flow: Array<{ stage: string; label: string }> }) {
  const { t } = useT();
  const pct = Number.isFinite(Number(task.progress_pct)) && task.progress_pct !== null
    ? Math.max(0, Math.min(100, Number(task.progress_pct)))
    : null;
  const eta = etaText(task.eta_seconds);
  const progressLabel = task.progress_overdue
    ? String(task.progress_label || t("已超历史均时"))
    : pct !== null
      ? `${task.progress_estimated ? "≈" : ""}${pct}%`
      : "";
  const taskFlow = analysisStageFlow(task, flow);
  const channelLabel = analysisChannelLabel(task);
  const channelTrace = analysisProviderTrace(task);
  const taskBindingLabel = analysisTaskBindingLabel(task);
  const taskBindingTrace = analysisTaskBindingTrace(task);
  const phaseLabel = llmPhaseLabel(task, t);
  return e("div", { className: "px-3 py-2" },
    e("div", { className: "flex items-center gap-2" },
      e("span", { className: "min-w-0 flex-1 truncate text-xs text-ink-2" }, taskTitle(task, t)),
      progressLabel && e("span", {
        className: `shrink-0 text-[10px] tabular-nums ${task.progress_overdue ? "text-warn" : "text-muted"}`,
      }, progressLabel),
      eta && e("span", { className: "shrink-0 text-[10px] text-muted" }, eta)
    ),
    channelLabel && e("div", { className: "mt-0.5 truncate text-[9px] text-muted", title: channelTrace || undefined }, channelLabel),
    taskBindingLabel && e("div", { className: "mt-0.5 truncate text-[9px] text-muted", title: taskBindingTrace || undefined }, `${t("任务绑定")} · ${taskBindingLabel}`),
    phaseLabel && e("div", { className: "mt-0.5 truncate text-[9px] text-muted" }, phaseLabel),
    e("div", { className: "mt-1.5 h-1 overflow-hidden rounded-full bg-panel" },
      pct !== null
        // 已知进度:宽度即进度,变化时 300ms 平滑过渡(变化动画一次,无循环)。
        ? e("div", {
            className: "h-full rounded-full bg-accent transition-[width] duration-300 ease-out",
            style: { width: `${Math.max(3, pct)}%` },
          })
        // 未知进度:整条低幅呼吸(不做左右扫动,克制)。
        : e("div", { className: "tpc-breath h-full rounded-full bg-accent" })
    ),
    isDeepTask(task)
      ? e(StageFlow, { task, flow: taskFlow })
      : task.stage_label && e("div", { className: "mt-1 text-[10px] text-muted" }, String(task.stage_label))
  );
}

function QueuedRow({ task }: { task: ProgressTask }) {
  const { t } = useT();
  const pos = Number(task.queue_position);
  const eta = etaText(task.eta_seconds);
  const retrying = String(task.status || "").toLowerCase() === "retrying";
  const taskFlow = analysisStageFlow(task);
  const channelLabel = analysisChannelLabel(task);
  const channelTrace = analysisProviderTrace(task);
  const taskBindingLabel = analysisTaskBindingLabel(task);
  const taskBindingTrace = analysisTaskBindingTrace(task);
  const phaseLabel = llmPhaseLabel(task, t);
  return e("div", { className: "px-3 py-1.5" },
    e("div", { className: "flex items-center gap-2" },
      Number.isFinite(pos) && pos > 0 && e("span", {
        className: "shrink-0 rounded bg-panel px-1.5 py-0.5 text-[10px] tabular-nums text-muted",
      }, t("第 {pos} 位", { pos })),
      e("span", { className: "min-w-0 flex-1 truncate text-xs text-ink-2" }, taskTitle(task, t)),
      retrying && e("span", { className: "shrink-0 text-[10px] text-warn" }, t("等待重试")),
      eta && e("span", { className: "shrink-0 text-[10px] text-muted" }, eta)
    ),
    isDeepTask(task) && e("div", { className: "mt-0.5 truncate pl-0 text-[9px] text-muted" },
      [taskFlow[0]?.label, taskFlow[1]?.label].filter(Boolean).join(" → ")
    ),
    channelLabel && e("div", { className: "mt-0.5 truncate text-[9px] text-muted", title: channelTrace || undefined }, channelLabel),
    taskBindingLabel && e("div", { className: "mt-0.5 truncate text-[9px] text-muted", title: taskBindingTrace || undefined }, `${t("任务绑定")} · ${taskBindingLabel}`),
    phaseLabel && e("div", { className: "mt-0.5 truncate text-[9px] text-muted" }, phaseLabel)
  );
}

function RecentRow({ item }: { item: ProgressRecentDone }) {
  const { t } = useT();
  const copy = analysisTerminalCopy(item);
  const channelLabel = analysisChannelLabel(item);
  const channelTrace = analysisProviderTrace(item);
  const taskBindingLabel = analysisTaskBindingLabel(item);
  const taskBindingTrace = analysisTaskBindingTrace(item);
  const phaseLabel = llmPhaseLabel(item, t);
  const dotClass = copy.tone === "ready" ? "bg-good" : copy.tone === "warn" ? "bg-warn" : "bg-crit";
  const statusClass = copy.tone === "ready" ? "text-good" : copy.tone === "warn" ? "text-warn" : "text-crit";
  return e("div", { className: "px-3 py-1.5" },
    e("div", { className: "flex items-center gap-2" },
      e("span", {
        className: `h-1.5 w-1.5 shrink-0 rounded-full ${dotClass}`,
        "aria-hidden": true,
      }),
      e("span", { className: "min-w-0 flex-1 truncate text-xs text-ink-2" }, taskTitle(item, t)),
      e("span", { className: `shrink-0 text-[10px] ${statusClass}` }, copy.label),
      e("span", { className: "shrink-0 text-[10px] text-muted" }, relativeFromNow(item.finished_at))
    ),
    (channelLabel || taskBindingLabel || phaseLabel || copy.detail) && e("div", { className: "mt-0.5 pl-3.5 text-[9px] leading-4 text-muted" },
      channelLabel && e("span", { title: channelTrace || undefined }, channelLabel),
      channelLabel && taskBindingLabel && e("span", null, " · "),
      taskBindingLabel && e("span", { title: taskBindingTrace || undefined }, `${t("任务绑定")} ${taskBindingLabel}`),
      (channelLabel || taskBindingLabel) && phaseLabel && e("span", null, " · "),
      phaseLabel && e("span", null, phaseLabel),
      (channelLabel || taskBindingLabel || phaseLabel) && copy.detail && e("span", null, " · "),
      copy.detail && e("span", null, copy.detail)
    ),
    // F3 失败可读:有新契约字段才渲染类别提示/动作;authorization 类跳 MY KOL 由负责人重发。
    copy.tone !== "ready" && hasReadableFailure(item) && e("div", { className: "pl-3.5" },
      e(FailureGuidance, {
        source: item,
        compact: false,
        onReissue: () => window.dispatchEvent(new CustomEvent("vkpi:open-mykol-kol", { detail: { kolPoolId: item.kol_pool_id ?? null } })),
      })
    )
  );
}

function SectionHeader(label: string, extra?: string) {
  return e("div", { className: "flex items-baseline justify-between px-3 pb-1 pt-2" },
    e("span", { className: "text-[10px] font-semibold uppercase tracking-wider text-muted" }, label),
    extra ? e("span", { className: "text-[10px] text-muted" }, extra) : null
  );
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export function TopProgressCenter() {
  const { t } = useT();
  const [data, setData] = React.useState<ProgressCenterData | null>(null);
  const [loadFailed, setLoadFailed] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  const boxRef = React.useRef<HTMLDivElement | null>(null);
  const aliveRef = React.useRef(true);

  React.useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);

  const load = React.useCallback(() => {
    return fetchProgressCenter()
      .then((res) => {
        if (!aliveRef.current) return;
        setData(res);
        setLoadFailed(false);
      })
      .catch(() => {
        if (aliveRef.current) setLoadFailed(true);
      });
  }, []);

  // SSE 优先 + 轮询兜底(归一定时器):有事件流走 SSE,否则 10s 轮询 + 可见性暂停。
  // 存储 token 只用于 POST 签发短时一次性 SSE ticket，不进入 EventSource URL。
  const streamToken = readStoredApiToken();
  useEventStreamOrPoll({
    pollFn: load,
    interval: POLL_MS,
    streamUrl: PROGRESS_STREAM_URL,
    streamToken: streamToken || undefined,
  });

  // 点开立即刷一次(不等下一拍)。
  React.useEffect(() => { if (open) load(); }, [open, load]);

  // 外点/Escape 关抽屉(GlobalSearchBox 同款)。
  React.useEffect(() => {
    if (!open) return;
    const onDown = (ev: MouseEvent) => {
      const node = boxRef.current;
      if (node && ev.target instanceof Node && !node.contains(ev.target)) setOpen(false);
    };
    const onKey = (ev: KeyboardEvent) => { if (ev.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const running = data?.running || [];
  const queued = data?.queued || [];
  const recentDone = data?.recent_done || [];
  const recentLlm = data?.recent_llm || [];
  const counts = data?.counts || { running: 0, queued: 0, active_total: 0, recent_total: 0 };
  const busy = counts.running > 0 || counts.queued > 0;
  const hasRunning = counts.running > 0;
  const workerOffline = data?.diagnostics?.worker_online === false;
  const queueBlocked = workerOffline && counts.queued > 0;
  const reservationTrackingUnavailable =
    data?.diagnostics?.llm_reservation_schema_available === false;
  const flow = data?.stage_flow || [];

  // 药丸态聚合进度:跑中任务已知 % 的均值;全未知则 null(呼吸整条)。
  const knownPcts = running
    .map((t) => Number(t.progress_pct))
    .filter((n) => Number.isFinite(n) && n > 0);
  const aggregatePct = knownPcts.length > 0
    ? Math.round(knownPcts.reduce((a, b) => a + b, 0) / knownPcts.length)
    : null;

  const pillLabel = hasRunning
    ? counts.queued > 0
      ? t("{running} 跑中 · {queued} 排队", { running: counts.running, queued: counts.queued })
      : t("{running} 跑中", { running: counts.running })
    : queueBlocked
      ? t("后台没在跑 · {queued} 等待", { queued: counts.queued })
      : t("{queued} 排队", { queued: counts.queued });

  return e(MotionConfig, { reducedMotion: "user" },
    e("div", { ref: boxRef, className: "relative" },
      e("style", null, BREATH_CSS),
      // ── 触发器:忙=药丸+呼吸条 / 闲=安静图标 ──────────────────────────────
      e("button", {
        type: "button",
        onClick: () => setOpen((v) => !v),
        "aria-label": "Task Progress Center",
        "aria-expanded": open,
        title: queueBlocked ? t("后台当前没在跑，排队任务尚未开始") : busy ? `${t("任务进度")}:${pillLabel}` : t("任务进度中心(当前空闲)"),
        className: busy
          ? queueBlocked && !hasRunning
            ? "relative flex items-center gap-1.5 overflow-hidden rounded-lg border border-warn-soft bg-warn-soft px-2.5 py-1.5 text-xs text-warn hover:border-warn"
            : "relative flex items-center gap-1.5 overflow-hidden rounded-lg border border-accent-soft bg-accent-soft px-2.5 py-1.5 text-xs text-accent hover:border-accent"
          : "rounded-lg p-2 text-muted hover:bg-accent-soft hover:text-ink-2",
      },
        e(Activity, { size: busy ? 13 : 16 }),
        busy && e("span", { className: "tabular-nums" }, pillLabel),
        busy && e(ChevronDown, { size: 12, className: queueBlocked && !hasRunning ? "text-warn" : "text-accent" }),
        // 底部 2px 细进度条:有任务跑时缓慢呼吸(点名要的"呼吸",非闪烁)。
        hasRunning && e("div", { className: "absolute inset-x-0 bottom-0 h-[2px] bg-panel" },
          e("div", {
            className: "tpc-breath h-full bg-accent transition-[width] duration-300 ease-out",
            style: { width: aggregatePct !== null ? `${Math.max(6, Math.min(100, aggregatePct))}%` : "100%" },
          })
        )
      ),
      // ── 下拉抽屉:入场动画一次(220ms),无循环 ────────────────────────────
      e(AnimatePresence, null,
        open && e(m.div, {
          key: "tpc-drawer",
          initial: { opacity: 0, y: -6 },
          animate: { opacity: 1, y: 0 },
          exit: { opacity: 0, y: -4 },
          transition: { duration: 0.22, ease: "easeOut" },
          className: "absolute right-0 top-full z-50 mt-2 max-h-[70vh] w-[380px] overflow-y-auto rounded-lg border border-line bg-card py-1 shadow-2xl shadow-black/40",
        },
          e("div", { className: "flex items-center justify-between border-b border-line px-3 pb-2 pt-1.5" },
            e("span", { className: "text-xs font-semibold text-ink" }, t("任务进度中心")),
            e("span", { className: "text-[10px] tabular-nums text-muted" },
              busy ? pillLabel : t("空闲")
            )
          ),
          loadFailed && e("div", {
            className: "mx-2 mt-2 rounded-md border border-warn-soft bg-warn-soft px-2.5 py-2 text-[10px] text-warn",
          }, data === null
            ? t("进度服务暂不可用，当前状态未知；这不代表队列空闲。")
            : t("进度服务暂不可用，下面保留的是上一次成功快照。")),
          data === null && !loadFailed && e("div", { className: "px-3 py-3 text-xs text-muted" }, t("任务数据加载中...")),
          data !== null && !busy && recentDone.length === 0 && recentLlm.length === 0 && e("div", { className: "px-3 py-3 text-xs text-muted" },
            t("队列空闲,没有在跑的任务")
          ),
          queueBlocked && e("div", { className: "mx-2 mt-2 rounded-md border border-warn-soft bg-warn-soft px-2.5 py-2 text-[10px] text-warn" },
            t("后台未在运行，排队任务不会开始")
          ),
          // 门面禁内部术语:不提 migration 号 / Gateway;诚实信息(在飞跟踪未启用、只显示已完成结果)保留。
          reservationTrackingUnavailable && e("div", {
            className: "mx-2 mt-2 rounded-md border border-warn-soft bg-warn-soft px-2.5 py-2 text-[10px] text-warn",
          }, t("模型分析的在飞跟踪尚未启用(需后端升级);当前只显示已完成的分析结果。")),
          running.length > 0 && e(React.Fragment, { key: "sec-running" },
            SectionHeader(t("跑中"), counts.running > running.length ? t("共 {n} 条", { n: counts.running }) : undefined),
            ...running.slice(0, 8).map((task) => e(RunningRow, { key: `run-${task.id}`, task, flow }))
          ),
          queued.length > 0 && e(React.Fragment, { key: "sec-queued" },
            SectionHeader(queueBlocked ? t("等待后台") : t("排队"), counts.queued > queued.length ? t("深度 {n}", { n: counts.queued }) : undefined),
            ...queued.slice(0, 6).map((task) => e(QueuedRow, { key: `q-${task.id}`, task })),
            counts.queued > 6 && e("div", { className: "px-3 pb-1 text-[10px] text-muted" },
              t("…还有 {n} 条在队", { n: counts.queued - Math.min(6, queued.length) })
            )
          ),
          recentDone.length > 0 && e(React.Fragment, { key: "sec-recent" },
            SectionHeader(t("最近结果")),
            ...recentDone.map((item) => e(RecentRow, { key: `done-${item.source}-${item.id}`, item }))
          ),
          recentLlm.length > 0 && e(React.Fragment, { key: "sec-recent-llm" },
            SectionHeader(t("模型分析记录"), t("已完成调用")),
            ...recentLlm.map((item) => e(RecentRow, { key: `llm-${item.source}-${item.id}`, item }))
          )
        )
      )
    )
  );
}
