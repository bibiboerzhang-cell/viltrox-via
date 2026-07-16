import React from "react";
import { fetchIntelligentStats, type IntelligentStats } from "../../../../services/vkpi/intelligent-api";
import { EmptyLine, ErrorCard, LoadingLine, PendingCard } from "./MarketVoicePage.modules";
import { MODULE_SOURCES, statsSeries } from "./IntelligentBoardPage.modules";
import { XbCard, useXbFetch, xbNoToken } from "./crossBoardModules.shell";

const BOARD_LABEL = "Intelligent 问答";
const source = MODULE_SOURCES.kpiI;
const fetchStats = (token: string) => fetchIntelligentStats(token);

function StatsBody({ data }: { data: IntelligentStats }) {
  if (data.status === "error") return <ErrorCard title="问答统计聚合失败" text={String(data.reason || "未知原因")} />;
  if (data.status === "empty") return <EmptyLine text={String(data.reason || "服务端暂无综合问答留痕。")} />;
  if (data.status !== "ready") return <PendingCard>统计状态为 {String(data.status || "未知")}，不推断数据。</PendingCard>;
  const series = statsSeries(data.by_day, 14);
  const max = Math.max(1, ...series);
  return (
    <div>
      <div className="flex items-end gap-1" style={{ height: 72 }} aria-label="近 14 天综合问答次数">
        {series.map((value, index) => (
          <span
            key={index}
            className="min-w-0 flex-1 rounded-t-sm bg-accent"
            style={{ height: `${value > 0 ? Math.max(8, (value / max) * 100) : 2}%` }}
            title={`第 ${index + 1} 天：${value} 次`}
          />
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between text-[10.5px] text-muted">
        <span>累计 {Number(data.total || 0).toLocaleString()} 次</span>
        <span>{data.last_at ? `最近 ${data.last_at}` : "暂无最近调用时间"}</span>
      </div>
    </div>
  );
}

export function IntelligentStatsXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchStats);
  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="intelligent/stats 读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="问答留痕读取中…" />;
  else body = <StatsBody data={remote.data} />;
  return (
    <XbCard
      title="问答调用趋势"
      cnt={remote.data?.status === "ready" ? `${Number(remote.data.total || 0)} 次` : undefined}
      srcLabel={source.label}
      srcRows={source.rows}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}
