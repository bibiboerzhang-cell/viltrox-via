import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ command }) => {
  const isBuild = command === "build";

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
        "/api": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
        "/uploads": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
        "/frames": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
        "/creator_profiles": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
        "/r": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
