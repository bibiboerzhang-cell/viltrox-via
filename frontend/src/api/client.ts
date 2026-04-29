import { apiFetch } from "../lib/api";

export async function apiClient<T>(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers || {});
  if (headers.has("Authorization")) {
    return apiFetch<T>(path, init);
  }
  let token: string | undefined;
  if (typeof window !== "undefined") {
    const storedToken = window.localStorage.getItem("via_token_v2") ?? "";
    if (storedToken) {
      token = storedToken;
    }
  }
  return apiFetch<T>(path, init, token);
}
