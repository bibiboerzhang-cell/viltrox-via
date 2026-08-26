/**
 * 「自动放宽 / 自动加筛选」告知条(2026-08-26)。
 *
 * 用户裁令:哪怕自动放宽成功了,也要让操作员看见发生过什么,并且随时能改回去。
 * 所以这块只要后端回了台账就一定显示,**不做「成功就静默」的优化**。
 *
 * 2026-08-26 复核纠偏:**加筛选与松筛选同等可见**。上一版只摆松绑那一半,系统替操作员
 * 加的硬筛一个字都不显示 —— 他被加上了自己从没说过的条件却毫不知情。现在两块并排:
 * 「系统松了什么」与「系统加了什么」都列出来,加的那几条每一条都带一颗「去掉这条」。
 * 全部文案来自 `SmartKolInputPanel.AutoRelax.ts`,这里只负责摆放。
 */
import { Info, RotateCcw, X } from "lucide-react";

import type { AutoRelaxView } from "./SmartKolInputPanel.AutoRelax";

const TONE_STYLES: Readonly<Record<AutoRelaxView["tone"], string>> = Object.freeze({
  relaxed: "border-cyan-300/25 bg-cyan-400/[0.07] text-cyan-100",
  short: "border-amber-300/25 bg-amber-400/[0.07] text-amber-100",
  plain: "border-white/[0.10] bg-white/[0.03] text-slate-300",
});

const BUTTON_CLASS =
  "inline-flex shrink-0 items-center gap-1 rounded-md border border-white/[0.14] bg-black/25 px-2 py-0.5 text-[9.5px] text-slate-200 transition-colors hover:border-white/[0.3] hover:text-white disabled:opacity-40";

/** 明细行 key 形如 `added:countries`;去掉那一条时后端要的是原键名。 */
function filterKeyOf(lineKey: string): string {
  return lineKey.replace(/^added:/, "");
}

export function AutoRelaxNotice({
  view,
  onRestore,
  onRemoveAdded,
  removedKeys = [],
  busy = false,
}: {
  view: AutoRelaxView | null;
  onRestore?: () => void;
  /** 去掉系统加的某一条。不给就不显示那颗按钮 —— 但加项本身照样如实显示,绝不隐藏。 */
  onRemoveAdded?: (key: string) => void;
  /** 已经点掉、正在等这次搜索回来的那几条:按钮置灰,避免重复排队。 */
  removedKeys?: string[];
  busy?: boolean;
}) {
  if (!view) return null;
  return (
    <div
      data-testid="kol-search-auto-relax-notice"
      className={`mt-2 rounded-md border px-2.5 py-2 text-[10px] leading-relaxed ${TONE_STYLES[view.tone]}`}
    >
      <div className="flex items-start gap-1.5">
        <Info size={11} className="mt-[1px] shrink-0 opacity-80" />
        <span className="min-w-0 flex-1 font-medium">{view.headline}</span>
        {view.restoreLabel && onRestore ? (
          <button type="button" disabled={busy} onClick={onRestore} className={BUTTON_CLASS}>
            <RotateCcw size={9} /> {view.restoreLabel}
          </button>
        ) : null}
      </div>
      {view.lines.length ? (
        <ul className="mt-1.5 space-y-0.5 pl-[18px]">
          {view.lines.map((line) => (
            <li key={line.key} className="list-disc opacity-90">{line.text}</li>
          ))}
        </ul>
      ) : null}
      {view.addedHeadline ? (
        <div
          data-testid="kol-search-auto-added"
          className="mt-2 rounded-md border border-white/[0.12] bg-black/20 px-2 py-1.5"
        >
          <div className="font-medium">{view.addedHeadline}</div>
          <ul className="mt-1 space-y-1">
            {view.addedLines.map((line) => (
              <li key={line.key} className="flex items-start gap-1.5">
                <span className="min-w-0 flex-1">
                  <span className="opacity-95">{line.text}</span>
                  <span className="block opacity-70">为什么加：{line.reason}</span>
                </span>
                {line.removable && onRemoveAdded ? (
                  <button
                    type="button"
                    disabled={busy || removedKeys.includes(filterKeyOf(line.key))}
                    onClick={() => onRemoveAdded(filterKeyOf(line.key))}
                    className={BUTTON_CLASS}
                  >
                    <X size={9} /> {view.removeLabel}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {view.droppedNote ? (
        <div className="mt-1.5 pl-[18px] text-[9px] opacity-80">{view.droppedNote}</div>
      ) : null}
      {view.scopeNote ? (
        <div className="mt-1.5 pl-[18px] text-[9px] opacity-70">{view.scopeNote}</div>
      ) : null}
      {view.protectedNote ? (
        <div className="mt-1.5 pl-[18px] text-[9px] opacity-70">{view.protectedNote}</div>
      ) : null}
      <div className="mt-0.5 pl-[18px] text-[9px] opacity-60">{view.sourceNote}</div>
    </div>
  );
}
