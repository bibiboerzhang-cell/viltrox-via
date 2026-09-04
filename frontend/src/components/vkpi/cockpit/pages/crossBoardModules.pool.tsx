import React from "react";
import { SmartKolInputPanel } from "../components/SmartKolInputPanel";
import { KolSearchHistoryPanel } from "../components/KolSearchHistoryPanel";
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

// SmartKolInputPanel 在源页由 ModuleCard 接管标题和卡壳；Dashboard 的 XbCard 同样
// 接管，因此只压平外壳、隐藏重复标题，搜索、历史、收藏/立项等交互保持原件。
const SMART_DASHBOARD_TRIM = [
  "[&>section]:!rounded-none [&>section]:!border-0",
  "[&>section]:!bg-transparent [&>section]:!p-0",
  "[&>section>div:first-child>div:first-child>span:first-child]:hidden",
  "[&>section>div:first-child_h2]:hidden",
].join(" ");

interface XbProps {
  apiToken: string;
  onOpenBoard: () => void;
  pageProps?: Record<string, Record<string, unknown>>;
}

function poolAccountId(pageProps: XbProps["pageProps"]): string | number | null {
  const currentUser = pageProps?.["kol-pool"]?.currentUser;
  if (!currentUser || typeof currentUser !== "object") return null;
  const id = (currentUser as { id?: unknown }).id;
  return typeof id === "string" || typeof id === "number" ? id : null;
}

export function PoolSmartSearchXbCard({ apiToken, onOpenBoard, pageProps }: XbProps) {
  const accountId = poolAccountId(pageProps);
  return (
    <XbCard
      title="找达人"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      statusLabel="可操作"
      srcLabel={src("smart").label}
      srcRows={[...src("smart").rows, ["跨板块", "Dashboard 内可直接查找、查看历史；点结果进入 KOL Pool"]]}
    >
      {!apiToken ? xbNoToken(BOARD_LABEL) : (
        <div data-testid="dashboard-kol-smart-search" className={SMART_DASHBOARD_TRIM}>
          <SmartKolInputPanel
            apiToken={apiToken}
            accountId={accountId}
            onOpenRecallItem={onOpenBoard}
            onOpenProfile={onOpenBoard}
          />
        </div>
      )}
    </XbCard>
  );
}

export function PoolSearchHistoryXbCard({ apiToken, onOpenBoard, pageProps }: XbProps) {
  const accountId = poolAccountId(pageProps);
  return (
    <XbCard
      title="搜索历史"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      statusLabel="账号记录"
      srcLabel="搜索会话历史"
      srcRows={[
        ["数据源", "GET /api/admin/vkpi/kol-search-history · 当前登录账号"],
        ["动作", "移除为软归档；已移除记录可恢复"],
        ["打开", "点记录跳转 KOL Pool 并恢复该会话"],
      ]}
    >
      <KolSearchHistoryPanel apiToken={apiToken} accountId={accountId} onOpenBoard={onOpenBoard} />
    </XbCard>
  );
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
