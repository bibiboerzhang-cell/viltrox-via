import React, { useCallback, useEffect, useState } from 'react';
import { getAmazonAttributionSummary, listAmazonAttributions, runShopifyBackfill, runShopifySync } from '../../../domains/attribution';
import type { VkpiDashboardData, VkpiMetricEvidenceKey } from '../vkpiTypes';
import { AmazonAttributionLivePanel } from '../panels/AmazonAttributionLivePanel';
import { CardHeader } from '../shared/CardHeader';
import { ProjectSelect } from '../shared/ProjectSelect';
import { AttributionTable } from '../tables/AttributionTable';
import { PageShell } from './PageShell';

type AuthorizationEvidence = {
  authorizationRef: string;
  reason: string;
  confirmedByHuman: boolean;
};

interface AttributionPageProps {
  data: VkpiDashboardData;
  viewMode: 'manager' | 'employee';
  apiToken?: string;
  onOpenEvidence: (metric: VkpiMetricEvidenceKey, metricValueId?: number | null) => void;
  onCreateAttribution?: (payload: { sourcePlatform: 'shopify' | 'amazon' | 'manual' | 'custom'; sourceRef: string; projectId?: string; linkId?: string; productSku?: string; orderId?: string; revenueUsd: number; commissionUsd?: number; confidence?: string; occurredAt?: string; authorizationEvidence?: AuthorizationEvidence }) => Promise<void>;
  onImportAmazonRows?: (payload: { projectId?: string; amazonTag?: string; asin?: string; marketplace?: string; reportDate?: string; rows: Array<Record<string, unknown>>; authorizationEvidence?: AuthorizationEvidence }) => Promise<void>;
  onUploadAmazonReport?: (payload: { projectId?: string; amazonTag?: string; asin?: string; marketplace?: string; reportDate?: string; file: File; authorizationEvidence?: AuthorizationEvidence }) => Promise<void>;
}

export function AttributionPage({ data, viewMode, apiToken, onOpenEvidence, onCreateAttribution, onImportAmazonRows, onUploadAmazonReport }: AttributionPageProps) {
  const [shopifyProjectId, setShopifyProjectId] = useState('');
  const [shopifySourceRef, setShopifySourceRef] = useState('');
  const [shopifyOrderId, setShopifyOrderId] = useState('');
  const [shopifyRevenue, setShopifyRevenue] = useState('');
  const [amazonProjectId, setAmazonProjectId] = useState('');
  const [amazonTag, setAmazonTag] = useState('');
  const [amazonAsin, setAmazonAsin] = useState('');
  const [amazonMarketplace, setAmazonMarketplace] = useState('US');
  const [amazonReportDate, setAmazonReportDate] = useState('');
  const [amazonRowsText, setAmazonRowsText] = useState('');
  const [amazonFile, setAmazonFile] = useState<File | null>(null);
  const [amazonSummary, setAmazonSummary] = useState<{ items?: Array<Record<string, unknown>>; totals?: Record<string, unknown> } | null>(null);
  const [amazonAttributionRows, setAmazonAttributionRows] = useState<Array<Record<string, unknown>>>([]);
  const [amazonLoading, setAmazonLoading] = useState(false);
  const [amazonError, setAmazonError] = useState('');
  const [shopifySyncMessage, setShopifySyncMessage] = useState('');
  const [authorizationRef, setAuthorizationRef] = useState('');
  const [authorizationReason, setAuthorizationReason] = useState('');
  const [authorizationConfirmed, setAuthorizationConfirmed] = useState(false);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const metricSourceCount = (metricKey: string) => data.metrics.find((metric) => metric.key === metricKey)?.sourceCount || 0;
  const authorizationReady = viewMode === 'manager'
    && Boolean(authorizationRef.trim() && authorizationReason.trim() && authorizationConfirmed);
  const authorizationEvidence: AuthorizationEvidence = {
    authorizationRef: authorizationRef.trim(),
    reason: authorizationReason.trim(),
    confirmedByHuman: authorizationConfirmed,
  };

  const loadAmazonData = useCallback(async () => {
    if (!apiToken) return;
    setAmazonLoading(true);
    setAmazonError('');
    try {
      const [summary, rows] = await Promise.all([
        getAmazonAttributionSummary(apiToken, { limit: 50 }),
        listAmazonAttributions(apiToken, { limit: 50 }),
      ]);
      setAmazonSummary(summary);
      setAmazonAttributionRows(rows.attributions || []);
    } catch (error) {
      setAmazonError(error instanceof Error ? error.message : 'Amazon 归因读取失败');
    } finally {
      setAmazonLoading(false);
    }
  }, [apiToken]);

  useEffect(() => {
    void loadAmazonData();
  }, [loadAmazonData]);

  const submitShopify = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onCreateAttribution || !shopifySourceRef.trim()) return;
    setBusy(true);
    try {
      await onCreateAttribution({
        sourcePlatform: 'shopify',
        sourceRef: shopifySourceRef.trim(),
        projectId: shopifyProjectId || undefined,
        orderId: shopifyOrderId || undefined,
        revenueUsd: Number(shopifyRevenue || 0),
        confidence: 'pending',
        authorizationEvidence,
      });
      setMessage('Shopify 人工记录已保存为待核验，不计入 GMV。');
      setShopifySourceRef('');
      setShopifyOrderId('');
      setShopifyRevenue('');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Shopify 归因补录失败');
    } finally {
      setBusy(false);
    }
  };

  const submitAmazonRows = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onImportAmazonRows) return;
    setBusy(true);
    try {
      const parsed = JSON.parse(amazonRowsText || '[]');
      if (!Array.isArray(parsed)) throw new Error('Amazon JSON 必须是数组');
      await onImportAmazonRows({
        projectId: amazonProjectId || undefined,
        amazonTag: amazonTag || undefined,
        asin: amazonAsin || undefined,
        marketplace: amazonMarketplace || 'US',
        reportDate: amazonReportDate || undefined,
        rows: parsed as Array<Record<string, unknown>>,
        authorizationEvidence,
      });
      await loadAmazonData();
      setMessage('Amazon JSON 报表已导入。');
      setAmazonRowsText('');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Amazon JSON 导入失败');
    } finally {
      setBusy(false);
    }
  };

  const submitAmazonFile = async () => {
    if (!onUploadAmazonReport || !amazonFile) return;
    setBusy(true);
    try {
      await onUploadAmazonReport({
        projectId: amazonProjectId || undefined,
        amazonTag: amazonTag || undefined,
        asin: amazonAsin || undefined,
        marketplace: amazonMarketplace || 'US',
        reportDate: amazonReportDate || undefined,
        file: amazonFile,
        authorizationEvidence,
      });
      await loadAmazonData();
      setMessage('Amazon CSV/XLSX 报表已上传导入。');
      setAmazonFile(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Amazon 文件导入失败');
    } finally {
      setBusy(false);
    }
  };

  const runShopifyJob = async (mode: 'sync' | 'backfill') => {
    if (!apiToken) return;
    setBusy(true);
    try {
      const response = mode === 'sync'
        ? await runShopifySync(apiToken, { source: 'attribution_page' })
        : await runShopifyBackfill(apiToken, { source: 'attribution_page' });
      setShopifySyncMessage(String(response.message || response.status || 'Shopify 同步请求已记录'));
    } catch (error) {
      setShopifySyncMessage(error instanceof Error ? error.message : 'Shopify 同步请求失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell title="销售归因" description="把 Shopify / Amazon / 手工导入销售归因到短链、项目、红人、成员和 KPI。">
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="人工业务写入授权" />
          <div className="vkpi-form-stack">
            <input value={authorizationRef} onChange={(event) => setAuthorizationRef(event.target.value)} placeholder="授权编号 / 工单 / 批次" />
            <input value={authorizationReason} onChange={(event) => setAuthorizationReason(event.target.value)} placeholder="写入原因与证据来源" />
            <label className="vkpi-help-text"><input type="checkbox" checked={authorizationConfirmed} onChange={(event) => setAuthorizationConfirmed(event.target.checked)} /> 我已人工复核此次写入</label>
            <span className="vkpi-help-text">仅 owner/admin 且功能门禁开启后可写；人工 Shopify 补录始终不会被当成 provider-confirmed GMV。</span>
          </div>
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="Shopify 订单补录" />
          <form className="vkpi-form-stack" onSubmit={submitShopify}>
            <ProjectSelect projects={data.projects} value={shopifyProjectId} onChange={setShopifyProjectId} allowEmpty />
            <input value={shopifySourceRef} onChange={(event) => setShopifySourceRef(event.target.value)} placeholder="source_ref，例如 shopify:订单ID" />
            <input value={shopifyOrderId} onChange={(event) => setShopifyOrderId(event.target.value)} placeholder="Shopify order id（可选）" />
            <input value={shopifyRevenue} onChange={(event) => setShopifyRevenue(event.target.value)} placeholder="销售额 USD" inputMode="decimal" />
            <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !onCreateAttribution || !authorizationReady}>保存待核验 Shopify 记录</button>
          </form>
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="Shopify API 状态" />
          <div className="vkpi-form-stack">
            <button className="vkpi-button" type="button" disabled={busy || viewMode !== 'manager'} onClick={() => void runShopifyJob('sync')}>测试同步</button>
            <button className="vkpi-button" type="button" disabled={busy || viewMode !== 'manager'} onClick={() => void runShopifyJob('backfill')}>记录补拉</button>
            <span className="vkpi-help-text">{shopifySyncMessage || '未配置真实 Shopify Admin API 时只显示未配置，不生成假订单。'}</span>
          </div>
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="Amazon 报表参数" />
          <div className="vkpi-form-stack">
            <ProjectSelect projects={data.projects} value={amazonProjectId} onChange={setAmazonProjectId} allowEmpty />
            <input value={amazonTag} onChange={(event) => setAmazonTag(event.target.value)} placeholder="Amazon tag / campaign id" />
            <input value={amazonAsin} onChange={(event) => setAmazonAsin(event.target.value)} placeholder="ASIN" />
            <input value={amazonMarketplace} onChange={(event) => setAmazonMarketplace(event.target.value)} placeholder="Marketplace，例如 US" />
            <input value={amazonReportDate} onChange={(event) => setAmazonReportDate(event.target.value)} placeholder="Report date，例如 2026-05-07" />
          </div>
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="Amazon 文件导入" />
          <input type="file" accept=".csv,.xlsx,.xls,.json" onChange={(event) => setAmazonFile(event.currentTarget.files?.[0] || null)} />
          <button className="vkpi-button vkpi-button--primary" type="button" disabled={busy || !amazonFile || !onUploadAmazonReport || !authorizationReady} onClick={() => void submitAmazonFile()}>上传并导入</button>
          <span className="vkpi-help-text">支持后端已实现的 Amazon Attribution CSV/XLSX/JSON 解析。</span>
        </section>
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="Amazon JSON 快速导入" />
        <form className="vkpi-form-stack" onSubmit={submitAmazonRows}>
          <textarea className="vkpi-textarea" value={amazonRowsText} onChange={(event) => setAmazonRowsText(event.target.value)} placeholder='[{"amazon_tag":"xxx","asin":"AF35","revenue_usd":123.45,"orders":2,"report_date":"2026-05-07"}]' />
          <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !onImportAmazonRows || !authorizationReady}>导入 JSON Rows</button>
        </form>
        {message ? <div className="vkpi-inline-message">{message}</div> : null}
      </section>
      <AmazonAttributionLivePanel
        summary={amazonSummary}
        rows={amazonAttributionRows}
        loading={amazonLoading}
        error={amazonError}
        onRefresh={() => void loadAmazonData()}
      />
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <button className="vkpi-card vkpi-drill-card" type="button" onClick={() => onOpenEvidence('gmv')}><span>销售额证据</span><strong>{metricSourceCount('gmv')}</strong><em>来自 metric snapshot</em></button>
        {viewMode === 'manager' ? <button className="vkpi-card vkpi-drill-card" type="button" onClick={() => onOpenEvidence('cost')}><span>成本证据</span><strong>{metricSourceCount('cost')}</strong><em>来自 metric snapshot</em></button> : null}
      </section>
      <section className="vkpi-card vkpi-table-card"><div className="vkpi-table-card__header"><div><h2>已确认 / 已导入归因</h2><span>{data.attributions.length} 条</span></div></div><AttributionTable rows={data.attributions} /></section>
      <section className="vkpi-card vkpi-table-card"><div className="vkpi-table-card__header"><div><h2>未匹配归因</h2><span>{data.unmatchedAttributions.length} 条</span></div></div><AttributionTable rows={data.unmatchedAttributions} /></section>
    </PageShell>
  );
}
