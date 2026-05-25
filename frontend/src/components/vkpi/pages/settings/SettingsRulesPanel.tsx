import React from 'react';
import { CardHeader } from '../../shared/CardHeader';
import { InfoBlock } from '../../shared/InfoBlock';
import {
  CommentAlertThresholdCard,
  FeatureFlagsPanel,
  PlatformCrawlPanel,
} from './SettingsControlPanels';

type Row = Record<string, unknown>;
export type SettingsRulesTab = 'core' | 'platform' | 'alerts' | 'sync';

export function SettingsRulesPanel({
  apiToken,
  busy,
  candidateLimitPerStaff,
  commentAlertSettings,
  failureRateThresholdLabel,
  featureFlags,
  platformCrawl,
  rowEnabled,
  rulesTab,
  syncGuardText,
  syncTime,
  syncTimezone,
  onRunMorningSync,
  onRulesTabChange,
  onSaveCommentAlertSettings,
  onToggleFeatureFlag,
  onTogglePlatformCrawl,
}: {
  apiToken?: string;
  busy: boolean;
  candidateLimitPerStaff: string;
  commentAlertSettings: Row;
  failureRateThresholdLabel: string;
  featureFlags: Row[];
  platformCrawl: Row[];
  rowEnabled: (row: Row, key?: string) => boolean;
  rulesTab: SettingsRulesTab;
  syncGuardText: string;
  syncTime: string;
  syncTimezone: string;
  onRunMorningSync: () => void;
  onRulesTabChange: (tab: SettingsRulesTab) => void;
  onSaveCommentAlertSettings: (event: React.FormEvent<HTMLFormElement>) => void;
  onToggleFeatureFlag: (row: Row) => void;
  onTogglePlatformCrawl: (row: Row) => void;
}) {
  return (
    <section className="vkpi-settings-rules">
      <div className="vkpi-settings-tabs">
        {[
          ['core', '核心功能'],
          ['platform', '平台抓取'],
          ['alerts', '告警规则'],
          ['sync', '同步策略'],
        ].map(([key, label]) => (
          <button className={rulesTab === key ? 'is-active' : ''} type="button" key={key} onClick={() => onRulesTabChange(key as SettingsRulesTab)}>{label}</button>
        ))}
      </div>
      {rulesTab === 'core' ? (
        <FeatureFlagsPanel
          featureFlags={featureFlags}
          busy={busy}
          apiToken={apiToken}
          rowEnabled={rowEnabled}
          onRunMorningSync={onRunMorningSync}
          onToggleFeatureFlag={onToggleFeatureFlag}
        />
      ) : null}
      {rulesTab === 'platform' ? (
        <PlatformCrawlPanel
          platformCrawl={platformCrawl}
          busy={busy}
          rowEnabled={rowEnabled}
          onTogglePlatformCrawl={onTogglePlatformCrawl}
        />
      ) : null}
      {rulesTab === 'alerts' ? (
        <CommentAlertThresholdCard
          key={JSON.stringify(commentAlertSettings)}
          settings={commentAlertSettings}
          busy={busy}
          onSave={onSaveCommentAlertSettings}
        />
      ) : null}
      {rulesTab === 'sync' ? (
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="同步策略" />
          <InfoBlock label="每日同步" value={`${syncTime} ${syncTimezone}`} />
          <InfoBlock label="Guard" value={syncGuardText} />
          <InfoBlock label="失败率阈值" value={failureRateThresholdLabel} />
          <InfoBlock label="每人候选" value={`${candidateLimitPerStaff} 条`} />
          <button className="vkpi-button vkpi-button--primary" type="button" disabled={busy || !apiToken} onClick={onRunMorningSync}>手动同步</button>
        </section>
      ) : null}
    </section>
  );
}
