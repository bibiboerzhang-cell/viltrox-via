import { useState } from 'react';
import { RecommendationCandidateTable } from './RecommendationCandidateTable';
import { RecommendationDetailDrawer } from './RecommendationDetailDrawer';
import { RecommendationOutcomeTable } from './RecommendationOutcomeTable';
import { RecommendationRunReviewPanel } from './RecommendationRunReviewPanel';
import { RecommendationSetupForms } from './RecommendationSetupForms';
import { useProductRecommendationPanel } from './useProductRecommendationPanel';

type Row = Record<string, unknown>;

interface ProductRecommendationPanelProps {
  apiToken?: string;
  busy: boolean;
  platform: string;
  launches: Row[];
  recommendations: Row[];
  outcomeSummary?: Row;
  onBusyChange: (busy: boolean) => void;
  onPlatformChange: (platform: string) => void;
  onMessage: (message: string) => void;
  onRefresh: () => Promise<void>;
  onRecommendationsChange: (rows: Row[]) => void;
}

export function ProductRecommendationPanel({
  apiToken,
  busy,
  platform,
  launches,
  recommendations,
  outcomeSummary,
  onBusyChange,
  onPlatformChange,
  onMessage,
  onRefresh,
  onRecommendationsChange,
}: ProductRecommendationPanelProps) {
  const panel = useProductRecommendationPanel({
    apiToken,
    platform,
    outcomeSummary,
    onBusyChange,
    onMessage,
    onRefresh,
    onRecommendationsChange,
  });
  const [activePreviewRun, setActivePreviewRun] = useState<Row | null>(null);

  const runRecommendations = async () => {
    setActivePreviewRun(null);
    await panel.runRecommendations();
  };

  return (
    <>
      <RecommendationSetupForms
        apiToken={apiToken}
        busy={busy}
        platform={platform}
        launches={launches}
        recommendations={recommendations}
        totals={panel.totals}
        launchName={panel.launchName}
        launchSku={panel.launchSku}
        launchCategory={panel.launchCategory}
        poolHandle={panel.poolHandle}
        poolFollowers={panel.poolFollowers}
        poolAvgViews={panel.poolAvgViews}
        poolEngagement={panel.poolEngagement}
        poolJson={panel.poolJson}
        selectedLaunchId={panel.selectedLaunchId}
        onPlatformChange={onPlatformChange}
        onLaunchNameChange={panel.setLaunchName}
        onLaunchSkuChange={panel.setLaunchSku}
        onLaunchCategoryChange={panel.setLaunchCategory}
        onPoolHandleChange={panel.setPoolHandle}
        onPoolFollowersChange={panel.setPoolFollowers}
        onPoolAvgViewsChange={panel.setPoolAvgViews}
        onPoolEngagementChange={panel.setPoolEngagement}
        onPoolJsonChange={panel.setPoolJson}
        onSelectedLaunchChange={panel.setSelectedLaunchId}
        onSubmitLaunch={panel.submitLaunch}
        onImportPoolItem={panel.importPoolItem}
        onImportPoolJson={panel.importPoolJson}
        onRunRecommendations={runRecommendations}
      />
      <RecommendationRunReviewPanel
        apiToken={apiToken}
        busy={busy}
        onBusyChange={onBusyChange}
        onMessage={onMessage}
        onRecommendationsChange={onRecommendationsChange}
        onRunLoaded={setActivePreviewRun}
      />
      <RecommendationOutcomeTable outcomeSummary={outcomeSummary} />
      <RecommendationCandidateTable
        busy={busy}
        recommendations={recommendations}
        readOnly={Boolean(activePreviewRun)}
        onSelect={panel.setSelectedRecommendation}
        onAction={panel.updateRecommendation}
      />
      {panel.selectedRecommendation ? (
        <RecommendationDetailDrawer
          recommendation={panel.selectedRecommendation}
          evidence={panel.recommendationEvidence}
          loading={panel.recommendationEvidenceLoading}
          onClose={() => panel.setSelectedRecommendation(null)}
        />
      ) : null}
    </>
  );
}
