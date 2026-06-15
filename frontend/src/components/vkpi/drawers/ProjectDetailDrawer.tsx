import { useEffect, useMemo, useState } from 'react';
import type { VkpiKolProfile, VkpiProjectDetail, VkpiProjectRow } from '../vkpiTypes';
import { DetailList } from '../shared/DetailList';
import { currencyFormatter, numberFormatter } from '../shared/vkpiFormatters';
import { coerceProjectStage, safeNumber, textValue } from '../shared/vkpiDataUtils';
import { primaryStageFlow, stageLabels } from '../shared/vkpiConstants';
import { ProjectEvidenceForms } from './ProjectEvidenceForms';
import { getAnalysisCache, type VkpiAnalysisCacheEntry } from '../../../services/vkpi/projects-api';

function parseProjectMetadata(value: unknown): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== 'string') return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function selectedProjectProducts(project: Record<string, unknown>, fallbackProject?: VkpiProjectRow): Array<{ sku: string; name: string }> {
  const metadata = parseProjectMetadata(project.metadata_json || project.metadata);
  const rawProducts = Array.isArray(metadata.products) ? metadata.products : [];
  const products = rawProducts
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const row = item as Record<string, unknown>;
      const sku = textValue(row.product_sku || row.productSku || row.sku, '');
      if (!sku) return null;
      return {
        sku,
        name: textValue(row.product_name || row.productName || row.name, sku),
      };
    })
    .filter((item): item is { sku: string; name: string } => Boolean(item));
  if (products.length) return products;
  const sku = textValue(project.product_sku || fallbackProject?.campaign, '');
  const name = textValue(project.product_name || project.productName, sku);
  return sku ? [{ sku, name: name || sku }] : [];
}

function evidenceHref(value: unknown): string {
  const text = textValue(value, '').trim();
  if (!text) return '';
  if (text.startsWith('/uploads/')) return text;
  if (/^https?:\/\//i.test(text)) return text;
  return '';
}

function firstEvidenceUrlFromText(value: unknown): string {
  const text = textValue(value, '');
  const match = text.match(/(https?:\/\/[^\s，。),;]+|\/uploads\/[^\s，。),;]+)/i);
  return match ? match[1] : '';
}

function firstEvidenceUrlFromRows(rows: Array<Record<string, unknown>>): string {
  for (const row of rows) {
    const direct = evidenceHref(row.evidence_url || row.asset_url || row.thumbnail_url || row.file_path);
    if (direct) return direct;
    const nested = firstEvidenceUrlFromText(row.note || row.description || row.metadata_json);
    if (nested) return nested;
  }
  return '';
}

function detailStageIndex(stage: ReturnType<typeof coerceProjectStage>) {
  if (stage === 'content_published') return primaryStageFlow.indexOf('published');
  const index = primaryStageFlow.indexOf(stage);
  if (stage === 'closed' || stage === 'released' || stage === 'cancelled' || stage === 'lost') return primaryStageFlow.length - 1;
  return index >= 0 ? index : 0;
}

function detailHealthScore(stage: ReturnType<typeof coerceProjectStage>, revenue: number, cost: number, projectEvents: Array<Record<string, unknown>>) {
  const index = detailStageIndex(stage);
  const roiBonus = cost ? Math.min(16, Math.max(-10, ((revenue / cost) - 1) * 8)) : 0;
  const score = Math.round(Math.min(96, Math.max(34, 50 + index * 5 + roiBonus + Math.min(8, projectEvents.length))));
  if (stage === 'cancelled' || stage === 'lost' || stage === 'stalled') return { score: Math.min(score, 45), label: '阻塞', className: 'is-bad' };
  if (score >= 78) return { score, label: '健康', className: 'is-good' };
  if (score >= 58) return { score, label: '注意', className: 'is-mid' };
  return { score, label: '待推进', className: 'is-bad' };
}

function EvidenceLink({ value, label = '打开证据' }: { value: unknown; label?: string }) {
  const href = evidenceHref(value) || firstEvidenceUrlFromText(value);
  if (!href) return <>{textValue(value, '-')}</>;
  return <a className="vkpi-evidence-link" href={href} target="_blank" rel="noreferrer">{label}</a>;
}

function analysisPreview(value: unknown): string {
  if (value == null) return '无结构化结果';
  const serialized = typeof value === 'string' ? value : JSON.stringify(value);
  const raw = typeof serialized === 'string' ? serialized : String(value);
  return raw.length > 260 ? `${raw.slice(0, 260)}...` : raw;
}

export function ProjectDetailDrawer({
  detail,
  apiToken,
  kolProfile,
  fallbackProject,
  viewMode,
  loading,
  error,
  onAddMessage,
  onAddContent,
  onUpsertTerms,
  onAddShipment,
  onUploadEvidenceFile,
  onClose,
}: {
  detail: VkpiProjectDetail | null;
  apiToken?: string;
  kolProfile?: VkpiKolProfile | null;
  fallbackProject?: VkpiProjectRow;
  viewMode: 'manager' | 'employee';
  loading?: boolean;
  error?: string;
  onAddMessage?: (payload: Record<string, unknown>) => Promise<void>;
  onAddContent?: (payload: Record<string, unknown>) => Promise<void>;
  onUpsertTerms?: (payload: Record<string, unknown>) => Promise<void>;
  onAddShipment?: (payload: Record<string, unknown>) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
  onClose: () => void;
}) {
  const project = detail?.project || (fallbackProject as unknown as Record<string, unknown>) || {};
  const projectId = textValue(project.id || fallbackProject?.id, '');
  const projectName = textValue(project.project_name || project.campaign, fallbackProject?.campaign || '项目详情');
  const stage = coerceProjectStage(project.stage || fallbackProject?.stage);
  const owner = textValue(project.staff_name || project.ownerName || project.assigned_staff_id, fallbackProject?.ownerName || '-');
  const kolName = textValue(project.kol_name || project.channel_name || project.kolName, fallbackProject?.kolName || '-');
  const events = detail?.events || [];
  const links = detail?.links || [];
  const linkClicks = detail?.link_clicks || [];
  const linkOrders = detail?.link_orders || [];
  const linkSummary = detail?.link_summary || {};
  const sales = detail?.sales_attributions || [];
  const costsRows = detail?.costs || [];
  const messages = detail?.messages || [];
  const contentPosts = detail?.content_posts || [];
  const contentAssets = detail?.content_assets || [];
  const terms = detail?.terms || {};
  const deliverables = detail?.deliverables || [];
  const samples = detail?.samples || [];
  const shipments = detail?.shipments || [];
  const auditEvents = detail?.audit_events || [];
  const showFinancials = viewMode === 'manager';
  const profileSummary = kolProfile?.summary || {};
  const profileKol = kolProfile?.kol || {};
  const roi = detail?.roi || {};
  const revenue = safeNumber(roi.revenue_cents || project.revenue_cents || (fallbackProject?.gmv || 0) * 100) / 100;
  const cost = safeNumber(roi.cost_cents || project.cost_cents || (fallbackProject?.cost || 0) * 100) / 100;
  const roiValue = roi.roi == null ? (cost ? revenue / cost : null) : Number(roi.roi);
  const selectedProducts = selectedProjectProducts(project, fallbackProject);
  const termsEvidenceUrl = firstEvidenceUrlFromText(terms.note) || firstEvidenceUrlFromRows(deliverables);
  const currentStageIndex = detailStageIndex(stage);
  const health = detailHealthScore(stage, revenue, cost, events);
  const clickCount = safeNumber(linkSummary.valid_click_count || linkSummary.click_count || fallbackProject?.clicks);
  const orderCount = safeNumber(linkSummary.order_count || fallbackProject?.orders);
  const contentCount = contentPosts.length || safeNumber(project.content_count || project.contentCount);
  const tabLinks = [
    ['overview', '总览'],
    ['messages', 'KOL与沟通'],
    ['content', '内容'],
    ['shipment', '物流样品'],
    ['costs', '费用'],
    ['attribution', '短链归因'],
    ['terms', '条款证据'],
  ];
  const analysisTarget = useMemo(() => {
    const firstContent = contentPosts.find((row) => textValue(row.id || row.evidence_id || row.post_url, ''));
    const videoTargetId = firstContent ? textValue(firstContent.id || firstContent.evidence_id || firstContent.post_url, '') : '';
    if (videoTargetId) return { targetType: 'video', targetId: videoTargetId, label: '内容视频' };
    return projectId ? { targetType: 'project', targetId: projectId, label: '项目' } : null;
  }, [contentPosts, projectId]);
  const [analysisEntry, setAnalysisEntry] = useState<VkpiAnalysisCacheEntry | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState('');

  useEffect(() => {
    setAnalysisEntry(null);
    setAnalysisError('');
    if (!apiToken || !analysisTarget) {
      setAnalysisLoading(false);
      return;
    }
    let cancelled = false;
    setAnalysisLoading(true);
    getAnalysisCache(apiToken, analysisTarget.targetType, analysisTarget.targetId)
      .then((response) => {
        if (!cancelled) setAnalysisEntry(response.entry || null);
      })
      .catch((error) => {
        if (!cancelled) setAnalysisError(error instanceof Error ? error.message : '分析缓存读取失败');
      })
      .finally(() => {
        if (!cancelled) setAnalysisLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, analysisTarget?.targetId, analysisTarget?.targetType]);
  const analysisStatus = analysisLoading ? '读取中' : analysisEntry ? '已缓存' : '分析中 / 待分析';

  return (
    <aside className="vkpi-evidence-drawer vkpi-project-detail-drawer vkpi-project-detail-campaign" role="dialog" aria-label="项目详情">
      <header>
        <div>
          <span>Campaign Detail</span>
          <h2>{projectName}</h2>
          <small>负责人 {owner} · KOL {kolName} · 当前阶段 {stageLabels[stage] || stage}</small>
        </div>
        <button type="button" onClick={onClose}>×</button>
      </header>
      <div className="vkpi-evidence-list">
        {loading ? <div className="vkpi-empty-state">正在加载项目详情...</div> : null}
        {error ? <div className="vkpi-empty-state">{error}</div> : null}
        <section className="vkpi-project-detail-hero" id="project-detail-overview">
          <div>
            <span className="vkpi-project-detail-eyebrow">项目作战卡</span>
            <h3>{projectName}</h3>
            <p>{textValue(project.product_sku || fallbackProject?.campaign, '-')} · {textValue(project.platform || fallbackProject?.platform, '-')} · {kolName}</p>
            <div className="vkpi-project-detail-meta">
              <span>负责人 {owner}</span>
              <span>总耗时 {fallbackProject?.totalDurationLabel || '-'}</span>
              <span>阶段停留 {fallbackProject?.stageDurationLabel || '-'}</span>
            </div>
          </div>
          <div className={`vkpi-project-detail-score ${health.className}`}>
            <span>健康分</span>
            <strong>{health.score}</strong>
            <em>{health.label}</em>
          </div>
        </section>
        <section className="vkpi-project-detail-kpis">
          <div><span>播放</span><strong>{numberFormatter.format(safeNumber(project.views || fallbackProject?.views))}</strong></div>
          <div><span>点击</span><strong>{numberFormatter.format(clickCount)}</strong></div>
          <div><span>订单</span><strong>{numberFormatter.format(orderCount)}</strong></div>
          <div><span>内容</span><strong>{numberFormatter.format(contentCount)}</strong></div>
          <div><span>销售</span><strong>{currencyFormatter.format(revenue)}</strong></div>
          {showFinancials ? <div><span>成本</span><strong>{currencyFormatter.format(cost)}</strong></div> : null}
          <div><span>ROI</span><strong>{roiValue == null ? '-' : `${roiValue.toFixed(2)}x`}</strong></div>
        </section>
        <section className="vkpi-project-detail-flow" aria-label="项目阶段漏斗">
          {primaryStageFlow.map((flowStage, index) => {
            const state = index < currentStageIndex ? 'done' : index === currentStageIndex ? 'now' : 'todo';
            return (
              <div className={`vkpi-project-detail-stage is-${state}`} key={flowStage}>
                <strong>{index + 1}</strong>
                <span>{stageLabels[flowStage]}</span>
                <em>{state === 'done' ? '已完成' : state === 'now' ? '当前阶段' : '待推进'}</em>
              </div>
            );
          })}
        </section>
        <nav className="vkpi-project-detail-tabs" aria-label="项目详情分区">
          {/* 全局 hashchange 把非法 hash 归一化回默认页——锚点会把整个应用踢回首页(2026-06-12 清查)。
              改 button+scrollIntoView,零 hash 写入。 */}
          {tabLinks.map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => document.getElementById(`project-detail-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
            >
              {label}
            </button>
          ))}
        </nav>
        <article id="project-detail-overview-card">
          <div><strong>项目概览</strong><span>{textValue(project.updated_at || fallbackProject?.updatedAt, '-')}</span></div>
          <p>产品：{textValue(project.product_sku || fallbackProject?.campaign, '-')} · 平台：{textValue(project.platform || fallbackProject?.platform, '-')}</p>
          <em>总耗时 {fallbackProject?.totalDurationLabel || '-'} · 当前阶段 {fallbackProject?.stageDurationLabel || '-'}</em>
        </article>
        <article id="project-detail-analysis-cache">
          <div><strong>分析缓存</strong><span>{analysisStatus}</span></div>
          {analysisEntry ? (
            <>
              <p>{analysisPreview(analysisEntry.result)}</p>
              <em>
                {analysisTarget?.label || '目标'} {analysisEntry.target_type}:{analysisEntry.target_id}
                {' · '}{analysisEntry.derive_method || '-'} / {analysisEntry.model || '-'}
                {' · '}成本 {analysisEntry.cost ?? 0}
              </em>
            </>
          ) : (
            <>
              <p>分析中 / 待分析</p>
              <em>{analysisTarget ? `${analysisTarget.label} ${analysisTarget.targetType}:${analysisTarget.targetId}` : '暂无目标'}{analysisError ? ` · ${analysisError}` : ''}</em>
            </>
          )}
        </article>
        <article id="project-detail-products">
          <div><strong>关联产品</strong><span>{selectedProducts.length ? `${selectedProducts.length} 个` : '未选择'}</span></div>
          {selectedProducts.length ? (
            <div className="vkpi-chip-row">
              {selectedProducts.map((item) => (
                <span className="vkpi-chip is-selected" key={`${item.sku}-${item.name}`}>{item.name || item.sku}</span>
              ))}
            </div>
          ) : (
            <p>暂无关联产品。新建项目时可从产品列表单选或多选。</p>
          )}
          <em>主产品仍写入项目主字段，多产品保存在项目 metadata，后续可升级为项目-产品关系表。</em>
        </article>
        <article id="project-detail-attribution">
          <div><strong>{showFinancials ? '销售 / 成本 / ROI' : '销售 / 项目进度'}</strong><span>来自项目详情接口</span></div>
          <p>
            销售 {currencyFormatter.format(revenue)}
            {showFinancials ? ` · 成本 ${currencyFormatter.format(cost)} · ROI ${roiValue == null ? '-' : `${roiValue.toFixed(2)}x`}` : ''}
          </p>
          <em>{showFinancials ? '管理层可复核成本、订单和 ROI 证据。' : '需要授权后才能查看内部镜头单价和成本明细。'}</em>
        </article>
        <article id="project-detail-messages">
          <div><strong>KOL Profile</strong><span>{kolProfile ? '真实档案' : '未加载'}</span></div>
          {kolProfile ? (
            <>
              <p>
                {textValue(profileKol.channel_name || kolName, '-')} · 粉丝 {numberFormatter.format(safeNumber(profileSummary.follower_count))} · 内容 {numberFormatter.format(safeNumber(profileSummary.content_count))}
              </p>
              <em>
                画像 {textValue(profileSummary.user_persona, '待判断')} · 评分 {textValue(profileSummary.account_score, '-')} · ROI {safeNumber(profileSummary.roi) ? `${safeNumber(profileSummary.roi).toFixed(2)}x` : '-'}
              </em>
            </>
          ) : (
            <p>暂无 KOL Profile 数据。搜索页抓取账号后会同步粉丝、内容、联系方式和画像评估。</p>
          )}
        </article>
        <ProjectEvidenceForms
          projectId={projectId}
          onAddMessage={onAddMessage}
          onAddContent={onAddContent}
          onUpsertTerms={onUpsertTerms}
          onAddShipment={onAddShipment}
          onUploadEvidenceFile={onUploadEvidenceFile}
        />
        <DetailList title="流程时间线" rows={events} empty="暂无流程事件。">
          {(row) => (
            <article key={`event-${String(row.id || row.created_at || Math.random())}`}>
              <div><strong>{stageLabels[coerceProjectStage(row.to_stage)] || textValue(row.to_stage, '阶段变化')}</strong><span>{textValue(row.effective_at || row.created_at, '-')}</span></div>
              <p>{textValue(row.note, '无备注')}</p>
              <em>{textValue(row.source_ref_type, 'manual')} · {textValue(row.source_ref_id, '-')}</em>
            </article>
          )}
        </DetailList>
        <div id="project-detail-attribution-links" />
        <DetailList title="短链" rows={links} empty="暂无短链。">
          {(row) => (
            <article key={`link-${String(row.id || row.slug || Math.random())}`}>
              <div><strong>{textValue(row.slug, '短链')}</strong><span>{textValue(row.status, '-')}</span></div>
              <p>{textValue(row.destination_url, '-')}</p>
              <em>有效点击 {numberFormatter.format(safeNumber(row.valid_click_count || row.click_count))} · 健康 {textValue(row.health_status, 'unknown')}</em>
            </article>
          )}
        </DetailList>
        <article>
          <div><strong>短链汇总</strong><span>项目维度</span></div>
          <p>
            短链 {numberFormatter.format(safeNumber(linkSummary.link_count || links.length))}
            {' · '}点击 {numberFormatter.format(safeNumber(linkSummary.click_count))}
            {' · '}有效 {numberFormatter.format(safeNumber(linkSummary.valid_click_count))}
            {' · '}机器人 {numberFormatter.format(safeNumber(linkSummary.bot_click_count))}
          </p>
          <em>订单 {numberFormatter.format(safeNumber(linkSummary.order_count || linkOrders.length))} · 销售 {currencyFormatter.format(safeNumber(linkSummary.revenue_cents) / 100)}</em>
        </article>
        <DetailList title="短链点击证据" rows={linkClicks} empty="暂无点击证据。">
          {(row) => (
            <article key={`project-click-${String(row.id || row.event_id || Math.random())}`}>
              <div><strong>{textValue(row.slug || row.event_id, 'click')}</strong><span>{textValue(row.clicked_at, '-')}</span></div>
              <p>{textValue(row.referrer, '无 referrer')}</p>
              <em>{textValue(row.device_type, '-')} · {safeNumber(row.is_bot) ? 'bot' : 'valid'} · {textValue(row.country_code, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="短链订单证据" rows={linkOrders} empty="暂无短链订单证据。">
          {(row) => (
            <article key={`project-link-order-${String(row.shopify_order_snapshot_id || row.source_ref || row.attribution_id || Math.random())}`}>
              <div><strong>{textValue(row.order_name || row.order_number || row.source_ref, '订单')}</strong><span>{currencyFormatter.format(safeNumber(row.revenue_cents) / 100)}</span></div>
              <p>{textValue(row.slug, '-')} · {textValue(row.financial_status || row.confidence, '-')}</p>
              <em>{textValue(row.processed_at || row.occurred_at, '-')} · Hash {textValue(row.raw_payload_hash, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="销售归因" rows={sales} empty="暂无销售归因。">
          {(row) => (
            <article key={`sale-${String(row.id || row.source_ref || Math.random())}`}>
              <div><strong>{currencyFormatter.format(safeNumber(row.revenue_cents) / 100)}</strong><span>{textValue(row.source_platform, '-')}</span></div>
              <p>{textValue(row.source_ref || row.order_id, '-')}</p>
              {row.order_snapshot ? (
                <p className="vkpi-detail-subline">
                  Shopify {textValue((row.order_snapshot as Record<string, unknown>).order_name || (row.order_snapshot as Record<string, unknown>).order_number, '-')}
                  {' · '}
                  {textValue((row.order_snapshot as Record<string, unknown>).financial_status, '-')}
                  {' · '}
                  {textValue((row.order_snapshot as Record<string, unknown>).refund_status, 'no refund')}
                </p>
              ) : null}
              <em>{textValue(row.confidence, '-')} · {textValue(row.occurred_at || row.created_at, '-')}</em>
            </article>
          )}
        </DetailList>
        <div id="project-detail-costs" />
        {showFinancials ? (
          <>
            <DetailList title="成本" rows={costsRows} empty="暂无成本记录。">
              {(row) => (
                <article key={`cost-${String(row.id || row.source_ref || Math.random())}`}>
                  <div><strong>{currencyFormatter.format(safeNumber(row.amount_cents) / 100)}</strong><span>{textValue(row.cost_type, '-')}</span></div>
                  <p>{textValue(row.note, '无备注')}</p>
                  <em>{textValue(row.status, '-')} · {textValue(row.incurred_at || row.created_at, '-')}</em>
                </article>
              )}
            </DetailList>
          </>
        ) : null}
        <DetailList title="消息记录" rows={messages} empty="暂无消息记录。">
          {(row) => (
            <article key={`message-${String(row.id || row.created_at || Math.random())}`}>
              <div><strong>{textValue(row.source, 'manual')}</strong><span>{textValue(row.captured_at || row.created_at, '-')}</span></div>
              <p>{textValue(row.snippet || row.body, '无内容')}</p>
              <em>{textValue(row.direction, '-')} · {row.evidence_url ? <EvidenceLink value={row.evidence_url} /> : '无证据链接'}</em>
            </article>
          )}
        </DetailList>
        <div id="project-detail-content" />
        <DetailList title="内容资产" rows={contentPosts} empty="暂无发布内容。">
          {(row) => (
            <article key={`content-${String(row.id || row.post_url || Math.random())}`}>
              <div><strong>{textValue(row.title || row.post_url, '内容')}</strong><span>{textValue(row.published_at || row.created_at, '-')}</span></div>
              <p>{textValue(row.post_url, '-')}</p>
              <em>播放 {numberFormatter.format(safeNumber(row.views))} · 点赞 {numberFormatter.format(safeNumber(row.likes))} · 权限 {textValue(row.rights_status, 'unknown')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="素材附件" rows={contentAssets} empty="暂无附件素材。">
          {(row) => (
            <article key={`asset-${String(row.id || row.asset_url || Math.random())}`}>
              <div><strong>{textValue(row.asset_type, 'asset')}</strong><span>{textValue(row.rights_status, '-')}</span></div>
              <p><EvidenceLink value={row.asset_url || row.thumbnail_url || row.file_path} label="打开素材" /></p>
              <em>{textValue(row.usage_rights || row.note, '-')}</em>
            </article>
          )}
        </DetailList>
        <article id="project-detail-terms">
          <div><strong>合作条款</strong><span>{terms && Object.keys(terms).length ? '已记录' : '0 条'}</span></div>
          {terms && Object.keys(terms).length ? (
            <>
              <p>{showFinancials ? `现金费用 ${currencyFormatter.format(safeNumber(terms.cash_fee_cents) / 100)} · ` : ''}使用权 {textValue(terms.usage_rights, '-')}</p>
              <em>{textValue(terms.sample_terms || terms.note, '-')}</em>
              {termsEvidenceUrl ? <p className="vkpi-detail-subline"><EvidenceLink value={termsEvidenceUrl} label="打开条款附件" /></p> : null}
            </>
          ) : <p>暂无合作条款。</p>}
        </article>
        <DetailList title="交付项" rows={deliverables} empty="暂无交付项。">
          {(row) => (
            <article key={`deliverable-${String(row.id || Math.random())}`}>
              <div><strong>{textValue(row.deliverable_type, 'deliverable')}</strong><span>{textValue(row.status, '-')}</span></div>
              <p>数量 {numberFormatter.format(safeNumber(row.quantity))} · 截止 {textValue(row.due_at, '-')}</p>
              <em><EvidenceLink value={row.evidence_url || row.note} label="打开交付证据" /></em>
            </article>
          )}
        </DetailList>
        <div id="project-detail-shipment" />
        <DetailList title="样品 / 物流" rows={[...samples.map((row) => ({ ...row, __kind: 'sample' })), ...shipments.map((row) => ({ ...row, __kind: 'shipment' }))]} empty="暂无样品或物流记录。">
          {(row) => (
            <article key={`sample-${String(row.__kind)}-${String(row.id || row.tracking_number || row.serial_number || Math.random())}`}>
              <div><strong>{textValue(row.product_sku || row.carrier, '样品/物流')}</strong><span>{textValue(row.status, '-')}</span></div>
              <p>单号 {textValue(row.tracking_number, '-')} · 序列号 {textValue(row.serial_number, '-')}</p>
              <em>
                {showFinancials ? `样品成本 ${currencyFormatter.format(safeNumber(row.sample_cost_cents) / 100)} · ` : ''}
                运费 {currencyFormatter.format(safeNumber(row.shipping_cost_cents) / 100)}
                {row.evidence_url ? <> · <EvidenceLink value={row.evidence_url} label="打开物流凭证" /></> : ''}
              </em>
            </article>
          )}
        </DetailList>
        {showFinancials ? (
          <DetailList title="审计记录" rows={auditEvents} empty="暂无项目审计记录。">
            {(row) => (
              <article key={`project-audit-${String(row.id || row.created_at || Math.random())}`}>
                <div><strong>{textValue(row.action_type, 'audit')}</strong><span>{textValue(row.created_at, '-')}</span></div>
                <p>{textValue(row.detail, '无说明')}</p>
                <em>{textValue(row.staff_name || row.staff_email || row.staff_id, '-')} · {textValue(row.target_type, '-')} #{textValue(row.target_id, '-')}</em>
              </article>
            )}
          </DetailList>
        ) : null}
      </div>
    </aside>
  );
}
