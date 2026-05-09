import { InfoBlock } from '../../shared/InfoBlock';
import { platformDisplay, safeNumber } from '../../shared/vkpiDataUtils';

type Row = Record<string, unknown>;

export function RecommendationOutcomeTable({ outcomeSummary }: { outcomeSummary?: Row }) {
  const totals = ((outcomeSummary?.totals || {}) as Row);
  const conversion = ((outcomeSummary?.conversion || {}) as Row);
  const outcomeSources = ((outcomeSummary?.source_rows || []) as Row[]);

  return (
    <section className="vkpi-card vkpi-table-card">
      <div className="vkpi-table-card__header">
        <div>
          <h2>推荐 Outcome 转化</h2>
          <span>{safeNumber(outcomeSummary?.source_count)} 条来源</span>
        </div>
      </div>
      <div className="vkpi-result-grid">
        <InfoBlock label="入选率" value={`${Math.round(safeNumber(conversion.shortlisted) * 100)}%`} />
        <InfoBlock label="认领率" value={`${Math.round(safeNumber(conversion.claimed) * 100)}%`} />
        <InfoBlock label="建项率" value={`${Math.round(safeNumber(conversion.project_created) * 100)}%`} />
        <InfoBlock label="发布率" value={`${Math.round(safeNumber(conversion.content_published) * 100)}%`} />
        <InfoBlock label="归因点击" value={String(safeNumber(totals.attributed_clicks))} />
        <InfoBlock label="归因订单" value={String(safeNumber(totals.attributed_orders))} />
      </div>
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead><tr><th>推荐</th><th>平台</th><th>分数</th><th>状态</th><th>建项</th><th>发布</th><th>出单</th><th>GMV</th><th>ROI</th></tr></thead>
          <tbody>
            {outcomeSources.length ? outcomeSources.slice(0, 12).map((row) => (
              <tr key={`${String(row.recommendation_id)}-${String(row.outcome_id || '')}`}>
                <td>{String(row.display_name || row.handle || row.recommendation_id || '-')}</td>
                <td>{platformDisplay(row.platform)}</td>
                <td>{String(row.score || '-')}</td>
                <td>{String(row.status || '-')}</td>
                <td>{safeNumber(row.project_created) ? '是' : '-'}</td>
                <td>{safeNumber(row.content_published) ? '是' : '-'}</td>
                <td>{safeNumber(row.order_attributed) ? '是' : '-'}</td>
                <td>${(safeNumber(row.attributed_gmv_cents) / 100).toLocaleString()}</td>
                <td>{row.computed_roi == null ? '-' : `${safeNumber(row.computed_roi).toFixed(2)}x`}</td>
              </tr>
            )) : <tr><td className="vkpi-table-empty" colSpan={9}>暂无推荐 outcome。推荐生成后会冻结特征，员工动作和后续销售会写入这里。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
