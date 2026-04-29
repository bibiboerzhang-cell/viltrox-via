import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ command }) => {
  const isBuild = command === "build";
  const adminApiTarget = process.env.VITE_ADMIN_API_TARGET || process.env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8102";
  const publicApiTarget = process.env.VITE_PUBLIC_API_TARGET || "http://127.0.0.1:8101";

  return {
    plugins: [react(), tailwindcss()],
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
        "/api/admin": {
          target: adminApiTarget,
          changeOrigin: true,
        },
        "/api/intelligence": {
          target: adminApiTarget,
          changeOrigin: true,
        },
        "/api/vios": {
          target: adminApiTarget,
          changeOrigin: true,
        },
        "/api/verify": {
          target: adminApiTarget,
          changeOrigin: true,
        },
        "/api": {
          target: adminApiTarget,
          changeOrigin: true,
        },
        "/uploads": {
          target: publicApiTarget,
          changeOrigin: true,
        },
        "/frames": {
          target: publicApiTarget,
          changeOrigin: true,
        },
        "/creator_profiles": {
          target: publicApiTarget,
          changeOrigin: true,
        },
        "/r": {
          target: publicApiTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
