import { arrayValue, objectValue, safeNumber, textValue } from '../../shared/vkpiDataUtils';
import type { SmartRecommendation } from './DiscoverRecommendationPanel';
import type { UiKol } from './DiscoverTypes';
import { rawToUiKol } from './DiscoverKolModel';

export function jsonObjectValue(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  try {
    const parsed = JSON.parse(String(value || '{}'));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function recommendationToSmart(row: Record<string, unknown>, index: number): SmartRecommendation {
  const explanation = jsonObjectValue(row.explanation_json);
  const scoringBreakdown = jsonObjectValue(row.scoring_breakdown_json);
  const kol = objectValue(row.kol);
  const launch = objectValue(row.launch);
  const suggestion = objectValue(row.suggestion);
  const competitor = objectValue(row.competitor);
  const handle = textValue(kol.handle || kol.display_name, `candidate-${index + 1}`);
  const score = safeNumber(row.score || scoringBreakdown.final_score || suggestion.score);
  return {
    id: textValue(row.recommendation_uid || row.id, `rec-${index}`),
    kolId: textValue(kol.id || row.kol_id || row.linked_main_kol_id, ''),
    handle: handle.startsWith('@') ? handle : `@${handle}`,
    displayName: textValue(kol.display_name || kol.media_name || handle, handle),
    platform: textValue(kol.platform, 'unknown'),
    score,
    rank: safeNumber(row.rank) || index + 1,
    status: textValue(row.status, 'pending'),
    reason: arrayValue(explanation.reasons).map((value) => textValue(value, '')).filter(Boolean)[0]
      || arrayValue(suggestion.reasons).map((value) => textValue(value, '')).filter(Boolean)[0]
      || textValue(suggestion.suggested_action, '推荐待复核'),
    source: textValue(row.source || suggestion.source || launch.product_sku, 'product recommendation'),
    competitorRiskTier: textValue(competitor.risk_tier || row.competitor_risk_tier, 'opportunity'),
    competitorRiskScore: safeNumber(competitor.risk_score || row.competitor_risk_score),
    competitorBrand: textValue(competitor.brand, ''),
    raw: row,
  };
}

export function recommendationToUiKol(row: SmartRecommendation): UiKol {
  return rawToUiKol({
    ...row.raw,
    id: row.kolId || row.handle,
    linked_main_kol_id: row.kolId || undefined,
    channel_name: row.handle.replace(/^@/, ''),
    media_name: row.displayName,
    handle: row.handle,
    platform: row.platform,
    score: row.score,
  });
}
