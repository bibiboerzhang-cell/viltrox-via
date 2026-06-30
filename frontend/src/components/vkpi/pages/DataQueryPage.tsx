import React from "react";
import {
  fetchAnalyticsIntents,
  askAnalytics,
  rowsToCsv,
  downloadCsv,
  type AnalyticsIntent,
  type AnalyticsAskResult,
  type AnalyticsCell,
} from "../../../services/vkpi/analyticsQuery-api";

// N6 问数 / 数据导出页:选白名单意图 -> 调 /analytics/ask -> 表格展示 -> 导出 CSV。
// 全只读;前端不拼 SQL,只传 intent / range / source。红线:不触 fit_score。

const RANGE_OPTIONS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 7, label: "近 7 天" },
  { value: 30, label: "近 30 天" },
  { value: 90, label: "近 90 天" },
  { value: 180, label: "近 180 天" },
];

function formatCell(value: AnalyticsCell): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

export function DataQueryPage({ apiToken = "" }: { apiToken?: string }) {
  const [intents, setIntents] = React.useState<AnalyticsIntent[]>([]);
  const [selectedKey, setSelectedKey] = React.useState<string>("");
  const [rangeDays, setRangeDays] = React.useState<number>(30);
  const [source, setSource] = React.useState<string>("");
  const [result, setResult] = React.useState<AnalyticsAskResult | null>(null);
  const [loading, setLoading] = React.useState<boolean>(false);
  const [error, setError] = React.useState<string>("");

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    fetchAnalyticsIntents(apiToken)
      .then((list) => {
        if (!alive) return;
        setIntents(list);
        if (list.length > 0) setSelectedKey((prev) => prev || list[0].intent);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "加载可问清单失败");
      });
    return () => {
      alive = false;
    };
  }, [apiToken]);

  const selectedIntent = React.useMemo<AnalyticsIntent | undefined>(
    () => intents.find((i) => i.intent === selectedKey),
    [intents, selectedKey],
  );

  const run = React.useCallback(() => {
    if (!apiToken) {
      setError("未登录 / 无 token");
      return;
    }
    if (!selectedKey) {
      setError("请先选择一个问题");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    askAnalytics(apiToken, {
      intent: selectedKey,
      range: selectedIntent?.uses_range ? rangeDays : undefined,
      source: selectedIntent?.uses_source ? source.trim() : undefined,
    })
      .then((res) => {
        setResult(res);
        if (res.intent === null) {
          setError(res.message || "未命中白名单意图");
        }
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "查询失败"))
      .finally(() => setLoading(false));
  }, [apiToken, selectedKey, selectedIntent, rangeDays, source]);

  const exportCsv = React.useCallback(() => {
    if (!result || result.intent === null || result.columns.length === 0) return;
    const csv = rowsToCsv(result.columns, result.rows);
    const stamp = new Date().toISOString().slice(0, 10);
    downloadCsv(`vkpi-${result.intent}-${stamp}.csv`, csv);
  }, [result]);

  const hasRows = !!result && result.intent !== null && result.rows.length > 0;
  const columns = result?.columns ?? [];

  return (
    <div className="space-y-4 p-4">
      <div>
        <h2 className="text-base font-semibold text-white">问数 / 数据导出</h2>
        <p className="mt-1 text-[12px] text-slate-400">
          选一个白名单问题 → 查询本地数据(只读)→ 表格展示 → 导出 CSV。
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-slate-400">问题</span>
          <select
            value={selectedKey}
            onChange={(e) => {
              setSelectedKey(e.target.value);
              setResult(null);
              setError("");
            }}
            className="min-w-[220px] rounded-md border border-white/10 bg-white/[0.03] px-2 py-1.5 text-[12px] text-slate-200"
          >
            {intents.length === 0 ? (
              <option value="">（加载中…）</option>
            ) : (
              intents.map((i) => (
                <option key={i.intent} value={i.intent}>
                  {i.title}
                </option>
              ))
            )}
          </select>
        </label>

        {selectedIntent?.uses_range ? (
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-slate-400">区间</span>
            <select
              value={rangeDays}
              onChange={(e) => setRangeDays(Number(e.target.value))}
              className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-1.5 text-[12px] text-slate-200"
            >
              {RANGE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {selectedIntent?.uses_source ? (
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-slate-400">地区短码（可选，如 US / CN）</span>
            <input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="留空=全部"
              className="w-[160px] rounded-md border border-white/10 bg-white/[0.03] px-2 py-1.5 text-[12px] text-slate-200 placeholder:text-slate-600"
            />
          </label>
        ) : null}

        <button
          onClick={run}
          disabled={loading || !apiToken || !selectedKey}
          className="rounded-md border border-emerald-300/30 bg-emerald-500/[0.15] px-3 py-1.5 text-[12px] text-emerald-100 hover:bg-emerald-500/[0.25] disabled:opacity-50"
        >
          {loading ? "查询中…" : "查询"}
        </button>

        <button
          onClick={exportCsv}
          disabled={!hasRows}
          className="rounded-md border border-white/10 px-3 py-1.5 text-[12px] text-slate-200 hover:bg-white/[0.06] disabled:opacity-40"
        >
          导出 CSV
        </button>
      </div>

      {selectedIntent ? (
        <div className="text-[11px] text-slate-500">
          {selectedIntent.description}
          {selectedIntent.examples.length > 0 ? (
            <span className="ml-1 text-slate-600">· 例：{selectedIntent.examples[0]}</span>
          ) : null}
        </div>
      ) : null}

      {error ? <div className="text-[12px] text-red-300/80">{error}</div> : null}

      {result && result.intent !== null && result.sql_explain ? (
        <div className="text-[10px] text-slate-600">{result.sql_explain}</div>
      ) : null}

      {hasRows ? (
        <div className="overflow-auto rounded-xl border border-white/[0.08] bg-white/[0.02]">
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr className="border-b border-white/[0.08] bg-white/[0.03]">
                {columns.map((c) => (
                  <th key={c} className="px-3 py-2 text-left font-medium text-sky-300/80">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result!.rows.map((row, ri) => (
                <tr key={ri} className="border-b border-white/[0.04] last:border-0">
                  {columns.map((c) => (
                    <td key={c} className="px-3 py-1.5 text-slate-300/90">
                      {formatCell(row[c] ?? null)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : result && result.intent !== null ? (
        <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-8 text-center text-[12px] text-slate-500">
          无数据返回
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-8 text-center text-[12px] text-slate-500">
          选问题 → 查询（本地数据，只读）
        </div>
      )}
    </div>
  );
}
