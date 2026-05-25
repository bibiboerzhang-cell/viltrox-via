import React, { useState } from 'react';
import {
  generateDailyOutreachDigest,
  getDailyOutreachDigestStatus,
  runProductCompare,
  runProductMonitor,
  upsertAnalyticsProduct,
} from '../../../../services/vkpi/product-api';
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
  kolPoolSummary?: Record<string, unknown>;
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
  kolPoolSummary = {},
  onBusyChange,
  onPlatformChange,
  onMessage,
  onRefresh,
}: AnalyticsMonitorPanelProps) {
  const [productA, setProductA] = useState('');
  const [productB, setProductB] = useState('');
  const [monitorSku, setMonitorSku] = useState('');
  const [digestProductSku, setDigestProductSku] = useState('');
  const [digestStatusOverride, setDigestStatusOverride] = useState<Record<string, unknown> | null>(null);
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
      setDigestProductSku(monitorSku.trim());
      setDigestStatusOverride(null);
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
      const productSku = digestProductSku.trim();
      const response = await generateDailyOutreachDigest(apiToken, productSku);
      const generated = safeNumber(response.items_total ?? response.items_per_staff);
      const eligible = safeNumber(response.eligible_staff_count ?? response.staff_count);
      const owned = safeNumber(response.owned_assignment_count);
      const fallback = safeNumber(response.fallback_assignment_count);
      const duplicates = safeNumber(response.duplicate_suggestion_count);
      onMessage(`今日未联系 KOL 清单已生成：${numberFormatter.format(generated)} 条，口径 ${productSku || '全部产品'}，覆盖 ${numberFormatter.format(eligible)} 名符合分发员工；负责人分配 ${numberFormatter.format(owned)}，兜底分配 ${numberFormatter.format(fallback)}，重复 ${numberFormatter.format(duplicates)}。`);
      const refreshedStatus = await getDailyOutreachDigestStatus(apiToken, productSku);
      setDigestStatusOverride(refreshedStatus);
      await onRefresh();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '生成今日清单失败');
    } finally {
      onBusyChange(false);
    }
  };

  const refreshDigestScope = async () => {
    if (!apiToken) return;
    onBusyChange(true);
    try {
      const productSku = digestProductSku.trim();
      const response = await getDailyOutreachDigestStatus(apiToken, productSku);
      setDigestStatusOverride(response);
      onMessage(`Daily Top100 口径已刷新：${productSku || '全部产品'}。`);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '刷新 Daily Top100 口径失败');
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
  const currentDigestStatus = digestStatusOverride || digestStatus;
  const digestStaffCount = safeNumber(currentDigestStatus.eligible_staff_count ?? currentDigestStatus.staff_count);
  const digestActiveStaffCount = safeNumber(currentDigestStatus.active_staff_count);
  const digestExcludedStaffCount = safeNumber(currentDigestStatus.excluded_staff_count);
  const digestGeneratedStaffCount = safeNumber(currentDigestStatus.generated_staff_count ?? currentDigestStatus.ready_staff_count);
  const digestReadyStaffCount = safeNumber(currentDigestStatus.ready_staff_count);
  const digestEmptyStaffCount = safeNumber(currentDigestStatus.empty_staff_count);
  const digestUncontactedCount = safeNumber(currentDigestStatus.uncontacted_count);
  const digestBridgeSeededCount = safeNumber(currentDigestStatus.bridge_seeded_count);
  const digestCandidateSource = String(currentDigestStatus.candidate_source || 'none');
  const digestTotalCandidates = safeNumber(currentDigestStatus.total_candidates ?? currentDigestStatus.candidate_count);
  const digestItemsTotal = safeNumber(currentDigestStatus.items_total ?? currentDigestStatus.item_count);
  const digestDuplicateSuggestionCount = safeNumber(currentDigestStatus.duplicate_suggestion_count);
  const digestAssignmentStrategy = String(currentDigestStatus.assignment_strategy || '未返回');
  const digestOwnedAssignmentCount = safeNumber(currentDigestStatus.owned_assignment_count);
  const digestFallbackAssignmentCount = safeNumber(currentDigestStatus.fallback_assignment_count);
  const digestScheduledTime = String(currentDigestStatus.scheduled_time || '08:00');
  const digestTimezone = String(currentDigestStatus.timezone || 'Asia/Shanghai');
  const digestFeatureEnabled = Boolean(currentDigestStatus.feature_enabled);
  const digestLastGeneratedAt = String(currentDigestStatus.last_generated_at || '未生成');
  const digestProductLabel = digestProductSku.trim() || '全部产品';
  const digestCandidateSourceLabel = digestCandidateSource === 'outreach_suggestions'
    ? '产品监控候选'
    : digestCandidateSource === 'kol_pool_bridge'
      ? 'KOL Pool 桥接'
      : digestCandidateSource === 'none'
        ? '暂无候选源'
        : digestCandidateSource;
  const kolPoolTotal = safeNumber(kolPoolSummary.total);
  const kolHistoricalCount = safeNumber(kolPoolSummary.historical_collaboration_count);
  const kolLinkedCount = safeNumber(kolPoolSummary.linked_main_kol_count);

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
          <div className="vkpi-digest-scope">
            <label>
              <span>候选产品口径</span>
              <input
                value={digestProductSku}
                onChange={(event) => {
                  setDigestProductSku(event.target.value);
                  setDigestStatusOverride(null);
                }}
                placeholder="留空=全部产品；可填 AF-35-55-F1.8-EVO-FE-Z"
              />
            </label>
            <button className="vkpi-mini-button" disabled={busy || !apiToken} type="button" onClick={() => void refreshDigestScope()}>刷新口径</button>
          </div>
          <div className="vkpi-digest-status-grid">
            <InfoBlock label="活跃员工" value={numberFormatter.format(digestActiveStaffCount)} />
            <InfoBlock label="符合分发" value={numberFormatter.format(digestStaffCount)} tone={digestStaffCount > 0 ? 'good' : 'warn'} />
            <InfoBlock label="已生成清单" value={`${numberFormatter.format(digestGeneratedStaffCount)} / ${numberFormatter.format(digestStaffCount)}`} />
            <InfoBlock label="有候选员工" value={`${numberFormatter.format(digestReadyStaffCount)} / ${numberFormatter.format(digestStaffCount)}`} tone={digestReadyStaffCount > 0 ? 'good' : undefined} />
            <InfoBlock label="无候选员工" value={numberFormatter.format(digestEmptyStaffCount)} tone={digestEmptyStaffCount > 0 ? 'warn' : undefined} />
            <InfoBlock label="已排除" value={numberFormatter.format(digestExcludedStaffCount)} />
          </div>
          <div className="vkpi-digest-source-strip">
            <span>口径: <strong>{digestProductLabel}</strong></span>
            <span>候选来源: <strong>{digestCandidateSourceLabel}</strong></span>
            <span>产品级候选: <strong>{numberFormatter.format(digestTotalCandidates)}</strong></span>
            <span>已分发: <strong>{numberFormatter.format(digestItemsTotal)}</strong></span>
            <span>重复分发: <strong className={digestDuplicateSuggestionCount > 0 ? 'is-warn' : ''}>{numberFormatter.format(digestDuplicateSuggestionCount)}</strong></span>
          </div>
          <details className="vkpi-digest-details">
            <summary>查看分发细节</summary>
            <div className="vkpi-result-grid">
              <InfoBlock label="自动同步" value={`${digestScheduledTime} ${digestTimezone}`} />
              <InfoBlock label="开关状态" value={digestFeatureEnabled ? '已开启' : '未开启'} tone={digestFeatureEnabled ? 'good' : 'warn'} />
              <InfoBlock label="未联系候选" value={numberFormatter.format(digestUncontactedCount)} />
              <InfoBlock label="桥接新增" value={numberFormatter.format(digestBridgeSeededCount)} />
              <InfoBlock label="分配策略" value={digestAssignmentStrategy === 'owner_first_then_round_robin' ? '负责人优先 + 兜底轮询' : digestAssignmentStrategy} />
              <InfoBlock label="负责人分配" value={numberFormatter.format(digestOwnedAssignmentCount)} tone={digestOwnedAssignmentCount > 0 ? 'good' : undefined} />
              <InfoBlock label="兜底分配" value={numberFormatter.format(digestFallbackAssignmentCount)} tone={digestFallbackAssignmentCount > 0 ? 'warn' : undefined} />
              <InfoBlock label="上次生成" value={digestLastGeneratedAt} />
            </div>
          </details>
          <p className="vkpi-help-text">活跃员工是启用账号；符合分发已排除测试、系统和不可分发账号；已生成清单才代表今日真正分到候选的人数。</p>
          <button className="vkpi-button vkpi-button--secondary" disabled={busy || !apiToken} type="button" onClick={() => void generateDigest()}>按当前口径生成 Top100</button>
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="监控概览" />
          <InfoBlock label="已配置产品" value={String(productsCount)} />
          <InfoBlock label="待联系建议" value={String(suggestionsCount)} />
          <InfoBlock label="今日清单" value={`${digestCount}/100`} />
          <InfoBlock label="当前平台" value={platformDisplay(platform)} />
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="数据池口径" />
          <InfoBlock label="历史合作名录" value={numberFormatter.format(kolHistoricalCount)} />
          <InfoBlock label="KOL 资产池" value={numberFormatter.format(kolPoolTotal)} />
          <InfoBlock label="Daily 新候选" value={numberFormatter.format(digestUncontactedCount)} />
          <InfoBlock label="已绑定主 KOL" value={numberFormatter.format(kolLinkedCount)} />
          <span className="vkpi-help-text">历史名录来自局部推广表，只补充资产画像；Daily Top100 只读取真实未联系候选。</span>
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
