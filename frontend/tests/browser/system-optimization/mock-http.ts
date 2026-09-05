import { deny } from "./fixture-state";
export type { AuthUser, BasicStatusResponse, LoginResponse, MeResponse } from "../../../src/lib/api";

export const API_BASE = "";
export class ApiResponseError extends Error { status = 403; payload: unknown = null; }
export function jsonBody(payload: unknown): string { return JSON.stringify(payload); }
export function buildApiUrl(_path: string): never { return deny("buildApiUrl"); }
export async function apiFetch<T>(_path: string, _options?: unknown, _token?: string): Promise<T> {
  return deny("apiFetch (no HTTP endpoints allowed)");
}
