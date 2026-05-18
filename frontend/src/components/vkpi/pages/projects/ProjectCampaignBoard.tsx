import { useMemo, useState } from 'react';
import type { VkpiPlatform, VkpiProjectRow, VkpiProjectStage, VkpiStaffMember } from '../../vkpiTypes';
import { Avatar } from '../../shared/Avatar';
import { PlatformPill } from '../../shared/PlatformPill';
import { StageBadge } from '../../shared/StageBadge';
import { primaryStageFlow, stageLabels } from '../../shared/vkpiConstants';
import { currencyFormatter, numberFormatter } from '../../shared/vkpiFormatters';
import { shortDateTime } from '../../shared/vkpiDataUtils';

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
  views: number;
  clicks: number;
  orders: number;
  gmv: number;
  cost: number;
  latestMessageAt?: string;
  latestMessageSource?: string;
  totalDurationLabel?: string;
  stageDurationLabel?: string;
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

function projectHealth(project: VkpiProjectRow) {
  if (cancelledStages.has(project.stage)) return { label: '风险', className: 'is-bad' };
  if (wrappingStages.has(project.stage) || terminalStages.has(project.stage)) return { label: '收尾', className: 'is-good' };
  if (parseDays(project.stageDurationLabel) >= 10) return { label: '需跟进', className: 'is-mid' };
  return { label: '推进中', className: 'is-good' };
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
      platforms: Array.from(new Set(sortedRows.map((row) => row.platform))).sort((a, b) => a.localeCompare(b)),
      views: sortedRows.reduce((sum, row) => sum + (row.views || 0), 0),
      clicks: sortedRows.reduce((sum, row) => sum + (row.clicks || 0), 0),
      orders: sortedRows.reduce((sum, row) => sum + (row.orders || 0), 0),
      gmv: sortedRows.reduce((sum, row) => sum + (row.gmv || 0), 0),
      cost: sortedRows.reduce((sum, row) => sum + (row.cost || 0), 0),
      latestMessageAt: primary.latestMessageAt || primary.updatedAt || primary.startedAt || primary.createdAt,
      latestMessageSource: primary.latestMessageSource,
      totalDurationLabel: focus.totalDurationLabel,
      stageDurationLabel: focus.stageDurationLabel,
    };
  }).sort((a, b) => latestTimestamp(b.primary) - latestTimestamp(a.primary));
}

function campaignStageCells(rows: VkpiProjectRow[]) {
  const indexes = rows.map((row) => stageIndex(row.stage));
  const currentIndex = indexes.length ? Math.min(...indexes) : 0;
  return primaryStageFlow.map((stage, index) => ({
    stage,
    state: index < currentIndex ? 'done' : index === currentIndex ? 'now' : 'todo',
    count: rows.filter((row) => stageIndex(row.stage) === index).length,
    doneCount: rows.filter((row) => stageIndex(row.stage) > index).length,
  }));
}

function formatMoney(value: number) {
  return currencyFormatter.format(value || 0);
}

function formatNumber(value: number | null | undefined) {
  return value == null ? '-' : numberFormatter.format(value);
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
      </div>

      <div className="vkpi-project-create-strip">
        <div>
          <strong>真实操作台</strong>
          <span>新建推广保留在下方入口；阶段、物流、费用、证据和删除进入项目详情处理。</span>
        </div>
        <button type="button" onClick={onOpenCreateProject} disabled={!onOpenCreateProject}>
          ✦ 新建推广
        </button>
      </div>

      <div className="vkpi-project-card-list">
        {filteredCampaignGroups.length ? filteredCampaignGroups.map((group) => {
          const healthState = projectHealth(group.focus);
          const messageSource = messageSourceLabels[group.latestMessageSource || ''] || group.latestMessageSource || '手动备注';
          const selected = group.rows.some((row) => row.id === selectedProjectId);
          return (
            <article
              key={group.id}
              className={`vkpi-project-campaign-card ${selected ? 'is-selected' : ''} ${group.status === '已取消' ? 'is-cancelled' : ''}`}
              onClick={() => onOpenProjectDetail(group.primary)}
              title="点击查看项目详情"
            >
              <div className="vkpi-project-card-top">
                <div className="vkpi-project-card-title">
                  <h3>{group.title}</h3>
                  <span className={`vkpi-project-status ${healthState.className}`}>{group.status}</span>
                  <StageBadge stage={group.focus.stage} />
                </div>
                <div className="vkpi-project-card-actions" onClick={(event) => event.stopPropagation()}>
                  <button className="vkpi-mini-button" type="button" onClick={() => onOpenProjectDetail(group.primary)}>详情</button>
                </div>
              </div>

              <div className="vkpi-project-card-main">
                <div className="vkpi-project-campaign-summary">
                  <div className="vkpi-project-campaign-count">
                    <strong>{group.rows.length}</strong>
                    <span>KOL</span>
                  </div>
                  <div className="vkpi-project-campaign-meta">
                    <b>平台</b>
                    <small>{group.platforms.map((item) => <PlatformPill key={item} platform={item} />)}</small>
                  </div>
                  <div className="vkpi-project-campaign-meta">
                    <b>当前重点</b>
                    <small>{stageLabels[group.focus.stage]} · {group.focus.kolHandle || group.focus.kolName || '未命名 KOL'}</small>
                  </div>
                </div>
                <button
                  className={`vkpi-project-owner ${group.primary.ownerId && onOpenStaffProfile ? 'is-clickable' : ''}`}
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    if (group.primary.ownerId && onOpenStaffProfile) void onOpenStaffProfile(group.primary.ownerId, { name: group.primary.ownerName, avatarUrl: group.primary.ownerAvatar });
                  }}
                >
                  <Avatar name={group.primary.ownerName} src={group.primary.ownerAvatar} size="xs" />
                  <span>负责人 {group.primary.ownerName || '-'}</span>
                </button>
              </div>

              <div className="vkpi-project-progress-grid" aria-label="项目阶段">
                {campaignStageCells(group.rows).map((cell) => (
                  <span className={`vkpi-project-progress-cell is-${cell.state}`} key={cell.stage}>
                    <b>{stageLabels[cell.stage]}</b>
                    <small>{cell.count ? `${cell.count} 个 KOL` : cell.state === 'done' ? `${cell.doneCount} 已过` : '待办'}</small>
                  </span>
                ))}
              </div>

              <div className="vkpi-project-card-stats">
                <span><small>播放</small><strong>{formatNumber(group.views)}</strong></span>
                <span><small>点击</small><strong>{formatNumber(group.clicks)}</strong></span>
                <span><small>订单</small><strong>{formatNumber(group.orders)}</strong></span>
                <span><small>销售</small><strong>{formatMoney(group.gmv)}</strong></span>
                {viewMode === 'manager' ? <span><small>成本</small><strong>{formatMoney(group.cost)}</strong></span> : null}
              </div>

              <div className="vkpi-project-card-footer">
                <span>{messageSource} · {shortDateTime(group.latestMessageAt)}</span>
                <span>总耗时 {group.totalDurationLabel || '-'} · 重点阶段 {group.stageDurationLabel || '-'}</span>
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
