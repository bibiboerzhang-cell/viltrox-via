import { useEffect, useState } from 'react';
import { getProductRecommendationEvidence } from '../../../../domains/products';

type Row = Record<string, unknown>;

interface UseRecommendationEvidenceArgs {
  apiToken?: string;
  onMessage: (message: string) => void;
}

export function useRecommendationEvidence({ apiToken, onMessage }: UseRecommendationEvidenceArgs) {
  const [selectedRecommendation, setSelectedRecommendation] = useState<Row | null>(null);
  const [recommendationEvidence, setRecommendationEvidence] = useState<Row | null>(null);
  const [recommendationEvidenceLoading, setRecommendationEvidenceLoading] = useState(false);

  const refreshRecommendationEvidence = async (recommendationId: unknown) => {
    if (!apiToken || !recommendationId) return;
    setRecommendationEvidenceLoading(true);
    try {
      const response = await getProductRecommendationEvidence(apiToken, String(recommendationId));
      setRecommendationEvidence(response as Row);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '推荐证据读取失败');
    } finally {
      setRecommendationEvidenceLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const recommendationId = selectedRecommendation?.id;
    setRecommendationEvidence(null);
    if (!apiToken || !recommendationId) return () => {
      cancelled = true;
    };
    setRecommendationEvidenceLoading(true);
    getProductRecommendationEvidence(apiToken, String(recommendationId))
      .then((response) => {
        if (!cancelled) setRecommendationEvidence(response as Row);
      })
      .catch((error) => {
        if (!cancelled) onMessage(error instanceof Error ? error.message : '推荐证据读取失败');
      })
      .finally(() => {
        if (!cancelled) setRecommendationEvidenceLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, selectedRecommendation?.id, onMessage]);

  return {
    selectedRecommendation,
    recommendationEvidence,
    recommendationEvidenceLoading,
    setSelectedRecommendation,
    refreshRecommendationEvidence,
  };
}
