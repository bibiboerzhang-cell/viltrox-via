import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./App";
import { AppProviders } from "./app/providers/AppProviders";
import "./styles/global.css";

// 部署后旧 chunk 被新版替换 → 页面里残留的动态 import() 拉到 404
// ("Importing a module script failed" / "Loading chunk failed" 等)。
// 自动刷新一次拿新版,免得用户卡在「视频读取失败 · 请刷新」。10s 内只刷一次,防死循环。
function recoverFromStaleChunk(reason: unknown): void {
  const msg = String((reason as { message?: string } | undefined)?.message || reason || "");
  if (!/Loading chunk|Importing a module script failed|Failed to fetch dynamically imported module|error loading dynamically imported module|dynamically imported module/i.test(msg)) {
    return;
  }
  try {
    const KEY = "vkpi_chunk_reload_at";
    const last = Number(window.sessionStorage.getItem(KEY) || 0);
    if (Date.now() - last < 10000) return;
    window.sessionStorage.setItem(KEY, String(Date.now()));
  } catch {
    /* sessionStorage 不可用时仍刷一次 */
  }
  window.location.reload();
}
window.addEventListener("vite:preloadError", (event) => {
  event.preventDefault();
  recoverFromStaleChunk((event as unknown as { payload?: unknown }).payload);
});
window.addEventListener("unhandledrejection", (event) => recoverFromStaleChunk(event.reason));

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <AppProviders>
      <App />
    </AppProviders>
  </React.StrictMode>,
);
