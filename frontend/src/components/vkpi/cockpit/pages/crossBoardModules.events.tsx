import React from "react";
import { listEvents, toUiEvent } from "../../../../services/vkpi/events-api";
import type { EventVm } from "../../pages/events/shared/types";
import { ErrorCard, LoadingLine } from "./MarketVoicePage.modules";
import { MODULE_SOURCES, UpcomingBody, upcomingEvents as selectUpcomingEvents } from "./EventsBoardPage.modules";
import { XbCard, useXbFetch, xbNoToken } from "./crossBoardModules.shell";
import { EventRadarModule } from "./EventRadarModule";

const BOARD_LABEL = "Events";
const source = MODULE_SOURCES.upcoming;
const fetchEvents = (token: string) => listEvents(token, { limit: 200 });

export function EventsRadarXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  return (
    <XbCard
      title="活动雷达"
      srcLabel="vkpi.event_radar"
      srcRows={[
        ["来源", "登记的发布者自有公开入口 / Dealer 公开日历"],
        ["边界", "外部机会与内部 Event 分离；不证明授权、库存、ROI 或参展"],
        ["跨板块", "人工批准后转 Event；点击来源板块进入完整 Events"],
      ]}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      <EventRadarModule apiToken={apiToken} limit={8} showHeading={false} />
    </XbCard>
  );
}

export function EventsUpcomingXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchEvents);
  const events = (Array.isArray(remote.data?.items) ? remote.data.items : [])
    .map(toUiEvent) as unknown as EventVm[];
  const upcomingCount = selectUpcomingEvents(events).length;

  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="events 读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="活动窗口读取中…" />;
  else body = <UpcomingBody events={events} onOpen={onOpenBoard} />;

  return (
    <XbCard
      title="即将开幕"
      cnt={remote.data ? `${upcomingCount} 场` : undefined}
      srcLabel={source.label}
      srcRows={[...source.rows, ["跨板块", "点活动行或来源徽进入 Events"]]}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}
