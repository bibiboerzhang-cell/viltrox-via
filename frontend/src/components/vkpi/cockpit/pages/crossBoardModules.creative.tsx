import React from "react";
import { searchCreativeSegments, type CreativeSegmentsResponse } from "../../../../services/vkpi/creativeLibrary-api";
import { EmptyLine, ErrorCard, LoadingLine, PendingCard } from "./MarketVoicePage.modules";
import { MODULE_SOURCES } from "./CreativeLibraryBoardPage.modules";
import { XbCard, useXbFetch, xbNoToken } from "./crossBoardModules.shell";

const BOARD_LABEL = "创意资产库";
const source = MODULE_SOURCES.health;
const fetchIndex = (token: string) => searchCreativeSegments(token, { limit: 24 });

function IndexBody({ data }: { data: CreativeSegmentsResponse }) {
  if (data.status === "error") return <ErrorCard title="创意索引聚合失败" text={String(data.reason || "未知原因")} />;
  if (data.status === "empty") return <EmptyLine text={String(data.reason || "深析库当前为空，索引未生成。")} />;
  if (data.status !== "ready") return <PendingCard>索引状态为 {String(data.status || "未知")}，不推断数据。</PendingCard>;
  const items = Array.isArray(data.items) ? data.items : [];
  const thumbOk = items.filter((item) => Boolean(
    item.video.best_thumbnail || item.video.cached_thumbnail_url || item.video.thumbnail_url || item.video.youtube_thumbnail_url,
  )).length;
  const rows = [
    ["深析视频", `${Number(data.scanned_videos || 0).toLocaleString()} 条`],
    ["段级索引", `${Number(data.segment_count || 0).toLocaleString()} 段`],
    ["覆盖 KOL", typeof data.kol_count === "number" ? `${data.kol_count.toLocaleString()} 位` : "计数缺席"],
    ["缩略图可用", `${thumbOk}/${items.length}（本次返回内）`],
  ];
  return (
    <div>
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-center justify-between gap-3 border-b border-line py-2 text-[11.5px] last:border-0">
          <span className="text-muted">{label}</span>
          <span className="text-right font-mono text-ink-2">{value}</span>
        </div>
      ))}
      {data.generated_at ? <div className="mt-2 text-[10px] text-muted">生成于 {data.generated_at}</div> : null}
    </div>
  );
}

export function CreativeIndexXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchIndex);
  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="creative-segments 读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="创意索引聚合中…" />;
  else body = <IndexBody data={remote.data} />;
  return (
    <XbCard
      title="索引健康"
      cnt={remote.data?.status === "ready" ? `${Number(remote.data.segment_count || 0)} 段` : undefined}
      srcLabel={source.label}
      srcRows={source.rows}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}
