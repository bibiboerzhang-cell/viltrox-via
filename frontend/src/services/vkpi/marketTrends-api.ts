import { apiFetch } from "../http";

// N7 市场趋势 / 观察读端点客户端。全只读。
// 后端:GET /api/intelligence/market/trends — market_observation.generate_observations 合成
//   market_brain + competitor_radar + bet_ledger 真数据,best-effort,无真数据诚实返回空。

export type MarketObservationKind = "热点" | "竞品" | "机会" | "风险";
export type MarketObservationConfidence = "high" | "med" | "low";

export interface MarketObservationEvidenceRef {
  type?: string;
  [key: string]: unknown;
}

export interface MarketObservation {
  topic: string;
  kind: MarketObservationKind;
  source: string;
  evidence_refs: MarketObservationEvidenceRef[];
  confidence: MarketObservationConfidence;
  suggested_action: string;
}

export interface MarketTrendsResponse {
  status: string;
  count: number;
  observations: MarketObservation[];
  sources_used: string[];
  by_kind: Record<string, number>;
  note: string;
  generated_at: string;
}

export async function getMarketTrends(
  token: string,
  kind?: MarketObservationKind,
): Promise<MarketTrendsResponse> {
  const qs = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return apiFetch<MarketTrendsResponse>(
    `/api/intelligence/market/trends${qs}`,
    { cache: "no-store", timeoutMs: 8000 },
    token,
  );
}
