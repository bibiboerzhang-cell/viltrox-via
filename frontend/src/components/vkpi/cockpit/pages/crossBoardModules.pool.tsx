import React from "react";
import { usePoolSummary } from "./KolPoolBoardPage.actions";
import { DiscoveryFunnelBody } from "./KolPoolBoardPage.charts";
import { MODULE_SOURCES } from "./KolPoolBoardPage.modules";
import { XbCard, xbNoToken } from "./crossBoardModules.shell";

// Dashboard 跨板块拉卡 · KOL Pool 一件(发现转化漏斗)。
//   数据 = 源板块导出的 usePoolSummary hook 原件(GET /api/admin/vkpi/kol-pool/summary
//   卡内自取);渲染件 = DiscoveryFunnelBody 原件(段缺席灰行诚实缺席,绝不编 0)。
// 红线:纯读展示;SrcChip 口径 = 源 MODULE_SOURCES 唯一注册表。

const BOARD_LABEL = "KOL Pool";
const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

interface XbProps {
  apiToken: string;
  onOpenBoard: () => void;
}

export function PoolDiscoveryFunnelXbCard({ apiToken, onOpenBoard }: XbProps) {
  const { funnel30d, summaryLoading } = usePoolSummary(apiToken);
  return (
    <XbCard
      title="发现转化 · 近30天"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      cnt={
        funnel30d && Number.isFinite(Number(funnel30d.discovered))
          ? Number(funnel30d.discovered).toLocaleString()
          : undefined
      }
      srcLabel={src("funnel").label}
      srcRows={[...src("funnel").rows, ["跨板块", "点来源徽 → KOL Pool 板块"]]}
    >
      {!apiToken ? xbNoToken(BOARD_LABEL) : <DiscoveryFunnelBody funnel={funnel30d} loading={summaryLoading} />}
    </XbCard>
  );
}
