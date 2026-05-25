import { platformLabels } from '../../shared/vkpiConstants';
import { platformFromRaw } from '../../shared/vkpiDataUtils';

export type RecommendationAction = 'shortlist' | 'reject' | 'claim';

export interface SmartRecommendation {
  id: string;
  kolId: string;
  handle: string;
  displayName: string;
  platform: string;
  score: number;
  rank: number;
  status: string;
  reason: string;
  source: string;
  competitorRiskTier: string;
  competitorRiskScore: number;
  competitorBrand: string;
  raw: Record<string, unknown>;
}

function DiscoverRecommendationSkeletons() {
  return (
    <>
      {[0, 1, 2].map((item) => (
        <div className="vkpi-discover-rec vkpi-discover-rec--skeleton" key={item} aria-hidden="true">
          <div>
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <div className="vkpi-discover-rec__actions">
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
            </div>
          </div>
          <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
        </div>
      ))}
    </>
  );
}

export function RecommendationPanel({
  recommendations,
  loading,
  message,
  localFallbackCount,
  selectedKolId,
  getActiveId,
  onSelect,
  onRefresh,
  onRegenerate,
  onAction,
}: {
  recommendations: SmartRecommendation[];
  loading: boolean;
  message: string;
  localFallbackCount: number;
  selectedKolId: string;
  getActiveId: (recommendation: SmartRecommendation) => string;
  onSelect: (recommendation: SmartRecommendation) => void;
  onRefresh: () => void;
  onRegenerate: () => void;
  onAction: (recommendation: SmartRecommendation, action: RecommendationAction) => void;
}) {
  return (
    <section className="vkpi-discover-panel">
      <div className="vkpi-discover-panel__header">
        <div>
          <h3>智能推荐</h3>
          <span>读取 Product Analysis 推荐表；操作回写推荐 action / outcome。</span>
        </div>
        <div className="vkpi-discover-rec-toolbar">
          <button type="button" onClick={onRefresh} disabled={loading}>刷新</button>
          <button type="button" onClick={onRegenerate} disabled={loading}>生成推荐</button>
        </div>
      </div>
      <div className="vkpi-discover-rec-filter">
        {['Campaign 补人', '新市场发现', '竞品对比', '入选', '认领', '拒绝'].map((item) => <span key={item}>{item}</span>)}
      </div>
      <div className="vkpi-discover-list">
        {message ? <div className="vkpi-discover-empty is-compact">{message}</div> : null}
        {recommendations.length ? recommendations.map((recommendation) => {
          const activeId = getActiveId(recommendation);
          const competitorTier = recommendation.competitorRiskTier;
          const competitorLabel = competitorTier
            ? `${recommendation.competitorBrand ? recommendation.competitorBrand.toUpperCase() : '竞品'} ${competitorTier}${recommendation.competitorRiskScore ? ` ${recommendation.competitorRiskScore.toFixed(1)}` : ''}`
            : '';
          return (
            <div
              className={`vkpi-discover-rec ${selectedKolId === activeId ? 'is-active' : ''} ${competitorTier ? `is-risk-${competitorTier}` : ''}`}
              role="button"
              tabIndex={0}
              key={recommendation.id}
              onClick={() => onSelect(recommendation)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') onSelect(recommendation);
              }}
            >
              <div>
                <strong>{recommendation.rank}. {recommendation.handle}</strong>
                <p>{platformLabels[platformFromRaw(recommendation.platform)] || recommendation.platform} · {recommendation.status} · {recommendation.source}</p>
                {competitorLabel ? <span className={`vkpi-discover-rec__risk is-${competitorTier}`}>{competitorLabel}</span> : null}
                <em>{recommendation.reason}</em>
                <div className="vkpi-discover-rec__actions" onClick={(event) => event.stopPropagation()}>
                  <button type="button" onClick={() => onAction(recommendation, 'shortlist')} disabled={loading}>入选</button>
                  <button type="button" onClick={() => onAction(recommendation, 'claim')} disabled={loading}>认领</button>
                  <button type="button" onClick={() => onAction(recommendation, 'reject')} disabled={loading}>拒绝</button>
                </div>
              </div>
              <b>{recommendation.score || '-'}</b>
            </div>
          );
        }) : loading ? (
          <DiscoverRecommendationSkeletons />
        ) : <div className="vkpi-discover-empty">推荐表暂无真实记录。本地候选可推荐 {localFallbackCount} 个，但这里不再用前端排序冒充智能推荐。</div>}
      </div>
    </section>
  );
}
