import { InfoBlock } from '../../shared/InfoBlock';
import { platformDisplay, safeNumber } from '../../shared/vkpiDataUtils';

type Row = Record<string, unknown>;

interface RecommendationDetailDrawerProps {
  recommendation: Row;
  evidence: Row | null;
  loading: boolean;
  onClose: () => void;
}

function prettyJson(value: unknown, fallback: unknown = {}) {
  try {
    if (typeof value === 'string') return JSON.stringify(JSON.parse(value || '{}'), null, 2);
    return JSON.stringify(value ?? fallback, null, 2);
  } catch {
    return JSON.stringify(fallback, null, 2);
  }
}

function recordValue(value: unknown): Row {
  if (!value) return {};
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value || '{}');
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

function money(value: unknown): string {
  const amount = Number(value || 0);
  return Number.isFinite(amount) && amount > 0 ? `$${amount.toLocaleString('en-US')}` : '-';
}

export function RecommendationDetailDrawer({
  recommendation,
  evidence,
  loading,
  onClose,
}: RecommendationDetailDrawerProps) {
  const evidenceRows = ((evidence?.source_rows || []) as Row[]);
  const selectedEvidence = ((evidence?.evidence || {}) as Row);
  const selectedOutcome = ((evidence?.outcome || {}) as Row);
  const selectedProjects = ((evidence?.projects || []) as Row[]);
  const selectedLinks = ((evidence?.links || []) as Row[]);
  const selectedMessages = ((evidence?.messages || []) as Row[]);
  const selectedContent = ((evidence?.content || []) as Row[]);
  const selectedAttribution = ((evidence?.attribution || []) as Row[]);
  const selectedCosts = ((evidence?.costs || []) as Row[]);
  const selectedOrders = ((evidence?.shopify_orders || []) as Row[]);
  const costCents = selectedCosts.reduce((sum, row) => sum + safeNumber(row.amount_cents), 0);
  const featureSnapshot = recordValue(evidence?.feature_snapshot || recommendation.feature_snapshot_json);
  const catalogProducts = arrayValue(featureSnapshot.matched_catalog_products);

  return (
    <aside className="vkpi-drawer">
      <div className="vkpi-drawer__header">
        <div>
          <span className="vkpi-eyebrow">RECOMMENDATION DETAIL</span>
          <h2>{String(recommendation.display_name || recommendation.handle || '推荐候选')}</h2>
          <p>{platformDisplay(recommendation.platform)} · rank {String(recommendation.rank || '-')}</p>
        </div>
        <button className="vkpi-icon-button" type="button" onClick={onClose}>×</button>
      </div>
      <div className="vkpi-detail-grid">
        <InfoBlock label="分数" value={String(recommendation.score || '-')} />
        <InfoBlock label="状态" value={String(recommendation.status || '-')} />
        <InfoBlock label="模型版本" value={String(selectedEvidence.model_version || 'rule_v0')} />
        <InfoBlock label="KOL Pool ID" value={String(recommendation.kol_pool_id || '-')} />
        <InfoBlock label="来源行" value={loading ? '读取中' : String(safeNumber(evidence?.source_count))} />
        <InfoBlock label="Outcome" value={safeNumber(selectedOutcome.project_created) ? '已建项' : safeNumber(selectedOutcome.was_shortlisted) ? '已入选' : '已冻结'} />
      </div>
      <section className="vkpi-detail-section">
        <h3>官方 SKU / Specs</h3>
        {catalogProducts.length ? (
          <div className="vkpi-rec-product-grid">
            {catalogProducts.map((product) => {
              const specs = recordValue(product.specs);
              return (
                <article key={String(product.sku || product.model_name)}>
                  <header>
                    <strong>{String(product.sku || product.model_name || '-')}</strong>
                    <span>{String(product.mount || '-')} · {money(product.price_usd)}</span>
                  </header>
                  <p>{String(product.model_name || product.marketing_name || '-')}</p>
                  <div>
                    {String(specs.focal_length || '') ? <em>{String(specs.focal_length)}</em> : null}
                    {String(specs.aperture || '') ? <em>{String(specs.aperture)}</em> : null}
                    {String(specs.lens_elements || '') ? <em>{String(specs.lens_elements)}</em> : null}
                    {String(specs.weight || '') ? <em>{String(specs.weight)}</em> : null}
                    {String(specs.filter_size || '') ? <em>{String(specs.filter_size)}</em> : null}
                  </div>
                  {String(product.product_url || '') ? <a href={String(product.product_url)} target="_blank" rel="noreferrer">打开官网</a> : null}
                </article>
              );
            })}
          </div>
        ) : (
          <p className="vkpi-help-text">暂无官方 SKU 变体。旧推荐仍会显示产品线；新 Product Fit 运行会写入 FE / Z / X / L 等卡口和美元价格。</p>
        )}
      </section>
      <section className="vkpi-detail-section">
        <h3>Evidence Source Rows</h3>
        <div className="vkpi-table-wrap">
          <table className="vkpi-table">
            <thead><tr><th>来源</th><th>ID</th><th>说明</th><th>证据</th></tr></thead>
            <tbody>
              {evidenceRows.length ? evidenceRows.slice(0, 24).map((row, index) => {
                const sourceEvidence = ((row.evidence || {}) as Row);
                const raw = ((row.row || {}) as Row);
                return (
                  <tr key={`${String(row.source_type)}-${String(row.source_id)}-${index}`}>
                    <td>{String(row.source_type || '-')}</td>
                    <td>{String(row.source_id || raw.id || '-')}</td>
                    <td>{String(row.label || '-')}</td>
                    <td>{String(sourceEvidence.model_version || sourceEvidence.strategy_version || sourceEvidence.variant || raw.status || raw.stage || raw.source_ref || '-')}</td>
                  </tr>
                );
              }) : <tr><td className="vkpi-table-empty" colSpan={4}>{loading ? '正在读取真实证据链...' : '暂无下游证据。只有生成推荐后冻结的特征与 outcome 会先显示。'}</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      <div className="vkpi-detail-grid">
        <InfoBlock label="入选" value={safeNumber(selectedOutcome.was_shortlisted) ? '是' : '-'} />
        <InfoBlock label="认领" value={safeNumber(selectedOutcome.was_claimed) ? '是' : '-'} />
        <InfoBlock label="建项" value={safeNumber(selectedOutcome.project_created) ? '是' : '-'} />
        <InfoBlock label="发布" value={safeNumber(selectedOutcome.content_published) ? '是' : '-'} />
      </div>
      <section className="vkpi-detail-section">
        <h3>闭环回流</h3>
        <div className="vkpi-result-grid">
          <InfoBlock label="项目" value={selectedProjects.length ? String(selectedProjects.length) : '-'} />
          <InfoBlock label="Shopify 短链" value={selectedLinks[0]?.slug ? `/go/${String(selectedLinks[0].slug)}` : '-'} />
          <InfoBlock label="消息" value={selectedMessages.length ? String(selectedMessages.length) : '-'} />
          <InfoBlock label="内容" value={selectedContent.length ? String(selectedContent.length) : '-'} />
          <InfoBlock label="订单归因" value={selectedAttribution.length ? String(selectedAttribution.length) : '-'} />
          <InfoBlock label="Shopify 订单" value={selectedOrders.length ? String(selectedOrders.length) : '-'} />
          <InfoBlock label="成本" value={selectedCosts.length ? `$${(costCents / 100).toLocaleString()}` : '-'} />
          <InfoBlock label="已冻结特征" value={selectedEvidence.no_fake_platform_stats ? '真实/已冻结' : '读取中'} />
        </div>
        {selectedProjects[0] ? (
          <p className="vkpi-help-text">当前项目：{String(selectedProjects[0].project_name || selectedProjects[0].project_uid || selectedProjects[0].id)} · 阶段 {String(selectedProjects[0].stage || '-')}</p>
        ) : (
          <p className="vkpi-help-text">还未从推荐创建项目。点击“建项目”后会自动回流项目、认领、短链和 outcome。</p>
        )}
      </section>
      <section className="vkpi-detail-section">
        <h3>Feature Snapshot</h3>
        <pre className="vkpi-code-block">{prettyJson(featureSnapshot)}</pre>
      </section>
      <section className="vkpi-detail-section">
        <h3>Scoring Breakdown</h3>
        <pre className="vkpi-code-block">{prettyJson(evidence?.scoring_breakdown || recommendation.scoring_breakdown_json)}</pre>
      </section>
      <section className="vkpi-detail-section">
        <h3>Explanation</h3>
        <pre className="vkpi-code-block">{prettyJson(evidence?.explanation || recommendation.explanation_json)}</pre>
      </section>
    </aside>
  );
}
