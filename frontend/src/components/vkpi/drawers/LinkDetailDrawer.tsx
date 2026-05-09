import type { VkpiLinkDetail } from '../vkpiTypes';
import { DetailList } from '../shared/DetailList';
import { currencyFormatter, numberFormatter } from '../shared/vkpiFormatters';
import { safeNumber, textValue } from '../shared/vkpiDataUtils';

export function LinkDetailDrawer({
  detail,
  loading,
  error,
  onClose,
}: {
  detail: VkpiLinkDetail;
  loading?: boolean;
  error?: string;
  onClose: () => void;
}) {
  const link = detail.link || {};
  const summary = detail.summary || {};
  const clicks = detail.clicks || [];
  const sales = detail.sales_attributions || [];
  const orders = detail.orders || [];
  const auditEvents = detail.audit_events || [];
  const title = textValue(link.slug, '短链详情');
  return (
    <aside className="vkpi-evidence-drawer vkpi-project-detail-drawer" role="dialog" aria-label="短链详情">
      <header>
        <div>
          <span>Link Detail</span>
          <h2>{title}</h2>
          <small>{textValue(link.destination_url || link.destination, '-')}</small>
        </div>
        <button type="button" onClick={onClose}>×</button>
      </header>
      <div className="vkpi-evidence-list">
        {loading ? <div className="vkpi-empty-state">正在加载短链详情...</div> : null}
        {error ? <div className="vkpi-empty-state">{error}</div> : null}
        <article>
          <div><strong>归属</strong><span>{textValue(link.status, '-')}</span></div>
          <p>项目：{textValue(detail.project?.project_name || link.project_id, '-')} · KOL：{textValue(detail.kol?.channel_name || link.kol_id, '-')}</p>
          <em>负责人：{textValue(detail.owner?.name || link.staff_id, '-')} · 健康：{textValue(link.health_status, 'unknown')}</em>
        </article>
        <article>
          <div><strong>点击 / 订单 / 销售</strong><span>真实点击和归因</span></div>
          <p>
            点击 {numberFormatter.format(safeNumber(summary.click_count || link.click_count))} · 有效 {numberFormatter.format(safeNumber(summary.valid_click_count || link.valid_click_count))} · 机器人 {numberFormatter.format(safeNumber(summary.bot_click_count || link.bot_click_count))}
          </p>
          <em>订单 {numberFormatter.format(safeNumber(summary.orders || orders.length))} · 销售 {currencyFormatter.format(safeNumber(summary.revenue_cents) / 100)}</em>
        </article>
        <DetailList title="最近点击" rows={clicks} empty="暂无点击记录。">
          {(row) => (
            <article key={`click-${String(row.id || row.event_id || Math.random())}`}>
              <div><strong>{textValue(row.event_id, 'click')}</strong><span>{textValue(row.clicked_at, '-')}</span></div>
              <p>{textValue(row.referrer, '无 referrer')}</p>
              <em>{textValue(row.device_type, '-')} · {safeNumber(row.is_bot) ? 'bot' : 'valid'} · {textValue(row.country_code, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="Shopify / Amazon 订单证据" rows={orders} empty="暂无订单证据。">
          {(row) => (
            <article key={`link-order-${String(row.shopify_order_snapshot_id || row.order_id || row.source_ref || Math.random())}`}>
              <div><strong>{textValue(row.order_name || row.order_number || row.shopify_order_id || row.source_ref, '订单')}</strong><span>{currencyFormatter.format(safeNumber(row.revenue_cents) / 100)}</span></div>
              <p>{textValue(row.financial_status || row.confidence, '-')} · {textValue(row.fulfillment_status || row.source_platform, '-')}</p>
              <em>{textValue(row.processed_at || row.occurred_at, '-')} · 项目 {textValue(row.project_name || row.project_id, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="归因订单" rows={sales} empty="暂无归因订单。">
          {(row) => (
            <article key={`link-sale-${String(row.id || row.source_ref || Math.random())}`}>
              <div><strong>{currencyFormatter.format(safeNumber(row.revenue_cents) / 100)}</strong><span>{textValue(row.source_platform, '-')}</span></div>
              <p>{textValue(row.order_name || row.order_number || row.source_ref, '-')}</p>
              <em>{textValue(row.confidence, '-')} · {textValue(row.occurred_at || row.created_at, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="审计记录" rows={auditEvents} empty="暂无短链审计记录。">
          {(row) => (
            <article key={`link-audit-${String(row.id || row.created_at || Math.random())}`}>
              <div><strong>{textValue(row.action_type, 'audit')}</strong><span>{textValue(row.created_at, '-')}</span></div>
              <p>{textValue(row.detail, '无说明')}</p>
              <em>{textValue(row.staff_name || row.staff_email || row.staff_id, '-')} · {textValue(row.target_type, '-')} #{textValue(row.target_id, '-')}</em>
            </article>
          )}
        </DetailList>
      </div>
    </aside>
  );
}
