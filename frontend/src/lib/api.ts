export interface AuthUser {
  id: number;
  email: string;
  name: string;
  role?: string;
  avatar_url?: string;
  staff_id?: number;
  employee_code?: string;
  avatar_required?: boolean;
  permissions?: Record<string, "none" | "read" | "write" | string>;
  is_owner?: boolean;
}

export interface LoginResponse {
  status: string;
  message?: string;
  token?: string;
  user?: AuthUser;
}

export interface BasicStatusResponse {
  status: string;
  message?: string;
}

export interface MeResponse {
  status: string;
  message?: string;
  user?: AuthUser;
}


const env = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env ?? {};
const configuredBase = String(env.VITE_API_BASE ?? "").trim().replace(/\/+$/, "");

export const API_BASE = configuredBase;

export function buildApiUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return API_BASE ? `${API_BASE}${normalized}` : normalized;
}

export function jsonBody(payload: unknown): string {
  return JSON.stringify(payload);
}

const DEFAULT_API_TIMEOUT_MS = 20000;

export async function apiFetch<T>(
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
  token?: string,
): Promise<T> {
  const { timeoutMs = DEFAULT_API_TIMEOUT_MS, signal: externalSignal, ...requestInit } = init;
  const headers = new Headers(init.headers ?? {});
  const isFormData = typeof FormData !== "undefined" && requestInit.body instanceof FormData;
  if (!isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (!headers.has("X-Requested-With")) {
    headers.set("X-Requested-With", "XMLHttpRequest");
  }

  const controller = new AbortController();
  const abortFromExternal = () => {
    controller.abort(externalSignal?.reason ?? new DOMException("Request aborted", "AbortError"));
  };
  const timeoutId = globalThis.setTimeout(() => {
    controller.abort(new DOMException("Request timed out", "TimeoutError"));
  }, timeoutMs);
  if (externalSignal) {
    if (externalSignal.aborted) {
      abortFromExternal();
    } else {
      externalSignal.addEventListener("abort", abortFromExternal, { once: true });
    }
  }

  let response: Response;
  try {
    response = await fetch(buildApiUrl(path), {
      ...requestInit,
      credentials: requestInit.credentials ?? (API_BASE ? "include" : "same-origin"),
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) {
      const reason = controller.signal.reason;
      const isTimeout =
        reason instanceof DOMException
          ? reason.name === "TimeoutError"
          : String((reason as { name?: string } | undefined)?.name || "").toLowerCase() === "timeouterror";
      throw new Error(isTimeout ? `请求超时：${timeoutMs}ms` : "请求已取消");
    }
    throw error instanceof Error ? error : new Error("网络请求失败");
  } finally {
    globalThis.clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", abortFromExternal);
  }

  const raw = await response.text();
  let parsed: unknown = {};
  if (raw) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = raw;
    }
  }

  if (!response.ok) {
    const message =
      typeof parsed === "object" && parsed && "detail" in parsed
        ? String((parsed as { detail?: string }).detail)
        : typeof parsed === "object" && parsed && "message" in parsed
          ? String((parsed as { message?: string }).message)
          : `${response.status} ${response.statusText}`;
    throw new Error(message);
  }

  return parsed as T;
}
