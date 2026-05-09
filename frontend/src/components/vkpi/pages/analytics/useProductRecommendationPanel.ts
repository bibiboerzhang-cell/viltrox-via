import { useState } from 'react';
import { useProductRecommendationActions } from './useProductRecommendationActions';
import { useRecommendationEvidence } from './useRecommendationEvidence';

type Row = Record<string, unknown>;

interface UseProductRecommendationPanelArgs {
  apiToken?: string;
  platform: string;
  outcomeSummary?: Row;
  onBusyChange: (busy: boolean) => void;
  onMessage: (message: string) => void;
  onRefresh: () => Promise<void>;
  onRecommendationsChange: (rows: Row[]) => void;
}

export function useProductRecommendationPanel({
  apiToken,
  platform,
  outcomeSummary,
  onBusyChange,
  onMessage,
  onRefresh,
  onRecommendationsChange,
}: UseProductRecommendationPanelArgs) {
  const [launchName, setLaunchName] = useState('');
  const [launchSku, setLaunchSku] = useState('');
  const [launchCategory, setLaunchCategory] = useState('');
  const [poolHandle, setPoolHandle] = useState('');
  const [poolFollowers, setPoolFollowers] = useState('');
  const [poolAvgViews, setPoolAvgViews] = useState('');
  const [poolEngagement, setPoolEngagement] = useState('');
  const [poolJson, setPoolJson] = useState('');
  const [selectedLaunchId, setSelectedLaunchId] = useState('');
  const totals = ((outcomeSummary?.totals || {}) as Row);

  const evidence = useRecommendationEvidence({ apiToken, onMessage });
  const actions = useProductRecommendationActions({
    apiToken,
    platform,
    launchName,
    launchSku,
    launchCategory,
    poolHandle,
    poolFollowers,
    poolAvgViews,
    poolEngagement,
    poolJson,
    selectedLaunchId,
    onBusyChange,
    onMessage,
    onRefresh,
    onRecommendationsChange,
    setSelectedLaunchId,
    setLaunchName,
    setLaunchSku,
    setLaunchCategory,
    setPoolHandle,
    setPoolFollowers,
    setPoolAvgViews,
    setPoolEngagement,
    setPoolJson,
    setSelectedRecommendation: evidence.setSelectedRecommendation,
    refreshRecommendationEvidence: evidence.refreshRecommendationEvidence,
  });

  return {
    launchName,
    launchSku,
    launchCategory,
    poolHandle,
    poolFollowers,
    poolAvgViews,
    poolEngagement,
    poolJson,
    selectedLaunchId,
    selectedRecommendation: evidence.selectedRecommendation,
    recommendationEvidence: evidence.recommendationEvidence,
    recommendationEvidenceLoading: evidence.recommendationEvidenceLoading,
    totals,
    setLaunchName,
    setLaunchSku,
    setLaunchCategory,
    setPoolHandle,
    setPoolFollowers,
    setPoolAvgViews,
    setPoolEngagement,
    setPoolJson,
    setSelectedLaunchId,
    setSelectedRecommendation: evidence.setSelectedRecommendation,
    ...actions,
  };
}
