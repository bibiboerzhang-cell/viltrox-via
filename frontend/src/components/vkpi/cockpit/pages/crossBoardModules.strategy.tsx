import React from "react";
import { apiFetch } from "../../../../services/http";
import { EmptyLine, ErrorCard, LoadingLine } from "./MarketVoicePage.modules";
import { MODULE_SOURCES, type BenchResp } from "./StrategyDeskPage.modules";
import { RankBody } from "./StrategyDeskPage.charts";
import { XbCard, useXbFetch, xbNoToken } from "./crossBoardModules.shell";

const BOARD_LABEL = "战略台";
const source = MODULE_SOURCES.rank;
const fetchBenchmark = (token: string) => apiFetch<BenchResp>(
  "/api/admin/vkpi/strategy/industry-benchmark?window_days=90",
  { timeoutMs: 20000 },
  token,
);

export function StrategySovXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchBenchmark);
  const status = String(remote.data?.status || "");
  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="strategy/industry-benchmark 读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="品牌声量聚合中…" />;
  else if (status === "error") body = <ErrorCard title="行业对照聚合失败" text={String(remote.data.reason || "未知原因")} />;
  else if (status !== "ok") body = <EmptyLine text={status === "no_data_in_window" ? "窗口内暂无入库视频证据。" : String(remote.data.reason || "窗口内未命中品牌词表。")} />;
  else body = (
    <RankBody
      viltrox={remote.data.viltrox || {}}
      competitors={Array.isArray(remote.data.competitors) ? remote.data.competitors : []}
    />
  );
  const sov = typeof remote.data?.viltrox?.share_of_voice === "number"
    ? `${(remote.data.viltrox.share_of_voice * 100).toFixed(1)}%`
    : undefined;
  return (
    <XbCard
      title="声量份额排名"
      cnt={sov ? `SoV ${sov}` : undefined}
      srcLabel={source.label}
      srcRows={[...source.rows, ["窗口", "近 90 天固定；品牌联动筛选在战略台完成"]]}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}
