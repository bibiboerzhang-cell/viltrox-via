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
import {
  fetchCannedQuestions,
  runCannedQuery,
  type CannedQuestion,
  type CannedRunResult,
} from "../../../services/vkpi/cannedQueries-api";

// N6 问数 / 数据导出页:
//  A4 升级:顶部「常用问题」chips 一排,点即出数(确定性 SQL 聚合,零 LLM),
//  结果带一句话摘要 + 来源表 + 行数(可追溯);下方保留原白名单意图自由问数链路。
// 全只读;前端不拼 SQL,只传 key/intent/range/source。红线:不触 fit_score。

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

  // A4 常用问题(预设问题库):chips 点即出数,零 LLM。
  const [cannedQuestions, setCannedQuestions] = React.useState<CannedQuestion[]>([]);
  const [cannedRange, setCannedRange] = React.useState<number>(30);
  const [cannedResult, setCannedResult] = React.useState<CannedRunResult | null>(null);
  const [cannedRunningKey, setCannedRunningKey] = React.useState<string>("");
  const [cannedError, setCannedError] = React.useState<string>("");

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
    fetchCannedQuestions(apiToken)
      .then((list) => {
        if (alive) setCannedQuestions(list);
      })
      .catch((e: unknown) => {
        if (alive) setCannedError(e instanceof Error ? e.message : "加载常用问题失败");
      });
    return () => {
      alive = false;
    };
  }, [apiToken]);

  const runCanned = React.useCallback(
    (q: CannedQuestion) => {
      if (!apiToken) {
        setCannedError("未登录 / 无 token");
        return;
      }
      setCannedRunningKey(q.key);
      setCannedError("");
      runCannedQuery(apiToken, q.key, q.uses_range ? cannedRange : undefined)
        .then((res) => {
          if (res.status === "error") {
            setCannedResult(null);
            setCannedError(res.reason || "查询失败");
            return;
          }
          setCannedResult(res);
        })
        .catch((e: unknown) => {
          setCannedResult(null);
          setCannedError(e instanceof Error ? e.message : "查询失败");
        })
        .finally(() => setCannedRunningKey(""));
    },
    [apiToken, cannedRange],
  );

  const exportCannedCsv = React.useCallback(() => {
    if (!cannedResult || cannedResult.rows.length === 0) return;
    const csv = rowsToCsv(cannedResult.columns, cannedResult.rows);
    const stamp = new Date().toISOString().slice(0, 10);
    downloadCsv(`vkpi-canned-${cannedResult.key}-${stamp}.csv`, csv);
  }, [cannedResult]);

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
          常用问题点即出数(带来源可追溯);或选白名单问题自由问数 → 表格展示 → 导出 CSV。全只读。
        </p>
      </div>

      {/* A4 常用问题:12 个预设问题 chips,点即出数(确定性 SQL,零 LLM) */}
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-[12px] font-medium text-slate-300">常用问题</span>
          <label className="flex items-center gap-1.5">
            <span className="text-[11px] text-slate-500">区间</span>
            <select
              value={cannedRange}
              onChange={(e) => setCannedRange(Number(e.target.value))}
              className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-[11px] text-slate-200"
            >
              {RANGE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <span className="text-[10px] text-slate-600">区间仅对带 ⏱ 的问题生效</span>
        </div>

        <div className="mt-2 flex flex-wrap gap-2">
          {cannedQuestions.length === 0 ? (
            <span className="text-[11px] text-slate-600">
              {cannedError ? "常用问题加载失败" : "（加载常用问题…）"}
            </span>
          ) : (
            cannedQuestions.map((q) => (
              <button
                key={q.key}
                onClick={() => runCanned(q)}
                disabled={!!cannedRunningKey || !apiToken}
                title={q.description}
                className={
                  cannedResult?.key === q.key
                    ? "rounded-full border border-emerald-300/40 bg-emerald-500/[0.2] px-3 py-1 text-[12px] text-emerald-100 disabled:opacity-60"
                    : "rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-[12px] text-slate-300 hover:bg-white/[0.08] disabled:opacity-50"
                }
              >
                {cannedRunningKey === q.key ? "查询中…" : `${q.title}${q.uses_range ? " ⏱" : ""}`}
              </button>
            ))
          )}
        </div>

        {cannedError ? <div className="mt-2 text-[12px] text-red-300/80">{cannedError}</div> : null}

        {cannedResult ? (
          <div className="mt-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[12px] font-medium text-emerald-200/90">{cannedResult.title}</span>
              <button
                onClick={exportCannedCsv}
                disabled={cannedResult.rows.length === 0}
                className="rounded-md border border-white/10 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-white/[0.06] disabled:opacity-40"
              >
                导出 CSV
              </button>
            </div>
            <div className="text-[12px] text-slate-200">{cannedResult.summary}</div>
            <div className="text-[10px] text-slate-500">
              来源表:{cannedResult.source_tables.join(", ")} · {cannedResult.row_count} 行
              {cannedResult.range_days !== null ? ` · 近${cannedResult.range_days}天` : ""}
              {cannedResult.generated_at ? ` · 生成于 ${cannedResult.generated_at}` : ""}
            </div>
            {cannedResult.rows.length > 0 ? (
              <div className="overflow-auto rounded-lg border border-white/[0.08] bg-white/[0.02]">
                <table className="w-full border-collapse text-[12px]">
                  <thead>
                    <tr className="border-b border-white/[0.08] bg-white/[0.03]">
                      {cannedResult.columns.map((c) => (
                        <th key={c} className="px-3 py-2 text-left font-medium text-emerald-300/80">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {cannedResult.rows.map((row, ri) => (
                      <tr key={ri} className="border-b border-white/[0.04] last:border-0">
                        {cannedResult.columns.map((c) => (
                          <td key={c} className="px-3 py-1.5 text-slate-300/90">
                            {formatCell(row[c] ?? null)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-4 text-center text-[12px] text-slate-500">
                无数据返回(表为空是真实状态,来源见上)
              </div>
            )}
          </div>
        ) : null}
      </div>

      <div className="text-[12px] font-medium text-slate-300">自由问数(白名单意图)</div>

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
