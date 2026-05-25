import type { VkpiAgentInboxItem } from '../../../../services/vkpi/dashboard-api';
import type {
  IntelligenceAction,
  IntelligenceCardModel,
} from '../../intelligence/IntelligenceCard';

export type Row = Record<string, unknown>;
export type ReadinessTone = 'ready' | 'partial' | 'planned';

export interface ExternalIntelligenceSource {
  key: string;
  title: string;
  scope: string;
  statusLabel: string;
  tone: ReadinessTone;
  readiness: number;
  cadence: string;
  evidence: string;
  blocked: string;
}

export interface PlatformAdvicePlan {
  key: string;
  platform: string;
  bestFor: string;
  signalNeed: string;
  output: string;
  readiness: number;
  query: string;
}

const INTELLIGENCE_FOCUS_KEY = 'vkpi:intelligence-center:focus';

export const externalIntelligenceSources: ExternalIntelligenceSource[] = [
  {
    key: 'owned-video-library',
    title: '自有官方视频库',
    scope: '18 个官方账号内容表现、缩略图、互动率、评论状态。',
    statusLabel: '已有数据',
    tone: 'ready',
    readiness: 78,
    cadence: '每日只读聚合',
    evidence: 'official channels / media cache / content metrics',
    blocked: '不做 1012 deep scan，不伪造视频理解。',
  },
  {
    key: 'kol-video-library',
    title: 'KOL 视频库',
    scope: '只分析 qualified KOL、合作 KOL、近期 Viltrox 提及账号的视频。',
    statusLabel: '分层后接入',
    tone: 'partial',
    readiness: 44,
    cadence: '按需 / 热层',
    evidence: 'refresh tier / collaboration / brand signals',
    blocked: '不回到 1021/1023 全量每日刷新。',
  },
  {
    key: 'reddit-market',
    title: 'Reddit / 社区趋势',
    scope: '摄影社区问题、购买顾虑、真实口碑和竞品讨论。',
    statusLabel: '待小口',
    tone: 'planned',
    readiness: 24,
    cadence: '先白名单 subreddit',
    evidence: 'OAuth 或 best-effort source refs',
    blocked: '不承诺全量评论，不绕过平台限制。',
  },
  {
    key: 'google-trends-news',
    title: 'Google Trends / News',
    scope: '镜头型号、卡口、竞品新品、地区兴趣变化。',
    statusLabel: '待接入',
    tone: 'planned',
    readiness: 18,
    cadence: '每日摘要',
    evidence: 'trend query / news source / capture time',
    blocked: '不自动外抓，不生成无来源事实。',
  },
  {
    key: 'competitor-radar',
    title: '竞品雷达',
    scope: 'Sigma、Tamron、Sony 等新品、KOL 重合、风险提及。',
    statusLabel: '部分已有',
    tone: 'partial',
    readiness: 52,
    cadence: '只读信号 + 人工复核',
    evidence: 'market_intelligence_v0 / competitor signals',
    blocked: '未复核前不自动改推荐排序。',
  },
  {
    key: 'gemini-llm-summary',
    title: 'Gemini 视频理解 + GPT/Claude 总结',
    scope: '只总结已有视频、帖子、评论证据，输出平台建议和内容角度。',
    statusLabel: '预算后启用',
    tone: 'planned',
    readiness: 31,
    cadence: '单 KOL / 单视频小口',
    evidence: 'video evidence refs / provider_calls / cost ledger',
    blocked: '不跑批量 Gemini，不让 LLM 生产事实。',
  },
];

export const platformAdvicePlans: PlatformAdvicePlan[] = [
  {
    key: 'youtube',
    platform: 'YouTube',
    bestFor: '长评测、对比、样片解释、SEO 长尾。',
    signalNeed: '标题趋势、完播、评论问题、竞品同档视频。',
    output: '决定本周该发长评测、对比片还是 Shorts 引流。',
    readiness: 46,
    query: 'Viltrox lens review creator YouTube',
  },
  {
    key: 'tiktok',
    platform: 'TikTok',
    bestFor: '快速视觉钩子、场景化样片、热门声音/形式复刻。',
    signalNeed: '当日热点、3 秒留存、互动话题、KOL 短视频风格。',
    output: '给出 3 条可拍脚本方向和适合投放的 KOL 类型。',
    readiness: 28,
    query: 'Viltrox camera lens TikTok creator',
  },
  {
    key: 'instagram',
    platform: 'Instagram',
    bestFor: 'Reels、摄影作品展示、创作者合作露出。',
    signalNeed: '缩略图清晰度、保存率、标签、创作者审美风格。',
    output: '建议发 Reels / Carousel / Story 的组合。',
    readiness: 40,
    query: 'Viltrox photography reels creator Instagram',
  },
  {
    key: 'reddit-x',
    platform: 'Reddit / X',
    bestFor: '真实问题发现、竞品舆情、发布前风险探测。',
    signalNeed: '社区问题、竞品吐槽、价格敏感、卡口需求。',
    output: '形成 FAQ、避坑话术和产品机会提醒。',
    readiness: 22,
    query: 'Viltrox Sigma Tamron lens discussion Reddit',
  },
  {
    key: 'facebook',
    platform: 'Facebook',
    bestFor: '社群内容、老用户维护、地区活动扩散。',
    signalNeed: '社群回应、地区热度、活动预算和转化链路。',
    output: '建议地区活动和复用素材，不作为核心趋势源。',
    readiness: 25,
    query: 'Viltrox photography community Facebook',
  },
];

export function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

export function asList(value: unknown): Row[] {
  return Array.isArray(value)
    ? value.filter((item): item is Row => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    : [];
}

export function readIntelligenceFocus(): Row {
  if (typeof window === 'undefined') return {};
  const raw = window.sessionStorage.getItem(INTELLIGENCE_FOCUS_KEY);
  if (!raw) return {};
  try {
    return asRecord(JSON.parse(raw));
  } catch {
    return {};
  }
}

export function clearIntelligenceFocus() {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(INTELLIGENCE_FOCUS_KEY);
}

export function text(value: unknown, fallback = '-'): string {
  const next = String(value ?? '').trim();
  return next || fallback;
}

export function numberText(value: unknown): string {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed.toLocaleString('en-US') : '0';
}

export function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function formatTime(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function priorityFromAgent(item?: VkpiAgentInboxItem): IntelligenceCardModel['priority'] {
  if (!item) return 'low';
  if (item.status === 'warning' || item.passed === false) return 'high';
  return item.status === 'active' ? 'medium' : 'low';
}

function confidenceFromAgent(item?: VkpiAgentInboxItem): number | undefined {
  if (!item) return undefined;
  if (item.status === 'active' && item.passed) return 0.82;
  if (item.status === 'warning') return 0.5;
  return 0.2;
}

export function cardFromAgent(
  item: VkpiAgentInboxItem | undefined,
  fallback: { id: string; type: IntelligenceCardModel['type']; title: string; summary: string; entityType: string },
): IntelligenceCardModel {
  const summary = item?.details?.summary || {};
  const recommendationId = text(summary.recommendation_id || summary.recommendationId || summary.rec_id, '');
  return {
    id: item?.id || fallback.id,
    type: fallback.type,
    priority: priorityFromAgent(item),
    status: item?.status === 'warning' ? 'blocked' : 'open',
    title: item?.title || fallback.title,
    summary: item?.summary || fallback.summary,
    entityType: fallback.entityType,
    entityId: recommendationId || undefined,
    confidence: confidenceFromAgent(item),
    freshnessLabel: item ? formatTime(item.generated_at || item.last_run_at) : '待输出',
    sourceLabel: item?.artifact_name || item?.source || 'runtime/ops',
    evidence: [
      { label: 'Agent', source: item?.agent_name || fallback.entityType, value: item?.mode || 'read_only' },
      { label: 'Artifact', source: 'runtime/ops', value: item?.artifact_name || 'not_ready' },
      ...Object.entries(summary).slice(0, 5).map(([key, value]) => ({ label: key, source: 'summary', value: text(value) })),
    ],
    actions: [
      { label: '查看证据', kind: 'primary' },
      { label: fallback.type === 'recommendation' ? '反馈待接入' : '生成任务待接入', kind: 'disabled' },
    ],
    metadata: {
      agent_id: item?.agent_id,
      artifact_name: item?.artifact_name,
      recommendation_id: recommendationId || undefined,
    },
  };
}

export function cardFromMarketSignal(item: Row, index: number, kind: 'market' | 'competitor'): IntelligenceCardModel {
  const brand = text(item.normalized_brand || item.brand, 'unknown');
  const signalType = text(item.signal_type, 'signal');
  const score = Number(item.score || 0);
  const normalizedScore = score > 1 ? score / 100 : score;
  const severity = text(item.severity, 'medium').toLowerCase();
  const isPending = text(item.review_status, '').toLowerCase() === 'pending_review';
  const sourceUrl = text(item.source_url, '');
  const sourceTable = text(item.source_table, 'vkpi_competitor_signals');
  const sourceId = text(item.source_id, '');
  const productHints = Array.isArray(item.product_hints) ? item.product_hints.map((value) => text(value, '')).filter(Boolean).slice(0, 4) : [];
  const priority = severity === 'high' || severity === 'danger' || normalizedScore >= 0.75
    ? 'high'
    : normalizedScore >= 0.4
      ? 'medium'
      : 'low';
  return {
    id: `${kind}-${text(item.signal_uid || item.id, String(index))}`,
    type: kind,
    priority,
    status: 'open',
    title: `${isPending ? '待确认竞品预警' : '竞品信号'} · ${brand} · ${signalType}`,
    summary: `${isPending ? '待人工确认，不是最终结论。' : ''}${text(item.detail, '等待市场信号详情')}`.slice(0, 220),
    entityType: kind === 'competitor' ? 'competitor_signal' : 'market_signal',
    entityId: text(item.id, '') || undefined,
    confidence: normalizedScore > 0 ? Math.min(0.95, Math.max(0.2, normalizedScore)) : undefined,
    freshnessLabel: `${text(item.platform, 'existing signal')} · ${text(item.review_status, 'pending_review')}`,
    sourceLabel: sourceTable,
    evidence: [
      { label: 'Brand', source: 'signal', value: brand },
      { label: 'Type', source: 'signal', value: signalType },
      { label: 'Status', source: 'review_status', value: text(item.review_status, 'pending_review') },
      { label: 'Severity', source: 'severity', value: severity },
      { label: 'Score', source: 'score', value: score ? score.toFixed(score > 1 ? 1 : 2) : '-' },
      { label: 'Products', source: 'product_hints', value: productHints.length ? productHints.join(' / ') : '-' },
      { label: 'Platform', source: 'source', value: text(item.platform, '-') },
      { label: 'Source Row', source: sourceTable, value: sourceId || '-' },
      { label: 'URL', source: 'source_url', value: sourceUrl || '-', url: sourceUrl || undefined },
    ],
    actions: [
      { label: '查看证据', kind: 'primary' },
      { label: isPending ? '待人工确认' : '转机会卡待接入', kind: 'disabled' },
    ],
    metadata: {
      review_status: item.review_status,
      signal_uid: item.signal_uid,
      source_url: sourceUrl,
      raw: item,
    },
  };
}

const cardTypes: IntelligenceCardModel['type'][] = ['brief', 'recommendation', 'evidence', 'sync', 'market', 'competitor', 'kol', 'repair', 'system'];
const cardPriorities: IntelligenceCardModel['priority'][] = ['high', 'medium', 'low'];
const cardStatuses: IntelligenceCardModel['status'][] = ['open', 'accepted', 'rejected', 'snoozed', 'done', 'blocked'];

export function normalizeApiCard(item: Row, index: number): IntelligenceCardModel {
  const evidenceRows = asList(item.evidence);
  const actionRows = asList(item.actions);
  const type = text(item.type, 'market') as IntelligenceCardModel['type'];
  const priority = text(item.priority, 'medium') as IntelligenceCardModel['priority'];
  const status = text(item.status, 'open') as IntelligenceCardModel['status'];
  return {
    id: text(item.id, `market-api-card-${index}`),
    type: cardTypes.includes(type) ? type : 'market',
    priority: cardPriorities.includes(priority) ? priority : 'medium',
    status: cardStatuses.includes(status) ? status : 'open',
    title: text(item.title, '市场情报卡'),
    summary: text(item.summary, '等待市场情报详情'),
    entityType: text(item.entityType || item.entity_type, 'market_signal'),
    entityId: text(item.entityId || item.entity_id, '') || undefined,
    confidence: typeof item.confidence === 'number' ? item.confidence : undefined,
    freshnessLabel: text(item.freshnessLabel || item.freshness_label, ''),
    sourceLabel: text(item.sourceLabel || item.source_label, 'market-intelligence/cards/v0'),
    evidence: evidenceRows.map((row) => ({
      label: text(row.label, 'Evidence'),
      source: text(row.source, 'market-intelligence'),
      value: text(row.value, '-'),
      url: text(row.url, '') || undefined,
    })),
    actions: actionRows.map((row) => {
      const kind = text(row.kind, 'secondary') as IntelligenceAction['kind'];
      return {
        label: text(row.label, '查看证据'),
        kind: ['primary', 'secondary', 'disabled'].includes(kind) ? kind : 'secondary',
      };
    }),
    metadata: {
      source: 'market-intelligence/cards/v0',
      quality_gate: item.quality_gate,
      raw: item,
    },
  };
}

function shortList(value: unknown, limit = 3): string {
  if (!Array.isArray(value)) return text(value, '-');
  return value.slice(0, limit).map((item) => text(item, '')).filter(Boolean).join(' / ') || '-';
}

export function cardFromRecommendationBacklog(item: Row, index: number): IntelligenceCardModel {
  const kol = asRecord(item.kol);
  const launch = asRecord(item.launch);
  const suggestion = asRecord(item.suggestion);
  const evidence = asRecord(item.evidence);
  const recommendationId = text(item.recommendation_id, '');
  const score = numberValue(item.score);
  const rank = numberValue(item.rank);
  const platform = text(kol.platform, 'platform');
  const handle = text(kol.handle || kol.display_name, `candidate ${index + 1}`);
  const product = text(launch.product_sku || launch.product_name || launch.name, '未绑定 SKU');
  const suggestedAction = text(suggestion.suggested_action, 'needs_human_review');
  const suggestedType = text(suggestion.suggested_feedback_type, 'needs_review');
  const reasons = Array.isArray(suggestion.reasons) ? suggestion.reasons.map((value) => text(value, '')).filter(Boolean) : [];
  const proEvidence = Array.isArray(evidence.evidence_pro) ? evidence.evidence_pro : [];
  const conEvidence = Array.isArray(evidence.evidence_con) ? evidence.evidence_con : [];
  return {
    id: `recommendation-backlog-${recommendationId || index}`,
    type: 'recommendation',
    priority: score >= 75 || suggestedAction.includes('reject') ? 'high' : score >= 45 ? 'medium' : 'low',
    status: 'open',
    title: `${platform}:${handle} · ${product}`,
    summary: `rank ${rank || '-'} · score ${score ? score.toFixed(1) : '-'} · ${suggestedAction}`,
    entityType: 'recommendation',
    entityId: recommendationId || undefined,
    confidence: score ? Math.max(0.2, Math.min(0.95, score / 100)) : undefined,
    freshnessLabel: text(item.run_uid, 'recommendation backlog'),
    sourceLabel: 'learning/recommendation-feedback-backlog',
    evidence: [
      { label: 'Recommendation ID', source: 'vkpi_kol_recommendations', value: recommendationId || '-' },
      { label: 'Run', source: text(item.strategy_version, 'strategy'), value: text(item.run_uid, '-') },
      { label: 'KOL', source: platform, value: `${handle} · kol_pool_id=${text(kol.kol_pool_id, '-')}` },
      { label: 'Launch', source: 'product_launch', value: product },
      { label: 'Suggested feedback', source: suggestedAction, value: suggestedType },
      { label: 'Reasons', source: 'suggestion', value: reasons.join(' / ') || '-' },
      { label: 'Evidence pro', source: 'explanation', value: shortList(proEvidence) },
      { label: 'Evidence con', source: 'explanation', value: shortList(conEvidence) },
    ],
    actions: [
      { label: '查看证据', kind: 'primary' },
      { label: '接受 / 拒绝可写入', kind: recommendationId ? 'secondary' : 'disabled' },
    ],
    metadata: {
      recommendation_id: recommendationId || undefined,
      recommendation_uid: item.recommendation_uid,
      run_uid: item.run_uid,
      strategy_version: item.strategy_version,
      suggested_action: suggestedAction,
      suggested_feedback_type: suggestedType,
      score,
      rank,
      kol,
      launch,
    },
  };
}

function toneLabel(tone: ReadinessTone): string {
  if (tone === 'ready') return '已可读';
  if (tone === 'partial') return '部分就绪';
  return '待接入';
}

export function sourceCardFromPlan(source: ExternalIntelligenceSource): IntelligenceCardModel {
  return {
    id: `source-plan-${source.key}`,
    type: source.key.includes('competitor') ? 'competitor' : 'market',
    priority: source.tone === 'ready' ? 'medium' : 'low',
    status: source.tone === 'ready' ? 'open' : 'blocked',
    title: source.title,
    summary: source.scope,
    entityType: 'intelligence_source',
    confidence: source.readiness / 100,
    freshnessLabel: source.statusLabel,
    sourceLabel: source.evidence,
    evidence: [
      { label: '准备状态', source: 'readiness', value: toneLabel(source.tone) },
      { label: '覆盖范围', source: 'source plan', value: source.scope },
      { label: '节奏', source: 'cadence', value: source.cadence },
      { label: '证据要求', source: 'evidence', value: source.evidence },
      { label: '禁止项', source: 'guardrail', value: source.blocked },
    ],
    actions: [
      { label: '查看证据', kind: 'primary' },
      { label: '接入前检查', kind: 'secondary' },
    ],
    metadata: {
      readiness: source.readiness,
      tone: source.tone,
      source_key: source.key,
    },
  };
}

export function platformCardFromPlan(plan: PlatformAdvicePlan): IntelligenceCardModel {
  return {
    id: `platform-advice-${plan.key}`,
    type: 'market',
    priority: plan.readiness >= 40 ? 'medium' : 'low',
    status: 'blocked',
    title: `${plan.platform} 平台建议`,
    summary: plan.output,
    entityType: 'platform_advice',
    confidence: plan.readiness / 100,
    freshnessLabel: '待接入趋势源',
    sourceLabel: 'platform advice plan',
    evidence: [
      { label: '适合内容', source: plan.platform, value: plan.bestFor },
      { label: '需要信号', source: 'trend inputs', value: plan.signalNeed },
      { label: '输出目标', source: 'planned output', value: plan.output },
      { label: '发现查询', source: 'discover focus', value: plan.query },
      { label: '当前状态', source: 'guardrail', value: '只展示计划，不运行外部搜索、Gemini 或 LLM。' },
    ],
    actions: [
      { label: '查看证据', kind: 'primary' },
      { label: '去发现红人', kind: 'secondary' },
      { label: '接入前检查', kind: 'secondary' },
    ],
    metadata: {
      platform: plan.platform,
      readiness: plan.readiness,
    },
  };
}

function priorityFromPlan(value: unknown): IntelligenceCardModel['priority'] {
  const priority = text(value, 'low');
  if (priority === 'high' || priority === 'medium' || priority === 'low') return priority;
  return 'low';
}

export function cardFromDailyPlanGroup(group: Row, index: number): IntelligenceCardModel {
  const sources = asList(group.candidate_sources);
  const sourceGroup = text(group.source_group, `daily-plan-${index}`);
  const label = text(group.label, sourceGroup);
  const plannedHttpCalls = numberValue(group.planned_http_calls);
  const plannedItemLimit = numberValue(group.planned_item_limit);
  const estimatedCost = Number(group.estimated_cost_usd ?? 0);
  return {
    id: `daily-plan-${sourceGroup}`,
    type: sourceGroup.includes('competitor') ? 'competitor' : 'market',
    priority: priorityFromPlan(group.priority),
    status: 'blocked',
    title: `今日候选 · ${label}`,
    summary: `${plannedHttpCalls} 次 HTTP / 最多 ${plannedItemLimit} 条结果 · ${text(group.reason, '等待人工确认后执行小口 smoke')}`,
    entityType: 'external_daily_plan',
    confidence: plannedHttpCalls > 0 ? Math.min(0.9, 0.45 + plannedHttpCalls * 0.08) : 0.2,
    freshnessLabel: '只读计划 · 待确认',
    sourceLabel: 'market-external-daily-plan/v0',
    evidence: [
      { label: 'Source group', source: 'daily plan', value: sourceGroup },
      { label: '计划 HTTP', source: 'bounded plan', value: `${plannedHttpCalls}` },
      { label: '计划结果上限', source: 'bounded plan', value: `${plannedItemLimit}` },
      { label: '预估成本', source: 'cost guard', value: `$${estimatedCost.toFixed(2)}` },
      { label: '执行模式', source: 'guardrail', value: text(group.execution_mode, 'manual_smoke_only') },
      { label: '人工确认', source: 'guardrail', value: group.requires_human_ack ? 'required' : 'missing' },
      ...sources.slice(0, 5).map((source) => ({
        label: text(source.source_key, 'source'),
        source: text(source.provider, 'provider'),
        value: text(source.query || source.url, '-'),
        url: text(source.url, '') || undefined,
      })),
    ],
    actions: [
      { label: '查看证据', kind: 'primary' },
      { label: '待人工确认执行', kind: 'disabled' },
    ],
    metadata: {
      source_group: sourceGroup,
      planned_http_calls: plannedHttpCalls,
      planned_item_limit: plannedItemLimit,
      daily_plan_group: true,
    },
  };
}

export function sourceForCard(card: IntelligenceCardModel): ExternalIntelligenceSource | undefined {
  const key = text(card.metadata?.source_key, '').replace(/^source-plan-/, '');
  return externalIntelligenceSources.find((source) => source.key === key);
}

export function platformPlanForCard(card: IntelligenceCardModel): PlatformAdvicePlan | undefined {
  const platform = text(card.metadata?.platform, '');
  if (platform) return platformAdvicePlans.find((plan) => plan.platform === platform);
  const key = card.id.replace(/^platform-advice-/, '');
  return platformAdvicePlans.find((plan) => plan.key === key);
}
