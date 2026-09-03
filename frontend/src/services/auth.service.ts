import { apiFetch, jsonBody } from "./http";
import type { BasicStatusResponse, LoginResponse, MeResponse } from "../types/api";

/** S-02:`session=cookie` 让后端只回 user、不回 token;JWT 只经 HttpOnly Set-Cookie 下发。 */
export const LOGIN_PATH_COOKIE_SESSION = "/api/auth/login?session=cookie";

export function login(email: string, password: string) {
  return apiFetch<LoginResponse>(LOGIN_PATH_COOKIE_SESSION, {
    method: "POST",
    body: jsonBody({ email, password }),
  });
}

/** 登出 = 服务端吊销该用户全部既有令牌(token_version +1)+ 清 cookie。 */
export function logout() {
  return apiFetch<BasicStatusResponse>("/api/auth/logout", {
    method: "POST",
  });
}

export function fetchMe(token?: string) {
  return apiFetch<MeResponse>("/api/auth/me", {}, token);
}

export function uploadMyAvatar(token: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<LoginResponse>("/api/auth/me/avatar", {
    method: "POST",
    body: form,
  }, token);
}

export function updateMyProfile(token: string, name: string) {
  return apiFetch<LoginResponse>("/api/auth/me/profile", {
    method: "POST",
    body: jsonBody({ name }),
  }, token);
}
