/**
 * 报告深度分析入口(接线 2026-08-25)。
 *
 * 后端端点、预算档、客户端函数三样早就齐了,唯独没有任何调用方 —— 功能等于不存在。
 * 本组件就是那个入口,并且把「这是一个花钱动作」这件事摆在明面上:
 *
 *  1. **两步走,绝不自动武装。** 进来只有一个「查看本次费用」;点了先拿报价(后端
 *     dry_run 分支,纯 SELECT,零成本),把「这一次要不要花钱、花多少」显示出来,
 *     再由人点第二下才真跑。组件挂载时**不**自动请求任何东西。
 *  2. **当天缓存如实说。** 命中当天同一份报告 -> 报价显示 0 成本、按钮改成「直接取用」,
 *     不假装是新算的,也不把复用说成免费的分析。
 *  3. **没派活就不许显示「正在…」。** busy 只在真有请求在飞时为真;空态说的是
 *     「尚未查询」,而不是任何进行时。
 *  4. **门面禁术语。** 不出现厂商名、模型名、内部口径词;界面只说「深度分析」。
 */
import { useCallback, useMemo, useState } from "react";
import { Loader2, Sparkles, Wallet } from "lucide-react";

import {
  fetchCockpitReportAnalysis,
  quoteCockpitReportAnalysis,
} from "../api";

type Row = Record<string, unknown>;

export interface ReportDeepAnalysisProps {
  apiToken?: string;
  /** 服务端已生成的报告正文。为空 = 还没有可分析的东西,入口保持禁用。 */
  reportText: string;
  period: string;
  language: string;
}

interface Quote {
  available: boolean;
  cached: boolean;
  willSpend: boolean;
  estimatedCostUsd: number;
  reason: string;
}

interface Analysis {
  executiveSummary: string;
  highlights: string[];
  risks: string[];
  recommendations: string[];
  cached: boolean;
}

const _str = (value: unknown): string => (typeof value === "string" ? value : "");
const _num = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) ? value : 0);
const _bool = (value: unknown): boolean => value === true;
const _list = (value: unknown): string[] =>
  Array.isArray(value) ? value.map((item) => _str(item)).filter(Boolean) : [];

/** 报价文案。三种结果三种说法,一句都不许含糊。 */
export function quoteMessage(quote: Quote): string {
  if (quote.reason === "report_too_short") return "报告内容太短，暂时无法分析。";
  if (quote.reason === "budget_blocked") return "本项今日额度已用完，明天再试。";
  if (quote.cached) return "今天已经分析过同一份报告，可直接取用，本次不产生费用。";
  return `本次分析预计花费 $${quote.estimatedCostUsd.toFixed(2)}，确认后才会执行。`;
}

function toQuote(row: Row): Quote {
  return {
    available: _bool(row.available),
    cached: _bool(row.cached),
    willSpend: _bool(row.will_spend),
    estimatedCostUsd: _num(row.estimated_cost_usd),
    reason: _str(row.reason),
  };
}

function toAnalysis(row: Row): Analysis | null {
  if (!_bool(row.available)) return null;
  const payload = (row.analysis ?? {}) as Row;
  const analysis: Analysis = {
    executiveSummary: _str(payload.executive_summary),
    highlights: _list(payload.highlights),
    risks: _list(payload.risks),
    recommendations: _list(payload.recommendations),
    cached: _bool(row.cached),
  };
  // 后端可能返回 available=true 却是空壳;空壳就当没有,别渲染一个空框骗人。
  return analysis.executiveSummary || analysis.highlights.length ? analysis : null;
}

function failureMessage(row: Row): string {
  const reason = _str(row.reason);
  if (reason === "budget_blocked") return "本项今日额度已用完，明天再试。";
  if (reason === "report_too_short") return "报告内容太短，暂时无法分析。";
  return "这次没能生成分析，请稍后重试。";
}

function Section({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">{title}</span>
      <ul className="mt-1.5 space-y-1">
        {items.map((item, index) => (
          <li key={`${title}-${index}`} className="text-[11px] leading-relaxed text-ink-2">
            · {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ReportDeepAnalysis({
  apiToken,
  reportText,
  period,
  language,
}: ReportDeepAnalysisProps) {
  const [quote, setQuote] = useState<Quote | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [busy, setBusy] = useState<"quote" | "run" | null>(null);
  const [error, setError] = useState<string>("");

  const ready = Boolean(apiToken) && reportText.trim().length >= 40;

  // 报告换了(内容 / 周期 / 语言),旧报价与旧结果都作废 —— 否则会拿上一份的价格
  // 去授权这一份的花费。
  const stamp = useMemo(
    () => `${period}|${language}|${reportText.length}|${reportText.slice(0, 64)}`,
    [period, language, reportText],
  );
  const [seenStamp, setSeenStamp] = useState(stamp);
  if (stamp !== seenStamp) {
    setSeenStamp(stamp);
    setQuote(null);
    setAnalysis(null);
    setError("");
  }

  const runQuote = useCallback(async () => {
    if (!apiToken || busy) return;
    setBusy("quote");
    setError("");
    try {
      setQuote(toQuote(await quoteCockpitReportAnalysis(apiToken, reportText, period, language)));
    } catch {
      setQuote(null);
      setError("费用查询失败，请稍后重试。");
    } finally {
      setBusy(null);
    }
  }, [apiToken, busy, reportText, period, language]);

  const runAnalysis = useCallback(async () => {
    if (!apiToken || busy) return;
    setBusy("run");
    setError("");
    try {
      const row = await fetchCockpitReportAnalysis(apiToken, reportText, period, language);
      const parsed = toAnalysis(row);
      if (!parsed) {
        setAnalysis(null);
        setError(failureMessage(row));
        return;
      }
      setAnalysis(parsed);
    } catch {
      setAnalysis(null);
      setError("分析请求失败，请稍后重试。");
    } finally {
      setBusy(null);
    }
  }, [apiToken, busy, reportText, period, language]);

  return (
    <section className="rounded-lg border border-line bg-card p-4" data-testid="report-deep-analysis">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Sparkles size={13} className="text-accent" />
          <h4 className="text-[12px] font-semibold">深度分析</h4>
        </div>
        {!ready ? (
          <span className="text-[10px] text-muted">先生成服务端报告，再做深度分析。</span>
        ) : null}
      </div>
      <p className="mt-1.5 text-[10px] leading-relaxed text-muted">
        按需执行的付费动作。先查看本次费用，确认后才会执行；同一份报告当天可直接取用。
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void runQuote()}
          disabled={!ready || busy !== null}
          className="flex items-center gap-1.5 rounded-md border border-line bg-card px-3 py-2 text-[10px] text-ink-2 disabled:opacity-50"
        >
          {busy === "quote" ? <Loader2 size={11} className="animate-spin" /> : <Wallet size={11} />}
          {busy === "quote" ? "查询中…" : "查看本次费用"}
        </button>
        {quote?.available ? (
          <button
            type="button"
            onClick={() => void runAnalysis()}
            disabled={busy !== null}
            className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-2 text-[10px] font-semibold text-[var(--ds-on-accent)] disabled:opacity-50"
          >
            {busy === "run" ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
            {busy === "run"
              ? "分析中…"
              : quote.cached
                ? "直接取用今天的分析"
                : `确认并执行（$${quote.estimatedCostUsd.toFixed(2)}）`}
          </button>
        ) : null}
      </div>

      {quote ? (
        <p role="status" className="mt-2 text-[10px] leading-relaxed text-ink-2">
          {quoteMessage(quote)}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 rounded-md border border-red-400/20 bg-red-400/10 px-3 py-2 text-[10px] text-red-300">
          {error}
        </p>
      ) : null}

      {analysis ? (
        <div className="mt-3 space-y-3 border-t border-line pt-3">
          {analysis.cached ? (
            <span className="text-[9px] text-muted">取用今天已生成的分析，未重复计费。</span>
          ) : null}
          {analysis.executiveSummary ? (
            <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-ink-2">
              {analysis.executiveSummary}
            </p>
          ) : null}
          <Section title="亮点" items={analysis.highlights} />
          <Section title="风险" items={analysis.risks} />
          <Section title="建议" items={analysis.recommendations} />
        </div>
      ) : null}
    </section>
  );
}
