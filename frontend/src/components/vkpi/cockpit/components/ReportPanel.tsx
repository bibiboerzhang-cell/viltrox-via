import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  CheckCircle2,
  Download,
  FileDown,
  FileText,
  Loader2,
  RefreshCw,
  RotateCcw,
  Server,
  X,
} from "lucide-react";
import {
  VKPI_REPORT_SECTION_KEYS,
  archiveVkpiReport,
  createVkpiReportExport,
  downloadVkpiFile,
  generateVkpiReport,
  listVkpiReports,
  reportApiErrorMessage,
  reportModelPolicyLabel,
  restoreVkpiReport,
  vkpiReportDownloadPath,
  type VkpiGeneratedReport,
  type VkpiReportExportFormat,
  type VkpiReportGenerateConfig,
  type VkpiReportHistoryItem,
  type VkpiReportLanguage,
  type VkpiReportLayout,
  type VkpiReportPeriod,
  type VkpiReportScope,
  type VkpiReportSectionKey,
} from "../../../../services/vkpi/reports-api";
import { loadStoredState, saveStoredState } from "../lib/storage";

interface ReportPanelProps {
  onClose: () => void;
  data?: Record<string, unknown>;
  apiToken?: string;
}

interface Notice {
  tone: "success" | "error" | "info";
  text: string;
}

const SECTIONS: Array<{ key: VkpiReportSectionKey; label: string }> = [
  { key: "kpiOverview", label: "KPI 总览" },
  { key: "attribution", label: "归因闭环" },
  { key: "projects", label: "项目明细" },
  { key: "ledger", label: "KPI Ledger" },
  { key: "risks", label: "风险提醒" },
  { key: "summary", label: "管理摘要" },
];

const UNKNOWN_STATUSES = new Set(["", "unknown", "awaiting_source", "empty", "unavailable"]);

function todayInputValue(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function storedSections(value: unknown): Record<VkpiReportSectionKey, boolean> {
  const row = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return Object.fromEntries(
    VKPI_REPORT_SECTION_KEYS.map((key) => [key, row[key] === undefined ? true : Boolean(row[key])]),
  ) as Record<VkpiReportSectionKey, boolean>;
}

function statusLabel(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized === "ready") return "已生成";
  if (normalized === "archived") return "已归档";
  if (normalized === "failed") return "失败";
  if (normalized === "rendering") return "生成中";
  return status || "未知";
}

function dataStatusLabel(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized === "real") return "真实数据";
  if (normalized === "partial") return "部分数据";
  if (normalized === "seeded") return "种子数据";
  return "数据待补";
}

function reportDateLabel(report: VkpiReportHistoryItem): string {
  const start = report.periodStart ? report.periodStart.slice(0, 10) : "?";
  const end = report.periodEnd ? report.periodEnd.slice(0, 10) : "?";
  return `${start} 至 ${end}`;
}

export function ReportPanel({ onClose, data, apiToken }: ReportPanelProps) {
  const stored = useMemo(loadStoredState, []);
  const [period, setPeriod] = useState<VkpiReportPeriod>(stored.reportPeriod === "weekly" ? "weekly" : "monthly");
  const [language, setLanguage] = useState<VkpiReportLanguage>(stored.reportLanguage === "en" ? "en" : "zh");
  const [format, setFormat] = useState<VkpiReportLayout>(stored.reportFormat === "markdown" ? "markdown" : "visual");
  const [scope, setScope] = useState<VkpiReportScope>("all");
  const [reportDate, setReportDate] = useState(todayInputValue);
  const [sections, setSections] = useState<Record<VkpiReportSectionKey, boolean>>(
    () => storedSections(stored.reportSections),
  );
  const [generated, setGenerated] = useState<VkpiGeneratedReport | null>(null);
  const [history, setHistory] = useState<VkpiReportHistoryItem[]>([]);
  const [archived, setArchived] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [exporting, setExporting] = useState<VkpiReportExportFormat | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [archiveArmedId, setArchiveArmedId] = useState<number | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [selectedHistory, setSelectedHistory] = useState<VkpiReportHistoryItem | null>(null);

  const selectedSectionKeys = useMemo(
    () => SECTIONS.filter((section) => sections[section.key]).map((section) => section.key),
    [sections],
  );
  const config = useMemo<VkpiReportGenerateConfig>(() => ({
    period,
    date: reportDate,
    language,
    sections: selectedSectionKeys,
    format,
    scope,
  }), [format, language, period, reportDate, scope, selectedSectionKeys]);

  useEffect(() => { saveStoredState({ reportPeriod: period }); }, [period]);
  useEffect(() => { saveStoredState({ reportLanguage: language }); }, [language]);
  useEffect(() => { saveStoredState({ reportFormat: format }); }, [format]);
  useEffect(() => { saveStoredState({ reportSections: sections }); }, [sections]);

  const refreshHistory = useCallback(async (includeArchived = archived) => {
    if (!apiToken) {
      setHistory([]);
      setNotice({ tone: "error", text: "缺少登录凭证，无法读取真实报告历史。" });
      return;
    }
    setHistoryLoading(true);
    try {
      const result = await listVkpiReports(apiToken, includeArchived);
      setHistory(result.reports);
      setNotice(null);
    } catch (error) {
      setHistory([]);
      setNotice({ tone: "error", text: reportApiErrorMessage(error, "报告历史加载失败") });
    } finally {
      setHistoryLoading(false);
    }
  }, [apiToken, archived]);

  useEffect(() => { void refreshHistory(archived); }, [archived, refreshHistory]);

  const toggleSection = (key: VkpiReportSectionKey) => {
    setGenerated(null);
    setSections((current) => ({ ...current, [key]: !current[key] }));
  };

  const runGenerate = async () => {
    if (!apiToken) {
      setNotice({ tone: "error", text: "缺少登录凭证，无法生成报告。" });
      return;
    }
    if (!selectedSectionKeys.length) {
      setNotice({ tone: "error", text: "至少选择一个报告章节。" });
      return;
    }
    setGenerating(true);
    setSelectedHistory(null);
    setNotice({ tone: "info", text: "正在由服务端读取数据、生成文件并登记历史…" });
    try {
      const result = await generateVkpiReport(apiToken, config);
      setGenerated(result);
      setArchived(false);
      await refreshHistory(false);
      setNotice({
        tone: result.status.toLowerCase() === "ready" ? "success" : "error",
        text: result.status.toLowerCase() === "ready"
          ? `服务端报告已生成，数据库历史与文件均已登记。${reportModelPolicyLabel(result.modelPolicy, result.claimLevel)}`
          : `服务端返回“${statusLabel(result.status)}”，没有确认可用文件。`,
      });
    } catch (error) {
      setGenerated(null);
      setNotice({ tone: "error", text: reportApiErrorMessage(error, "报告生成失败") });
    } finally {
      setGenerating(false);
    }
  };

  const runExport = async (exportFormat: VkpiReportExportFormat) => {
    if (!apiToken || exporting) return;
    if (!selectedSectionKeys.length) {
      setNotice({ tone: "error", text: "至少选择一个报告章节。" });
      return;
    }
    setExporting(exportFormat);
    setNotice({ tone: "info", text: `正在由服务端生成 ${exportFormat.toUpperCase()}…` });
    try {
      const result = await createVkpiReportExport(apiToken, exportFormat, config);
      if (!result.downloadUrl) throw new Error("接口没有返回下载链接。");
      await downloadVkpiFile(apiToken, result.downloadUrl, `vkpi-${period}-${reportDate}.${exportFormat}`);
      setNotice({ tone: "success", text: `${exportFormat.toUpperCase()} 已由服务端生成并下载。` });
    } catch (error) {
      setNotice({ tone: "error", text: reportApiErrorMessage(error, `${exportFormat.toUpperCase()} 导出失败`) });
    } finally {
      setExporting(null);
    }
  };

  const downloadReport = async (report: VkpiReportHistoryItem, downloadUrl = "") => {
    if (!apiToken || report.id < 1 || report.status.toLowerCase() !== "ready") return;
    setBusyId(report.id);
    try {
      await downloadVkpiFile(
        apiToken,
        downloadUrl || vkpiReportDownloadPath(report.id, "pdf"),
        `${report.reportUid}.pdf`,
      );
      setNotice({ tone: "success", text: "报告文件已下载。" });
    } catch (error) {
      setNotice({ tone: "error", text: reportApiErrorMessage(error, "报告下载失败") });
    } finally {
      setBusyId(null);
    }
  };

  const archiveReport = async (report: VkpiReportHistoryItem) => {
    if (!apiToken) return;
    if (archiveArmedId !== report.id) {
      setArchiveArmedId(report.id);
      return;
    }
    setBusyId(report.id);
    setArchiveArmedId(null);
    try {
      await archiveVkpiReport(apiToken, report.id);
      if (selectedHistory?.id === report.id) setSelectedHistory(null);
      await refreshHistory(false);
      setNotice({ tone: "success", text: "报告已软归档，可随时恢复。" });
    } catch (error) {
      setNotice({ tone: "error", text: reportApiErrorMessage(error, "报告归档失败") });
    } finally {
      setBusyId(null);
    }
  };

  const restoreReport = async (report: VkpiReportHistoryItem) => {
    if (!apiToken) return;
    setBusyId(report.id);
    try {
      await restoreVkpiReport(apiToken, report.id);
      await refreshHistory(true);
      setNotice({ tone: "success", text: "报告已恢复到当前历史。" });
    } catch (error) {
      setNotice({ tone: "error", text: reportApiErrorMessage(error, "报告恢复失败") });
    } finally {
      setBusyId(null);
    }
  };

  const currentHistory = selectedHistory;
  const sourceStatus = String((data?.dashboard as Record<string, unknown> | undefined)?.sourceHealth ? "已连接" : "由服务端复核");

  return (
    <div className="fixed inset-0 z-[1200] flex items-center justify-center bg-black/65 p-3 backdrop-blur-md" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="flex h-[min(880px,94vh)] w-[min(1240px,96vw)] min-w-0 flex-col overflow-hidden rounded-xl border border-line bg-[color:var(--ds-surface-1)] text-ink shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="report-panel-title">
        <header className="flex min-h-16 shrink-0 items-center justify-between gap-4 border-b border-line px-5 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Server size={17} className="text-accent" aria-hidden="true" />
              <h2 id="report-panel-title" className="text-base font-semibold">Report Center</h2>
              <span className="rounded border border-emerald-400/20 bg-emerald-400/10 px-2 py-0.5 text-[10px] text-emerald-300">服务端真实生成</span>
            </div>
            <p className="mt-1 text-[11px] text-muted">报告记录、权限、来源状态与下载文件均由后端登记；当前数据源：{sourceStatus}</p>
          </div>
          <button type="button" onClick={onClose} className="vkpi-icon-button" aria-label="关闭 Report Center"><X size={17} /></button>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[330px_minmax(0,1fr)]">
          <aside className="min-h-0 overflow-y-auto border-b border-line p-4 lg:border-b-0 lg:border-r">
            <div className="space-y-4">
              <div>
                <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">报告周期</span>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {(["weekly", "monthly"] as VkpiReportPeriod[]).map((value) => <button key={value} type="button" onClick={() => setPeriod(value)} className={`rounded-md border px-3 py-2 text-[11px] ${period === value ? "border-accent bg-accent-soft text-accent" : "border-line bg-card text-ink-2"}`}>{value === "weekly" ? "周报" : "月报"}</button>)}
                </div>
              </div>
              <label className="block text-[10px] font-semibold uppercase tracking-wider text-muted">报告截止日期
                <input aria-label="报告截止日期" type="date" value={reportDate} onChange={(event) => setReportDate(event.target.value)} className="mt-2 w-full rounded-md border border-line bg-card px-3 py-2 text-[11px] text-ink" />
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button type="button" onClick={() => setLanguage("zh")} className={`rounded-md border px-3 py-2 text-[11px] ${language === "zh" ? "border-accent bg-accent-soft text-accent" : "border-line bg-card text-ink-2"}`}>中文</button>
                <button type="button" onClick={() => setLanguage("en")} className={`rounded-md border px-3 py-2 text-[11px] ${language === "en" ? "border-accent bg-accent-soft text-accent" : "border-line bg-card text-ink-2"}`}>English</button>
                <button type="button" onClick={() => setFormat("visual")} className={`rounded-md border px-3 py-2 text-[11px] ${format === "visual" ? "border-accent bg-accent-soft text-accent" : "border-line bg-card text-ink-2"}`}>图表版</button>
                <button type="button" onClick={() => setFormat("markdown")} className={`rounded-md border px-3 py-2 text-[11px] ${format === "markdown" ? "border-accent bg-accent-soft text-accent" : "border-line bg-card text-ink-2"}`}>Markdown</button>
                <button type="button" onClick={() => setScope("all")} className={`rounded-md border px-3 py-2 text-[11px] ${scope === "all" ? "border-accent bg-accent-soft text-accent" : "border-line bg-card text-ink-2"}`}>公司范围</button>
                <button type="button" onClick={() => setScope("self")} className={`rounded-md border px-3 py-2 text-[11px] ${scope === "self" ? "border-accent bg-accent-soft text-accent" : "border-line bg-card text-ink-2"}`}>仅本人</button>
              </div>
              <div>
                <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">报告章节</span>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {SECTIONS.map((section) => <button key={section.key} type="button" aria-pressed={sections[section.key]} onClick={() => toggleSection(section.key)} className={`flex items-center gap-2 rounded-md border px-2.5 py-2 text-left text-[10px] ${sections[section.key] ? "border-emerald-400/25 bg-emerald-400/10 text-emerald-300" : "border-line bg-card text-muted"}`}><CheckCircle2 size={12} />{section.label}</button>)}
                </div>
              </div>
              <button type="button" onClick={() => void runGenerate()} disabled={generating} className="flex w-full items-center justify-center gap-2 rounded-md bg-accent px-4 py-2.5 text-[12px] font-semibold text-[var(--ds-on-accent)] disabled:opacity-50">{generating ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />}{generating ? "服务端生成中…" : "生成服务端报告"}</button>
              <div className="grid grid-cols-3 gap-2">
                {(["pdf", "csv", "xlsx"] as VkpiReportExportFormat[]).map((value) => <button key={value} type="button" onClick={() => void runExport(value)} disabled={Boolean(exporting)} className="flex items-center justify-center gap-1 rounded-md border border-line bg-card px-2 py-2 text-[10px] text-ink-2 disabled:opacity-50">{exporting === value ? <Loader2 size={11} className="animate-spin" /> : <FileDown size={11} />}{value.toUpperCase()}</button>)}
              </div>
              {notice ? <p role={notice.tone === "error" ? "alert" : "status"} className={`rounded-md border px-3 py-2 text-[10px] leading-relaxed ${notice.tone === "error" ? "border-red-400/20 bg-red-400/10 text-red-300" : notice.tone === "success" ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300" : "border-blue-400/20 bg-blue-400/10 text-blue-300"}`}>{notice.text}</p> : null}
            </div>
          </aside>

          <main className="flex min-h-0 min-w-0 flex-col">
            <section className="min-h-0 flex-[1.05] overflow-y-auto border-b border-line p-5">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div><h3 className="text-sm font-semibold">服务端结果</h3><p className="mt-1 text-[10px] text-muted">不会用页面当前卡片拼接成报告；以下仅显示服务端返回内容。</p></div>
                {generated?.downloadUrl && generated.reportId ? <button type="button" onClick={() => void downloadReport({ id: generated.reportId!, reportUid: generated.reportUid, reportType: generated.reportType, periodStart: generated.periodStart, periodEnd: generated.periodEnd, scopeType: scope, scopeId: null, triggeredAt: "", status: generated.status, summary: generated.summary, dataStatus: generated.dataStatus, schemaVersion: "", archivedAt: "", archiveReason: "", modelPolicy: generated.modelPolicy, claimLevel: generated.claimLevel }, generated.downloadUrl)} className="flex items-center gap-1 rounded-md border border-line bg-card px-3 py-2 text-[10px]"><Download size={12} />下载生成文件</button> : null}
              </div>
              {generated ? <div className="space-y-4">
                <div className="flex flex-wrap gap-2 text-[10px]"><span className="rounded border border-line bg-card px-2 py-1">{generated.reportUid}</span><span className="rounded border border-emerald-400/20 bg-emerald-400/10 px-2 py-1 text-emerald-300">{statusLabel(generated.status)}</span><span className="rounded border border-line bg-card px-2 py-1">{dataStatusLabel(generated.dataStatus)}</span><span className="rounded border border-line bg-card px-2 py-1">{reportModelPolicyLabel(generated.modelPolicy, generated.claimLevel)}</span><span className="rounded border border-line bg-card px-2 py-1">{generated.periodStart.slice(0, 10)} 至 {generated.periodEnd.slice(0, 10)}</span></div>
                <div className="rounded-lg border border-line bg-card p-4"><div className="text-[10px] uppercase tracking-wider text-muted">管理摘要</div><p className="mt-2 whitespace-pre-wrap text-[12px] leading-relaxed text-ink-2">{generated.summary || "服务端没有返回摘要。"}</p></div>
                <div className="grid grid-cols-2 gap-2 md:grid-cols-3">{generated.metrics.map((metric) => { const unknown = UNKNOWN_STATUSES.has(metric.dataStatus.toLowerCase()) || metric.rawValue === null; return <article key={metric.key} className="rounded-lg border border-line bg-card p-3"><div className="text-[9px] uppercase tracking-wider text-muted">{metric.label || metric.key}</div><strong className="mt-2 block text-lg font-semibold">{unknown ? "待数据" : String(metric.value ?? metric.rawValue)}</strong><span className="mt-1 block text-[9px] text-muted">{dataStatusLabel(metric.dataStatus)}{metric.note ? ` · ${metric.note}` : ""}</span></article>; })}</div>
              </div> : currentHistory ? <div className="rounded-lg border border-line bg-card p-4"><div className="flex flex-wrap gap-2 text-[10px]"><span>{currentHistory.reportUid}</span><span>{reportDateLabel(currentHistory)}</span><span>{statusLabel(currentHistory.status)}</span><span>{dataStatusLabel(currentHistory.dataStatus)}</span><span>{reportModelPolicyLabel(currentHistory.modelPolicy, currentHistory.claimLevel)}</span></div><p className="mt-4 whitespace-pre-wrap text-[12px] leading-relaxed text-ink-2">{currentHistory.truthInvalidated ? "该报告已因真实业务口径升级撤销，仅保留审计记录。" : currentHistory.summary || "此历史记录没有服务端摘要。"}</p></div> : <div className="flex min-h-52 flex-col items-center justify-center rounded-lg border border-dashed border-line text-center"><Server size={24} className="text-muted" /><p className="mt-3 text-[12px] text-ink-2">尚未选择或生成报告</p><p className="mt-1 text-[10px] text-muted">生成后将同时登记数据库历史与下载文件。</p></div>}
            </section>

            <section className="min-h-0 flex-1 overflow-y-auto p-5">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2"><h3 className="text-sm font-semibold">报告历史</h3><span className="rounded border border-line px-2 py-0.5 text-[9px] text-muted">{history.length} 条</span></div>
                <div className="flex items-center gap-2"><button type="button" onClick={() => setArchived(false)} className={`rounded-md px-3 py-1.5 text-[10px] ${!archived ? "bg-accent-soft text-accent" : "text-muted"}`}>当前</button><button type="button" onClick={() => setArchived(true)} className={`rounded-md px-3 py-1.5 text-[10px] ${archived ? "bg-accent-soft text-accent" : "text-muted"}`}>已归档</button><button type="button" onClick={() => void refreshHistory()} aria-label="刷新报告历史" className="vkpi-icon-button">{historyLoading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}</button></div>
              </div>
              <div className="space-y-2" aria-busy={historyLoading}>{!historyLoading && !history.length ? <div className="rounded-md border border-dashed border-line px-4 py-5 text-center text-[10px] text-muted">{notice?.tone === "error" ? "历史读取失败，请处理上方错误后重试。" : archived ? "没有已归档报告。" : "尚未生成真实报告。"}</div> : history.map((report) => <article key={report.id} className={`grid gap-3 rounded-lg border p-3 md:grid-cols-[minmax(0,1fr)_auto] ${selectedHistory?.id === report.id ? "border-accent bg-accent-soft" : "border-line bg-card"}`}>
                <button type="button" onClick={() => { setGenerated(null); setSelectedHistory(report); }} className="min-w-0 text-left"><div className="flex flex-wrap items-center gap-2"><strong className="truncate text-[11px]">{report.reportUid}</strong><span className="rounded border border-line px-1.5 py-0.5 text-[9px] text-muted">{statusLabel(report.status)}</span><span className="rounded border border-line px-1.5 py-0.5 text-[9px] text-muted">{dataStatusLabel(report.dataStatus)}</span><span className="rounded border border-line px-1.5 py-0.5 text-[9px] text-muted">{reportModelPolicyLabel(report.modelPolicy, report.claimLevel)}</span></div><p className="mt-1 truncate text-[10px] text-muted">{reportDateLabel(report)} · {report.scopeType || "all"}</p></button>
                <div className="flex items-center gap-1.5">{!archived ? <><button type="button" onClick={() => { setGenerated(null); setSelectedHistory(report); }} className="rounded border border-line px-2 py-1 text-[9px]">重开</button>{report.status.toLowerCase() === "ready" ? <button type="button" onClick={() => void downloadReport(report)} disabled={busyId === report.id} className="rounded border border-line px-2 py-1 text-[9px]">下载</button> : null}<button type="button" onClick={() => void archiveReport(report)} disabled={busyId === report.id} className={`rounded border px-2 py-1 text-[9px] ${archiveArmedId === report.id ? "border-red-400/40 bg-red-400/10 text-red-300" : "border-line"}`}><Archive size={10} className="mr-1 inline" />{archiveArmedId === report.id ? "确认归档" : "归档"}</button></> : report.truthInvalidated ? <span className="rounded border border-amber-400/30 bg-amber-400/10 px-2 py-1 text-[9px] text-amber-300">已撤销</span> : <button type="button" onClick={() => void restoreReport(report)} disabled={busyId === report.id} className="rounded border border-line px-2 py-1 text-[9px]"><RotateCcw size={10} className="mr-1 inline" />恢复</button>}</div>
              </article>)}</div>
            </section>
          </main>
        </div>
      </section>
    </div>
  );
}
