// Ask ⌘K 答案卡(懒加载):结构化展示 /intelligent/query 的回答——状态、数字卡、覆盖/时间、
// 缺口、来源证据、动作。无打字机动效;动作区支持键盘区(↑↓ 选、Enter 执行)。

import React from "react";
import { AlertCircle, ArrowRight, BookOpenCheck, CheckCircle2, Clock3, Database, HelpCircle } from "lucide-react";
import type { IntelligentAction, IntelligentEvidence, IntelligentFact, IntelligentQueryAnswer } from "../../../../../services/vkpi/intelligent-api";
import { isRunnableIntelligentAction } from "./AskActions";

export interface AskAnswerCardProps {
  answer: IntelligentQueryAnswer;
  locale: "zh-CN" | "en-US";
  t: (text: string) => string;
  zoneActive: boolean;
  activeActionIndex: number;
  onRunAction: (action: IntelligentAction) => void;
  onHoverAction: (index: number) => void;
}

function hasFactValue(fact: IntelligentFact): boolean {
  if (fact.value === null || fact.value === undefined || fact.value === "") return false;
  return !Array.isArray(fact.value) || fact.value.length > 0;
}

function formatFactValue(fact: IntelligentFact, locale: "zh-CN" | "en-US"): string {
  const raw = fact.value;
  if (Array.isArray(raw)) return raw.join(locale === "en-US" ? ", " : "、");
  if (typeof raw === "number") {
    const value = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(raw);
    return fact.unit ? `${value} ${fact.unit}` : value;
  }
  return `${String(raw ?? "")}${fact.unit ? ` ${fact.unit}` : ""}`;
}

function safeEvidenceUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value, window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function intentLabel(intent: string, t: (text: string) => string): string {
  const labels: Record<string, string> = {
    "kol.pool.overview": "KOL数量",
    "kol.video_topic.count": "KOL视频主题",
    "project.search": "项目搜索",
    "market.viltrox.weekly_voice": "本周市场评价",
    unknown: "待澄清问题",
  };
  return t(labels[intent] || "智能查询");
}

function confidenceLabel(value: string | undefined, t: (text: string) => string): string {
  return t(value === "high" ? "高可信" : value === "medium" ? "中等可信" : "低可信");
}

export default function AskAnswerCard({ answer, locale, t, zoneActive, activeActionIndex, onRunAction, onHoverAction }: AskAnswerCardProps) {
  const visibleFacts = answer.facts.filter(hasFactValue);
  const coverage = answer.coverage;
  const querySourceStates = Object.values(answer.trace.source_status || {}).map((item) => item.status);
  const hasUnreadyQuerySource = querySourceStates.some((status) => !["ready", "ok"].includes(status));
  const trustworthyZeroCoverage = Boolean(coverage && ["complete", "empty"].includes(coverage.status) && !hasUnreadyQuerySource);
  const coverageSummary = [
    coverage.matched_entities !== undefined && (coverage.matched_entities > 0 || trustworthyZeroCoverage)
      ? `${coverage.matched_entities.toLocaleString(locale)} ${t("个匹配")}`
      : "",
    coverage.evidence_count !== undefined && (coverage.evidence_count > 0 || trustworthyZeroCoverage)
      ? `${coverage.evidence_count.toLocaleString(locale)} ${t("条证据")}`
      : "",
  ].filter(Boolean);
  const freshness = answer.freshness;
  const freshnessText = freshness?.data_updated_at || freshness?.generated_at || "";
  const answerState = answer.status === "ready" && answer.degraded_reason ? "degraded" : answer.status || "ready";
  const answerStateLabel: Record<string, string> = {
    ready: t("已完成"),
    partial: t("结果不完整"),
    degraded: t("部分数据源不可用"),
    empty: t("没有匹配数据"),
    needs_clarification: t("需要补充条件"),
    error: t("查询未完成"),
    blocked: t("数据访问受限"),
    unavailable: t("数据暂不可用"),
  };
  const degradedNotice = answerState === "blocked"
    ? t("查询被权限范围阻止，未读取受限数据。")
    : answerState === "error" || answerState === "unavailable"
      ? t("查询暂时不可用，本次没有把故障当成零结果。")
      : t("部分数据源暂不可用，以下内容可能不完整。");
  let runnableCursor = 0;

  return (
    <div className={`vkpi-ask-dialog__answer is-${answerState} ${zoneActive ? "is-zone-active" : ""}`} aria-label={t("答案")}>
      <div className="vkpi-ask-dialog__answer-meta">
        <span>{answerState === "ready" ? <CheckCircle2 size={12} /> : answerState === "needs_clarification" ? <HelpCircle size={12} /> : <AlertCircle size={12} />}{answerStateLabel[answerState] || answerStateLabel.error}</span>
        <small title={answer.request_id}>{intentLabel(answer.intent, t)} · {Math.max(0, answer.trace.took_ms)}ms</small>
      </div>
      {answer.degraded_reason ? <div className="vkpi-ask-dialog__degraded">{degradedNotice}</div> : null}
      <p>{answer.answer}</p>

      {visibleFacts.length > 0 ? (
        <div className="vkpi-ask-dialog__facts" aria-label={t("关键数据")}>
          {visibleFacts.map((fact) => (
            <article key={fact.key}>
              <span>{fact.label}</span>
              <strong>{formatFactValue(fact, locale)}</strong>
              {fact.basis ? <small title={fact.basis}>{t("计算口径已记录")}</small> : null}
              <em className={`is-${fact.confidence}`}>{confidenceLabel(fact.confidence, t)}</em>
            </article>
          ))}
        </div>
      ) : null}

      {coverageSummary.length > 0 || freshnessText ? (
        <div className="vkpi-ask-dialog__quality">
          {coverageSummary.length > 0 ? (
            <div><BookOpenCheck size={14} /><span>{t("数据覆盖")}</span><strong>{coverageSummary.join(" · ")}</strong>{coverage.ratio !== undefined ? <small>{Math.round(Math.max(0, Math.min(100, coverage.ratio <= 1 ? coverage.ratio * 100 : coverage.ratio)))}%</small> : null}</div>
          ) : null}
          {freshnessText ? <div><Clock3 size={14} /><span>{t("数据时间")}</span><strong>{freshnessText}</strong><small>{freshness?.status === "stale" ? t("可能已过期") : t("已标注时间")}</small></div> : null}
        </div>
      ) : null}

      {answer.missing_fields.length > 0 ? (
        <section className="vkpi-ask-dialog__missing" aria-label={t("数据缺口")}>
          <h3><AlertCircle size={13} />{t("数据缺口")}</h3>
          {answer.missing_fields.map((item) => <div key={item.field}><strong>{item.field}</strong><span>{item.reason}</span>{item.impact ? <small>{item.impact}</small> : null}</div>)}
        </section>
      ) : null}

      {answer.evidence.length > 0 ? <EvidenceList evidence={answer.evidence} t={t} /> : null}

      {answer.actions.length > 0 ? (
        <div className="vkpi-ask-dialog__actions">
          {answer.actions.map((action, index) => {
            const key = `${action.type || action.route || "action"}-${index}`;
            if (action.requires_approval) {
              return <div className="vkpi-ask-dialog__proposal" key={key}><span>{action.label}</span><small>{t("待人工审批的提案")}</small></div>;
            }
            const runnable = isRunnableIntelligentAction(action);
            const runnableIndex = runnable ? runnableCursor++ : -1;
            const selected = zoneActive && runnable && runnableIndex === activeActionIndex;
            return (
              <button
                type="button"
                key={key}
                id={runnable ? `vkpi-ask-action-${runnableIndex}` : undefined}
                className={selected ? "is-active" : ""}
                data-active={selected ? "true" : undefined}
                onMouseEnter={() => { if (runnable) onHoverAction(runnableIndex); }}
                onClick={() => onRunAction(action)}
                disabled={!runnable}
              >
                {action.label}<ArrowRight size={12} />
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function EvidenceList({ evidence, t }: { evidence: IntelligentEvidence[]; t: (text: string) => string }) {
  return (
    <details className="vkpi-ask-dialog__evidence">
      <summary><Database size={12} />{t("来源与证据")}（{evidence.length}）</summary>
      <div>
        {evidence.map((item, index) => {
          const href = safeEvidenceUrl(item.url);
          const title = item.title || item.source || item.kind || `${t("证据")} ${index + 1}`;
          return (
            <article key={item.id || `${item.kind}-${index}`}>
              <header><strong>{title}</strong>{item.confidence ? <small>{confidenceLabel(item.confidence, t)}</small> : null}</header>
              {item.snippet ? <p>{item.snippet}</p> : null}
              <footer>{item.source ? <span>{t("来源")}：{item.source}</span> : null}{item.observed_at ? <time>{item.observed_at}</time> : null}{href ? <a href={href} target="_blank" rel="noreferrer">{t("打开来源")}</a> : null}</footer>
            </article>
          );
        })}
      </div>
    </details>
  );
}
