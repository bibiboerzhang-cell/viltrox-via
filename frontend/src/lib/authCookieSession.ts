// 登录态只存在 HttpOnly cookie 里(S-02,2026-09-02):JS 从不持有 JWT,也不再落 localStorage。
//   · 后端 /api/auth/login?session=cookie 只回 user 不回 token;cookie 由 Set-Cookie 下发(HttpOnly+SameSite=Lax)。
//   · 全站沿用的 apiToken prop 改为占位值 COOKIE_SESSION_TOKEN:它不是凭证,只是让 700+ 处
//     `if (!apiToken)` 门禁照常放行;后端把 `Authorization: Bearer cookie-session` 当作「走 cookie」
//     处理(与 backend/app/core/security.COOKIE_SESSION_MARKER 同值,改一处必改另一处)。
//   · 旧版本落在 localStorage 的 JWT 在首次加载 / 清会话时清掉(purgeLegacyTokenStorage)。
// 这里不 import React / api 模块,保证 lib/api.ts ← authCookieSession 无环。

export const COOKIE_SESSION_TOKEN = "cookie-session";

/** 历史上把 JWT 落盘的 localStorage 键;只用于清理,永不再写。 */
export const LEGACY_TOKEN_STORAGE_KEYS: readonly string[] = ["viltrox_marketing_token_v1"];

let cookieSessionActive = false;

/** AuthProvider 在 /me 成功 / 登录成功后置 true,清会话时置 false。 */
export function setCookieSessionActive(active: boolean): void {
  cookieSessionActive = Boolean(active);
}

export function isCookieSessionActive(): boolean {
  return cookieSessionActive;
}

/**
 * 纯展示组件(不吃 apiToken prop 的顶栏搜索 / 进度中心)读「当前会话 token」的唯一入口:
 * 已登录 → 占位值;未登录 → 空串(调用方据此跳过请求)。永不触碰 localStorage。
 */
export function readSessionToken(): string {
  return cookieSessionActive ? COOKIE_SESSION_TOKEN : "";
}

/** 清掉旧版本落盘的 JWT;返回实际删掉的键(测试断言用)。storage 不可用时静默。 */
export function purgeLegacyTokenStorage(): string[] {
  if (typeof window === "undefined") return [];
  const removed: string[] = [];
  for (const key of LEGACY_TOKEN_STORAGE_KEYS) {
    try {
      if (window.localStorage.getItem(key) !== null) {
        window.localStorage.removeItem(key);
        removed.push(key);
      }
    } catch {
      // localStorage 被禁 / 隐私模式:没有可清的东西,也不影响登录。
    }
  }
  return removed;
}
