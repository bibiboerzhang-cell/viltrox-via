// 全局会话失效(401)处理的纯工具层(U-B3,2026-09-02)。
//   · lib/api.ts 在任何带 token 的请求拿到 401 时调用 notifyAuthExpired() 广播一次;
//   · AuthProvider(hooks/useAuth.tsx)监听 AUTH_EXPIRED_EVENT:清本地会话 → 记一次性提示 → 跳登录页并带回跳;
//   · AdminLoginRoute 读 ?next= 回跳 + consumeSessionExpiredNotice() 显示一次「登录已失效」。
// 这里不 import 任何 React / api 模块,保证 lib/api.ts ← authSession 无环。

export const AUTH_EXPIRED_EVENT = "vkpi:auth-expired";
export const SESSION_EXPIRED_NOTICE_KEY = "vkpi:session-expired-notice";
export const LOGIN_PATH = "/login";
export const NEXT_QUERY_KEY = "next";

export interface AuthExpiredDetail {
  path: string;
  status: number;
}

let authExpiredNotified = false;

/** 只广播第一次 401(同一会话里几十张卡同时失败只弹一次);登录成功后 resetAuthExpiredNotice() 复位。 */
export function notifyAuthExpired(detail: AuthExpiredDetail): boolean {
  if (authExpiredNotified) return false;
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return false;
  authExpiredNotified = true;
  window.dispatchEvent(new CustomEvent<AuthExpiredDetail>(AUTH_EXPIRED_EVENT, { detail }));
  return true;
}

export function resetAuthExpiredNotice(): void {
  authExpiredNotified = false;
}

/** 登录/登出自身的 401 是「密码错/已登出」,不是会话过期。 */
export function isCredentialEndpoint(path: string): boolean {
  const pathname = String(path || "").split("?")[0].replace(/\/+$/, "");
  return /\/api\/auth\/(login|logout)$/.test(pathname);
}

/** 只接受站内相对路径(防开放重定向);登录页自身不回跳。 */
export function sanitizeNextPath(raw: string | null | undefined): string {
  const value = String(raw ?? "").trim();
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  if (value === LOGIN_PATH || value.startsWith(`${LOGIN_PATH}?`) || value.startsWith(`${LOGIN_PATH}/`)) return "/";
  return value;
}

export function isLoginPath(pathname: string): boolean {
  return String(pathname || "").replace(/\/+$/, "") === LOGIN_PATH;
}

export function buildLoginRedirect(nextPath: string): string {
  const next = sanitizeNextPath(nextPath);
  if (next === "/") return LOGIN_PATH;
  return `${LOGIN_PATH}?${NEXT_QUERY_KEY}=${encodeURIComponent(next)}`;
}

export function currentLocationPath(): string {
  if (typeof window === "undefined") return "/";
  const { pathname, search, hash } = window.location;
  return `${pathname || "/"}${search || ""}${hash || ""}`;
}

export function markSessionExpiredNotice(): void {
  try {
    window.sessionStorage.setItem(SESSION_EXPIRED_NOTICE_KEY, "1");
  } catch {
    // sessionStorage 不可用(隐私模式/被禁)时放弃提示,不影响跳转。
  }
}

/** 读一次即清:提示只在回到登录页的第一帧出现一次。 */
export function consumeSessionExpiredNotice(): boolean {
  try {
    const flagged = window.sessionStorage.getItem(SESSION_EXPIRED_NOTICE_KEY) === "1";
    if (flagged) window.sessionStorage.removeItem(SESSION_EXPIRED_NOTICE_KEY);
    return flagged;
  } catch {
    return false;
  }
}
