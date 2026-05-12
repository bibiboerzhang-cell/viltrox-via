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
  const gitSha = process.env.VITE_APP_GIT_SHA || gitValue("rev-parse HEAD") || "unknown";
  const buildInfo = {
    version: packageVersion(),
    gitSha,
    gitShortSha: gitSha === "unknown" ? "unknown" : gitSha.slice(0, 8),
    gitBranch: process.env.VITE_APP_GIT_BRANCH || gitValue("rev-parse --abbrev-ref HEAD") || "unknown",
    builtAt: process.env.VITE_APP_BUILD_TIME || new Date().toISOString(),
  };

  return {
    plugins: [react()],
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
        },
      },
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
        "/uploads": {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
