import { apiFetch, jsonBody } from "./http";
import type { BasicStatusResponse, LoginResponse, MeResponse, RegisterResponse } from "../types/api";

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

export function register(name: string, email: string, password: string, studentId = "") {
  return apiFetch<RegisterResponse>("/api/auth/register", {
    method: "POST",
    body: jsonBody({ name, email, password, student_id: studentId.trim() }),
  });
}

export function fetchMe(token: string) {
  return apiFetch<MeResponse>("/api/auth/me", {}, token);
}

export function resendVerification(email: string) {
  return apiFetch<BasicStatusResponse>("/api/auth/resend-verification", {
    method: "POST",
    body: jsonBody({ email }),
  });
}

export function forgotPassword(email: string) {
  return apiFetch<BasicStatusResponse>("/api/auth/forgot-password", {
    method: "POST",
    body: jsonBody({ email }),
  });
}

export function changePassword(token: string, currentPassword: string, newPassword: string) {
  return apiFetch<BasicStatusResponse>(
    "/api/auth/change-password",
    {
      method: "POST",
      body: jsonBody({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    },
    token,
  );
}
