import { apiFetch, jsonBody } from "./http";
import type { BasicStatusResponse, LoginResponse, MeResponse } from "../types/api";

export function login(email: string, password: string) {
  return apiFetch<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: jsonBody({ email, password }),
  });
}

export function logout() {
  return apiFetch<BasicStatusResponse>("/api/auth/logout", {
    method: "POST",
  });
}

export function fetchMe(token: string) {
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
