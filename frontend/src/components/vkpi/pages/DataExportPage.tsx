import React from "react";
import { getReportTypes, getReport, downloadReport } from "../../../services/vkpi/agentOps-api";

// 数据导出页:选 report 类型 → 本地数据出结构化 report(接 /metrics/report-types + /metrics/report)。
// PDF/Excel 导出:前端把结构化数据下成 JSON(v1);PDF/Excel 渲染留后续。红线:只读。

export function DataExportPage({ apiToken = "" }: { apiToken?: string }) {
  const [types, setTypes] = React.useState<any[]>([]);
  const [sel, setSel] = React.useState("industry_overview");
  const [report, setReport] = React.useState<any>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    if (!apiToken) return;
    getReportTypes(apiToken).then((r: any) => setTypes(Array.isArray(r?.types) ? r.types : [])).catch(() => {});
  }, [apiToken]);

  const run = React.useCallback(() => {
    if (!apiToken) { setError("未登录 / 无 token"); return; }
    setLoading(true); setError(""); setReport(null);
    getReport(apiToken, sel)
      .then((r: any) => setReport(r))
      .catch((e: any) => setError(e?.message || "生成失败"))
      .finally(() => setLoading(false));
  }, [apiToken, sel]);

  const download = React.useCallback(() => {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `vkpi-report-${sel}.json`; a.click();
    URL.revokeObjectURL(url);
  }, [report, sel]);

  return (
    <div className="space-y-4 p-4">
      <h2 className="text-base font-semibold text-white">数据导出 · 问什么给什么</h2>
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={sel}
          onChange={(e) => setSel(e.target.value)}
          className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-1.5 text-[12px] text-slate-200"
        >
          {types.length
            ? types.map((t: any) => <option key={t.report_type} value={t.report_type}>{t.title}</option>)
            : <option value="industry_overview">行业大盘概览</option>}
        </select>
        <button onClick={run} disabled={loading || !apiToken} className="rounded-md border border-emerald-300/30 bg-emerald-500/[0.15] px-3 py-1.5 text-[12px] text-emerald-100 hover:bg-emerald-500/[0.25] disabled:opacity-50">
          {loading ? "生成中…" : "生成报告"}
        </button>
        {report ? (
          <>
            <button onClick={download} className="rounded-md border border-white/10 px-3 py-1.5 text-[12px] text-slate-200 hover:bg-white/[0.06]">下载 JSON</button>
            <button onClick={() => downloadReport(apiToken, sel, "pdf").catch((e) => setError(e?.message || "PDF 导出失败"))} className="rounded-md border border-rose-300/30 bg-rose-500/[0.12] px-3 py-1.5 text-[12px] text-rose-100 hover:bg-rose-500/[0.2]">下载 PDF</button>
            <button onClick={() => downloadReport(apiToken, sel, "xlsx").catch((e) => setError(e?.message || "Excel 导出失败"))} className="rounded-md border border-emerald-300/30 bg-emerald-500/[0.12] px-3 py-1.5 text-[12px] text-emerald-100 hover:bg-emerald-500/[0.2]">下载 Excel</button>
          </>
        ) : null}
      </div>

      {error ? <div className="text-[12px] text-red-300/80">{error}</div> : null}

      {report ? (
        <div className="space-y-3">
          <div className="text-[12px] text-slate-300">{report.title} · 窗口 {report.window_days} 天</div>
          {(report.sections || []).map((s: any, i: number) => (
            <div key={i} className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
              <div className="mb-1 text-[11px] font-medium text-sky-300/80">{s.name}</div>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-[10px] leading-relaxed text-slate-300/90">
                {JSON.stringify(s.data, null, 2)}
              </pre>
            </div>
          ))}
          <div className="text-[10px] text-slate-500">PDF/Excel 导出:当前先下结构化 JSON;Excel/PDF 渲染留后续(可与你细化模板)。</div>
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-8 text-center text-[12px] text-slate-500">选类型 → 生成报告(本地数据,只读)</div>
      )}
    </div>
  );
}
