import { useMutation, useQuery } from "@tanstack/react-query";

import { apiClient } from "../client";
import type { SurfaceKey } from "../../lib/contracts.generated";

interface ViaSessionResponse {
  session?: {
    session_key?: string;
  };
  persona?: {
    display_name?: string;
    outfit_code?: string;
  };
}

export function useViaSession(surface: SurfaceKey = "upload") {
  return useMutation({
    mutationFn: () =>
      apiClient<ViaSessionResponse>("/api/via/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ surface }),
      }),
  });
}

export function useViaStockWatch() {
  return useQuery({
    queryKey: ["via", "stock-watch"],
    queryFn: () => apiClient<{ items?: Array<Record<string, unknown>> }>("/api/via/stock-watch"),
  });
}
