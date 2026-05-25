import React, { useState } from 'react';
import { getMarketingLinkDetail } from '../../../domains/attribution';
import type { VkpiDashboardData, VkpiLinkDetail, VkpiLinkRow } from '../vkpiTypes';
import { LinkDetailDrawer } from '../drawers/LinkDetailDrawer';
import { CardHeader } from '../shared/CardHeader';
import { ProjectSelect } from '../shared/ProjectSelect';
import { linkPlatformOptions } from '../shared/vkpiConstants';
import { LinkTable } from '../tables/LinkTable';
import { PageShell } from './PageShell';

interface LinksPageProps {
  data: VkpiDashboardData;
  apiToken?: string;
  onCreateLink?: (payload: { destinationUrl: string; slug?: string; projectId?: string; kolId?: string; platform?: string; productSku?: string; campaignName?: string; utmSource?: string; utmMedium?: string; utmCampaign?: string; utmContent?: string }) => Promise<void>;
  onPauseLink?: (linkId: string) => Promise<void>;
  onArchiveLink?: (linkId: string) => Promise<void>;
  onHealthCheckLink?: (linkId: string) => Promise<void>;
}

export function LinksPage({ data, apiToken, onCreateLink, onPauseLink, onArchiveLink, onHealthCheckLink }: LinksPageProps) {
  const [destinationUrl, setDestinationUrl] = useState('');
  const [productInput, setProductInput] = useState('');
  const [productSku, setProductSku] = useState('');
  const [slug, setSlug] = useState('');
  const [projectId, setProjectId] = useState('');
  const [platform, setPlatform] = useState('youtube');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [linkDetail, setLinkDetail] = useState<VkpiLinkDetail | null>(null);
  const [linkDetailLoading, setLinkDetailLoading] = useState(false);
  const [linkDetailError, setLinkDetailError] = useState('');
  const selectedProject = data.projects.find((project) => project.id === projectId);
  const openLinkDetail = (link: VkpiLinkRow) => {
    setLinkDetail({ link: link as unknown as Record<string, unknown>, clicks: [], sales_attributions: [], orders: [] });
    setLinkDetailError('');
    if (!apiToken) return;
    setLinkDetailLoading(true);
    getMarketingLinkDetail(apiToken, link.id)
      .then((detail) => setLinkDetail(detail))
      .catch((error) => setLinkDetailError(error instanceof Error ? error.message : '短链详情加载失败'))
      .finally(() => setLinkDetailLoading(false));
  };
  const submitLink = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onCreateLink) return;
    const rawDestination = destinationUrl.trim() || productInput.trim();
    const destination = rawDestination.startsWith('http')
      ? rawDestination
      : `https://www.viltrox.com/products/${encodeURIComponent((rawDestination || productSku || selectedProject?.campaign || 'viltrox-product').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''))}`;
    setBusy(true);
    try {
      await onCreateLink({
        destinationUrl: destination,
        slug: slug.trim() || undefined,
        projectId: projectId || undefined,
        platform,
        productSku: productSku.trim() || undefined,
        campaignName: selectedProject?.campaign,
        utmSource: platform,
        utmMedium: 'kol',
        utmCampaign: projectId ? `project_${projectId}` : undefined,
        utmContent: productSku.trim() || undefined,
      });
      setDestinationUrl('');
      setProductInput('');
      setProductSku('');
      setSlug('');
      setMessage('短链已创建。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '短链创建失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <PageShell title="短链中心" description="按项目和产品给 KOL 生成 Shopify / Amazon 链接，并把点击、订单、销售额绑定到员工。">
      <section className="vkpi-card vkpi-action-card"><CardHeader title="按产品生成 KOL 链接" /><form className="vkpi-form-grid" onSubmit={submitLink}><label>项目<ProjectSelect projects={data.projects} value={projectId} onChange={setProjectId} allowEmpty /></label><label>推广平台<select value={platform} onChange={(event) => setPlatform(event.target.value)}>{linkPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><label>Shopify 产品链接 / Handle<input value={productInput} onChange={(event) => setProductInput(event.target.value)} placeholder="af-35mm-f1-2 或 https://www.viltrox.com/products/..." /></label><label>产品 SKU<input value={productSku} onChange={(event) => setProductSku(event.target.value)} placeholder="AF35-F1.2" /></label><label>Slug（可选）<input value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="af35-review-jianbo" /></label><label>手动目标链接（可选）<input value={destinationUrl} onChange={(event) => setDestinationUrl(event.target.value)} placeholder="只在需要 Amazon / 特殊落地页时填写" /></label><button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !onCreateLink || (!productInput.trim() && !destinationUrl.trim() && !productSku.trim())}>生成 KOL 链接</button></form>{message ? <div className="vkpi-inline-message">{message}</div> : null}</section>
      <section className="vkpi-card vkpi-table-card"><div className="vkpi-table-card__header"><div><h2>短链列表</h2><span>{data.links.length} 条</span></div></div><LinkTable links={data.links} onSelectLink={openLinkDetail} onPauseLink={onPauseLink} onArchiveLink={onArchiveLink} onHealthCheckLink={onHealthCheckLink} /></section>
      {linkDetail ? (
        <LinkDetailDrawer
          detail={linkDetail}
          loading={linkDetailLoading}
          error={linkDetailError}
          onClose={() => {
            setLinkDetail(null);
            setLinkDetailError('');
          }}
        />
      ) : null}
    </PageShell>
  );
}
