import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const configDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(configDir, "..");

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
    ],
    define: {
      __VKPI_BUILD_INFO__: JSON.stringify(buildInfo),
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
            if (
              id.includes("/src/services/") ||
              id.includes("/src/domains/") ||
              id.includes("/src/components/vkpi/cockpit/data/countryInfo")
            ) {
              return "app-data";
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
            // ③ cockpit 部件层(原主 chunk 近半体量):components/ 全目录 + 它运行时 import 的
            //    4 个 vkpi/shared 叶子件(不带走它们会形成 主chunk↔widgets 双向边 = 成环)。
            //    豁免三件重货(ReportPanel / TaskProgressBoard / SmartKolInputPanel 家族):
            //    它们只被装配区(CockpitApp*/KOLPoolPage/CockpitSidebar)import,widgets 内部零引用,
            //    留给 rollup 自动归位(跟随装配 chunk)—— 既控 widgets 体量,又不可能成环。
            if (
              id.includes("/src/components/vkpi/cockpit/components/ReportPanel") ||
              id.includes("/src/components/vkpi/cockpit/components/TaskProgressBoard") ||
              id.includes("/src/components/vkpi/cockpit/components/SmartKolInputPanel")
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
