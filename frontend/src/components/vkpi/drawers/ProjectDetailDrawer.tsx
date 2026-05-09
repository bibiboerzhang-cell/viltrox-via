import type { VkpiKolProfile, VkpiProjectDetail, VkpiProjectRow } from '../vkpiTypes';
import { DetailList } from '../shared/DetailList';
import { currencyFormatter, numberFormatter } from '../shared/vkpiFormatters';
import { coerceProjectStage, formatMoneyCents, safeNumber, textValue } from '../shared/vkpiDataUtils';
import { stageLabels } from '../shared/vkpiConstants';
import { ProjectEvidenceForms } from './ProjectEvidenceForms';

export function ProjectDetailDrawer({
  detail,
  kolProfile,
  fallbackProject,
  viewMode,
  loading,
  error,
  onAddMessage,
  onAddContent,
  onUpsertTerms,
  onAddShipment,
  onClose,
}: {
  detail: VkpiProjectDetail | null;
  kolProfile?: VkpiKolProfile | null;
  fallbackProject?: VkpiProjectRow;
  viewMode: 'manager' | 'employee';
  loading?: boolean;
  error?: string;
  onAddMessage?: (payload: Record<string, unknown>) => Promise<void>;
  onAddContent?: (payload: Record<string, unknown>) => Promise<void>;
  onUpsertTerms?: (payload: Record<string, unknown>) => Promise<void>;
  onAddShipment?: (payload: Record<string, unknown>) => Promise<void>;
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

  return (
    <aside className="vkpi-evidence-drawer vkpi-project-detail-drawer" role="dialog" aria-label="项目详情">
      <header>
        <div>
          <span>Project Detail</span>
          <h2>{projectName}</h2>
          <small>负责人 {owner} · KOL {kolName} · 当前阶段 {stageLabels[stage] || stage}</small>
        </div>
        <button type="button" onClick={onClose}>×</button>
      </header>
      <div className="vkpi-evidence-list">
        {loading ? <div className="vkpi-empty-state">正在加载项目详情...</div> : null}
        {error ? <div className="vkpi-empty-state">{error}</div> : null}
        <article>
          <div><strong>项目概览</strong><span>{textValue(project.updated_at || fallbackProject?.updatedAt, '-')}</span></div>
          <p>产品：{textValue(project.product_sku || fallbackProject?.campaign, '-')} · 平台：{textValue(project.platform || fallbackProject?.platform, '-')}</p>
          <em>总耗时 {fallbackProject?.totalDurationLabel || '-'} · 当前阶段 {fallbackProject?.stageDurationLabel || '-'}</em>
        </article>
        <article>
          <div><strong>{showFinancials ? '销售 / 成本 / ROI' : '销售 / 项目进度'}</strong><span>来自项目详情接口</span></div>
          <p>
            销售 {currencyFormatter.format(revenue)}
            {showFinancials ? ` · 成本 ${currencyFormatter.format(cost)} · ROI ${roiValue == null ? '-' : `${roiValue.toFixed(2)}x`}` : ''}
          </p>
          <em>{showFinancials ? '管理层可复核成本、订单和 ROI 证据。' : '员工视角隐藏内部镜头单价和成本明细。'}</em>
        </article>
        <article>
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
        {showFinancials ? (
          <DetailList title="成本" rows={costsRows} empty="暂无成本记录。">
            {(row) => (
              <article key={`cost-${String(row.id || row.source_ref || Math.random())}`}>
                <div><strong>{currencyFormatter.format(safeNumber(row.amount_cents) / 100)}</strong><span>{textValue(row.cost_type, '-')}</span></div>
                <p>{textValue(row.note, '无备注')}</p>
                <em>{textValue(row.status, '-')} · {textValue(row.incurred_at || row.created_at, '-')}</em>
              </article>
            )}
          </DetailList>
        ) : null}
        <DetailList title="消息记录" rows={messages} empty="暂无消息记录。">
          {(row) => (
            <article key={`message-${String(row.id || row.created_at || Math.random())}`}>
              <div><strong>{textValue(row.source, 'manual')}</strong><span>{textValue(row.captured_at || row.created_at, '-')}</span></div>
              <p>{textValue(row.snippet || row.body, '无内容')}</p>
              <em>{textValue(row.direction, '-')} · {textValue(row.evidence_url, '无证据链接')}</em>
            </article>
          )}
        </DetailList>
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
              <p>{textValue(row.asset_url || row.thumbnail_url || row.file_path, '-')}</p>
              <em>{textValue(row.usage_rights || row.note, '-')}</em>
            </article>
          )}
        </DetailList>
        <article>
          <div><strong>合作条款</strong><span>{terms && Object.keys(terms).length ? '已记录' : '0 条'}</span></div>
          {terms && Object.keys(terms).length ? (
            <>
              <p>{showFinancials ? `现金费用 ${currencyFormatter.format(safeNumber(terms.cash_fee_cents) / 100)} · ` : ''}使用权 {textValue(terms.usage_rights, '-')}</p>
              <em>{textValue(terms.sample_terms || terms.note, '-')}</em>
            </>
          ) : <p>暂无合作条款。</p>}
        </article>
        <DetailList title="交付项" rows={deliverables} empty="暂无交付项。">
          {(row) => (
            <article key={`deliverable-${String(row.id || Math.random())}`}>
              <div><strong>{textValue(row.deliverable_type, 'deliverable')}</strong><span>{textValue(row.status, '-')}</span></div>
              <p>数量 {numberFormatter.format(safeNumber(row.quantity))} · 截止 {textValue(row.due_at, '-')}</p>
              <em>{textValue(row.evidence_url || row.note, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="样品 / 物流" rows={[...samples.map((row) => ({ ...row, __kind: 'sample' })), ...shipments.map((row) => ({ ...row, __kind: 'shipment' }))]} empty="暂无样品或物流记录。">
          {(row) => (
            <article key={`sample-${String(row.__kind)}-${String(row.id || row.tracking_number || row.serial_number || Math.random())}`}>
              <div><strong>{textValue(row.product_sku || row.carrier, '样品/物流')}</strong><span>{textValue(row.status, '-')}</span></div>
              <p>单号 {textValue(row.tracking_number, '-')} · 序列号 {textValue(row.serial_number, '-')}</p>
              <em>{showFinancials ? `样品成本 ${currencyFormatter.format(safeNumber(row.sample_cost_cents) / 100)} · ` : ''}运费 {currencyFormatter.format(safeNumber(row.shipping_cost_cents) / 100)}</em>
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
