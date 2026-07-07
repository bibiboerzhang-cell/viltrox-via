// browserAssist/enable.ts — 本地协助的开关(deploy dark:默认关,显式开启)。
//
// 默认关闭,避免在员工不知情时占用他们的浏览器算力。开启方式(任一):
//   - localStorage 里 vkpi_browser_assist = "1"(可在设置页做个开关翻它);
//   - 构建期 VITE_BROWSER_ASSIST = "1"(全员默认开)。
// 上线后可先只给自己或试点员工开,验证观感与负载,再决定是否全员默认开。

export { useBrowserAssist } from "./useBrowserAssist";
export type { BrowserAssistStatus } from "./useBrowserAssist";

const LS_KEY = "vkpi_browser_assist";

export function isBrowserAssistEnabled(): boolean {
  try {
    if (localStorage.getItem(LS_KEY) === "1") return true;
  } catch {
    // localStorage 不可用忽略,继续看构建期开关。
  }
  const buildFlag = (import.meta as unknown as { env?: Record<string, string> }).env
    ?.VITE_BROWSER_ASSIST;
  return String(buildFlag || "") === "1";
}
