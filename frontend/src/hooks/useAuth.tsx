import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import type { AuthUser } from "../types/api";
import { clearApiCache } from "../lib/apiCache";
import {
  COOKIE_SESSION_TOKEN,
  purgeLegacyTokenStorage,
  setCookieSessionActive,
} from "../lib/authCookieSession";
import {
  AUTH_EXPIRED_EVENT,
  buildLoginRedirect,
  currentLocationPath,
  isLoginPath,
  markSessionExpiredNotice,
  resetAuthExpiredNotice,
} from "../lib/authSession";
import { fetchMe, login, logout } from "../services/auth.service";

type AuthStatus = "loading" | "guest" | "authenticated";

interface AuthContextValue {
  status: AuthStatus;
  /**
   * S-02:JS 不再持有 JWT。已登录时这是占位值 COOKIE_SESSION_TOKEN(给沿用 apiToken prop 的组件),
   * 凭证本体只在 HttpOnly cookie 里;未登录为空串。
   */
  token: string;
  user: AuthUser | null;
  signIn: (email: string, password: string) => Promise<AuthUser>;
  signOut: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function defaultSessionExpiredRedirect(loginUrl: string): void {
  // 整页跳转:顺带终止所有轮询/在飞请求,登录页从干净状态起。
  window.location.replace(loginUrl);
}

interface AuthProviderProps {
  children: ReactNode;
  /** 会话失效后的跳转实现(默认 window.location.replace);测试注入用。 */
  onSessionExpiredRedirect?: (loginUrl: string) => void;
}

export function AuthProvider({ children, onSessionExpiredRedirect }: AuthProviderProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  // 登录态由 cookie 决定,首帧一律先问 /me(不再有 localStorage 可读)。
  const [status, setStatus] = useState<AuthStatus>("loading");
  const token = status === "authenticated" ? COOKIE_SESSION_TOKEN : "";

  function clearLocalSession() {
    purgeLegacyTokenStorage();
    setCookieSessionActive(false);
    clearApiCache();
    setUser(null);
    setStatus("guest");
  }

  function acceptUser(nextUser: AuthUser) {
    setCookieSessionActive(true);
    setUser(nextUser);
    setStatus("authenticated");
  }

  async function loadUser(): Promise<void> {
    try {
      const response = await fetchMe(COOKIE_SESSION_TOKEN);
      if (response.status !== "success" || !response.user) {
        throw new Error(response.message ?? "Not authenticated");
      }
      acceptUser(response.user);
    } catch {
      clearLocalSession();
    }
  }

  useEffect(() => {
    // 旧版本落盘的 JWT 一律清掉;然后凭 cookie 恢复会话。
    purgeLegacyTokenStorage();
    void loadUser();
    // loadUser 只依赖稳定的 setState,首帧跑一次即可。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // U-B3:lib/api 在任何带 token 的请求拿到 401 时广播一次 → 这里统一清会话、记一次性提示、
  // 跳登录页并带回跳(/login?next=当前地址)。已在登录页则只清会话,不重复跳。
  useEffect(() => {
    const redirect = onSessionExpiredRedirect ?? defaultSessionExpiredRedirect;
    const handleExpired = () => {
      clearLocalSession();
      if (isLoginPath(window.location.pathname)) return;
      markSessionExpiredNotice();
      redirect(buildLoginRedirect(currentLocationPath()));
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, handleExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleExpired);
    // clearLocalSession 只依赖稳定的 setState,不需要进依赖数组。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onSessionExpiredRedirect]);

  async function signIn(email: string, password: string): Promise<AuthUser> {
    const response = await login(email, password);

    // 后端(?session=cookie)只回 user,JWT 走 Set-Cookie;即便响应里有 token 也不落任何存储。
    if (response.status !== "success" || !response.user) {
      throw new Error(response.message ?? "Login failed");
    }

    resetAuthExpiredNotice();
    acceptUser(response.user);
    return response.user;
  }

  async function signOut() {
    clearLocalSession();
    try {
      // 服务端吊销(token_version +1)+ 清 cookie;本地状态已先清,失败也不影响退出。
      await logout();
    } catch {
      // Clearing local state is the important part; server-side revocation is best effort here.
    }
  }

  async function refreshUser() {
    await loadUser();
  }

  return (
    <AuthContext.Provider
      value={{
        status,
        token,
        user,
        signIn,
        signOut,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
