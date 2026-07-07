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
import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import {
  fetchProgressCenter,
  type ProgressCenterData,
  type ProgressRecentDone,
  type ProgressTask,
} from "../../../../services/vkpi/progressCenter-api";
import { relativeFromNow } from "../../lib/timeLocal";

const e = React.createElement;

const POLL_MS = 10000;

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
]);

function isDeepTask(task: ProgressTask): boolean {
  const kind = String(task.kind || "");
  if (DEEP_KINDS.has(kind)) return true;
  return /深析|全案/.test(kind) || /final_v1|deep|report/.test(String(task.job_type || ""));
}

// 人性化 ETA(TaskProgressBoard 同口径):<60s 秒,≥60s 分钟;非法值不显示。
function etaText(seconds: unknown): string {
  const eta = Number(seconds);
  if (!Number.isFinite(eta) || eta <= 0) return "";
  if (eta < 60) return `约 ${Math.max(1, Math.round(eta))} 秒`;
  return `约 ${Math.round(eta / 60)} 分钟`;
}

function taskTitle(task: ProgressTask | ProgressRecentDone): string {
  const kind = String(task.kind || "任务");
  const label = String(task.label || "").trim();
  return label ? `${kind} · ${label}` : kind;
}

// ── 抽屉里的小件们 ────────────────────────────────────────────────────────────

/** 4 步阶段流:当前步高亮,已过步实心,未来步暗。纯静态,无动画。 */
function StageFlow({ task, flow }: { task: ProgressTask; flow: Array<{ stage: string; label: string }> }) {
  const current = Math.max(0, flow.findIndex((s) => s.stage === String(task.stage || "")));
  return e("div", { className: "mt-1 flex items-center gap-1 text-[10px]" },
    ...flow.map((step, i) => e(React.Fragment, { key: step.stage },
      i > 0 && e("span", { className: "text-slate-700" }, "→"),
      e("span", {
        className: i === current
          ? "font-medium text-blue-300"
          : i < current
            ? "text-slate-400"
            : "text-slate-600",
      }, step.label)
    ))
  );
}

/** 跑中一行:标题 + 进度条(%已知走宽度,未知走整条低幅呼吸)+ ETA/阶段。 */
function RunningRow({ task, flow }: { task: ProgressTask; flow: Array<{ stage: string; label: string }> }) {
  const pct = Number.isFinite(Number(task.progress_pct)) && task.progress_pct !== null
    ? Math.max(0, Math.min(100, Number(task.progress_pct)))
    : null;
  const eta = etaText(task.eta_seconds);
  return e("div", { className: "px-3 py-2" },
    e("div", { className: "flex items-center gap-2" },
      e("span", { className: "min-w-0 flex-1 truncate text-xs text-slate-200" }, taskTitle(task)),
      pct !== null && e("span", { className: "shrink-0 text-[10px] tabular-nums text-slate-400" }, `${pct}%`),
      eta && e("span", { className: "shrink-0 text-[10px] text-slate-500" }, eta)
    ),
    e("div", { className: "mt-1.5 h-1 overflow-hidden rounded-full bg-white/[0.06]" },
      pct !== null
        // 已知进度:宽度即进度,变化时 300ms 平滑过渡(变化动画一次,无循环)。
        ? e("div", {
            className: "h-full rounded-full bg-blue-400/80 transition-[width] duration-300 ease-out",
            style: { width: `${Math.max(3, pct)}%` },
          })
        // 未知进度:整条低幅呼吸(不做左右扫动,克制)。
        : e("div", { className: "tpc-breath h-full rounded-full bg-blue-400/60" })
    ),
    isDeepTask(task)
      ? e(StageFlow, { task, flow })
      : task.stage_label && e("div", { className: "mt-1 text-[10px] text-slate-500" }, String(task.stage_label))
  );
}

function QueuedRow({ task }: { task: ProgressTask }) {
  const pos = Number(task.queue_position);
  const eta = etaText(task.eta_seconds);
  return e("div", { className: "flex items-center gap-2 px-3 py-1.5" },
    Number.isFinite(pos) && pos > 0 && e("span", {
      className: "shrink-0 rounded bg-white/[0.05] px-1.5 py-0.5 text-[10px] tabular-nums text-slate-400",
    }, `第 ${pos} 位`),
    e("span", { className: "min-w-0 flex-1 truncate text-xs text-slate-300" }, taskTitle(task)),
    eta && e("span", { className: "shrink-0 text-[10px] text-slate-500" }, eta)
  );
}

function RecentRow({ item }: { item: ProgressRecentDone }) {
  const ok = String(item.status || "") === "done";
  return e("div", { className: "flex items-center gap-2 px-3 py-1.5" },
    e("span", {
      className: `h-1.5 w-1.5 shrink-0 rounded-full ${ok ? "bg-emerald-400" : "bg-rose-400"}`,
      "aria-hidden": true,
    }),
    e("span", { className: "min-w-0 flex-1 truncate text-xs text-slate-300" }, taskTitle(item)),
    e("span", { className: "shrink-0 text-[10px] text-slate-500" },
      [ok ? "完成" : "失败", relativeFromNow(item.finished_at)].filter(Boolean).join(" · ")
    )
  );
}

function SectionHeader(label: string, extra?: string) {
  return e("div", { className: "flex items-baseline justify-between px-3 pb-1 pt-2" },
    e("span", { className: "text-[10px] font-semibold uppercase tracking-wider text-slate-500" }, label),
    extra ? e("span", { className: "text-[10px] text-slate-600" }, extra) : null
  );
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export function TopProgressCenter() {
  const [data, setData] = React.useState<ProgressCenterData | null>(null);
  const [open, setOpen] = React.useState(false);
  const boxRef = React.useRef<HTMLDivElement | null>(null);
  const aliveRef = React.useRef(true);

  const load = React.useCallback(() => {
    fetchProgressCenter()
      .then((res) => { if (aliveRef.current) setData(res); })
      .catch(() => { /* 拉取失败保留上次快照,静默;首拉失败=闲态图标 */ });
  }, []);

  // 10s 轮询;标签页不可见时跳过该拍(省请求,回来下一拍自然刷新)。
  React.useEffect(() => {
    aliveRef.current = true;
    load();
    const timer = window.setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState !== "hidden") load();
    }, POLL_MS);
    return () => { aliveRef.current = false; window.clearInterval(timer); };
  }, [load]);

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
  const counts = data?.counts || { running: 0, queued: 0, active_total: 0, recent_total: 0 };
  const busy = counts.running > 0 || counts.queued > 0;
  const flow = data?.stage_flow || [];

  // 药丸态聚合进度:跑中任务已知 % 的均值;全未知则 null(呼吸整条)。
  const knownPcts = running
    .map((t) => Number(t.progress_pct))
    .filter((n) => Number.isFinite(n) && n > 0);
  const aggregatePct = knownPcts.length > 0
    ? Math.round(knownPcts.reduce((a, b) => a + b, 0) / knownPcts.length)
    : null;

  const pillLabel = counts.queued > 0
    ? `${counts.running} 跑中 · ${counts.queued} 排队`
    : `${counts.running} 跑中`;

  return e(MotionConfig, { reducedMotion: "user" },
    e("div", { ref: boxRef, className: "relative" },
      e("style", null, BREATH_CSS),
      // ── 触发器:忙=药丸+呼吸条 / 闲=安静图标 ──────────────────────────────
      e("button", {
        type: "button",
        onClick: () => setOpen((v) => !v),
        "aria-label": "Task Progress Center",
        "aria-expanded": open,
        title: busy ? `任务进度:${pillLabel}` : "任务进度中心(当前空闲)",
        className: busy
          ? "relative flex items-center gap-1.5 overflow-hidden rounded-lg border border-blue-500/25 bg-blue-500/[0.08] px-2.5 py-1.5 text-xs text-blue-200 hover:border-blue-500/40 hover:bg-blue-500/[0.15]"
          : "rounded-lg p-2 text-slate-500 hover:bg-white/[0.04] hover:text-slate-300",
      },
        e(Activity, { size: busy ? 13 : 16 }),
        busy && e("span", { className: "tabular-nums" }, pillLabel),
        busy && e(ChevronDown, { size: 12, className: "text-blue-300/70" }),
        // 底部 2px 细进度条:有任务跑时缓慢呼吸(点名要的"呼吸",非闪烁)。
        busy && e("div", { className: "absolute inset-x-0 bottom-0 h-[2px] bg-white/[0.06]" },
          e("div", {
            className: "tpc-breath h-full bg-blue-400/80 transition-[width] duration-300 ease-out",
            style: { width: aggregatePct !== null ? `${Math.max(6, Math.min(100, aggregatePct))}%` : "100%" },
          })
        )
      ),
      // ── 下拉抽屉:入场动画一次(220ms),无循环 ────────────────────────────
      e(AnimatePresence, null,
        open && e(motion.div, {
          key: "tpc-drawer",
          initial: { opacity: 0, y: -6 },
          animate: { opacity: 1, y: 0 },
          exit: { opacity: 0, y: -4 },
          transition: { duration: 0.22, ease: "easeOut" },
          className: "absolute right-0 top-full z-50 mt-2 max-h-[70vh] w-[380px] overflow-y-auto rounded-lg border border-white/[0.08] bg-[#0a0f1e] py-1 shadow-2xl shadow-black/40",
        },
          e("div", { className: "flex items-center justify-between border-b border-white/[0.06] px-3 pb-2 pt-1.5" },
            e("span", { className: "text-xs font-semibold text-white" }, "任务进度中心"),
            e("span", { className: "text-[10px] tabular-nums text-slate-500" },
              busy ? pillLabel : "空闲"
            )
          ),
          data === null && e("div", { className: "px-3 py-3 text-xs text-slate-500" }, "任务数据加载中..."),
          data !== null && !busy && recentDone.length === 0 && e("div", { className: "px-3 py-3 text-xs text-slate-500" },
            "队列空闲,没有在跑的任务"
          ),
          running.length > 0 && e(React.Fragment, { key: "sec-running" },
            SectionHeader("跑中", counts.running > running.length ? `共 ${counts.running} 条` : undefined),
            ...running.slice(0, 8).map((task) => e(RunningRow, { key: `run-${task.id}`, task, flow }))
          ),
          queued.length > 0 && e(React.Fragment, { key: "sec-queued" },
            SectionHeader("排队", counts.queued > queued.length ? `深度 ${counts.queued}` : undefined),
            ...queued.slice(0, 6).map((task) => e(QueuedRow, { key: `q-${task.id}`, task })),
            counts.queued > 6 && e("div", { className: "px-3 pb-1 text-[10px] text-slate-600" },
              `…还有 ${counts.queued - Math.min(6, queued.length)} 条在队`
            )
          ),
          recentDone.length > 0 && e(React.Fragment, { key: "sec-recent" },
            SectionHeader("最近完成"),
            ...recentDone.map((item) => e(RecentRow, { key: `done-${item.source}-${item.id}`, item }))
          )
        )
      )
    )
  );
}
