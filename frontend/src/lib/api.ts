export interface AuthUser {
  id: number;
  email: string;
  name: string;
  role?: string;
  auth_role?: string;
  staff_role?: string;
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

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function apiErrorMessage(payload: unknown, status: number, statusText: string): string {
  const root = recordValue(payload);
  const detail = root.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  const detailRecord = recordValue(detail);
  if (typeof detailRecord.reason === "string" && detailRecord.reason.trim()) return detailRecord.reason;
  if (typeof detailRecord.message === "string" && detailRecord.message.trim()) return detailRecord.message;
  if (typeof detailRecord.mode === "string" && detailRecord.mode.trim()) return detailRecord.mode;
  if (typeof root.message === "string" && root.message.trim()) return root.message;
  return `${status} ${statusText}`;
}

export class ApiResponseError extends Error {
  status: number;
  statusText: string;
  payload: unknown;
  detail: unknown;

  constructor(response: Response, payload: unknown) {
    super(apiErrorMessage(payload, response.status, response.statusText));
    this.name = "ApiResponseError";
    this.status = response.status;
    this.statusText = response.statusText;
    this.payload = payload;
    this.detail = recordValue(payload).detail;
  }
}

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
    throw new ApiResponseError(response, parsed);
  }

  return parsed as T;
}
