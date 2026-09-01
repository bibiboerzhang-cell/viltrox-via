import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// W5: 前端测试从 0 起。jsdom 环境 + globals;只跑 src 下的 *.test.ts(x),不碰 build。
export default defineConfig({
  plugins: [react()],
  // Vite resolves the default `localhost` host with DNS even when Vitest runs
  // in middleware mode. Candidate verification deliberately denies every
  // network operation, including DNS, so use the numeric loopback address.
  // This does not open a listener; Vitest's default API stays middleware-only.
  server: {
    host: "127.0.0.1",
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    css: false,
    coverage: {
      provider: "v8",
      reportsDirectory: "./coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.{test,spec}.{ts,tsx}", "src/test/**", "src/**/*.d.ts"],
    },
  },
});
