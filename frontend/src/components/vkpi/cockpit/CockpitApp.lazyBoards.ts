import React from "react";

// Keep every cockpit page behind the same lazy boundary so the shell does not
// pull board implementations into the first paint bundle.
export const KOLPoolPage = React.lazy(() => import("./pages/KolPoolBoardPage").then((module) => ({ default: module.KolPoolBoardPage })));
export const ShopifyBoardPage = React.lazy(() => import("./pages/ShopifyBoardPage").then((module) => ({ default: module.ShopifyBoardPage })));
export const DealerMapPage = React.lazy(() => import("./pages/DealersBoardPage").then((module) => ({ default: module.DealersBoardPage })));
export const MyKolBoardPage = React.lazy(() => import("./pages/MyKolBoardPage").then((module) => ({ default: module.MyKolBoardPage })));
export const LegacyProjectsPage = React.lazy(() => import("./pages/ProjectsBoardPage").then((module) => ({ default: module.ProjectsBoardPage })));
export const EventsMockupPage = React.lazy(() => import("./pages/EventsBoardPage").then((module) => ({ default: module.EventsBoardPage })));
export const DataQualityPage = React.lazy(() => import("../pages/DataQualityPage").then((module) => ({ default: module.DataQualityPage })));
export const DataQueryPage = React.lazy(() => import("../pages/DataQueryPage").then((module) => ({ default: module.DataQueryPage })));
export const MarketTrendsPage = React.lazy(() => import("../pages/MarketTrendsPage").then((module) => ({ default: module.MarketTrendsPage })));
export const SkillStudioPage = React.lazy(() => import("../pages/SkillStudioPage").then((module) => ({ default: module.SkillStudioPage })));
export const IntelligentPage = React.lazy(() => import("./pages/IntelligentBoardPage").then((module) => ({ default: module.IntelligentBoardPage })));
export const ReplyQueuePage = React.lazy(() => import("./pages/ReplyQueueBoardPage").then((module) => ({ default: module.ReplyQueueBoardPage })));
export const Sku360Page = React.lazy(() => import("./pages/Sku360BoardPage").then((module) => ({ default: module.Sku360BoardPage })));
export const KolProfilePage = React.lazy(() => import("./pages/KolProfileBoardPage").then((module) => ({ default: module.KolProfileBoardPage })));
export const LaunchPadPage = React.lazy(() => import("./pages/LaunchPadBoardPage").then((module) => ({ default: module.LaunchPadBoardPage })));
export const AutonomyBoardPage = React.lazy(() => import("./pages/AutonomyDrivePage").then((module) => ({ default: module.AutonomyDrivePage })));
export const MarketVoicePage = React.lazy(() => import("./pages/MarketVoicePage").then((module) => ({ default: module.MarketVoicePage })));
export const CreativeLibraryPage = React.lazy(() => import("./pages/CreativeLibraryBoardPage").then((module) => ({ default: module.CreativeLibraryBoardPage })));
export const StrategyBoardPage = React.lazy(() => import("./pages/StrategyDeskPage").then((module) => ({ default: module.StrategyDeskPage })));
export const GtmCommandPage = React.lazy(() => import("./pages/GtmCommandBoardPage").then((module) => ({ default: module.GtmCommandBoardPage })));

export const COCKPIT_BOARDS = [
  "dashboard", "kol-pool", "my-kol", "projects", "events", "shopify", "dealers",
  "triage", "dataQuery", "marketTrends", "skillStudio", "intelligent", "replyQueue",
  "sku360", "kolProfile", "launchpad", "autonomy", "marketVoice", "creativeLibrary", "strategyBoard", "gtmCommand",
] as const;
