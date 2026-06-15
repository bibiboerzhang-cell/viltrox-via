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
            // NOTE: most vkpi route pages are already React.lazy()-split (see WorkspacePage.tsx),
            // so they get their own async chunks automatically. We only name a few stable groups
            // here to keep historical chunk boundaries; do NOT add page-dir rules that would force
            // lazy route modules to merge back into eager shared chunks.
            if (id.includes("/src/components/vkpi/pages/myKol/")) return "vkpi-my-kol";
            if (id.includes("/src/components/vkpi/pages/projects/")) return "vkpi-projects";
            if (id.includes("/src/components/vkpi/v615-replica/")) return "vkpi-v615";
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
