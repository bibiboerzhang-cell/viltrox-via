import React from "react";
import { apiFetch } from "../../../../services/http";
import { EmptyLine, ErrorCard, LoadingLine } from "./MarketVoicePage.modules";
import { MODULE_SOURCES, fmtRate } from "./AutonomyDrivePage.modules";
import { XbCard, useXbFetch, xbNoToken, type Row } from "./crossBoardModules.shell";

const BOARD_LABEL = "自治驾照";
const source = MODULE_SOURCES.scorecard;
const fetchScorecard = (token: string) => apiFetch<Row>("/api/admin/vkpi/learning/weekly-scorecard?weeks=8", {}, token);

function ScorecardBody({ data }: { data: Row }) {
  if (String(data.status || "") !== "ok") return <EmptyLine text={String(data.reason || "周度窗口内暂无裁决数据。")} />;
  const overall = data.overall && typeof data.overall === "object" ? data.overall as Row : {};
  const backlog = data.pending_backlog && typeof data.pending_backlog === "object" ? data.pending_backlog as Row : {};
  const groups = Array.isArray(data.groups) ? data.groups as Row[] : [];
  return (
    <div>
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-md border border-line p-2">
          <div className="text-[9.5px] text-muted">8 周命中率</div>
          <div className="mt-1 text-[18px] font-semibold text-ink">{fmtRate(overall.in_range_hit_rate as number | null)}</div>
        </div>
        <div className="rounded-md border border-line p-2">
          <div className="text-[9.5px] text-muted">待对答案</div>
          <div className="mt-1 text-[18px] font-semibold text-ink">{Number(backlog.pending_total || 0).toLocaleString()}</div>
        </div>
      </div>
      <div className="mt-2">
        {groups.slice(0, 5).map((group, index) => (
          <div key={String(group.action_type || index)} className="flex items-center gap-2 border-b border-line py-1.5 text-[10.5px] last:border-0">
            <span className="min-w-0 flex-1 truncate text-ink-2">{String(group.label || group.action_type || "未命名组")}</span>
            <span className="font-mono text-muted">判定 {Number(group.in_range_judged || 0)}</span>
            <span className="font-mono text-ink">{fmtRate(group.in_range_hit_rate as number | null)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function AutonomyScorecardXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchScorecard);
  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="weekly-scorecard 读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="周度裁决聚合中…" />;
  else body = <ScorecardBody data={remote.data} />;
  return (
    <XbCard
      title="周度记分卡"
      cnt={remote.data?.status === "ok" ? "8 周" : undefined}
      srcLabel={source.label}
      srcRows={source.rows}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}
