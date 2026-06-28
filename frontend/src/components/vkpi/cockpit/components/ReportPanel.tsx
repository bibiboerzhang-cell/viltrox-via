// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Check, Copy, Download, FileText, Loader2, Printer, Wand2, X } from "lucide-react";
import { markdownToHtml } from "../lib/markdown";
import { generateRuntimeReportMarkdown, generateRuntimeVisualHtml } from "../lib/reportRuntime";
import { listUpcomingEvents } from "../../../../services/vkpi/events-api";
import { loadStoredState, saveStoredState } from "../lib/storage";
import { fetchCockpitReportAnalysis } from "../api";

const e = React.createElement;

export function ReportPanel({ onClose, data, apiToken }: any) {
  // 加载上次选项
  const stored = loadStoredState();
  const [period, setPeriod] = useState(stored.reportPeriod || "monthly");
  const [language, setLanguage] = useState(stored.reportLanguage || "zh"); // V6.10.1: zh / en
  const [sections, setSections] = useState(stored.reportSections || {
    kpiOverview:    true,
    kolVsCompany:   true, // V6.10.3: 公司账号 vs KOL 详细对比
    platformDeepDive: true, // V6.10.3: 各平台 Deep Dive
    goodNews:       true,
    badNews:        true,
    brandSentiment: true,
    contentRecs:    true, // V6.10.3: 内容改进建议
    eventsProgress: true,
    attribution:    true,
    aiInsights:     true,
  });
  const [format, setFormat] = useState(stored.reportFormat || "visual"); // V6.10.2: visual | markdown
  
  useEffect(() => { saveStoredState({ reportPeriod: period }); }, [period]);
  useEffect(() => { saveStoredState({ reportLanguage: language }); }, [language]);
  useEffect(() => { saveStoredState({ reportSections: sections }); }, [sections]);
  useEffect(() => { saveStoredState({ reportFormat: format }); }, [format]);

  // A3:报告「活动进度」接真数据 —— 拉 /events/upcoming 注入报告 data(de-fake 待接入)。
  const [upcomingEvents, setUpcomingEvents] = useState<any[]>([]);
  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    void listUpcomingEvents(apiToken, 6)
      .then((res: any) => { if (alive) setUpcomingEvents(Array.isArray(res?.items) ? res.items : []); })
      .catch(() => { if (alive) setUpcomingEvents([]); });
    return () => { alive = false; };
  }, [apiToken]);
  const fullData = useMemo(() => ({ ...(data || {}), upcomingEvents }), [data, upcomingEvents]);

  // V6.10.1: 生成报告内容 — 双语 + 全量化
  const reportContent = useMemo(
    () => generateRuntimeReportMarkdown({ period, language, sections, data: fullData }),
    [period, language, sections, fullData]
  );
  const visualReportHtml = useMemo(
    () => generateRuntimeVisualHtml({ period, language, sections, data: fullData }),
    [period, language, sections, fullData]
  );

  // 2026-06-15:报告深度分析 —— 把拼好的全量真实数据 POST 给后端,LLM 整理成经营分析,插回报告顶部。
  const [analysis, setAnalysis] = useState<any>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeErr, setAnalyzeErr] = useState("");
  // 报告内容变了(改周期/语言/section)→ 旧分析作废,需重新分析。
  useEffect(() => { setAnalysis(null); setAnalyzeErr(""); }, [reportContent]);

  const A_LABELS = language === "zh"
    ? { h: "🧭 经营深度分析", hi: "关键亮点", rk: "风险与问题", rc: "行动建议", mk: "市场 / 竞品 / 社区洞察" }
    : { h: "🧭 Executive Analysis", hi: "Highlights", rk: "Risks & Issues", rc: "Recommendations", mk: "Market / Competitor / Community" };

  const analysisMd = useMemo(() => {
    if (!analysis) return "";
    const out: any[] = [`## ${A_LABELS.h}`, ""];
    if (analysis.executive_summary) out.push(`> ${analysis.executive_summary}`, "");
    const blk = (title: string, arr: any) => {
      if (Array.isArray(arr) && arr.length) {
        out.push(`**${title}**`, "");
        arr.forEach((x: any) => out.push(`- ${x}`));
        out.push("");
      }
    };
    blk(A_LABELS.hi, analysis.highlights);
    blk(A_LABELS.rk, analysis.risks);
    blk(A_LABELS.rc, analysis.recommendations);
    blk(A_LABELS.mk, analysis.market_insights);
    out.push("---", "");
    return out.join("\n");
  }, [analysis, language]);

  const analysisHtml = useMemo(() => {
    if (!analysis) return "";
    const esc = (s: any) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const grid = (arr: any) => `<div class="ai-grid">${(Array.isArray(arr) ? arr : []).map((x: any) => `<div class="ai-item">${esc(x)}</div>`).join("")}</div>`;
    const card = (title: string, arr: any) => (Array.isArray(arr) && arr.length) ? `<div class="card"><div class="card-title">${esc(title)}</div>${grid(arr)}</div>` : "";
    return `<section><h2>${esc(A_LABELS.h)}</h2>${analysis.executive_summary ? `<div class="ai-callout"><div class="ai-body">${esc(analysis.executive_summary)}</div></div>` : ""}${card(A_LABELS.hi, analysis.highlights)}${card(A_LABELS.rk, analysis.risks)}${card(A_LABELS.rc, analysis.recommendations)}${card(A_LABELS.mk, analysis.market_insights)}</section>`;
  }, [analysis, language]);

  // 有分析则插到报告顶部(执行摘要在前,详细数据在后 —— 给老板看的报告标准结构);复制 / 下载 / PDF 全包含。
  const finalReportContent = analysisMd ? `${analysisMd}\n${reportContent}` : reportContent;
  const finalVisualHtml = analysisHtml ? `${analysisHtml}${visualReportHtml}` : visualReportHtml;

  const handleAnalyze = async () => {
    if (analyzing || !apiToken) return;
    setAnalyzing(true);
    setAnalyzeErr("");
    try {
      const res: any = await fetchCockpitReportAnalysis(apiToken, reportContent, period, language);
      if (res && res.available && res.analysis) {
        setAnalysis(res.analysis);
      } else {
        const reason = res && res.reason;
        setAnalyzeErr(
          reason === "budget_blocked"
            ? (language === "zh" ? "今日深度分析额度已用满,明天再试。" : "Daily analysis budget reached. Try again tomorrow.")
            : (language === "zh" ? "暂时无法生成深度分析,请稍后重试。" : "Analysis unavailable, please retry later.")
        );
      }
    } catch {
      setAnalyzeErr(language === "zh" ? "深度分析请求失败,请重试。" : "Analysis request failed, please retry.");
    } finally {
      setAnalyzing(false);
    }
  };

  const toggleSection = (key: any) => {
    setSections((s: any) => ({ ...s, [key]: !s[key] }));
  };
  
  // Copy to clipboard
  const handleCopy = async () => {
    const msg = language === "zh" ? "已复制到剪贴板 ✓" : "Report copied to clipboard ✓";
    try {
      await navigator.clipboard.writeText(finalReportContent);
      alert(msg);
    } catch (err) {
      // Fallback
      const ta = document.createElement("textarea");
      ta.value = finalReportContent;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      alert(msg);
    }
  };
  
  // Download as .md
  const handleDownloadMd = () => {
    const blob = new Blob([finalReportContent], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `viltrox-${period}-report-${language}-${new Date().toISOString().split("T")[0]}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };
  
  // V6.10.2: Print as PDF — 根据 format 选 visual 或 markdown
  const handlePrintPdf = () => {
    const visualCss = `
      * { box-sizing: border-box; }
      body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", sans-serif; 
             max-width: 800px; margin: 32px auto; padding: 0 24px; color: #1a202c; line-height: 1.5; background: #fff; }
      .header { border-bottom: 3px solid #a855f7; padding-bottom: 18px; margin-bottom: 24px; }
      .brand { font-size: 11px; font-weight: 700; letter-spacing: 4px; color: #a855f7; margin-bottom: 6px; }
      h1 { font-size: 26px; font-weight: 700; color: #0f172a; margin: 0 0 10px; }
      .meta { display: flex; gap: 24px; font-size: 12px; color: #64748b; }
      section { margin-bottom: 28px; page-break-inside: avoid; }
      h2 { font-size: 18px; font-weight: 600; color: #1e293b; border-left: 4px solid #a855f7; padding-left: 10px; margin: 22px 0 14px; }
      .kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 18px; }
      .kpi-card { border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; background: #fff; }
      .kpi-card.pending { background: #fffbeb; border-color: #fde68a; }
      .kpi-label { font-size: 9px; text-transform: uppercase; letter-spacing: 1px; color: #94a3b8; margin-bottom: 4px; }
      .kpi-value { font-size: 22px; font-weight: 300; color: #0f172a; line-height: 1.1; }
      .kpi-delta { font-size: 10px; margin-top: 4px; font-weight: 500; }
      .kpi-delta.up { color: #10b981; }
      .kpi-delta.pending { color: #d97706; }
      .kpi-sub { font-size: 9px; color: #94a3b8; margin-top: 2px; }
      .card { border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; margin-bottom: 12px; background: #fff; }
      .card-title { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: #475569; margin-bottom: 10px; }
      .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
      .news-list { display: flex; flex-direction: column; gap: 6px; }
      .news-item { display: flex; gap: 10px; padding: 8px 12px; border-radius: 6px; font-size: 12px; }
      .news-item.good { background: #f0fdf4; border-left: 3px solid #10b981; }
      .news-item.bad  { background: #fef2f2; border-left: 3px solid #ef4444; }
      .news-tag { flex-shrink: 0; font-size: 9px; font-weight: 700; padding: 2px 6px; border-radius: 3px; height: fit-content; }
      .news-tag.good { background: #10b981; color: #fff; }
      .news-tag.bad  { background: #ef4444; color: #fff; }
      .news-text { flex: 1; color: #1e293b; line-height: 1.5; }
      .keyword-cloud { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; padding: 4px 0; }
      .keyword-cloud .kw { color: #059669; font-weight: 500; }
      .keyword-cloud .kw.bad { color: #dc2626; }
      .keyword-cloud .kw sub { font-size: 9px; color: #94a3b8; margin-left: 2px; }
      table { width: 100%; border-collapse: collapse; font-size: 11px; }
      th, td { padding: 6px 10px; border-bottom: 1px solid #e2e8f0; text-align: left; }
      th { background: #f8fafc; font-weight: 600; color: #475569; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
      td.pos { color: #10b981; font-weight: 600; }
      td.neg { color: #ef4444; font-weight: 600; }
      .event-list { display: flex; flex-direction: column; gap: 8px; }
      .event-card { border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; background: #fff; }
      .event-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
      .event-status { font-size: 14px; }
      .event-name { font-weight: 600; font-size: 13px; color: #0f172a; flex: 1; }
      .event-date { font-size: 11px; color: #64748b; font-variant-numeric: tabular-nums; }
      .event-meta { font-size: 10px; color: #94a3b8; margin-bottom: 8px; }
      .event-progress-row { display: flex; align-items: center; gap: 10px; }
      .event-progress-bar { flex: 1; height: 6px; background: #f1f5f9; border-radius: 3px; overflow: hidden; }
      .event-progress-fill { height: 100%; border-radius: 3px; }
      .event-progress-text { font-size: 11px; font-weight: 600; color: #1e293b; width: 36px; text-align: right; font-variant-numeric: tabular-nums; }
      .event-budget { font-size: 10px; color: #64748b; }
      .callout { background: #f8fafc; border-left: 4px solid #a855f7; padding: 10px 14px; font-size: 12px; color: #334155; margin-top: 10px; border-radius: 4px; }
      .pipeline-title { font-size: 13px; font-weight: 600; color: #1e293b; margin-bottom: 10px; }
      .pipeline { position: relative; padding-left: 4px; }
      .pipeline::before { content: ""; position: absolute; left: 5px; top: 6px; bottom: 6px; width: 1px; background: #e2e8f0; }
      .pipeline-row { display: flex; gap: 12px; margin-bottom: 12px; position: relative; }
      .pipeline-dot { width: 10px; height: 10px; border-radius: 50%; border: 2px solid; background: #fff; flex-shrink: 0; margin-top: 4px; z-index: 1; }
      .pipeline-content { flex: 1; }
      .pipeline-head { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }
      .pipeline-step { font-weight: 600; font-size: 12px; color: #0f172a; }
      .pipeline-badge { font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px; padding: 1px 5px; border-radius: 3px; font-weight: 600; }
      .pipeline-date { font-size: 10px; color: #64748b; margin-left: auto; font-variant-numeric: tabular-nums; }
      .pipeline-desc { font-size: 11px; color: #475569; }
      .pipeline-owner { font-size: 10px; color: #94a3b8; margin-top: 2px; }
      .ai-callout { background: linear-gradient(135deg, #faf5ff, #f0f9ff); border: 1px solid #c4b5fd; border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; }
      .ai-confidence { font-size: 11px; font-weight: 600; color: #7c3aed; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
      .ai-body { font-size: 12px; color: #1e293b; line-height: 1.6; }
      .ai-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
      .ai-item { border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; background: #fff; font-size: 11px; color: #334155; line-height: 1.5; }
      .ai-item-h { font-weight: 600; font-size: 11px; margin-bottom: 4px; color: #0f172a; }
      .footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 10px; color: #94a3b8; text-align: center; font-style: italic; }
      @media print {
        body { margin: 0; padding: 16px 24px; max-width: 100%; }
        section { page-break-inside: avoid; }
      }
      /* V6.10.3 new classes */
      .platform-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
      .platform-card { border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; background: #fff; }
      .platform-head { display: flex; align-items: center; gap: 8px; padding-bottom: 6px; margin-bottom: 8px; border-bottom: 2px solid; }
      .platform-emoji { font-size: 16px; }
      .platform-name { font-weight: 700; font-size: 13px; flex: 1; }
      .platform-er { font-size: 10px; color: #64748b; font-variant-numeric: tabular-nums; }
      .platform-stats { display: flex; gap: 12px; font-size: 11px; color: #475569; margin-bottom: 8px; }
      .ps-label { color: #94a3b8; }
      .platform-types { margin: 8px 0; }
      .ps-sublabel { font-size: 9px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; margin-bottom: 4px; }
      .platform-type-row { display: flex; align-items: center; gap: 8px; margin-bottom: 3px; font-size: 10px; }
      .pt-label { width: 60px; color: #475569; }
      .pt-bar { flex: 1; height: 5px; background: #f1f5f9; border-radius: 3px; overflow: hidden; }
      .pt-fill { height: 100%; border-radius: 3px; }
      .pt-value { width: 36px; text-align: right; font-weight: 600; color: #1e293b; }
      .platform-rec { margin-top: 8px; padding: 6px 8px; background: #f8fafc; border-radius: 4px; font-size: 10px; color: #475569; }
      .platform-rec.urgent { background: #fef2f2; color: #991b1b; font-weight: 600; }
      .perf-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: 11px; }
      .perf-row.good { color: #064e3b; } .perf-row.bad { color: #7f1d1d; }
      .perf-name { flex: 1; font-weight: 500; }
      .perf-er { font-weight: 600; font-variant-numeric: tabular-nums; }
      .perf-er.good { color: #10b981; } .perf-er.bad { color: #ef4444; }
      .perf-vs { font-size: 10px; padding: 1px 5px; border-radius: 3px; font-weight: 600; }
      .perf-vs.good { background: #d1fae5; color: #047857; } .perf-vs.bad { background: #fee2e2; color: #b91c1c; }
      .perf-note { font-size: 10px; color: #64748b; margin-left: 0; margin-bottom: 4px; padding-left: 8px; border-left: 2px solid #e2e8f0; }
      .action-list { display: flex; flex-direction: column; gap: 6px; }
      .action-row { display: flex; gap: 10px; align-items: flex-start; font-size: 11px; color: #1e293b; line-height: 1.5; }
      .action-num { width: 22px; height: 22px; background: #a855f7; color: #fff; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; flex-shrink: 0; }
      .action-text { flex: 1; }
    `;
    
    const markdownCss = `
      body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif; max-width: 760px; margin: 40px auto; padding: 24px; color: #1a202c; line-height: 1.6; }
      h1 { color: #1a202c; border-bottom: 2px solid #a855f7; padding-bottom: 8px; }
      h2 { color: #2d3748; margin-top: 32px; }
      table { width: 100%; border-collapse: collapse; margin: 16px 0; }
      th, td { padding: 8px 12px; border: 1px solid #e2e8f0; text-align: left; }
      th { background: #f7fafc; font-weight: 600; }
      blockquote { border-left: 4px solid #a855f7; margin: 16px 0; padding: 8px 16px; background: #faf5ff; color: #4a5568; }
      hr { border: 0; border-top: 1px solid #e2e8f0; margin: 32px 0; }
      ul { padding-left: 24px; } li { margin: 4px 0; }
    `;
    
    const isVisual = format === "visual";
    const bodyHtml = isVisual ? finalVisualHtml : markdownToHtml(finalReportContent);
    const css = isVisual ? visualCss : markdownCss;
    
    const html = `<!DOCTYPE html><html lang="${language === "zh" ? "zh-CN" : "en"}"><head><meta charset="utf-8"><title>Viltrox Marketing Report</title><style>${css}</style></head><body>${bodyHtml}</body></html>`;
    
    const w = window.open("", "_blank");
    if (!w) {
      alert(language === "zh" ? "请允许弹窗以打开打印预览。" : "Please allow pop-ups for V-KPI to open the print preview.");
      return;
    }
    w.document.write(html);
    w.document.close();
    setTimeout(() => { w.print(); }, 600);
  };
  
  const SECTION_LABELS = language === "zh" ? {
    kpiOverview:    { label: "📊 KPI 总览",          desc: "真实 KPI + 明确待接入项" },
    kolVsCompany:   { label: "⚖️ 公司 vs KOL 对比",  desc: "只比较当前 API 有信号的字段" },
    platformDeepDive: { label: "🎯 平台 Deep Dive",  desc: "KOL Pool 平台字段聚合" },
    goodNews:       { label: "✅ 好消息",            desc: "真实 Top movers / 项目信号" },
    badNews:        { label: "⚠️ 问题与风险",        desc: "待接入阻塞 + 市场信号" },
    brandSentiment: { label: "📣 风评分析",          desc: "评论情绪 endpoint 待接入" },
    contentRecs:    { label: "💡 内容改进建议",      desc: "copilot-brief / tasks 候选" },
    eventsProgress: { label: "🎪 活动进度",          desc: "events/upcoming endpoint 待接入" },
    attribution:    { label: "🔗 归因管线",          desc: "Shopify / 成本数据待接入" },
    aiInsights:     { label: "决策洞察",       desc: "实时聚合" },
  } : {
    kpiOverview:    { label: "📊 KPI Overview",          desc: "Real metrics + explicit pending gaps" },
    kolVsCompany:   { label: "⚖️ Company vs KOL",         desc: "Only fields backed by current APIs" },
    platformDeepDive: { label: "🎯 Platform Deep Dive",   desc: "KOL Pool platform aggregation" },
    goodNews:       { label: "✅ Good News",              desc: "Real top movers / project signals" },
    badNews:        { label: "⚠️ Issues & Concerns",     desc: "Pending blockers + market signals" },
    brandSentiment: { label: "📣 Brand Sentiment",        desc: "Sentiment endpoint pending" },
    contentRecs:    { label: "💡 Content Recommendations", desc: "copilot-brief / tasks candidates" },
    eventsProgress: { label: "🎪 Events Progress",        desc: "events/upcoming endpoint pending" },
    attribution:    { label: "🔗 Attribution Pipeline",   desc: "Shopify / cost data pending" },
    aiInsights:     { label: "Intelligence",        desc: "Real-time aggregation" },
  };
  
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev) => ev.stopPropagation(),
      className: "relative w-full max-w-4xl max-h-[88vh] flex flex-col rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      // Header
      e("div", { className: "shrink-0 border-b border-white/[0.06] px-5 py-3.5 flex items-center justify-between" },
        e("div", { className: "flex items-center gap-3" },
          e("div", { 
            className: "flex h-9 w-9 items-center justify-center rounded-lg",
            style: { background: "#a855f722", color: "#a855f7" }
          }, e(FileText, { size: 18 })),
          e("div", null,
            e("h2", { className: "text-base font-semibold text-white" }, language === "zh" ? "生成报告" : "Generate Report"),
            e("p", { className: "text-[11px] text-slate-400" }, language === "zh" ? "真实 API / 待接入缺口 · 不使用 mock 数字" : "Real API / pending gaps · no mock numbers")
          )
        ),
        e("button", {
          onClick: onClose,
          className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white hover:bg-white/10"
        }, e(X, { size: 14 }))
      ),
      
      // Body: left options + right preview
      e("div", { className: "flex flex-1 min-h-0" },
        // ─ Left: Options ─
        e("div", { className: "w-72 shrink-0 border-r border-white/[0.06] overflow-y-auto p-4 space-y-4" },
          // V6.10.1: Language toggle
          e("div", null,
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "Language · 语言"),
            e("div", { className: "grid grid-cols-2 gap-1.5" },
              [
                { id: "zh", label: "中文", sub: "给老板看" },
                { id: "en", label: "English", sub: "For partners / abroad" },
              ].map(l => e("button", {
                key: l.id,
                onClick: () => setLanguage(l.id),
                className: `rounded-lg border p-2 text-left transition ${language === l.id ? "border-purple-500/40 bg-purple-500/[0.08]" : "border-white/[0.06] bg-white/[0.02] hover:border-white/[0.12]"}`
              },
                e("div", { className: "text-[11px] font-medium text-white" }, l.label),
                e("div", { className: "text-[9px] text-slate-500" }, l.sub)
              ))
            )
          ),
          
          // Period
          e("div", null,
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, language === "zh" ? "周期" : "Period"),
            e("div", { className: "grid grid-cols-2 gap-1.5" },
              [
                { id: "weekly",  label: language === "zh" ? "周报" : "Weekly",  sub: language === "zh" ? "过去 7 天" : "Last 7 days" },
                { id: "monthly", label: language === "zh" ? "月报" : "Monthly", sub: language === "zh" ? "过去 30 天" : "Last 30 days" },
              ].map(p => e("button", {
                key: p.id,
                onClick: () => setPeriod(p.id),
                className: `rounded-lg border p-2 text-left transition ${period === p.id ? "border-purple-500/40 bg-purple-500/[0.08]" : "border-white/[0.06] bg-white/[0.02] hover:border-white/[0.12]"}`
              },
                e("div", { className: "text-[11px] font-medium text-white" }, p.label),
                e("div", { className: "text-[9px] text-slate-500" }, p.sub)
              ))
            )
          ),
          
          // V6.10.2: Format toggle (Visual vs Markdown)
          e("div", null,
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, language === "zh" ? "格式" : "Format"),
            e("div", { className: "grid grid-cols-2 gap-1.5" },
              [
                { id: "visual",   label: language === "zh" ? "图表版" : "Visual",   sub: language === "zh" ? "图表卡片 · 给老板" : "Charts · Stakeholder" },
                { id: "markdown", label: language === "zh" ? "文本版" : "Markdown", sub: language === "zh" ? "纯文本 · 给邮件" : "Plain · Email/Slack" },
              ].map(f => e("button", {
                key: f.id,
                onClick: () => setFormat(f.id),
                className: `rounded-lg border p-2 text-left transition ${format === f.id ? "border-purple-500/40 bg-purple-500/[0.08]" : "border-white/[0.06] bg-white/[0.02] hover:border-white/[0.12]"}`
              },
                e("div", { className: "text-[11px] font-medium text-white" }, f.label),
                e("div", { className: "text-[9px] text-slate-500" }, f.sub)
              ))
            )
          ),
          
          // Sections
          e("div", null,
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, language === "zh" ? "包含内容" : "Include Sections"),
            e("div", { className: "space-y-1.5" },
              Object.entries(SECTION_LABELS).map(([key, info]) => e("button", {
                key,
                onClick: () => toggleSection(key),
                className: `w-full rounded-lg border p-2 text-left transition ${sections[key] ? "border-purple-500/30 bg-purple-500/[0.05]" : "border-white/[0.06] bg-white/[0.01] opacity-50"}`
              },
                e("div", { className: "flex items-start gap-2" },
                  e("div", { 
                    className: `mt-0.5 h-3.5 w-3.5 shrink-0 rounded border-2 flex items-center justify-center ${sections[key] ? "border-purple-400 bg-purple-500" : "border-white/20"}`
                  }, sections[key] && e(Check, { size: 8, className: "text-white" })),
                  e("div", { className: "min-w-0 flex-1" },
                    e("div", { className: "text-[11px] font-medium text-white" }, info.label),
                    e("div", { className: "text-[9px] text-slate-500 leading-tight" }, info.desc)
                  )
                )
              ))
            )
          ),
          
          // 深度分析(据全量真实数据整理经营洞察,插到报告顶部)
          e("div", null,
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, language === "zh" ? "深度分析" : "Deep Analysis"),
            e("button", {
              onClick: handleAnalyze,
              disabled: analyzing || !apiToken,
              title: !apiToken ? (language === "zh" ? "缺少凭证,无法分析" : "Missing token") : undefined,
              className: `w-full rounded-lg border p-2 text-left transition ${analysis ? "border-emerald-500/30 bg-emerald-500/[0.06]" : "border-purple-500/40 bg-purple-500/[0.08]"} ${(analyzing || !apiToken) ? "opacity-60 cursor-not-allowed" : "hover:bg-purple-500/[0.14]"}`
            },
              e("div", { className: "flex items-center gap-2" },
                analyzing
                  ? e(Loader2, { size: 13, className: "text-purple-300 animate-spin shrink-0" })
                  : e(Wand2, { size: 13, className: (analysis ? "text-emerald-300" : "text-purple-300") + " shrink-0" }),
                e("div", { className: "min-w-0" },
                  e("div", { className: "text-[11px] font-medium text-white" },
                    analyzing ? (language === "zh" ? "分析中…" : "Analyzing…")
                    : analysis ? (language === "zh" ? "已生成 · 重新分析" : "Generated · Re-analyze")
                    : (language === "zh" ? "生成深度分析" : "Generate Analysis")),
                  e("div", { className: "text-[9px] text-slate-400 leading-tight" }, language === "zh" ? "据全量数据整理经营洞察,插入报告顶部" : "Synthesize insights from all data into the report top")
                )
              )
            ),
            analyzeErr && e("div", { className: "mt-1.5 text-[9px] text-amber-400 leading-snug" }, analyzeErr),
            analysis && !analyzeErr && e("div", { className: "mt-1.5 text-[9px] text-emerald-400 leading-snug" }, language === "zh" ? "✓ 已插入报告顶部 · 复制 / 下载 / PDF 都会包含" : "✓ Added to report top · included in copy / download / PDF")
          ),

          // Format
          e("div", null,
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, language === "zh" ? "导出方式" : "Output"),
            e("div", { className: "space-y-1.5" },
              e("button", {
                onClick: handleCopy,
                className: "w-full rounded-lg border border-white/[0.08] bg-white/[0.02] p-2 text-left hover:border-white/[0.18] hover:bg-white/[0.04]"
              },
                e("div", { className: "flex items-center gap-2" },
                  e(Copy, { size: 12, className: "text-slate-400" }),
                  e("div", null,
                    e("div", { className: "text-[11px] font-medium text-white" }, language === "zh" ? "复制到剪贴板" : "Copy to Clipboard"),
                    e("div", { className: "text-[9px] text-slate-500" }, language === "zh" ? "粘到 Slack / 邮件正文" : "Paste into Slack / Email")
                  )
                )
              ),
              e("button", {
                onClick: handleDownloadMd,
                className: "w-full rounded-lg border border-white/[0.08] bg-white/[0.02] p-2 text-left hover:border-white/[0.18] hover:bg-white/[0.04]"
              },
                e("div", { className: "flex items-center gap-2" },
                  e(Download, { size: 12, className: "text-slate-400" }),
                  e("div", null,
                    e("div", { className: "text-[11px] font-medium text-white" }, language === "zh" ? "下载 Markdown" : "Download Markdown"),
                    e("div", { className: "text-[9px] text-slate-500" }, language === "zh" ? ".md 文件归档" : ".md file for archive")
                  )
                )
              ),
              e("button", {
                onClick: handlePrintPdf,
                className: "w-full rounded-lg border border-purple-500/30 bg-purple-500/[0.08] p-2 text-left hover:border-purple-500/50 hover:bg-purple-500/[0.12]"
              },
                e("div", { className: "flex items-center gap-2" },
                  e(Printer, { size: 12, className: "text-purple-300" }),
                  e("div", null,
                    e("div", { className: "text-[11px] font-medium text-white" }, language === "zh" ? "打印 → PDF" : "Print → PDF"),
                    e("div", { className: "text-[9px] text-purple-200/70" }, language === "zh" ? "浏览器打印对话框 · 另存为 PDF" : "Browser print dialog · save as PDF")
                  )
                )
              )
            )
          )
        ),
        
        // ─ Right: Preview ─
        e("div", { className: "flex-1 min-w-0 overflow-y-auto bg-[#080d18]" },
          format === "visual"
            ? e("div", { className: "p-4" },
                e("div", { className: "mb-2 flex items-center justify-between" },
                  e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, language === "zh" ? "预览 · 图表版" : "Preview · Visual"),
                  e("div", { className: "text-[10px] text-slate-500" }, language === "zh" ? "打印 PDF 时为完整高分辨率" : "Full resolution on PDF print")
                ),
                // 用 iframe 渲染 visual HTML(避免 css 互相干扰)
                e("iframe", {
                  srcDoc: `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
                    * { box-sizing: border-box; }
                    body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; margin: 0; padding: 16px; color: #1a202c; line-height: 1.5; background: #fff; font-size: 12px; }
                    .header { border-bottom: 3px solid #a855f7; padding-bottom: 14px; margin-bottom: 18px; }
                    .brand { font-size: 9px; font-weight: 700; letter-spacing: 4px; color: #a855f7; margin-bottom: 4px; }
                    h1 { font-size: 20px; font-weight: 700; color: #0f172a; margin: 0 0 6px; }
                    .meta { display: flex; gap: 16px; font-size: 10px; color: #64748b; flex-wrap: wrap; }
                    section { margin-bottom: 20px; }
                    h2 { font-size: 14px; font-weight: 600; color: #1e293b; border-left: 4px solid #a855f7; padding-left: 8px; margin: 16px 0 10px; }
                    .kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 14px; }
                    .kpi-card { border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; background: #fff; }
                    .kpi-card.pending { background: #fffbeb; border-color: #fde68a; }
                    .kpi-label { font-size: 8px; text-transform: uppercase; letter-spacing: 1px; color: #94a3b8; margin-bottom: 3px; }
                    .kpi-value { font-size: 18px; font-weight: 300; color: #0f172a; line-height: 1.1; }
                    .kpi-delta { font-size: 9px; margin-top: 3px; font-weight: 500; }
                    .kpi-delta.up { color: #10b981; }
                    .kpi-delta.pending { color: #d97706; }
                    .kpi-sub { font-size: 8px; color: #94a3b8; margin-top: 2px; }
                    .card { border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; margin-bottom: 10px; background: #fff; }
                    .card-title { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: #475569; margin-bottom: 8px; }
                    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
                    .news-list { display: flex; flex-direction: column; gap: 4px; }
                    .news-item { display: flex; gap: 8px; padding: 6px 10px; border-radius: 4px; font-size: 11px; }
                    .news-item.good { background: #f0fdf4; border-left: 3px solid #10b981; }
                    .news-item.bad  { background: #fef2f2; border-left: 3px solid #ef4444; }
                    .news-tag { flex-shrink: 0; font-size: 8px; font-weight: 700; padding: 1px 5px; border-radius: 3px; height: fit-content; }
                    .news-tag.good { background: #10b981; color: #fff; }
                    .news-tag.bad  { background: #ef4444; color: #fff; }
                    .news-text { flex: 1; color: #1e293b; line-height: 1.5; }
                    .keyword-cloud { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline; padding: 4px 0; }
                    .keyword-cloud .kw { color: #059669; font-weight: 500; }
                    .keyword-cloud .kw.bad { color: #dc2626; }
                    .keyword-cloud .kw sub { font-size: 8px; color: #94a3b8; margin-left: 1px; }
                    table { width: 100%; border-collapse: collapse; font-size: 10px; }
                    th, td { padding: 5px 8px; border-bottom: 1px solid #e2e8f0; text-align: left; }
                    th { background: #f8fafc; font-weight: 600; color: #475569; font-size: 9px; text-transform: uppercase; }
                    td.pos { color: #10b981; font-weight: 600; }
                    td.neg { color: #ef4444; font-weight: 600; }
                    .event-list { display: flex; flex-direction: column; gap: 6px; }
                    .event-card { border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 10px; background: #fff; }
                    .event-head { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
                    .event-status { font-size: 12px; }
                    .event-name { font-weight: 600; font-size: 11px; color: #0f172a; flex: 1; }
                    .event-date { font-size: 10px; color: #64748b; }
                    .event-meta { font-size: 9px; color: #94a3b8; margin-bottom: 6px; }
                    .event-progress-row { display: flex; align-items: center; gap: 8px; }
                    .event-progress-bar { flex: 1; height: 5px; background: #f1f5f9; border-radius: 3px; overflow: hidden; }
                    .event-progress-fill { height: 100%; border-radius: 3px; }
                    .event-progress-text { font-size: 10px; font-weight: 600; color: #1e293b; width: 30px; text-align: right; }
                    .event-budget { font-size: 9px; color: #64748b; }
                    .callout { background: #f8fafc; border-left: 4px solid #a855f7; padding: 8px 12px; font-size: 11px; color: #334155; margin-top: 8px; border-radius: 4px; }
                    .pipeline-title { font-size: 12px; font-weight: 600; color: #1e293b; margin-bottom: 8px; }
                    .pipeline { position: relative; padding-left: 4px; }
                    .pipeline::before { content: ""; position: absolute; left: 5px; top: 6px; bottom: 6px; width: 1px; background: #e2e8f0; }
                    .pipeline-row { display: flex; gap: 10px; margin-bottom: 10px; }
                    .pipeline-dot { width: 10px; height: 10px; border-radius: 50%; border: 2px solid; background: #fff; flex-shrink: 0; margin-top: 3px; z-index: 1; }
                    .pipeline-content { flex: 1; }
                    .pipeline-head { display: flex; align-items: center; gap: 6px; margin-bottom: 2px; }
                    .pipeline-step { font-weight: 600; font-size: 11px; color: #0f172a; }
                    .pipeline-badge { font-size: 8px; text-transform: uppercase; padding: 1px 4px; border-radius: 3px; font-weight: 600; }
                    .pipeline-date { font-size: 9px; color: #64748b; margin-left: auto; }
                    .pipeline-desc { font-size: 10px; color: #475569; }
                    .pipeline-owner { font-size: 9px; color: #94a3b8; margin-top: 2px; }
                    .ai-callout { background: linear-gradient(135deg, #faf5ff, #f0f9ff); border: 1px solid #c4b5fd; border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; }
                    .ai-confidence { font-size: 10px; font-weight: 600; color: #7c3aed; margin-bottom: 4px; text-transform: uppercase; }
                    .ai-body { font-size: 11px; color: #1e293b; line-height: 1.5; }
                    .ai-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
                    .ai-item { border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 10px; background: #fff; font-size: 10px; color: #334155; line-height: 1.4; }
                    .ai-item-h { font-weight: 600; font-size: 10px; margin-bottom: 3px; color: #0f172a; }
                    .footer { margin-top: 20px; padding-top: 12px; border-top: 1px solid #e2e8f0; font-size: 9px; color: #94a3b8; text-align: center; font-style: italic; }
                    .platform-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
                    .platform-card { border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; background: #fff; }
                    .platform-head { display: flex; align-items: center; gap: 6px; padding-bottom: 5px; margin-bottom: 6px; border-bottom: 2px solid; }
                    .platform-emoji { font-size: 14px; }
                    .platform-name { font-weight: 700; font-size: 11px; flex: 1; }
                    .platform-er { font-size: 9px; color: #64748b; }
                    .platform-stats { display: flex; gap: 10px; font-size: 10px; color: #475569; margin-bottom: 6px; flex-wrap: wrap; }
                    .ps-label { color: #94a3b8; }
                    .platform-types { margin: 6px 0; }
                    .ps-sublabel { font-size: 8px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; margin-bottom: 3px; }
                    .platform-type-row { display: flex; align-items: center; gap: 6px; margin-bottom: 2px; font-size: 9px; }
                    .pt-label { width: 56px; color: #475569; }
                    .pt-bar { flex: 1; height: 4px; background: #f1f5f9; border-radius: 2px; overflow: hidden; }
                    .pt-fill { height: 100%; border-radius: 2px; }
                    .pt-value { width: 30px; text-align: right; font-weight: 600; color: #1e293b; }
                    .platform-rec { margin-top: 6px; padding: 5px 7px; background: #f8fafc; border-radius: 3px; font-size: 9px; color: #475569; }
                    .platform-rec.urgent { background: #fef2f2; color: #991b1b; font-weight: 600; }
                    .perf-row { display: flex; align-items: center; gap: 6px; margin-top: 5px; font-size: 10px; }
                    .perf-row.good { color: #064e3b; } .perf-row.bad { color: #7f1d1d; }
                    .perf-name { flex: 1; font-weight: 500; }
                    .perf-er { font-weight: 600; }
                    .perf-er.good { color: #10b981; } .perf-er.bad { color: #ef4444; }
                    .perf-vs { font-size: 9px; padding: 1px 4px; border-radius: 3px; font-weight: 600; }
                    .perf-vs.good { background: #d1fae5; color: #047857; } .perf-vs.bad { background: #fee2e2; color: #b91c1c; }
                    .perf-note { font-size: 9px; color: #64748b; margin-bottom: 3px; padding-left: 6px; border-left: 2px solid #e2e8f0; }
                    .action-list { display: flex; flex-direction: column; gap: 5px; }
                    .action-row { display: flex; gap: 8px; align-items: flex-start; font-size: 10px; color: #1e293b; line-height: 1.5; }
                    .action-num { width: 18px; height: 18px; background: #a855f7; color: #fff; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; flex-shrink: 0; }
                    .empty { border: 1px dashed #cbd5e1; border-radius: 8px; padding: 12px; color: #64748b; font-size: 11px; }
                  </style></head><body>${finalVisualHtml}</body></html>`,
                  style: { width: "100%", height: "calc(88vh - 200px)", border: "0", borderRadius: "8px", background: "#fff" },
                  title: "Report Preview"
                })
              )
            : e("div", { className: "p-5" },
                e("div", { className: "mb-2 flex items-center justify-between" },
                  e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, language === "zh" ? "预览 · Markdown" : "Preview · Markdown"),
                  e("div", { className: "text-[10px] text-slate-500" }, `${finalReportContent.length} ${language === "zh" ? "字符" : "characters"}`)
                ),
                e("pre", {
                  className: "rounded-lg border border-white/[0.06] bg-[#040712] p-4 text-[11px] text-slate-300 whitespace-pre-wrap font-mono leading-relaxed",
                  style: { maxHeight: "100%" }
                }, finalReportContent)
              )
        )
      ),
      
      // Footer
      e("div", { className: "shrink-0 border-t border-white/[0.06] px-5 py-2.5 flex items-center justify-between" },
        e("div", { className: "text-[10px] text-slate-500" }, 
          language === "zh" 
            ? `已选 ${Object.values(sections).filter(Boolean).length} / ${Object.keys(sections).length} 个 section`
            : `${Object.values(sections).filter(Boolean).length} of ${Object.keys(sections).length} sections selected`
        ),
        e("div", { className: "flex gap-1.5" },
          e("button", {
            onClick: onClose,
            className: "rounded-md border border-white/10 bg-white/[0.03] px-3 py-1 text-[11px] text-slate-300 hover:bg-white/[0.06]"
          }, language === "zh" ? "取消" : "Cancel"),
          e("button", {
            onClick: handlePrintPdf,
            className: "rounded-md px-3 py-1 text-[11px] font-medium text-white bg-purple-600 hover:bg-purple-500"
          }, language === "zh" ? "生成 PDF →" : "Generate PDF →")
        )
      )
    )
  );
}
