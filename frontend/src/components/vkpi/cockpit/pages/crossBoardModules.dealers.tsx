import React from "react";
import { listDealers, type VkpiDealer } from "../../../../services/vkpi/dealers-api";
import { EmptyLine, ErrorCard, LoadingLine } from "./MarketVoicePage.modules";
import { MODULE_SOURCES, RegionBars, ZERO_NOTE } from "./DealersBoardPage.modules";
import { XbCard, useXbFetch, xbNoToken } from "./crossBoardModules.shell";

const BOARD_LABEL = "Dealers";
const source = MODULE_SOURCES.regionD;
const fetchDealers = (token: string) => listDealers(token, { limit: 500 });

function regions(dealers: VkpiDealer[]) {
  const counts = new Map<string, number>();
  dealers.forEach((dealer) => {
    const key = String(dealer.state || "").trim().toUpperCase() || "未标注";
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return [...counts.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((left, right) => right.count - left.count)
    .slice(0, 10);
}

export function DealersRegionsXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchDealers);
  const dealers = Array.isArray(remote.data?.dealers) ? remote.data.dealers : [];
  const rows = regions(dealers);
  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="dealers 读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="经销商地区聚合中…" />;
  else if (dealers.length === 0) body = <EmptyLine text={ZERO_NOTE} />;
  else body = <RegionBars rows={rows} />;
  return (
    <XbCard
      title="地区分布"
      cnt={remote.data && dealers.length > 0 ? `${dealers.length} 家` : undefined}
      srcLabel={source.label}
      srcRows={source.rows}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}
