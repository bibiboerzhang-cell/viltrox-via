import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const configDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(configDir, "..");
const robotsPolicy = "noindex, nofollow, noarchive, nosnippet, noimageindex";

function gitValue(args: string): string {
  try {
    return execSync(`git ${args}`, { cwd: projectRoot, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return "";
  }
}

function packageVersion(): string {
  try {
    const parsed = JSON.parse(readFileSync(resolve(configDir, "package.json"), "utf8")) as { version?: string };
    return parsed.version || "0.0.0";
  } catch {
    return "0.0.0";
  }
}

export default defineConfig(({ command }) => {
  const isBuild = command === "build";
  const apiTarget = process.env.VITE_API_PROXY_TARGET || process.env.VITE_ADMIN_API_TARGET || "http://127.0.0.1:8102";
  const normalizeProxyOrigin = (proxyReq: { setHeader: (name: string, value: string) => void }) => {
    proxyReq.setHeader("Origin", apiTarget);
    proxyReq.setHeader("Referer", `${apiTarget}/`);
  };
  const gitSha = process.env.VITE_APP_GIT_SHA || gitValue("rev-parse HEAD") || "unknown";
  const buildInfo = {
    version: packageVersion(),
    gitSha,
    gitShortSha: gitSha === "unknown" ? "unknown" : gitSha.slice(0, 8),
    gitBranch: process.env.VITE_APP_GIT_BRANCH || gitValue("rev-parse --abbrev-ref HEAD") || "unknown",
    builtAt: process.env.VITE_APP_BUILD_TIME || new Date().toISOString(),
  };

  return {
    plugins: [
      react(),
      {
        name: "vkpi-build-info",
        generateBundle() {
          this.emitFile({
            type: "asset",
            fileName: "build-info.json",
            source: `${JSON.stringify(buildInfo, null, 2)}\n`,
          });
        },
      },
      {
        name: "vkpi-private-dev-surface",
        configureServer(server) {
          server.middlewares.use((request, response, next) => {
            response.setHeader("X-Robots-Tag", robotsPolicy);
            const pathname = String(request.url || "").split("?", 1)[0];
            if (
              pathname === "/docs" ||
              pathname === "/redoc" ||
              pathname === "/openapi.json" ||
              pathname === "/assets/__private_surface_probe_missing__.js"
            ) {
              response.statusCode = 404;
              response.setHeader("Content-Type", "application/json; charset=utf-8");
              response.end('{"detail":"Not Found"}');
              return;
            }
            next();
          });
        },
      },
    ],
    define: {
      __VKPI_BUILD_INFO__: JSON.stringify(buildInfo),
      "process.env.NODE_ENV": JSON.stringify(isBuild ? "production" : "development"),
    },
    esbuild: isBuild
      ? {
          drop: ["console", "debugger"],
        }
      : undefined,
    build: {
      sourcemap: false,
      cssMinify: true,
      reportCompressedSize: false,
      rollupOptions: {
        output: {
          entryFileNames: "assets/app-[hash].js",
          chunkFileNames: "assets/chunk-[hash].js",
          assetFileNames: "assets/asset-[hash][extname]",
          manualChunks(id) {
            // Vite 的动态 import 预加载 helper 必须留在首屏共享层；若 Rollup 将它
            // 吸入某个异步业务块，入口会反向静态 import 该业务块并把其重依赖一并
            // 预加载，等于悄悄击穿懒加载边界。
            if (id.includes("vite/preload-helper")) return "vendor";
            if (id.includes("node_modules")) {
              // Keep the React runtime in one cohesive chunk (react + react-dom + scheduler)
              // so it cannot be split across chunks in a way that breaks the runtime.
              if (
                id.includes("/node_modules/react-dom/") ||
                id.includes("/node_modules/react/") ||
                id.includes("/node_modules/scheduler/")
              ) {
                return "vendor-react";
              }
              if (id.includes("react-router") || id.includes("@remix-run/router")) return "vendor-router";
              if (id.includes("framer-motion") || id.includes("motion-dom") || id.includes("motion-utils")) return "vendor-motion";
              if (id.includes("lucide-react")) return "vendor-icons";
              if (id.includes("recharts")) return "vendor-charts";
              if (id.includes("@tanstack")) return "vendor-query";
              // Heavy 3D / map / geo libs are only used on a few pages — isolate them
              // so they no longer inflate the shared vendor chunk past the 500 kB warning.
              if (id.includes("/node_modules/three/")) return "vendor-three";
              if (id.includes("/node_modules/leaflet/")) return "vendor-leaflet";
              if (id.includes("d3-geo") || id.includes("topojson-client") || id.includes("d3-array")) return "vendor-geo";
              return "vendor";
            }
            // 红线(R3 修):绝不给 src 页面目录加 manualChunks 规则 —— 这些是 React.lazy() 异步路由模块,
            // 强行塞进具名 chunk 会打乱 lazy chunk 加载图 → 运行时 RouteErrorBoundary「页面加载失败」
            // (build 静态不报、tsc/vitest 抓不到)。页面(路由)模块永远交给 lazy 自动分。
            //
            // F2 分包(2026-07-07):cockpit 装配主 chunk 曾达 ~1.15MB → 按「共享层」拆,不按页面拆。
            // 以下规则只圈非路由的共享层,import 方向已逐边核对成 DAG:
            //   主chunk → vkpi-cockpit-widgets → vkpi-cockpit-core / vkpi-foundation → app-data → vendor-*
            // 若新增规则造成 chunk 间静态 import 成环 = R3 级运行时炸(TDZ「页面加载失败」)。
            // 护栏:scripts/check_chunk_graph.py 在 verify STEP 3 里对 dist 做「无环 + <600KB」硬校验。
            //
            // ① 数据/领域层:services + domains。已核对:对 components 只有 import type(打包后零回边);
            //    含少量 import() 数据预取目标(kolPool-api / domains/kol 等)—— 不是 React.lazy 路由模块,安全。
            //    countryInfo 是零依赖纯数据叶子,但被 domains/dashboard/geo.ts 运行时引用 ——
            //    留在 cockpit-core 会形成 core↔app-data 双向边(check_chunk_graph 实测抓到),归位到这层。
            // These review-integrity leaves are consumed only by lazy review
            // surfaces. Let Rollup keep them with those dynamic paths instead
            // of charging the always-loaded app-data chunk.
            if (
              id.includes("/src/services/vkpi/action-review-candidate.ts") ||
              id.includes("/src/services/vkpi/outreach-reply-candidate.ts") ||
              id.includes("/src/services/vkpi/outreach-truth-api.ts") ||
              id.includes("/src/services/vkpi/prediction-ledger-api.ts") ||
              id.includes("/src/services/vkpi/review-integrity.ts")
            ) {
              return undefined;
            }
            if (
              id.includes("/src/services/") ||
              id.includes("/src/domains/") ||
              id.includes("/src/components/vkpi/cockpit/data/countryInfo")
            ) {
              return "app-data";
            }
            // 默认界面是中文；完整英文词典只在恢复英文偏好或打开语言入口时按需加载。
            // 必须先于 cockpit/data 通用规则命中，否则 manual chunk 会把动态词典重新塞回首屏。
            if (id.includes("/src/components/vkpi/cockpit/data/i18nEn.ts")) {
              return "vkpi-i18n-en";
            }
            // ② cockpit 基础件层:被 widgets 与主 chunk 共用的工具/图标映射/api 桥(仅依赖 ①/vendor)。
            if (
              id.includes("/src/components/vkpi/cockpit/lib/") ||
              id.includes("/src/components/vkpi/cockpit/data/") ||
              id.includes("/src/components/vkpi/cockpit/api.ts") ||
              id.includes("/src/components/vkpi/cockpit/useWorkflowRunsStream.ts")
            ) {
              return "vkpi-cockpit-core";
            }
            // KOL 视频深析渲染器 + 纯 helper 同时被搜索结果和档案抽屉复用，
            // 而且只向下依赖 services/shared/vendor，不回引任何上层 KOL UI。单独归组
            // 可给 workbench 的单 chunk 红线留出稳定余量，同时保持两条按需路径复用同一份实现。
            if (
              id.includes("/src/components/vkpi/cockpit/components/KOLVideoAnalysisPanel.tsx") ||
              id.includes("/src/components/vkpi/cockpit/components/KOLDetailDrawer.helpers.ts") ||
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel.helpers.ts") ||
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel.cachePrivacy.ts") ||
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel.evidence.ts") ||
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel.presentation.ts") ||
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel.derivers.ts") ||
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel.progress-derivers.ts")
            ) {
              return "vkpi-kol-analysis-core";
            }
            // 详情抽屉只在用户点开某个 KOL 后需要。它与找达人首屏共用少量
            // analysis-core 纯函数，但自身面板很多；单独保留为异步块，避免空闲时
            // 把整套抽屉代码计入 KOL Pool 首屏。
            if (
              id.includes("/src/components/vkpi/cockpit/components/KOLDetailDrawer") ||
              id.includes("/src/components/vkpi/cockpit/components/KOLDrawer") ||
              id.includes("/src/components/vkpi/cockpit/components/useKOLDrawerViewerContext")
            ) {
              return "vkpi-kol-detail";
            }
            // 评论正文只在已解析的视频 evidence 上读取，账号分析总览只在 profile
            // URL 命中入库 KOL 后出现。两者都通过 React.lazy 按需加载；让 Rollup
            // 保留真实异步边界，避免重新聚合进所有 KOL 搜索/档案共用的 workbench。
            if (
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel.UrlSummary.Comments.tsx") ||
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel.UrlSummary.AccountOverview.tsx")
            ) {
              return undefined;
            }
            // KOL 搜索与档案抽屉共同复用视频深析渲染原子，属于同一套按需工作台。
            // 两个家族放在同一块可保留这条复用边，同时避免整套 KOL 深析 UI 被塞进
            // 所有 cockpit 页面共享的 widgets chunk；KolSearchHistoryPanel 一并归组，
            // 防止其对 SmartKolInputPanel.Sections 的复用产生 widgets -> workbench 回边。
            if (
              id.includes("/src/components/vkpi/cockpit/components/KOLVideoAnalysisPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/KolSearchHistoryPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/MarketCoverageCard") ||
              id.includes("/src/components/vkpi/cockpit/components/KOLTable") ||
              id.includes("/src/components/vkpi/cockpit/components/KolRecommendationCards") ||
              id.includes("/src/components/vkpi/cockpit/components/FilterBar") ||
              id.includes("/src/components/vkpi/cockpit/components/SearchProgressBar") ||
              id.includes("/src/components/vkpi/cockpit/components/KPAvatar") ||
              id.includes("/src/components/vkpi/cockpit/components/DeviceSummary") ||
              id.includes("/src/components/vkpi/cockpit/components/RefreshStateStripe") ||
              id.includes("/src/components/vkpi/cockpit/components/TrendDot") ||
              id.includes("/src/components/vkpi/cockpit/components/V6FitBar") ||
              id.includes("/src/components/vkpi/cockpit/components/KPIBar") ||
              id.includes("/src/components/vkpi/cockpit/components/modals/ContactModal") ||
              id.includes("/src/components/vkpi/cockpit/components/modals/KolPoolAllModal") ||
              id.includes("/src/components/vkpi/cockpit/components/AudienceTypeChip") ||
              id.includes("/src/components/vkpi/cockpit/components/CandidateKindChip") ||
              id.includes("/src/components/vkpi/cockpit/components/GeoTierChip") ||
              id.includes("/src/components/vkpi/cockpit/components/PlatformPill") ||
              id.includes("/src/components/vkpi/cockpit/components/SignaturePanel") ||
              id.includes("/src/components/vkpi/cockpit/components/AudienceGeoPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/CommerceSignalsPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/FocalMatrixPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/QualityCompliancePanel") ||
              id.includes("/src/components/vkpi/cockpit/components/SimilarVideosPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/ForecastPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/RateCardPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/OutreachCriticSignalCard") ||
              id.includes("/src/components/vkpi/cockpit/components/SafetyAuthenticityPanel")
            ) {
              return "vkpi-kol-workbench";
            }
            // ③ cockpit 部件层(原主 chunk 近半体量):components/ 全目录 + 它运行时 import 的
            //    4 个 vkpi/shared 叶子件(不带走它们会形成 主chunk↔widgets 双向边 = 成环)。
            // Leaflet 只在地图真正出现时需要；RealMap 自身也必须和它一起延后，
            // 否则任一首屏可视化会把整套地图运行时拉进初始依赖闭包。
            if (id.includes("/src/components/vkpi/cockpit/components/RealMap.tsx")) {
              return "vkpi-map";
            }
            // 纯叶子可视化层:仅依赖 React/vendor/services/core,不回引其他 cockpit 组件。
            // 先于 components/ 总规则归组,为 600KB 红线保留稳定增长余量。
            if (
              id.includes("/src/components/vkpi/cockpit/components/ui/") ||
              id.includes("/src/components/vkpi/cockpit/components/viz/") ||
              id.includes("/src/components/vkpi/cockpit/components/provenance/") ||
              id.includes("/src/components/vkpi/cockpit/components/AnimatedNumber.tsx") ||
              id.includes("/src/components/vkpi/cockpit/components/Globe.tsx") ||
              id.includes("/src/components/vkpi/cockpit/components/NorthStarGauges.tsx") ||
              id.includes("/src/components/vkpi/cockpit/components/StrategySimPanel.tsx") ||
              id.includes("/src/components/vkpi/cockpit/components/VerdictPanel.tsx") ||
              id.includes("/src/components/vkpi/cockpit/components/MissReviewPanel.tsx") ||
              id.includes("/src/components/vkpi/cockpit/components/ShadowEvalPanel.tsx")
            ) {
              return "vkpi-cockpit-viz";
            }
            //    豁免三件重货(ReportPanel / TaskProgressBoard / SmartKolInputPanel 家族):
            //    它们只被装配区(CockpitApp*/KOLPoolPage/CockpitSidebar)import,widgets 内部零引用,
            //    留给 rollup 自动归位(跟随装配 chunk)—— 既控 widgets 体量,又不可能成环。
            if (
              id.includes("/src/components/vkpi/cockpit/components/ReportPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/TaskProgressBoard") ||
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/modals/AITodayEvidenceModal") ||
              id.includes("/src/components/vkpi/cockpit/components/modals/EditGroupModal") ||
              id.includes("/src/components/vkpi/cockpit/components/modals/EventDetailModal") ||
              id.includes("/src/components/vkpi/cockpit/components/modals/KPIDetailModal") ||
              id.includes("/src/components/vkpi/cockpit/components/modals/ProjectDetailModal") ||
              id.includes("/src/components/vkpi/cockpit/components/modals/SignalDetailModal") ||
              id.includes("/src/components/vkpi/cockpit/components/modals/TeamModal") ||
              id.includes("/src/components/vkpi/cockpit/components/BrandPulsePanel") ||
              id.includes("/src/components/vkpi/cockpit/components/CommentOpportunitiesPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/GiftedFunnelPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/MorningBriefCard") ||
              id.includes("/src/components/vkpi/cockpit/components/SemanticRecallCard") ||
              id.includes("/src/components/vkpi/cockpit/components/WorkerDevicesPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/AIIntelligenceCard") ||
              // These panels each belong to one lazy board family. Keeping them
              // out of the always-shared widgets chunk lets Rollup attach them to
              // the owning route rather than charging every initial navigation.
              id.includes("/src/components/vkpi/cockpit/components/ProjectTimeline") ||
              id.includes("/src/components/vkpi/cockpit/components/ActionResultReviewQueue") ||
              id.includes("/src/components/vkpi/cockpit/components/OutreachTruthReviewQueue") ||
              id.includes("/src/components/vkpi/cockpit/components/PredictionLedgerPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/WeeklyScorecardPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/AgentLoopPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/DealerFitPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/OfficialPlannerPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/IndieSitePanel") ||
              id.includes("/src/components/vkpi/cockpit/components/ChannelMixPanel")
            ) {
              return undefined;
            }
            if (
              id.includes("/src/components/vkpi/cockpit/components/") ||
              id.includes("/src/components/vkpi/shared/GoaffproLinkSection") ||
              id.includes("/src/components/vkpi/shared/ShareModal") ||
              id.includes("/src/components/vkpi/shared/ShareKolModal") ||
              id.includes("/src/components/vkpi/shared/mediaProxy")
            ) {
              return "vkpi-cockpit-widgets";
            }
            // ④ vkpi 基础层:i18n/format/图标数据/api 桥 —— 零向上依赖的叶子层。
            if (
              id.includes("/src/components/vkpi/lib/") ||
              id.includes("/src/components/vkpi/data/") ||
              id.includes("/src/components/vkpi/api.ts")
            ) {
              return "vkpi-foundation";
            }
          },
        },
      },
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: {
        "/health": {
          target: apiTarget,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on("proxyReq", normalizeProxyOrigin);
          },
        },
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on("proxyReq", normalizeProxyOrigin);
          },
        },
        "/uploads": {
          target: apiTarget,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on("proxyReq", normalizeProxyOrigin);
          },
        },
      },
    },
  };
});
