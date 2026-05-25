import { creatorPlatformOptions, platformLabels, stageLabels } from '../../shared/vkpiConstants';
import { arrayValue, objectValue, safeNumber, textValue } from '../../shared/vkpiDataUtils';
import type { VkpiDashboardData, VkpiPlatform, VkpiProjectRow } from '../../vkpiTypes';
import type { DiscoveryQueueItem } from './DiscoverQueuePanels';

export interface DirectionChip {
  label: string;
  query: string;
  source: string;
}

interface QueueKol {
  topic: string;
  country: string;
  platform: VkpiPlatform;
  contactCount: number;
  hasCollaboration: boolean;
  score: number;
}

function platformInputValue(platformLabel: string): string {
  const normalized = String(platformLabel || '').toLowerCase();
  return creatorPlatformOptions.find((option) => option.value === normalized || option.label.toLowerCase() === normalized)?.value || normalized || 'other';
}

function cleanProductLabel(value: unknown) {
  let label = textValue(value, '未命名产品');
  label = label.replace(/^viltrox\s+/i, '').replace(/\s+/g, ' ').trim();
  label = label.replace(/\s+(FE|E|Z|X|L|RF)$/i, ' ($1)');
  return label;
}

function rankedValues(values: string[]): string[] {
  const counts = new Map<string, number>();
  values.map((value) => value.trim()).filter(Boolean).forEach((value) => {
    counts.set(value, (counts.get(value) || 0) + 1);
  });
  return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).map(([value]) => value);
}

function addDirection(chips: DirectionChip[], chip: DirectionChip) {
  if (chips.some((item) => item.label === chip.label || item.query === chip.query)) return;
  chips.push(chip);
}

export function buildDirectionChips(kols: QueueKol[], productLaunches: VkpiDashboardData['productLaunches']): DirectionChip[] {
  const chips: DirectionChip[] = [];
  const products = productLaunches
    .map((product) => cleanProductLabel(product.productName || product.productSku || product.launchName))
    .filter(Boolean);
  const topics = rankedValues(kols.flatMap((kol) => kol.topic.split(/[\/,，;；|]/)).filter((topic) => !['待归类', '已建档红人'].includes(topic.trim())));
  const countries = rankedValues(kols.map((kol) => kol.country).filter((country) => country !== '-'));
  const platforms = rankedValues(kols.map((kol) => platformLabels[kol.platform] || kol.platform));
  const withContacts = kols.filter((kol) => kol.contactCount > 0).length;
  const withCollab = kols.filter((kol) => kol.hasCollaboration).length;
  const highScore = kols.filter((kol) => kol.score >= 80).length;

  if (products[0]) {
    addDirection(chips, {
      label: `${products[0]} 匹配方向`,
      query: `${products[0]} review portrait street`,
      source: '来自产品上市数据',
    });
  }
  if (topics[0]) {
    addDirection(chips, {
      label: `${topics[0]} 内容补人`,
      query: `找${countries[0] || ''}${topics[0]}${platforms[0] ? ` ${platforms[0]}` : ''} 中腰部红人`,
      source: '来自当前红人主题分布',
    });
  }
  if (countries[0] && platforms[0]) {
    addDirection(chips, {
      label: `${countries[0]} ${platforms[0]} 增量`,
      query: `${countries[0]} ${platforms[0]} 测评 街拍 portrait`,
      source: '来自地区和平台占比',
    });
  }
  if (withContacts) {
    addDirection(chips, {
      label: `优先可联系 ${withContacts}`,
      query: '有联系方式 高评分 可合作 红人',
      source: '来自联系方式完整度',
    });
  }
  if (withCollab || highScore) {
    addDirection(chips, {
      label: `高分复用 ${withCollab || highScore}`,
      query: '历史合作 高评分 ROI 可复用',
      source: '来自合作和评分信号',
    });
  }
  if (products[1]) {
    addDirection(chips, {
      label: `${products[1]} 新市场`,
      query: `${products[1]} YouTube Instagram 新市场`,
      source: '来自产品排期数据',
    });
  }

  if (!chips.length) {
    return [
      { label: '新品上市匹配方向', query: '找新品上市可合作红人', source: '等待真实数据后自动替换' },
      { label: '内容缺口补人方向', query: '找测评 街拍 portrait 中腰部红人', source: '等待真实数据后自动替换' },
      { label: '竞品对比方向', query: 'Sigma Tamron 对比内容 红人', source: '等待真实数据后自动替换' },
      { label: '可联系优先方向', query: '有联系方式 可合作 高评分红人', source: '等待真实数据后自动替换' },
    ];
  }

  return chips.slice(0, 5);
}

function discoveryPriority(score: number, fallback: DiscoveryQueueItem['priority'] = 'medium'): DiscoveryQueueItem['priority'] {
  if (score >= 75) return 'high';
  if (score >= 45) return 'medium';
  return fallback;
}

function recommendationDiscoveryItems(backlog: Record<string, unknown>): DiscoveryQueueItem[] {
  return arrayValue(backlog.items).slice(0, 4).map((item, index) => {
    const row = objectValue(item);
    const kol = objectValue(row.kol);
    const launch = objectValue(row.launch);
    const suggestion = objectValue(row.suggestion);
    const handle = textValue(kol.handle || kol.display_name, `candidate ${index + 1}`);
    const platformLabel = textValue(kol.platform, 'all');
    const product = textValue(launch.product_sku || launch.product_name || launch.name, '');
    const score = safeNumber(row.score);
    const reason = arrayValue(suggestion.reasons).map((value) => textValue(value, '')).filter(Boolean)[0]
      || textValue(suggestion.suggested_action, '推荐待复核');
    return {
      id: `rec-${textValue(row.recommendation_id || row.recommendation_uid, String(index))}`,
      type: 'recommendation',
      priority: discoveryPriority(score, 'medium'),
      title: `${handle} 待复核`,
      summary: product ? `${product} · score ${score || '-'}` : `score ${score || '-'} · 推荐待反馈`,
      query: handle.startsWith('@') ? handle : `${handle} ${product}`.trim(),
      platform: platformInputValue(platformLabel),
      source: 'recommendation feedback backlog',
      evidence: [
        `rank ${textValue(row.rank, '-')}`,
        reason,
        product || '未绑定 SKU',
      ],
    };
  });
}

function projectGapDiscoveryItems(projects: VkpiProjectRow[]): DiscoveryQueueItem[] {
  const activeProjects = projects
    .filter((project) => !['closed', 'lost', 'cancelled', 'released'].includes(project.stage))
    .slice(0, 5);
  return activeProjects.map((project, index) => {
    const product = project.productSku || project.productName || project.campaign;
    const platformLabel = platformLabels[project.platform] || project.platform;
    return {
      id: `project-gap-${project.id || index}`,
      type: 'project_gap',
      priority: ['stalled', 'contacted', 'replied', 'in_discussion'].includes(project.stage) ? 'high' : 'medium',
      title: `${project.campaign} 补相似 KOL`,
      summary: `${project.kolName} · ${stageLabels[project.stage] || project.stage}`,
      query: `${product} ${platformLabel} review creator`.trim(),
      platform: platformInputValue(platformLabel),
      source: 'active project gap',
      evidence: [
        `owner ${project.ownerName || '-'}`,
        `stage ${stageLabels[project.stage] || project.stage}`,
        product || '未绑定产品',
      ],
    };
  });
}

function brandSignalDiscoveryItems(signals: Array<Record<string, unknown>>): DiscoveryQueueItem[] {
  return signals.slice(0, 4).map((signal, index) => {
    const brand = textValue(signal.brand || signal.keyword || signal.entity, 'brand signal');
    const signalType = textValue(signal.signal_type, 'signal');
    const role = textValue(signal.brand_role, '');
    const platformLabel = textValue(signal.platform, 'all');
    const detail = textValue(signal.detail || signal.text || signal.title || signal.source_url, '');
    const isCompetitor = role.toLowerCase() === 'competitor' || signalType.toLowerCase().includes('competitor');
    return {
      id: `signal-${textValue(signal.id || signal.signal_uid, String(index))}`,
      type: 'brand_signal',
      priority: isCompetitor ? 'high' : 'medium',
      title: isCompetitor ? `${brand} 竞品信号` : `${brand} 品牌信号`,
      summary: detail ? detail.slice(0, 92) : signalType,
      query: `${brand} ${signalType} camera lens creator`.trim(),
      platform: platformInputValue(platformLabel),
      source: 'brand signal',
      evidence: [
        signalType,
        role || 'brand_role unknown',
        platformLabel,
      ],
    };
  });
}

export function buildDiscoveryQueue(input: {
  recommendationBacklog: Record<string, unknown>;
  projects: VkpiProjectRow[];
  brandSignals: Array<Record<string, unknown>>;
}): DiscoveryQueueItem[] {
  const items = [
    ...recommendationDiscoveryItems(input.recommendationBacklog),
    ...projectGapDiscoveryItems(input.projects),
    ...brandSignalDiscoveryItems(input.brandSignals),
  ];
  const priorityOrder: Record<DiscoveryQueueItem['priority'], number> = { high: 0, medium: 1, low: 2 };
  return items
    .filter((item) => item.query.trim())
    .sort((a, b) => priorityOrder[a.priority] - priorityOrder[b.priority])
    .slice(0, 8);
}
