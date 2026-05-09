import { InfoBlock } from '../shared/InfoBlock';
import { numberFormatter } from '../shared/vkpiFormatters';
import { formatMoneyCents, safeNumber, textValue } from '../shared/vkpiDataUtils';

export function AmazonAttributionLivePanel({
  summary,
  rows,
  loading,
  error,
  onRefresh,
}: {
  summary: { items?: Array<Record<string, unknown>>; totals?: Record<string, unknown> } | null;
  rows: Array<Record<string, unknown>>;
  loading: boolean;
  error: string;
  onRefresh: () => void;
}) {
  const items = summary?.items || [];
  const totals = summary?.totals || {};
  const totalRows = safeNumber(totals.rows || rows.length);
  return (
    <section className="vkpi-card vkpi-table-card">
      <div className="vkpi-table-card__header">
        <div>
          <h2>Amazon 真实归因</h2>
          <span>{loading ? '读取中' : `${totalRows} 条`}</span>
        </div>
        <button className="vkpi-button" type="button" disabled={loading} onClick={onRefresh}>{loading ? '刷新中' : '刷新'}</button>
      </div>
      {error ? <div className="vkpi-inline-message">{error}</div> : null}
      <div className="vkpi-card-grid vkpi-card-grid--forms">
        <InfoBlock label="Amazon 销售额" value={formatMoneyCents(totals.revenue_cents)} />
        <InfoBlock label="佣金" value={formatMoneyCents(totals.commission_cents)} />
        <InfoBlock label="待人工匹配" value={`${safeNumber(totals.needs_reconciliation)} 条`} tone={safeNumber(totals.needs_reconciliation) ? 'warn' : undefined} />
      </div>
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead><tr><th>Campaign / Tag</th><th>ASIN / SKU</th><th>Marketplace</th><th>Rows</th><th>销售额</th><th>佣金</th><th>待匹配</th></tr></thead>
          <tbody>
            {items.length ? items.map((row, index) => (
              <tr key={`${textValue(row.amazon_tag || row.campaign_id || row.campaign_name, 'amazon')}-${textValue(row.asin || row.product_sku, String(index))}`}>
                <td>{textValue(row.amazon_tag || row.campaign_id || row.campaign_name, '-')}</td>
                <td>{textValue(row.asin || row.product_sku, '-')}</td>
                <td>{textValue(row.marketplace, '-')}</td>
                <td>{numberFormatter.format(safeNumber(row.rows))}</td>
                <td>{formatMoneyCents(row.revenue_cents)}</td>
                <td>{formatMoneyCents(row.commission_cents)}</td>
                <td>{numberFormatter.format(safeNumber(row.needs_reconciliation))}</td>
              </tr>
            )) : <tr><td className="vkpi-table-empty" colSpan={7}>暂无 Amazon 真实导入数据。</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead><tr><th>Source Ref</th><th>Campaign</th><th>ASIN / SKU</th><th>项目</th><th>销售额</th><th>置信度</th><th>时间</th></tr></thead>
          <tbody>
            {rows.length ? rows.slice(0, 8).map((row, index) => (
              <tr key={textValue(row.id || row.source_ref, String(index))}>
                <td>{textValue(row.source_ref, '-')}</td>
                <td>{textValue(row.amazon_tag || row.campaign_id || row.campaign_name, '-')}</td>
                <td>{textValue(row.asin || row.product_sku, '-')}</td>
                <td>{textValue(row.project_id || row.project_name, '-')}</td>
                <td>{formatMoneyCents(row.revenue_cents)}</td>
                <td>{textValue(row.confidence, '-')}</td>
                <td>{textValue(row.occurred_at || row.report_date || row.imported_at || row.created_at, '-')}</td>
              </tr>
            )) : <tr><td className="vkpi-table-empty" colSpan={7}>暂无 Amazon 归因明细。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
