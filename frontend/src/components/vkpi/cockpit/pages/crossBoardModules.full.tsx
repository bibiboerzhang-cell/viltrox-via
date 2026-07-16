import React from "react";
import { ArrowUpRight } from "lucide-react";
import { LazyErrorBoundary } from "../components/LazyErrorBoundary";

const PAGE_COMPONENTS: Record<string, React.LazyExoticComponent<React.ComponentType<any>>> = {
  "my-kol": React.lazy(() => import("./MyKolBoardPage").then((module) => ({ default: module.MyKolBoardPage }))),
  "kol-pool": React.lazy(() => import("./KolPoolBoardPage").then((module) => ({ default: module.KolPoolBoardPage }))),
  kolProfile: React.lazy(() => import("./KolProfileBoardPage").then((module) => ({ default: module.KolProfileBoardPage }))),
  projects: React.lazy(() => import("./ProjectsBoardPage").then((module) => ({ default: module.ProjectsBoardPage }))),
  events: React.lazy(() => import("./EventsBoardPage").then((module) => ({ default: module.EventsBoardPage }))),
  shopify: React.lazy(() => import("./ShopifyBoardPage").then((module) => ({ default: module.ShopifyBoardPage }))),
  dealers: React.lazy(() => import("./DealersBoardPage").then((module) => ({ default: module.DealersBoardPage }))),
  triage: React.lazy(() => import("../../pages/DataQualityPage").then((module) => ({
    default: (props: Record<string, unknown>) => <module.DataQualityPage {...props} viewMode="manager" />,
  }))),
  dataQuery: React.lazy(() => import("../../pages/DataQueryPage").then((module) => ({ default: module.DataQueryPage }))),
  marketTrends: React.lazy(() => import("../../pages/MarketTrendsPage").then((module) => ({ default: module.MarketTrendsPage }))),
  skillStudio: React.lazy(() => import("../../pages/SkillStudioPage").then((module) => ({ default: module.SkillStudioPage }))),
  intelligent: React.lazy(() => import("./IntelligentBoardPage").then((module) => ({ default: module.IntelligentBoardPage }))),
  marketVoice: React.lazy(() => import("./MarketVoicePage").then((module) => ({ default: module.MarketVoicePage }))),
  sku360: React.lazy(() => import("./Sku360BoardPage").then((module) => ({ default: module.Sku360BoardPage }))),
  creativeLibrary: React.lazy(() => import("./CreativeLibraryBoardPage").then((module) => ({ default: module.CreativeLibraryBoardPage }))),
  replyQueue: React.lazy(() => import("./ReplyQueueBoardPage").then((module) => ({ default: module.ReplyQueueBoardPage }))),
  launchpad: React.lazy(() => import("./LaunchPadBoardPage").then((module) => ({ default: module.LaunchPadBoardPage }))),
  autonomy: React.lazy(() => import("./AutonomyDrivePage").then((module) => ({ default: module.AutonomyDrivePage }))),
  strategyBoard: React.lazy(() => import("./StrategyDeskPage").then((module) => ({ default: module.StrategyDeskPage }))),
  gtmCommand: React.lazy(() => import("./GtmCommandBoardPage").then((module) => ({ default: module.GtmCommandBoardPage }))),
};

const CONTEXT_REQUIRED = new Set(["my-kol", "kol-pool", "projects", "events"]);

export interface FullBoardModuleXbCardProps {
  apiToken: string;
  onOpenBoard: () => void;
  onNavigate: (navKey: string) => void;
  board?: string;
  boardLabel?: string;
  sourceModuleKey?: string;
  pageProps?: Record<string, Record<string, unknown>>;
}

export function FullBoardModuleXbCard({
  apiToken,
  onOpenBoard,
  onNavigate,
  board = "",
  boardLabel = "源页面",
  sourceModuleKey = "",
  pageProps = {},
}: FullBoardModuleXbCardProps) {
  const PageComponent = PAGE_COMPONENTS[board];
  const sourceProps = pageProps[board];
  const missingContext = CONTEXT_REQUIRED.has(board) && !sourceProps;

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid={`full-board-module-${board}-${sourceModuleKey}`}>
      <div className="flex flex-none justify-end px-2 pb-1">
        <button
          type="button"
          onClick={onOpenBoard}
          className="inline-flex items-center gap-1 rounded-md border border-line bg-card px-2 py-1 text-[9.5px] font-semibold text-muted transition-colors hover:border-accent hover:text-accent"
          title={`打开 ${boardLabel} 源页面`}
        >
          来源 · {boardLabel}
          <ArrowUpRight size={10} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {!PageComponent ? (
          <div className="flex h-full min-h-[120px] items-center justify-center rounded-xl border border-dashed border-line p-4 text-[11px] text-muted">
            源页面模块尚未注册。
          </div>
        ) : missingContext ? (
          <div className="flex h-full min-h-[120px] flex-col items-center justify-center rounded-xl border border-dashed border-line p-4 text-center text-[11px] text-muted">
            <b className="mb-1 text-ink-2">等待源页面上下文</b>
            当前运行入口未提供 {boardLabel} 的页面数据；点击来源页可正常查看，不以空壳冒充真实模块。
          </div>
        ) : (
          <LazyErrorBoundary name={`${boardLabel} 嵌入模块`}>
            <React.Suspense fallback={<div className="p-4 text-[11px] text-muted">正在加载 {boardLabel} 模块…</div>}>
              <PageComponent
                {...(sourceProps || {})}
                apiToken={apiToken}
                onNavigate={onNavigate}
                embeddedModuleKey={sourceModuleKey}
              />
            </React.Suspense>
          </LazyErrorBoundary>
        )}
      </div>
    </div>
  );
}
