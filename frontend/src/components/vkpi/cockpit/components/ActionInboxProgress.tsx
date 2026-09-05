import React from "react";
import { CATEGORY_META, EXEC_REASON } from "./actionInboxLabels";
import { asRecord, executionStage, itemStage, receiptSummary } from "./actionInboxStatus";

type Translate = (key: string) => string;
const toneClass = { info: "text-sky-300/90", warning: "text-amber-300/90", error: "text-red-300/90" };

export function ActionInboxProgress({ item, t }: { item: unknown; t: Translate }) {
  const stage = itemStage(item);
  return <div className="mt-1 rounded border border-line bg-card/40 px-1.5 py-1 text-[9px]">
    <div className={`font-medium ${toneClass[stage.tone]}`}>{t(stage.label)}</div>
    <div className="mt-0.5 text-muted">{t("下一步")}: {t(stage.next)}</div>
  </div>;
}

export function ActionInboxReceipt({ value, t }: { value: unknown; t: Translate }) {
  const receipt = asRecord(value);
  const changes = Array.isArray(receipt.before_after) ? receipt.before_after.map(asRecord).filter(
    (row) => typeof row.table === "string" && [row.before, row.after, row.delta].every(
      (number) => typeof number === "number" && Number.isFinite(number),
    ),
  ) : [];
  return <div className="mt-1 rounded border border-line bg-card/40 px-1.5 py-1 text-[9px] text-ink-2">
    <div>{t("执行回执 · 不等于业务验收")}</div>
    <div className="mt-0.5 text-muted">{receiptSummary(value, t)}</div>
    {changes.length ? <div className="mt-0.5 text-muted">{changes.map((row) => `${row.table} ${row.before}→${row.after} (${Number(row.delta) >= 0 ? "+" : ""}${row.delta})`).join(" · ")}</div> : null}
  </div>;
}

export function ActionInboxLedger({ items, loading, error, t }: {
  items: unknown[]; loading: boolean; error: string; t: Translate;
}) {
  return <div className="mt-2 max-h-44 space-y-1 overflow-y-auto rounded border border-line bg-black/20 p-1.5">
    {loading ? <div className="py-2 text-center text-[9px] text-muted">{t("加载执行台账…")}</div>
      : error ? <div role="alert" className="py-2 text-[9px] text-amber-300">{t("执行台账读取失败，无法判断是否有执行记录。")}</div>
        : items.length === 0 ? <div className="py-2 text-center text-[9px] text-muted">{t("本次未返回执行记录，不代表业务已完成。")}</div>
          : items.map((value, index) => {
            const row = asRecord(value);
            const detail = asRecord(row.detail_json);
            const stage = executionStage(row.outcome, detail, row.mode ?? "");
            const category = String(row.category || "");
            const meta = CATEGORY_META[category as keyof typeof CATEGORY_META];
            const reason = String(row.error || detail.reason || asRecord(detail.result_checklist).failed_reason || "");
            return <div key={String(row.id ?? index)} className="rounded bg-panel px-1.5 py-1 text-[9px]">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-ink-2">{t(meta?.label || category || "执行记录")} · {t("台账")}#{String(row.id ?? "—")}</span>
                <span className={`shrink-0 ${toneClass[stage.tone]}`}>{t(stage.label)}</span>
              </div>
              <div className="mt-0.5 text-muted">{t(stage.next)}</div>
              <div className="mt-0.5 text-muted">{receiptSummary(detail.result_checklist, t)}</div>
              {reason ? <div className="mt-0.5 text-amber-300">{t(EXEC_REASON[reason] || reason)}</div> : null}
            </div>;
          })}
  </div>;
}
