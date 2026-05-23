import { platformDisplay } from '../../shared/vkpiDataUtils';

type Row = Record<string, unknown>;
type RecommendationAction = 'shortlist' | 'reject' | 'claim' | 'create_project';

interface RecommendationCandidateTableProps {
  busy: boolean;
  recommendations: Row[];
  readOnly?: boolean;
  onSelect: (row: Row) => void;
  onAction: (id: unknown, action: RecommendationAction) => void | Promise<void>;
}

function recordValue(value: unknown): Row {
  if (!value) return {};
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Row : {};
    } catch {
      return {};
    }
  }
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

function arrayValue(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item): item is Row => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : [];
}

function catalogProducts(row: Row): Row[] {
  const snapshot = recordValue(row.feature_snapshot || row.feature_snapshot_json);
  return arrayValue(snapshot.matched_catalog_products || row.matched_catalog_products);
}

function scoringBreakdown(row: Row): Row {
  return recordValue(row.scoring_breakdown || row.scoring_breakdown_json);
}

function numberValue(value: unknown): number {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function money(value: unknown): string {
  const amount = Number(value || 0);
  return Number.isFinite(amount) && amount > 0 ? `$${amount.toLocaleString('en-US')}` : '-';
}

export function RecommendationCandidateTable({
  busy,
  recommendations,
  readOnly = false,
  onSelect,
  onAction,
}: RecommendationCandidateTableProps) {
  return (
    <section className="vkpi-card vkpi-table-card">
      <div className="vkpi-table-card__header"><div><h2>产品推荐候选</h2><span>{recommendations.length} 条</span></div></div>
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead><tr><th>排名</th><th>平台</th><th>红人</th><th>分数</th><th>状态</th><th>模型</th><th>主 KOL</th><th>操作</th></tr></thead>
          <tbody>
            {recommendations.length ? recommendations.map((row) => {
              const variants = catalogProducts(row).slice(0, 3);
              const breakdown = scoringBreakdown(row);
              const competitor = recordValue(breakdown.competitor);
              const feedback = recordValue(breakdown.operator_feedback);
              const competitorTier = String(competitor.risk_tier || '');
              const feedbackAdjustment = numberValue(feedback.score_adjustment);
              return (
                <tr key={String(row.id)}>
                  <td>{String(row.rank || '-')}</td>
                  <td>{platformDisplay(row.platform)}</td>
                  <td>
                    <div className="vkpi-rec-candidate-main">
                      <button className="vkpi-link-button" type="button" onClick={() => onSelect(row)}>{String(row.display_name || row.handle || '-')}</button>
                      {variants.length ? (
                        <div className="vkpi-rec-sku-list" aria-label="官方 SKU 变体">
                          {variants.map((product) => (
                            <span key={String(product.sku || product.model_name)}>
                              <strong>{String(product.sku || '-')}</strong>
                              <em>{String(product.mount || '-')} · {money(product.price_usd)}</em>
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </td>
                  <td>
                    <div className="vkpi-rec-candidate-main">
                      <strong>{String(row.score || '-')}</strong>
                      {feedback.source === 'vkpi_recommendation_feedback' ? (
                        <span className="vkpi-help-text">反馈 {feedbackAdjustment > 0 ? '+' : ''}{feedbackAdjustment}</span>
                      ) : null}
                      {competitorTier && competitorTier !== 'opportunity' ? (
                        <span className="vkpi-help-text">竞品 {String(competitor.brand || 'risk')} · {competitorTier}</span>
                      ) : null}
                    </div>
                  </td>
                  <td>{String(row.status || '-')}</td>
                  <td>rule_v0</td>
                  <td>{row.linked_main_kol_id ? `#${String(row.linked_main_kol_id)}` : '未落库'}</td>
                  <td>
                    {readOnly ? (
                      <span className="vkpi-help-text">Preview only</span>
                    ) : (
                      <>
                        <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onAction(row.id, 'shortlist')}>入选</button>
                        <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onAction(row.id, 'claim')}>认领</button>
                        <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onAction(row.id, 'create_project')}>建项目</button>
                        <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onAction(row.id, 'reject')}>忽略</button>
                      </>
                    )}
                  </td>
                </tr>
              );
            }) : <tr><td className="vkpi-table-empty" colSpan={8}>暂无推荐候选。先创建发布项目并导入真实 KOL 池。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
