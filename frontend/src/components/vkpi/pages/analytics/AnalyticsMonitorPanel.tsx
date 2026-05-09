import React, { useState } from 'react';
import {
  generateDailyOutreachDigest,
  runProductCompare,
  runProductMonitor,
  upsertAnalyticsProduct,
} from '../../../../services/vkpi.ui-api';
import { CardHeader } from '../../shared/CardHeader';
import { InfoBlock } from '../../shared/InfoBlock';
import { creatorPlatformOptions } from '../../shared/vkpiConstants';
import { platformDisplay, safeNumber } from '../../shared/vkpiDataUtils';
import { numberFormatter } from '../../shared/vkpiFormatters';

interface AnalyticsMonitorPanelProps {
  apiToken?: string;
  busy: boolean;
  platform: string;
  productsCount: number;
  suggestionsCount: number;
  digestCount: number;
  digestStatus: Record<string, unknown>;
  onBusyChange: (busy: boolean) => void;
  onPlatformChange: (platform: string) => void;
  onMessage: (message: string) => void;
  onRefresh: () => Promise<void>;
}

export function AnalyticsMonitorPanel({
  apiToken,
  busy,
  platform,
  productsCount,
  suggestionsCount,
  digestCount,
  digestStatus,
  onBusyChange,
  onPlatformChange,
  onMessage,
  onRefresh,
}: AnalyticsMonitorPanelProps) {
  const [productA, setProductA] = useState('');
  const [productB, setProductB] = useState('');
  const [monitorSku, setMonitorSku] = useState('');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  const submitCompare = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken || !productA.trim() || !productB.trim()) return;
    onBusyChange(true);
    try {
      const response = await runProductCompare(apiToken, {
        product_a: productA.trim(),
        product_b: productB.trim(),
        platform,
        max_videos: 20,
      });
      setResult(response);
      onMessage('产品对比已完成。没有真实抓取结果时会显示 provider_status，不会生成假 KOL。');
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '产品对比失败');
    } finally {
      onBusyChange(false);
    }
  };

  const submitMonitor = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken || !monitorSku.trim()) return;
    onBusyChange(true);
    try {
      await upsertAnalyticsProduct(apiToken, {
        product_sku: monitorSku.trim(),
        product_name: monitorSku.trim(),
        platforms: [platform],
      });
      const response = await runProductMonitor(apiToken, {
        product_sku: monitorSku.trim(),
        platform,
        max_videos: 30,
      });
      setResult(response);
      onMessage('产品日监控已执行，新增建议联系会进入下方队列。');
      await onRefresh();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '产品监控失败');
    } finally {
      onBusyChange(false);
    }
  };

  const generateDigest = async () => {
    if (!apiToken) return;
    onBusyChange(true);
    try {
      const response = await generateDailyOutreachDigest(apiToken);
      const digest = (response.digest || {}) as Record<string, unknown>;
      onMessage(`今日未联系 KOL 清单已生成：${String(digest.items_per_staff || 0)} 条/人。`);
      await onRefresh();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '生成今日清单失败');
    } finally {
      onBusyChange(false);
    }
  };

  const resultSummary = (result?.summary || {}) as Record<string, unknown>;
  const resultPayload = (result?.result || {}) as Record<string, unknown>;
  const resultMetadata = (resultPayload.metadata || {}) as Record<string, unknown>;
  const resultOverview = ((resultSummary.overview || resultPayload.overview || {}) as Record<string, unknown>);
  const providerStatus = String(resultSummary.provider_status || resultMetadata.provider_status || resultMetadata.provider_status_a || 'done');
  const providerMessage = String(resultSummary.message || resultPayload.error || '');
  const totalVideos = safeNumber(resultOverview.total_videos);
  const totalViews = safeNumber(resultOverview.total_views);
  const totalLikes = safeNumber(resultOverview.total_likes);
  const totalComments = safeNumber(resultOverview.total_comments);
  const suggestionsCreated = safeNumber(resultSummary.suggestions_created);
  const digestStaffCount = safeNumber(digestStatus.staff_count);
  const digestReadyStaffCount = safeNumber(digestStatus.ready_staff_count);
  const digestUncontactedCount = safeNumber(digestStatus.uncontacted_count);
  const digestScheduledTime = String(digestStatus.scheduled_time || '08:00');
  const digestTimezone = String(digestStatus.timezone || 'Asia/Shanghai');
  const digestFeatureEnabled = Boolean(digestStatus.feature_enabled);
  const digestLastGeneratedAt = String(digestStatus.last_generated_at || '未生成');

  return (
    <>
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="两个产品 VS" />
          <form className="vkpi-form-stack" onSubmit={submitCompare}>
            <input value={productA} onChange={(event) => setProductA(event.target.value)} placeholder="产品 A，例如 Viltrox AF 35mm F1.2" />
            <input value={productB} onChange={(event) => setProductB(event.target.value)} placeholder="产品 B，例如 Sigma 35mm F1.4" />
            <select value={platform} onChange={(event) => onPlatformChange(event.target.value)}>{creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
            <button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken} type="submit">开始对比</button>
          </form>
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="平台日监控" />
          <form className="vkpi-form-stack" onSubmit={submitMonitor}>
            <input value={monitorSku} onChange={(event) => setMonitorSku(event.target.value)} placeholder="我司产品 / SKU / 搜索词" />
            <select value={platform} onChange={(event) => onPlatformChange(event.target.value)}>{creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
            <button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken} type="submit">立即监控</button>
          </form>
          <span className="vkpi-help-text">未配置 Apify / 官方 API 时，后端返回 provider_status，不展示假视频或假粉丝。</span>
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="每日 Top100 候选" />
          <InfoBlock label="自动同步" value={`${digestScheduledTime} ${digestTimezone}`} />
          <InfoBlock label="开关状态" value={digestFeatureEnabled ? '已开启' : '未开启'} tone={digestFeatureEnabled ? 'good' : 'warn'} />
          <InfoBlock label="员工覆盖" value={`${digestReadyStaffCount}/${digestStaffCount}`} />
          <InfoBlock label="未联系候选" value={numberFormatter.format(digestUncontactedCount)} />
          <InfoBlock label="上次生成" value={digestLastGeneratedAt} />
          <button className="vkpi-button vkpi-button--secondary" disabled={busy || !apiToken} type="button" onClick={() => void generateDigest()}>手动生成 Top100</button>
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="监控概览" />
          <InfoBlock label="已配置产品" value={String(productsCount)} />
          <InfoBlock label="待联系建议" value={String(suggestionsCount)} />
          <InfoBlock label="今日清单" value={`${digestCount}/100`} />
          <InfoBlock label="当前平台" value={platformDisplay(platform)} />
        </section>
      </section>
      {result ? (
        <section className="vkpi-card">
          <CardHeader title="最近一次监控结果" />
          <div className="vkpi-result-grid">
            <InfoBlock label="抓取状态" value={providerStatus === 'done' ? '已同步' : providerStatus} tone={providerStatus === 'done' ? 'good' : 'warn'} />
            <InfoBlock label="视频数量" value={totalVideos ? numberFormatter.format(totalVideos) : '待同步 / 暂无'} />
            <InfoBlock label="总播放量" value={totalViews ? numberFormatter.format(totalViews) : '待同步 / 暂无'} />
            <InfoBlock label="总点赞" value={totalLikes ? numberFormatter.format(totalLikes) : '待同步 / 暂无'} />
            <InfoBlock label="总评论" value={totalComments ? numberFormatter.format(totalComments) : '待同步 / 暂无'} />
            <InfoBlock label="新增建议联系" value={numberFormatter.format(suggestionsCreated)} />
          </div>
          {providerMessage ? <p className="vkpi-help-text">{providerMessage}</p> : null}
        </section>
      ) : null}
    </>
  );
}
