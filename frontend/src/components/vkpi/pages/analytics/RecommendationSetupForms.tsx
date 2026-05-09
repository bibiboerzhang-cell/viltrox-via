import React from 'react';
import { CardHeader } from '../../shared/CardHeader';
import { InfoBlock } from '../../shared/InfoBlock';
import { creatorPlatformOptions } from '../../shared/vkpiConstants';
import { safeNumber } from '../../shared/vkpiDataUtils';

type Row = Record<string, unknown>;

interface RecommendationSetupFormsProps {
  apiToken?: string;
  busy: boolean;
  platform: string;
  launches: Row[];
  recommendations: Row[];
  totals: Row;
  launchName: string;
  launchSku: string;
  launchCategory: string;
  poolHandle: string;
  poolFollowers: string;
  poolAvgViews: string;
  poolEngagement: string;
  poolJson: string;
  selectedLaunchId: string;
  onPlatformChange: (platform: string) => void;
  onLaunchNameChange: (value: string) => void;
  onLaunchSkuChange: (value: string) => void;
  onLaunchCategoryChange: (value: string) => void;
  onPoolHandleChange: (value: string) => void;
  onPoolFollowersChange: (value: string) => void;
  onPoolAvgViewsChange: (value: string) => void;
  onPoolEngagementChange: (value: string) => void;
  onPoolJsonChange: (value: string) => void;
  onSelectedLaunchChange: (value: string) => void;
  onSubmitLaunch: (event: React.FormEvent) => void;
  onImportPoolItem: (event: React.FormEvent) => void;
  onImportPoolJson: () => void | Promise<void>;
  onRunRecommendations: () => void | Promise<void>;
}

export function RecommendationSetupForms({
  apiToken,
  busy,
  platform,
  launches,
  recommendations,
  totals,
  launchName,
  launchSku,
  launchCategory,
  poolHandle,
  poolFollowers,
  poolAvgViews,
  poolEngagement,
  poolJson,
  selectedLaunchId,
  onPlatformChange,
  onLaunchNameChange,
  onLaunchSkuChange,
  onLaunchCategoryChange,
  onPoolHandleChange,
  onPoolFollowersChange,
  onPoolAvgViewsChange,
  onPoolEngagementChange,
  onPoolJsonChange,
  onSelectedLaunchChange,
  onSubmitLaunch,
  onImportPoolItem,
  onImportPoolJson,
  onRunRecommendations,
}: RecommendationSetupFormsProps) {
  return (
    <section className="vkpi-card-grid vkpi-card-grid--forms">
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="产品发布 / 推荐项目" />
        <form className="vkpi-form-stack" onSubmit={onSubmitLaunch}>
          <input value={launchName} onChange={(event) => onLaunchNameChange(event.target.value)} placeholder="发布项目名，例如 E2E 35mm F1.2 Launch" />
          <input value={launchSku} onChange={(event) => onLaunchSkuChange(event.target.value)} placeholder="SKU，例如 E2E-35-F12" />
          <input value={launchCategory} onChange={(event) => onLaunchCategoryChange(event.target.value)} placeholder="品类，例如 E-mount APS-C Lens" />
          <button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken} type="submit">创建发布项目</button>
        </form>
        <span className="vkpi-help-text">先冻结发布 brief，再生成推荐和 outcome 标签。</span>
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="导入 KOL 池" />
        <form className="vkpi-form-stack" onSubmit={onImportPoolItem}>
          <select value={platform} onChange={(event) => onPlatformChange(event.target.value)}>{creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
          <input value={poolHandle} onChange={(event) => onPoolHandleChange(event.target.value)} placeholder="@handle / channel id" />
          <input value={poolFollowers} onChange={(event) => onPoolFollowersChange(event.target.value)} placeholder="粉丝数（可选，真实数据）" inputMode="numeric" />
          <input value={poolAvgViews} onChange={(event) => onPoolAvgViewsChange(event.target.value)} placeholder="平均播放（可选，真实数据）" inputMode="numeric" />
          <input value={poolEngagement} onChange={(event) => onPoolEngagementChange(event.target.value)} placeholder="互动率（可选，例如 0.035）" inputMode="decimal" />
          <button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken} type="submit">导入 KOL</button>
        </form>
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="历史数据 JSON / Apify 导入" />
        <textarea
          className="vkpi-textarea"
          value={poolJson}
          onChange={(event) => onPoolJsonChange(event.target.value)}
          placeholder='粘贴 Apify dataset JSON 数组，例如 [{"platform":"youtube","handle":"creator","followers":12000}]'
          rows={7}
        />
        <button className="vkpi-button vkpi-button--secondary" disabled={busy || !apiToken || !poolJson.trim()} type="button" onClick={() => void onImportPoolJson()}>导入历史数据</button>
        <span className="vkpi-help-text">只导入你给的真实 JSON；不会访问外部 API，也不会补假头像。</span>
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="运行推荐" />
        <div className="vkpi-form-stack">
          <select value={selectedLaunchId} onChange={(event) => onSelectedLaunchChange(event.target.value)}>
            <option value="">选择发布项目</option>
            {launches.map((row) => <option key={String(row.id)} value={String(row.id)}>{String(row.name || row.product_name || row.id)}</option>)}
          </select>
          <select value={platform} onChange={(event) => onPlatformChange(event.target.value)}>{creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
          <button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken || !selectedLaunchId} type="button" onClick={() => void onRunRecommendations()}>生成推荐</button>
        </div>
        <div className="vkpi-result-grid">
          <InfoBlock label="发布项目" value={String(launches.length)} />
          <InfoBlock label="推荐候选" value={String(recommendations.length)} />
          <InfoBlock label="评分策略" value="rule_v0 / 可灰度 ML" />
          <InfoBlock label="已建项目" value={String(safeNumber(totals.project_created))} />
          <InfoBlock label="已发布内容" value={String(safeNumber(totals.content_published))} />
          <InfoBlock label="推荐出单" value={String(safeNumber(totals.order_attributed))} />
        </div>
      </section>
    </section>
  );
}
