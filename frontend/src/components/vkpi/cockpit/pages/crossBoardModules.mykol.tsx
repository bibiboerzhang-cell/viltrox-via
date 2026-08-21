import React from "react";
import { usePermissions } from "../../../../hooks/usePermissions";
import { getMyKolAggregate, type VkpiMyKolAggregateResponse } from "../../../../services/vkpi/kol-api";
import {
  getMyKolBoardExt,
  mapLibraryRows,
  type VkpiFunnelGroup,
  type VkpiMyKolBoardExtResponse,
  type VkpiRecentVideosGroup,
} from "../../../../services/vkpi/myKolBoard-api";
import { EmptyLine } from "./MarketVoicePage.modules";
import { AnalysisActivityModule, MODULE_SOURCES } from "./MyKolBoardPage.modules";
import { ContentWallModule } from "./MyKolBoardPage.content-wall";
import { FunnelBody } from "./MyKolBoardPage.charts";
import { XbCard, useXbFetch, xbGroupGate, xbNoToken, type Row } from "./crossBoardModules.shell";

// Dashboard 跨板块拉卡 · MY KOL 三件(合作漏斗 / 分析动态 / 内容墙)。
//   数据(卡内自取,零依赖 MY KOL 页级 state):
//     GET /api/admin/vkpi/my-kol/board-ext?days=30 —— funnel / recent_videos 组
//       (后端按身份 scope:管理层全团队 · 员工 own-only,与源板块同一次调用同口径);
//     GET /api/admin/vkpi/my-kol/aggregate —— 仅内容墙 KOL 下拉名单(管理层 scope=team
//       镜像源板块口径;读取失败 → 名单降级为「全部收藏 KOL」,口径行如实标注);
//     分析动态 = AnalysisActivityModule 原件(task-queue 泳道流自取,行点击走全局
//       vkpi:open-kol-pool-item 事件管道直达 KOL Pool —— CockpitApp 既有监听,任意页可发)。
//   源板块内漏斗「点段过滤 KOL 库」是页级联动;跨板块视图点段 = 跳 MY KOL 板块
//   (口径行如实注明,不装联动)。
// 红线:纯读展示;fit 分只读透传绝不回写;SrcChip 口径 = 源 MODULE_SOURCES 唯一注册表。

const BOARD_LABEL = "MY KOL";
const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

const fetchExt30 = (token: string) => getMyKolBoardExt(token, { days: 30 });

interface XbProps {
  apiToken: string;
  onOpenBoard: () => void;
  /** 通用跳板(CockpitApp setActiveNav 同管道):分析动态兜底跳 KOL Pool 用 */
  onNavigate: (navKey: string) => void;
}

const basisRows = (group?: Row | null): Array<[string, string]> =>
  group && typeof group.basis === "string" ? [["后端口径", group.basis]] : [];

export function MyKolFunnelXbCard({ apiToken, onOpenBoard }: XbProps) {
  const ext = useXbFetch<VkpiMyKolBoardExtResponse>(apiToken, fetchExt30);
  const g = ext.data?.funnel;
  const gate = xbGroupGate({
    apiToken,
    boardLabel: BOARD_LABEL,
    errorTitle: "board-ext 读取失败",
    error: ext.error,
    loaded: ext.data != null,
    loadingText: "看板聚合读取中…",
    group: g as Row | undefined,
  });
  return (
    <XbCard
      title="合作漏斗"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      cnt={g && String(g.status) === "ready" ? Number(g.total || 0).toLocaleString() : undefined}
      srcLabel={src("funnel").label}
      srcRows={[...src("funnel").rows, ...basisRows(g as Row | undefined), ["跨板块", "点段 → MY KOL 板块(阶段过滤在源板块内进行)"]]}
    >
      {gate ?? <FunnelBody funnel={g as VkpiFunnelGroup} selectedStage="" onSelectStage={() => onOpenBoard()} />}
    </XbCard>
  );
}

export function MyKolActivityXbCard({ apiToken, onOpenBoard, onNavigate }: XbProps) {
  return (
    <XbCard
      title="分析动态"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      srcLabel={src("activity").label}
      srcRows={[...src("activity").rows, ["跨板块", "点行直达 KOL Pool(有 KOL 主体的任务直接开详情)"]]}
    >
      {apiToken ? (
        <AnalysisActivityModule apiToken={apiToken} onJumpPool={() => onNavigate("kol-pool")} />
      ) : (
        xbNoToken(BOARD_LABEL)
      )}
    </XbCard>
  );
}

export function MyKolContentWallXbCard({ apiToken, onOpenBoard }: XbProps) {
  // 视角镜像源板块:管理层 scope=team 全团队收藏集,成员 own-only(服务端硬闸兜底)。
  const perms = usePermissions();
  const isManager = perms.isManager();
  const fetchAgg = React.useCallback(
    (token: string) => getMyKolAggregate(token, {
      ...(isManager ? { scope: "team" as const } : {}),
      mode: "summary",
      favoritesLimit: 50,
    }),
    [isManager],
  );
  const ext = useXbFetch<VkpiMyKolBoardExtResponse>(apiToken, fetchExt30);
  const agg = useXbFetch<VkpiMyKolAggregateResponse>(apiToken, fetchAgg);
  const g = ext.data?.recent_videos;
  const gate = xbGroupGate({
    apiToken,
    boardLabel: BOARD_LABEL,
    errorTitle: "board-ext 读取失败",
    error: ext.error,
    loaded: ext.data != null,
    loadingText: "看板聚合读取中…",
    group: g as Row | undefined,
  });
  // KOL 下拉名单(aggregate 收藏行同源;失败 = 仅「全部收藏 KOL」,口径行如实标注)
  const kolOptions = React.useMemo(
    () =>
      mapLibraryRows(agg.data?.pool_favorites as Row[] | undefined, agg.data?.claims as Row[] | undefined)
        .map((row) => ({ poolId: row.poolId, name: row.name }))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [agg.data],
  );
  const n = g && String(g.status) === "ready" && Array.isArray(g.items) ? g.items.length : undefined;
  return (
    <XbCard
      title="内容墙"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      cnt={n != null ? `近 ${n} 条` : undefined}
      srcLabel={src("contentWall").label}
      srcRows={[
        ...src("contentWall").rows,
        ...basisRows(g as Row | undefined),
        ["视角", isManager ? "管理层 · 全团队收藏集(scope=team)" : "成员 · own-only(服务端裁剪)"],
        ...(agg.error ? ([["KOL 下拉", `aggregate 读取失败:${agg.error} —— 名单降级为「全部收藏 KOL」`]] as Array<[string, string]>) : []),
      ]}
    >
      {String((g as Row | undefined)?.status || "") === "empty" ? (
        <EmptyLine text="暂无采集视频——去 MY KOL 库行发起采集。" />
      ) : (
        gate ?? <ContentWallModule apiToken={apiToken} group={g as VkpiRecentVideosGroup} kolOptions={kolOptions} />
      )}
    </XbCard>
  );
}
