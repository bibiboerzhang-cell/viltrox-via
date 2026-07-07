// U3 会呼吸的指挥室 · ThresholdBar:条件化预判的阈值进度条。
// ----------------------------------------------------------------------------
// 吃 forecast 段 escalate_if / retreat_if 文本里的量化值(显示层解析,不改口径):
//   两条件同单位 → 单条三色区间条(撤退区 rose / 中间观察区 slate / 加码区 emerald),
//   离加码/撤退多远一眼看懂;单位不同或只解析出一条 → 每条件一条 mini 阈值带;
//   全解析不出 → 返回 null,调用方保留原文本卡(诚实退化,绝不硬编数值)。
// 审美纪律:区间填充入场补间一次(300ms),无循环;prefers-reduced-motion 直显终值
//   (framer-motion useReducedMotion 读同名 media query)。深色 slate/白 6% 体系,
//   emerald/rose 沿用页面既有语义色,不引新色板。
// 显示层宪法:只消费 public_plan 已给的条件文本,不渲染任何内部评分。

import React from "react";
import { motion, useReducedMotion } from "framer-motion";

const e = React.createElement;

export interface ParsedThreshold {
  value: number;
  unit: string; // "%" | "$" | "条" | "个" | … | ""(纯数)
  raw: string; // 命中的原文片段(标签展示用)
}

// 时间窗词(48h 内 / 7 天内…)不是阈值,跳过;取阅读顺序上第一个「数+量词」。
const UNIT_PATTERN = "条|个|人|次|位|款|篇|单|家|万|[kK]";

export function parseThresholdValue(text: string): ParsedThreshold | null {
  if (!text || typeof text !== "string") return null;
  const re = new RegExp(
    // ① $金额 ② 数值% ③ 数值+量词(时间单位不算)
    `\\$\\s*(\\d+(?:[.,]\\d+)?)|(\\d+(?:\\.\\d+)?)\\s*%|(\\d+(?:\\.\\d+)?)\\s*(${UNIT_PATTERN})`,
    "g",
  );
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m[1] !== undefined) return { value: parseFloat(m[1].replace(/,/g, "")), unit: "$", raw: m[0].trim() };
    if (m[2] !== undefined) return { value: parseFloat(m[2]), unit: "%", raw: m[0].trim() };
    if (m[3] !== undefined) {
      const unit = m[4] === "k" || m[4] === "K" ? "K" : m[4];
      return { value: parseFloat(m[3]), unit, raw: m[0].trim() };
    }
  }
  return null;
}

const TRACK = "relative h-2 w-full overflow-hidden rounded-full bg-white/[0.06]";
const EMERALD = "rgba(52,211,153,0.45)"; // emerald-400 @ 45%,与页面 emerald-500/[0.12] 同族
const ROSE = "rgba(251,113,133,0.40)"; // rose-400 @ 40%
const SLATE = "rgba(148,163,184,0.18)"; // slate-400 @ 18%(中间观察区)

function fmt(v: number, unit: string): string {
  const n = Number.isInteger(v) ? String(v) : v.toFixed(1);
  return unit === "$" ? `$${n}` : `${n}${unit}`;
}

// 区间填充:入场从 0 宽补间到位一次;reduced motion 直显。
function Zone({ left, width, color, reduced, delay }: { left: number; width: number; color: string; reduced: boolean; delay: number }) {
  const style = { left: `${left}%`, background: color };
  if (reduced) return e("div", { className: "absolute top-0 h-full", style: { ...style, width: `${width}%` } });
  return e(motion.div, {
    className: "absolute top-0 h-full",
    style,
    initial: { width: 0 },
    animate: { width: `${width}%` },
    transition: { duration: 0.3, delay, ease: [0.16, 1, 0.3, 1] },
  });
}

function Tick({ at, color, label }: { at: number; color: string; label: string }) {
  return e(
    "div",
    { className: "absolute -top-0.5 h-3 w-px", style: { left: `${at}%`, background: color }, title: label },
  );
}

// 单条件 mini 阈值带(单位不同 / 只解析出一条时的退化形态)。
function MiniStrip({ kind, parsed, reduced, delay }: { kind: "escalate" | "retreat"; parsed: ParsedThreshold; reduced: boolean; delay: number }) {
  const isEsc = kind === "escalate";
  const max = parsed.value > 0 ? parsed.value * 1.4 : 1;
  const at = Math.min(100, (parsed.value / max) * 100);
  return e(
    "div",
    { className: "min-w-0 flex-1", "data-testid": `threshold-strip-${kind}` },
    e(
      "div",
      { className: "mb-1 flex items-center justify-between gap-2 text-[9.5px]" },
      e("span", { className: isEsc ? "text-emerald-200/90" : "text-rose-200/90" }, isEsc ? "加码线" : "撤退线"),
      e("span", { className: "tabular-nums text-slate-400" }, fmt(parsed.value, parsed.unit)),
    ),
    e(
      "div",
      { className: TRACK },
      // 加码:线右侧是加码区;撤退:线左侧是撤退区。
      isEsc
        ? e(Zone, { left: at, width: 100 - at, color: EMERALD, reduced, delay })
        : e(Zone, { left: 0, width: at, color: ROSE, reduced, delay }),
      e(Tick, { at, color: isEsc ? "rgba(52,211,153,0.9)" : "rgba(251,113,133,0.9)", label: parsed.raw }),
    ),
  );
}

export function ThresholdBar({ escalateIf, retreatIf }: { escalateIf: string; retreatIf: string }) {
  const reduced = !!useReducedMotion();
  const esc = parseThresholdValue(escalateIf);
  const ret = parseThresholdValue(retreatIf);
  if (!esc && !ret) return null; // 解析不出 → 调用方保留文本卡,不硬编

  // 同单位双条件 → 单条三色区间条。
  if (esc && ret && esc.unit === ret.unit) {
    const higherIsBetter = esc.value >= ret.value;
    const hi = Math.max(esc.value, ret.value);
    const max = hi > 0 ? hi * 1.25 : 1;
    const escAt = Math.min(100, (esc.value / max) * 100);
    const retAt = Math.min(100, (ret.value / max) * 100);
    const lowAt = Math.min(escAt, retAt);
    const highAt = Math.max(escAt, retAt);
    return e(
      "div",
      { className: "mt-2", "data-testid": "threshold-bar" },
      e(
        "div",
        { className: "mb-1 flex items-center justify-between gap-2 text-[9.5px]" },
        e("span", { className: "text-rose-200/90" }, `撤退线 ${fmt(ret.value, ret.unit)}`),
        e("span", { className: "text-emerald-200/90" }, `加码线 ${fmt(esc.value, esc.unit)}`),
      ),
      e(
        "div",
        { className: TRACK },
        // 三色区间:低于撤退 / 观察带 / 高于加码(higherIsBetter);反向指标语义对调。
        e(Zone, { left: 0, width: lowAt, color: higherIsBetter ? ROSE : EMERALD, reduced, delay: 0 }),
        e(Zone, { left: lowAt, width: highAt - lowAt, color: SLATE, reduced, delay: 0.06 }),
        e(Zone, { left: highAt, width: 100 - highAt, color: higherIsBetter ? EMERALD : ROSE, reduced, delay: 0.12 }),
        e(Tick, { at: retAt, color: "rgba(251,113,133,0.9)", label: `撤退:${ret.raw}` }),
        e(Tick, { at: escAt, color: "rgba(52,211,153,0.9)", label: `加码:${esc.raw}` }),
      ),
      e(
        "div",
        { className: "mt-1 text-[9px] text-slate-600" },
        "阈值取自条件文本(显示层解析,口径以文字为准)",
      ),
    );
  }

  // 单位不同 / 只解析出一条 → 每条件一条 mini 阈值带。
  return e(
    "div",
    { className: "mt-2 flex gap-3", "data-testid": "threshold-bar" },
    esc ? e(MiniStrip, { kind: "escalate", parsed: esc, reduced, delay: 0 }) : null,
    ret ? e(MiniStrip, { kind: "retreat", parsed: ret, reduced, delay: 0.08 }) : null,
  );
}

export default ThresholdBar;
