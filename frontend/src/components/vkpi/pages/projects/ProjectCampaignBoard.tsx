import { useMemo, useState } from 'react';
import { AlertCircle, PackageCheck } from 'lucide-react';
import type { VkpiPlatform, VkpiProjectRow, VkpiProjectStage, VkpiStaffMember } from '../../vkpiTypes';
import { primaryStageFlow } from '../../shared/vkpiConstants';
import { currencyFormatter, numberFormatter } from '../../shared/vkpiFormatters';
import { shortDateTime } from '../../shared/vkpiDataUtils';
import {
  formatLargeNum,
  formatMoneyShort,
  healthBg,
  healthColor,
  ownerColor,
  PROJECT_STAGE_COLOR,
  PROJECT_STAGE_FLOW,
  PROJECT_STATUS_COLOR,
  statusBg,
} from './projectDeliverableStyle';

const boardStatuses = ['全部', '规划中', '进行中', '收尾中', '已结束', '已取消'] as const;
type BoardStatus = typeof boardStatuses[number];

const terminalStages = new Set<VkpiProjectStage>(['closed', 'released']);
const cancelledStages = new Set<VkpiProjectStage>(['cancelled', 'lost', 'stalled']);
const planningStages = new Set<VkpiProjectStage>(['invited', 'discovery']);
const wrappingStages = new Set<VkpiProjectStage>(['content_published', 'published', 'measured']);
const activeStages = new Set<VkpiProjectStage>(['contacted', 'replied', 'in_discussion', 'agreed', 'shipped', 'received']);
interface CampaignGroup {
  id: string;
  title: string;
  rows: VkpiProjectRow[];
  primary: VkpiProjectRow;
  focus: VkpiProjectRow;
  status: BoardStatus;
  platforms: VkpiPlatform[];
  kolCount: number;
  kolWithEvidence: number;
  evidenceCount: number;
  views: number;
  clicks: number;
  orders: number;
  gmv: number;
  cost: number;
  latestMessageAt?: string;
  latestMessageSource?: string;
  totalDurationLabel?: string;
  stageDurationLabel?: string;
  stageCounts: Record<string, number>;
  publishedCount: number;
  healthScore: number;
  healthBasis?: string;
  currentFocus?: string;
  bottleneck?: string;
}

const messageSourceLabels: Record<string, string> = {
  DM: '私信',
  Email: '邮件',
  'Comment reply': '评论回复',
  'Manual note': '手动备注',
  'No reply': '未回复',
};

function statusForProject(project: VkpiProjectRow): BoardStatus {
  if (cancelledStages.has(project.stage)) return '已取消';
  if (terminalStages.has(project.stage)) return '已结束';
  if (wrappingStages.has(project.stage)) return '收尾中';
  if (planningStages.has(project.stage)) return '规划中';
  if (activeStages.has(project.stage)) return '进行中';
  return '进行中';
}

function stageIndex(stage: VkpiProjectStage) {
  if (stage === 'content_published') return primaryStageFlow.indexOf('published');
  const index = primaryStageFlow.indexOf(stage);
  if (terminalStages.has(stage) || cancelledStages.has(stage)) return primaryStageFlow.length - 1;
  return index >= 0 ? index : 0;
}

function parseDays(label?: string) {
  const match = String(label || '').match(/(\d+)/);
  return match ? Number(match[1]) : 0;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function boardHealth(projects: VkpiProjectRow[]) {
  if (!projects.length) return { score: 0, label: '暂无项目', className: 'is-muted', reason: '创建真实项目后会计算推进健康度。' };
  const published = projects.filter((project) => wrappingStages.has(project.stage) || terminalStages.has(project.stage)).length;
  const cancelled = projects.filter((project) => cancelledStages.has(project.stage)).length;
  const blocked = projects.filter((project) => parseDays(project.stageDurationLabel) >= 10 && !terminalStages.has(project.stage)).length;
  const score = clamp(Math.round(62 + (published / projects.length) * 26 - (cancelled / projects.length) * 24 - blocked * 4), 30, 96);
  if (score >= 78) return { score, label: '健康', className: 'is-good', reason: '发布和统计项目占比高，当前阻塞较少。' };
  if (score >= 58) return { score, label: '注意', className: 'is-mid', reason: '部分项目停留时间偏长，需要检查沟通或物流节点。' };
  return { score, label: '阻塞', className: 'is-bad', reason: '取消、停滞或长时间未推进的项目偏多。' };
}

function uniquePlatforms(projects: VkpiProjectRow[]) {
  return Array.from(new Set(projects.map((project) => project.platform))).sort((a, b) => a.localeCompare(b));
}

function campaignGroupKey(project: VkpiProjectRow) {
  const campaign = String(project.campaign || '').trim().toLowerCase();
  return campaign || `project:${project.id}`;
}

function latestTimestamp(project: VkpiProjectRow) {
  const raw = project.latestMessageAt || project.updatedAt || project.startedAt || project.createdAt || '';
  const time = raw ? new Date(raw).getTime() : 0;
  return Number.isNaN(time) ? 0 : time;
}

function groupStatus(rows: VkpiProjectRow[]): BoardStatus {
  const statuses = rows.map(statusForProject);
  if (statuses.includes('进行中')) return '进行中';
  if (statuses.includes('收尾中')) return '收尾中';
  if (statuses.includes('规划中')) return '规划中';
  if (statuses.includes('已结束')) return statuses.every((status) => status === '已结束') ? '已结束' : '进行中';
  return '已取消';
}

function pickFocusRow(rows: VkpiProjectRow[]) {
  return [...rows].sort((a, b) => {
    const aBlocked = cancelledStages.has(a.stage) || parseDays(a.stageDurationLabel) >= 10 ? 1 : 0;
    const bBlocked = cancelledStages.has(b.stage) || parseDays(b.stageDurationLabel) >= 10 ? 1 : 0;
    if (bBlocked !== aBlocked) return bBlocked - aBlocked;
    const stageDelta = stageIndex(a.stage) - stageIndex(b.stage);
    if (stageDelta) return stageDelta;
    return latestTimestamp(b) - latestTimestamp(a);
  })[0] || rows[0];
}

function rowKolCount(row: VkpiProjectRow) {
  return typeof row.kolCount === 'number' && row.kolCount > 0 ? row.kolCount : 1;
}

function rowKolWithEvidence(row: VkpiProjectRow) {
  if (typeof row.kolWithEvidence === 'number') return row.kolWithEvidence;
  return row.evidenceCount || row.stageEventCount ? 1 : 0;
}

function rowEvidenceCount(row: VkpiProjectRow) {
  if (typeof row.evidenceCount === 'number') return row.evidenceCount;
  return row.stageEventCount || 0;
}

function buildCampaignGroups(projects: VkpiProjectRow[]): CampaignGroup[] {
  const grouped = new Map<string, VkpiProjectRow[]>();
  projects.forEach((project) => {
    const key = campaignGroupKey(project);
    grouped.set(key, [...(grouped.get(key) || []), project]);
  });
  return Array.from(grouped.entries()).map(([id, rows]) => {
    const sortedRows = [...rows].sort((a, b) => latestTimestamp(b) - latestTimestamp(a));
    const primary = sortedRows[0];
    const focus = pickFocusRow(sortedRows);
    return {
      id,
      title: primary.campaign || '未命名项目',
      rows: sortedRows,
      primary,
      focus,
      status: groupStatus(sortedRows),
      platforms: Array.from(new Set(primary.platforms?.length ? primary.platforms : sortedRows.map((row) => row.platform))).sort((a, b) => a.localeCompare(b)),
      kolCount: sortedRows.reduce((sum, row) => sum + rowKolCount(row), 0),
      kolWithEvidence: sortedRows.reduce((sum, row) => sum + rowKolWithEvidence(row), 0),
      evidenceCount: sortedRows.reduce((sum, row) => sum + rowEvidenceCount(row), 0),
      views: sortedRows.reduce((sum, row) => sum + (row.views || 0), 0),
      clicks: sortedRows.reduce((sum, row) => sum + (row.clicks || 0), 0),
      orders: sortedRows.reduce((sum, row) => sum + (row.orders || 0), 0),
      gmv: sortedRows.reduce((sum, row) => sum + (row.gmv || 0), 0),
      cost: sortedRows.reduce((sum, row) => sum + (row.cost || 0), 0),
      latestMessageAt: primary.latestMessageAt || primary.updatedAt || primary.startedAt || primary.createdAt,
      latestMessageSource: primary.latestMessageSource,
      totalDurationLabel: focus.totalDurationLabel,
      stageDurationLabel: focus.stageDurationLabel,
      stageCounts: mergeStageCounts(sortedRows),
      publishedCount: sortedRows.reduce((sum, row) => sum + (row.publishedCount ?? (stageIndex(row.stage) >= stageIndex('published') ? rowKolCount(row) : 0)), 0),
      healthScore: primary.healthScore ?? 0,
      healthBasis: primary.healthBasis,
      currentFocus: primary.currentFocus,
      bottleneck: primary.bottleneck,
    };
  }).sort((a, b) => latestTimestamp(b.primary) - latestTimestamp(a.primary));
}

function formatMoney(value: number) {
  return currencyFormatter.format(value || 0);
}

function formatNumber(value: number | null | undefined) {
  return value == null ? '-' : numberFormatter.format(value);
}

function projectStageCounts(row: VkpiProjectRow) {
  const source = row.stageCounts || {};
  return Object.fromEntries(PROJECT_STAGE_FLOW.map((stage) => [stage.key, Number(source[stage.key] || 0)]));
}

function mergeStageCounts(rows: VkpiProjectRow[]) {
  const merged = Object.fromEntries(PROJECT_STAGE_FLOW.map((stage) => [stage.key, 0]));
  rows.forEach((row) => {
    const counts = projectStageCounts(row);
    PROJECT_STAGE_FLOW.forEach((stage) => {
      merged[stage.key] += counts[stage.key] || 0;
    });
  });
  return merged;
}

function ownerInitial(name?: string) {
  return String(name || '-').trim().slice(0, 1).toUpperCase() || '-';
}

export function ProjectCampaignBoard({
  projects,
  selectedProjectId,
  viewMode,
  onOpenProjectDetail,
  onOpenStaffProfile,
  onOpenCreateProject,
  onOpenImportKols,
}: {
  projects: VkpiProjectRow[];
  selectedProjectId?: string;
  viewMode: 'manager' | 'employee';
  onOpenProjectDetail: (project: VkpiProjectRow) => void;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onOpenCreateProject?: () => void;
  onOpenImportKols?: () => void;
}) {
  const [status, setStatus] = useState<BoardStatus>('全部');
  const [platform, setPlatform] = useState<'全部平台' | VkpiPlatform>('全部平台');
  const [search, setSearch] = useState('');
  const campaignGroups = useMemo(() => buildCampaignGroups(projects), [projects]);
  const focusRows = useMemo(() => campaignGroups.map((group) => group.focus), [campaignGroups]);
  const platforms = useMemo(() => uniquePlatforms(projects), [projects]);
  const statusCounts = useMemo(() => {
    const counts = new Map<BoardStatus, number>(boardStatuses.map((item) => [item, 0]));
    campaignGroups.forEach((group) => {
      counts.set('全部', (counts.get('全部') || 0) + 1);
      counts.set(group.status, (counts.get(group.status) || 0) + 1);
    });
    return counts;
  }, [campaignGroups]);
  const filteredCampaignGroups = useMemo(() => {
    const query = search.trim().toLowerCase();
    return campaignGroups.filter((group) => {
      if (status !== '全部' && group.status !== status) return false;
      if (platform !== '全部平台' && !group.platforms.includes(platform)) return false;
      if (!query) return true;
      return [
        group.title,
        group.primary.ownerName,
        group.latestMessageSource,
        ...group.platforms,
        ...group.rows.flatMap((row) => [row.kolName, row.kolHandle, row.platform]),
      ].join(' ').toLowerCase().includes(query);
    });
  }, [campaignGroups, platform, search, status]);
  const health = boardHealth(focusRows);
  const stalledCount = campaignGroups.filter((group) => cancelledStages.has(group.focus.stage) || parseDays(group.stageDurationLabel) >= 10).length;
  const totalGmv = campaignGroups.reduce((sum, group) => sum + group.gmv, 0);
  const totalCost = campaignGroups.reduce((sum, group) => sum + group.cost, 0);
  const totalClicks = campaignGroups.reduce((sum, group) => sum + group.clicks, 0);
  const totalViews = campaignGroups.reduce((sum, group) => sum + group.views, 0);
  const priorityGroup = campaignGroups.find((group) => cancelledStages.has(group.focus.stage) || parseDays(group.stageDurationLabel) >= 10)
    || campaignGroups.find((group) => group.status === '进行中')
    || campaignGroups[0];
  const priorityProject = priorityGroup?.primary;

  return (
    <section className="vkpi-project-board" aria-label="我的项目看板">
      <div className="vkpi-project-hero">
        <div>
          <span className="vkpi-project-eyebrow">VILTROX MARKETING · CAMPAIGN OS</span>
          <h2>把「项目跟进」从表格变成每日决策台。</h2>
          <p>保留新建推广、9 阶段推进、证据归档和 KOL 项目卡片；视觉上只突出进度、风险、ROI 和下一步动作。</p>
          <div className="vkpi-project-hero-buttons">
            <button className="vkpi-project-hero-button is-primary" type="button" disabled={!priorityProject} onClick={() => priorityProject && onOpenProjectDetail(priorityProject)}>
              查看当前重点项目
            </button>
            <button className="vkpi-project-hero-button" type="button" onClick={onOpenImportKols} disabled={!onOpenImportKols}>
              导入 KOL 名单
            </button>
          </div>
        </div>
        <div className="vkpi-project-health-grid">
          <div className={`vkpi-project-health-card ${health.className}`}>
            <span>今日健康度</span>
            <strong>{health.score ? `${health.score}` : '-'}</strong>
            <em>{Math.max(0, campaignGroups.length - stalledCount)} 个项目正常推进</em>
          </div>
          <div className="vkpi-project-health-card">
            <span>需要处理</span>
            <strong>{stalledCount}</strong>
            <em>停滞 / 取消 / 超 10 天未推进</em>
          </div>
          <div className="vkpi-project-health-card">
            <span>本周预计曝光</span>
            <strong>{formatNumber(totalViews)}</strong>
            <em>基于当前项目播放数据</em>
          </div>
          <div className="vkpi-project-health-card">
            <span>{viewMode === 'manager' ? '预算消耗' : '点击'}</span>
            <strong>{viewMode === 'manager' ? formatMoney(totalCost) : formatNumber(totalClicks)}</strong>
            <em>{viewMode === 'manager' ? `${formatMoney(totalGmv)} GMV` : '员工视角显示短链点击'}</em>
          </div>
        </div>
      </div>

      <div className="vkpi-project-filter-bar">
        <div className="vkpi-project-segments" aria-label="项目状态筛选">
          {boardStatuses.map((item) => (
            <button
              key={item}
              className={`vkpi-project-segment ${status === item ? 'is-active' : ''}`}
              type="button"
              onClick={() => setStatus(item)}
            >
              {item}<span>{statusCounts.get(item) || 0}</span>
            </button>
          ))}
        </div>
        <input
          className="vkpi-project-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索项目 / KOL / handle / 负责人"
        />
        <select className="vkpi-project-select" value={platform} onChange={(event) => setPlatform(event.target.value as '全部平台' | VkpiPlatform)}>
          <option value="全部平台">全部平台</option>
          {platforms.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <button className="vkpi-project-new-button" type="button" onClick={onOpenCreateProject} disabled={!onOpenCreateProject}>
          ✦ 新建推广
        </button>
      </div>

      <div className="vkpi-project-card-list">
        {filteredCampaignGroups.length ? filteredCampaignGroups.map((group) => {
          const cardHealthColor = healthColor(group.healthScore);
          const cardHealthBg = healthBg(group.healthScore);
          const messageSource = messageSourceLabels[group.latestMessageSource || ''] || group.latestMessageSource || '手动备注';
          const selected = group.rows.some((row) => row.id === selectedProjectId);
          const initial = ownerInitial(group.primary.ownerName);
          return (
            <article
              key={group.id}
              className={`rounded-2xl border border-white/[0.06] bg-white/[0.015] hover:bg-white/[0.025] hover:border-white/[0.10] transition-all cursor-pointer overflow-hidden ${selected ? 'ring-1 ring-purple-500/40' : ''} ${group.status === '已取消' ? 'opacity-60 grayscale' : ''}`}
              onClick={() => onOpenProjectDetail(group.primary)}
              title="点击查看项目详情"
            >
              <div className="p-4 border-b border-white/[0.04]">
                <div className="flex items-start gap-3">
                  <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style={{ background: cardHealthBg, color: cardHealthColor }}>
                    <PackageCheck size={18} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <div className="text-[14px] font-semibold text-white">{group.title}</div>
                      <span className="text-[10px] px-1.5 py-0.5 rounded font-medium" style={{ background: statusBg(group.status), color: PROJECT_STATUS_COLOR[group.status] || PROJECT_STATUS_COLOR['已结束'] }}>{group.status}</span>
                      <span className="text-[10px] text-slate-500">·</span>
                      <span className="text-[10.5px] text-slate-400">{group.currentFocus || group.focus.stageDurationLabel || '真实项目'}</span>
                    </div>
                    <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                      {group.platforms.map((item) => <span className="text-[9.5px] px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-400" key={item}>{item}</span>)}
                      <span className="text-[9.5px] px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-400">{formatNumber(group.kolWithEvidence)} 有证据</span>
                    </div>
                  </div>
                  <div className="text-right shrink-0" title={group.healthBasis === 'simplified_no_reliable_time' ? '简化版：产出率 60% + 阶段推进 40%' : undefined}>
                    <div className="text-[10px] text-slate-500 mb-0.5">健康度</div>
                    <div className="text-[28px] font-bold leading-none tabular-nums" style={{ color: cardHealthColor }}>{group.healthScore || '-'}</div>
                  </div>
                </div>
              </div>

              <div
                className="px-4 py-3 border-b border-white/[0.04] cursor-default select-none"
                aria-label="项目阶段，仅展示"
                onClick={(event) => event.stopPropagation()}
                title="列表阶段条仅展示；进入详情页后可点击漏斗筛选"
              >
                <div className="grid grid-cols-9 gap-1">
                  {PROJECT_STAGE_FLOW.map((stage, index) => {
                    const count = group.stageCounts[stage.key] || 0;
                    const active = count > 0;
                    const color = PROJECT_STAGE_COLOR[stage.key];
                    return (
                      <div
                        className="rounded p-1.5 text-center cursor-default"
                        key={stage.key}
                        style={{ background: active ? `${color}1a` : 'rgba(255,255,255,0.015)', border: active ? `1px solid ${color}50` : '1px solid rgba(255,255,255,0.04)' }}
                      >
                        <div className="text-[8.5px] text-slate-400 mb-0.5">{index + 1}.{stage.label}</div>
                        <div className="text-[14px] font-bold tabular-nums leading-none" style={{ color: active ? color : '#475569' }}>{count || '—'}</div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="px-4 py-3 flex items-center justify-between gap-4">
                <div className="grid grid-cols-5 gap-4 flex-1">
                  {[
                    ['KOL', formatNumber(group.kolCount)],
                    ['已发', formatNumber(group.publishedCount)],
                    ['曝光', formatLargeNum(group.views)],
                    ['点击', formatNumber(group.clicks)],
                    ['成本', formatMoneyShort(group.cost)],
                  ].map(([label, value]) => (
                    <div key={label}>
                      <div className="text-[9.5px] text-slate-500">{label}</div>
                      <div className="text-[13px] font-semibold text-white tabular-nums">{value}</div>
                    </div>
                  ))}
                </div>
                <button
                  className="flex items-center gap-2 shrink-0"
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    if (group.primary.ownerId && onOpenStaffProfile) void onOpenStaffProfile(group.primary.ownerId, { name: group.primary.ownerName, avatarUrl: group.primary.ownerAvatar });
                  }}
                  title={`负责人 ${group.primary.ownerName || '-'}`}
                >
                  <div className="flex -space-x-1.5">
                    {group.primary.ownerAvatar ? (
                      <img alt={group.primary.ownerName || 'owner'} className="w-6 h-6 rounded-full border-2 border-[#0a0a0d] object-cover" src={group.primary.ownerAvatar} />
                    ) : (
                      <div className="w-6 h-6 rounded-full border-2 border-[#0a0a0d] flex items-center justify-center text-[9px] font-bold text-white" style={{ background: ownerColor(initial) }}>{initial}</div>
                    )}
                  </div>
                </button>
              </div>

              <div className="px-4 py-2 flex items-center gap-2 border-t border-white/[0.04]" style={{ background: group.healthScore < 70 ? 'rgba(239,68,68,0.05)' : group.healthScore < 85 ? 'rgba(251,191,36,0.05)' : 'rgba(255,255,255,0.01)' }}>
                <AlertCircle size={11} style={{ color: cardHealthColor }} />
                <div className="text-[11px] flex-1" style={{ color: group.healthScore < 85 ? '#fbbf24' : '#94a3b8' }}>{group.bottleneck || group.currentFocus || '暂无瓶颈'}</div>
                <div className="text-[10px] text-slate-500">更新 {shortDateTime(group.latestMessageAt)} · {messageSource}</div>
              </div>
            </article>
          );
        }) : (
          <div className="vkpi-project-board-empty">
            当前筛选下没有项目。请先创建真实项目，或调整搜索和状态筛选。
          </div>
        )}
      </div>
    </section>
  );
}
