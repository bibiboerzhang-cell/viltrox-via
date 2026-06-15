import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// W5: 前端测试从 0 起。jsdom 环境 + globals;只跑 src 下的 *.test.ts(x),不碰 build。
export default defineConfig({
  plugins: [react()],
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
