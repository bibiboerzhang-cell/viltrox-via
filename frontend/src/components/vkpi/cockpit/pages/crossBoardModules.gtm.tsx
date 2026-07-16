import React from "react";
import { apiFetch } from "../../../../services/http";
import { getMarketBrainSummary } from "../../../../services/vkpi/gtmCommand-api";
import { EmptyLine, ErrorCard, LoadingLine } from "./MarketVoicePage.modules";
import { MODULE_SOURCES, SignalsBody } from "./GtmCommandBoardPage.modules";
import { XbCard, useXbFetch, xbNoToken, type Row } from "./crossBoardModules.shell";

const BOARD_LABEL = "GTM Command";
const source = MODULE_SOURCES.signals;
const fetchSummary = (token: string) => getMarketBrainSummary(token);
const fetchAiReadiness = (token: string) => apiFetch<Row>("/api/admin/vkpi/agents/marketing-brain/scorecard", {}, token);

const AI_READINESS_SOURCE = {
  label: "agents/marketing-brain/scorecard",
  rows: [
    ["能力分", "代码、数据表与评测合约是否具备；不等于真实业务效果"],
    ["证据分", "近期真实运行、人工裁决、反馈与 actual 评测的加权结果"],
    ["放行", "人工 finalized outcome、非演示反馈、绑定真实结果的 prediction eval 三腿均须达标且新鲜"],
  ] as Array<[string, string]>,
};

const CHECK_LABELS: Record<string, string> = {
  finalized_outcomes: "人工 finalized outcome",
  prediction_evals: "带真实 actual 的预测评测",
  real_feedback: "非演示人工反馈",
};

const CHECK_STATUS_LABELS: Record<string, string> = {
  ready: "达标",
  insufficient: "待补证",
  stale: "已过期",
  freshness_unknown: "新鲜度未知",
};

function numberOrDash(value: unknown): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(1) : "—";
}

function AiReadinessBody({ data }: { data: Row }) {
  if (String(data.status || "") !== "ok") {
    return <EmptyLine text={String(data.reason || "AI 证据评分尚未生成。")} />;
  }
  const readiness = data.data_readiness && typeof data.data_readiness === "object"
    ? data.data_readiness as Row
    : {};
  const checks = readiness.checks && typeof readiness.checks === "object"
    ? readiness.checks as Record<string, Row>
    : {};
  const checkRows: Array<Row & { key: string }> = Object.keys(CHECK_LABELS).map((key) => ({
    ...(checks[key] || {}),
    key,
  }));
  const validated = String(data.claim_status || readiness.claim_level || "") === "validated";
  return (
    <div>
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-md border border-line p-2">
          <div className="text-[9.5px] text-muted">系统能力分</div>
          <div className="mt-1 text-[18px] font-semibold text-ink">{numberOrDash(data.capability_score)}</div>
          <div className="mt-0.5 text-[9.5px] text-muted">会不会做</div>
        </div>
        <div className="rounded-md border border-line p-2">
          <div className="text-[9.5px] text-muted">真实证据分</div>
          <div className="mt-1 text-[18px] font-semibold text-ink">{numberOrDash(data.observed_evidence_score ?? data.score)}</div>
          <div className="mt-0.5 text-[9.5px] text-muted">是否已证明有效</div>
        </div>
      </div>
      <div className="mt-2 rounded-md border border-line px-2 py-1.5 text-[10px]">
        <span className={validated ? "font-semibold text-success" : "font-semibold text-warn"}>
          {validated ? "已通过证据门槛" : "仅描述性结论"}
        </span>
        {!validated && <span className="ml-2 text-muted">三条真实证据腿未全部达标，不声明增长效果</span>}
      </div>
      <div className="mt-2">
        {checkRows.map((check) => {
          const observed = Number(check.observed || 0);
          const minimum = Number(check.minimum || 0);
          const ready = String(check.status || "") === "ready";
          return (
            <div key={check.key} className="flex items-center gap-2 border-b border-line py-1.5 text-[10.5px] last:border-0">
              <span className="min-w-0 flex-1 truncate text-ink-2">{CHECK_LABELS[check.key]}</span>
              <span className="font-mono text-muted">{observed}/{minimum}</span>
              <span className={ready ? "font-semibold text-success" : "font-semibold text-warn"}>
                {CHECK_STATUS_LABELS[String(check.status || "")] || "缺证据"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function GtmSignalsXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchSummary);
  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="market-brain/summary 读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="本周信号聚合中…" />;
  else if (remote.data.status === "scope_unavailable") {
    body = <ErrorCard title="当前租户的 GTM 聚合尚未接通" text={remote.data.reason} />;
  }
  else body = <SignalsBody summary={remote.data} />;
  return (
    <XbCard
      title="本周信号"
      cnt={remote.data ? `${remote.data.weekly_signals.items.length} 条` : undefined}
      srcLabel={source.label}
      srcRows={source.rows}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}

export function GtmAiReadinessXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchAiReadiness);
  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="marketing-brain/scorecard 读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="AI 能力与真实证据核对中…" />;
  else body = <AiReadinessBody data={remote.data} />;
  const validated = String(remote.data?.claim_status || "") === "validated";
  return (
    <XbCard
      title="AI 证据就绪度"
      cnt={remote.data ? (validated ? "已验证" : "仅描述") : undefined}
      srcLabel={AI_READINESS_SOURCE.label}
      srcRows={AI_READINESS_SOURCE.rows}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}
