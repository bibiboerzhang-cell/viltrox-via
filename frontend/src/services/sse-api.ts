import { API_BASE, apiFetch, jsonBody } from "./http";

export interface SseTicketReady {
  status: "ready";
  expires_in: number;
}

export function sseEndpointPath(streamUrl: string): string {
  const base = typeof window !== "undefined" ? window.location.origin : "http://localhost";
  const parsed = new URL(streamUrl, base);
  const apiBasePath = API_BASE ? new URL(API_BASE, base).pathname.replace(/\/$/, "") : "";
  if (apiBasePath && parsed.pathname.startsWith(`${apiBasePath}/`)) {
    return parsed.pathname.slice(apiBasePath.length);
  }
  return parsed.pathname;
}

/**
 * Authorize one EventSource connection. The server places the opaque ticket in
 * a short-lived HttpOnly cookie; no JWT or ticket is returned to JavaScript.
 */
export async function prepareSseStream(streamUrl: string, token?: string): Promise<string> {
  await apiFetch<SseTicketReady>(
    "/api/auth/sse-ticket",
    {
      method: "POST",
      body: jsonBody({ endpoint: sseEndpointPath(streamUrl) }),
      timeoutMs: 5000,
    },
    token,
  );
  return streamUrl;
}
