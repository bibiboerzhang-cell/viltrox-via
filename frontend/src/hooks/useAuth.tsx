import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import type { AuthUser } from "../types/api";
import { fetchMe, login, logout } from "../services/auth.service";

type AuthStatus = "loading" | "guest" | "authenticated";

interface AuthContextValue {
  status: AuthStatus;
  token: string;
  user: AuthUser | null;
  signIn: (email: string, password: string) => Promise<AuthUser>;
  signOut: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const TOKEN_KEY = "viltrox_marketing_token_v1";
const AuthContext = createContext<AuthContextValue | null>(null);

function readInitialToken(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return window.localStorage.getItem(TOKEN_KEY) ?? "";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string>(readInitialToken);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>(token ? "loading" : "guest");

  function clearLocalSession() {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(TOKEN_KEY);
    }
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

    if (typeof window !== "undefined") {
      window.localStorage.setItem(TOKEN_KEY, response.token);
    }
    setToken(response.token);
    setUser(response.user);
    setStatus("authenticated");
    return response.user;
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
