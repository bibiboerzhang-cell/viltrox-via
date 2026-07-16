import React from "react";
import { listProductLaunches } from "../../../../services/vkpi/launchBoard-api";
import { ErrorCard, LoadingLine } from "./MarketVoicePage.modules";
import { MODULE_SOURCES } from "./LaunchPadBoardPage.modules";
import { LaunchesBody } from "./LaunchPadBoardPage.ops";
import { XbCard, useXbFetch, xbNoToken, type Row } from "./crossBoardModules.shell";

const BOARD_LABEL = "发射台";
const source = MODULE_SOURCES.launches;
const fetchLaunches = (token: string) => listProductLaunches(token, 20);

export function LaunchpadPlansXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchLaunches);
  const launches = Array.isArray(remote.data?.launches) ? remote.data.launches as Row[] : [];
  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="product-analysis/launches 读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="发布计划读取中…" />;
  else body = <LaunchesBody launches={launches} />;
  return (
    <XbCard
      title="发布计划"
      cnt={remote.data && launches.length > 0 ? `${launches.length} 项` : undefined}
      srcLabel={source.label}
      srcRows={source.rows}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}
