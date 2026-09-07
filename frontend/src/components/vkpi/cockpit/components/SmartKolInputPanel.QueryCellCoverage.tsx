import { asRecord, type Row } from "./SmartKolInputPanel.helpers";

const EXECUTION_LABELS: Record<string, string> = {
  covered: "已执行", partial: "部分执行", not_executed: "未执行",
  not_executed_duplicate_query: "重复查询未执行", blocked_or_unverified: "执行待核",
  legacy_unverified: "旧回执待核",
};
const RESULT_LABELS: Record<string, string> = { covered: "已覆盖", partial: "覆盖不足" };

export function queryCellCount(row: Row, key: string): string {
  const value = row[key];
  if (asRecord(row.count_statuses)[key] === "unknown") return "待核";
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? String(value) : "待核";
}

export function queryCellCoverageRows(coverage: unknown) {
  const raw = asRecord(coverage).cells;
  const cells = Array.isArray(raw) ? raw.filter((value) => value && typeof value === "object" && !Array.isArray(value)) : [];
  return cells.slice(0, 8).map((value) => {
    const row = asRecord(value);
    return {
      id: typeof row.query_cell_id === "string" && row.query_cell_id.trim() ? row.query_cell_id.trim() : "格名待核",
      execution: EXECUTION_LABELS[String(row.coverage_status)] || "执行待核",
      result: row.qualification_status === "not_evaluated" ? "尚未验收" : RESULT_LABELS[String(row.result_coverage_status)] || "结果待核",
      qualified: queryCellCount(row, "qualified_count"),
      pending: queryCellCount(row, "pending_count"),
      selected: queryCellCount(row, "selected_count"),
      duplicates: queryCellCount(row, "duplicate_count"),
      retrieval: `返回 ${queryCellCount(row, "retrieved_count")} · 唯一 ${queryCellCount(row, "unique_count")}`,
      // Current server contract has no cell-scoped settled cost evidence.
      // Ignore aggregate/accidental numbers rather than allocating a bill.
      cost: "未归因",
    };
  });
}

export function OnlineQueryCellCoverage({ coverage, blockedReason = "" }: { coverage: unknown; blockedReason?: string }) {
  const rows = queryCellCoverageRows(coverage);
  if (!rows.length && !blockedReason) return null;
  return (
    <section className="my-2 space-y-2 text-[10px]" data-testid="online-query-cell-evidence">
      {blockedReason ? (
        <div role="status" data-testid="online-source-blocked" className="rounded border border-amber-300/25 bg-amber-400/[0.06] px-2.5 py-2 text-amber-100">
          <div className="font-medium">联网检索待接入处理</div>
          <div>{blockedReason}；这不是零结果，也不会自动重试。</div>
        </div>
      ) : null}
      {rows.length ? (
        <div className="overflow-x-auto rounded border border-white/10 p-2" data-testid="online-query-cell-coverage">
          <div className="mb-1 font-medium text-slate-300">逐格执行与结果（最多 8 格）</div>
          <table className="w-full min-w-[640px] text-left text-[9px] text-slate-400">
            <thead><tr>{["格名", "实际执行", "结果覆盖", "合格", "待补证", "入选", "重复", "费用"].map((title) => <th key={title} className="px-1 py-1 font-medium">{title}</th>)}</tr></thead>
            <tbody>{rows.map((row, index) => (
              <tr key={`${row.id}-${index}`} data-testid="online-query-cell-row" className="border-t border-white/5">
                <th scope="row" className="max-w-[180px] break-words px-1 py-1 font-normal">{row.id}</th>
                <td className="px-1 py-1" title={row.retrieval}>{row.execution}</td><td className="px-1 py-1">{row.result}</td>
                {[row.qualified, row.pending, row.selected, row.duplicates, row.cost].map((value, column) => <td key={column} className="px-1 py-1 tabular-nums">{value}</td>)}
              </tr>
            ))}</tbody>
          </table>
          <div className="mt-1 text-[9px] text-slate-500">执行不等于合格；跨格可能重叠，不可相加当总人数。费用缺少逐格结算证据，不作均摊。</div>
        </div>
      ) : null}
    </section>
  );
}
