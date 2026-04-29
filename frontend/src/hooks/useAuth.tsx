import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import type { AuthUser } from "../types/api";
import { fetchMe, login, logout } from "../services/auth.service";
import { useViaStore } from "../stores/useViaStore";

type AuthStatus = "loading" | "guest" | "authenticated";
export type AuthModalMode = "signin" | "register" | "recovery";

interface AuthContextValue {
  status: AuthStatus;
  token: string;
  user: AuthUser | null;
  isAuthModalOpen: boolean;
  authModalMode: AuthModalMode;
  signIn: (email: string, password: string) => Promise<AuthUser>;
  acceptSession: (nextToken: string, nextUser: AuthUser) => void;
  signOut: () => Promise<void>;
  refreshUser: () => Promise<void>;
  openAuthModal: (mode?: AuthModalMode) => void;
  closeAuthModal: () => void;
}

const TOKEN_KEY = "via_token_v2";
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string>(() => {
    if (typeof window === "undefined") {
      return "";
    }
    return window.localStorage.getItem(TOKEN_KEY) ?? "";
  });
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>(token ? "loading" : "guest");
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false);
  const [authModalMode, setAuthModalMode] = useState<AuthModalMode>("signin");

  function clearLocalSession() {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(TOKEN_KEY);
    }
    useViaStore.getState().clearRuntimeState();
    setToken("");
    setUser(null);
    setStatus("guest");
  }

  async function loadUser(activeToken: string): Promise<void> {
    try {
      const response = await fetchMe(activeToken);
      if (response.status !== "success" || !response.user) {
        throw new Error(response.message ?? "Not authenticated");
      }
      setUser(response.user);
      setStatus("authenticated");
    } catch {
      clearLocalSession();
    }
  }

  useEffect(() => {
    if (!token) {
      setStatus("guest");
      setUser(null);
      return;
    }
    void loadUser(token);
  }, [token]);

  async function signIn(email: string, password: string): Promise<AuthUser> {
    const response = await login(email, password);

    if (response.status !== "success" || !response.token || !response.user) {
      throw new Error(response.message ?? "Login failed");
    }

    useViaStore.getState().clearRuntimeState();
    if (typeof window !== "undefined") {
      window.localStorage.setItem(TOKEN_KEY, response.token);
    }
    setToken(response.token);
    setUser(response.user);
    setStatus("authenticated");
    return response.user;
  }

  function acceptSession(nextToken: string, nextUser: AuthUser) {
    useViaStore.getState().clearRuntimeState();
    if (typeof window !== "undefined") {
      window.localStorage.setItem(TOKEN_KEY, nextToken);
    }
    setToken(nextToken);
    setUser(nextUser);
    setStatus("authenticated");
  }

  async function signOut() {
    clearLocalSession();
    try {
      await logout();
    } catch {
      // Clearing local state is the important part; cookie cleanup is best effort.
    }
  }

  async function refreshUser() {
    if (!token) {
      setStatus("guest");
      return;
    }
    await loadUser(token);
  }

  function openAuthModal(mode: AuthModalMode = "signin") {
    setAuthModalMode(mode);
    setIsAuthModalOpen(true);
  }

  function closeAuthModal() {
    setIsAuthModalOpen(false);
  }

  return (
    <AuthContext.Provider
      value={{
        status,
        token,
        user,
        isAuthModalOpen,
        authModalMode,
        signIn,
        acceptSession,
        signOut,
        refreshUser,
        openAuthModal,
        closeAuthModal,
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
