import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  EMPTY_MISSION_CONTROL_SNAPSHOT,
  buildKpis,
  buildPlatforms,
  buildRegions,
  buildTrend,
  compact,
  contentTitle,
  contentUrl,
  countryFlag,
  dateLabel,
  fetchMissionControlSnapshot,
  intelligenceTabs,
  latestContent,
  num,
  officialTotals,
  record,
  rows,
  scopeOptions,
  trendSegments,
  type DashboardScope,
  type IntelligenceTab,
  type KpiCard,
  type MissionControlSnapshot,
  type Row,
  type TrendSegment,
} from '../../../domains/dashboard';
import type { VkpiPageKey } from '../vkpiTypes';
import { V2ReportPanel } from '../mission-control-v2/reports/V2ReportPanel';
import { V2ShellTopbar } from '../v2-shell/V2ShellTopbar';
import '../mission-control-v2/dashboard/mission-control-v2.css';
import '../mission-control-v2/dashboard/mission-control-v2-refine.css';

interface MissionControlV2PageProps {
  apiToken?: string;
  userName?: string;
  userRole?: string;
  userAvatar?: string;
  windowDays?: number;
  onSelectPage?: (page: VkpiPageKey) => void;
  onSignOut?: () => Promise<void> | void;
}

interface EvidenceItem {
  label: string;
  source: string;
  value: string;
}

interface IntelligenceItem {
  id: string;
  title: string;
  value: string;
  meta: string;
  tone: 'green' | 'amber' | 'red' | 'blue' | 'violet';
  evidence: EvidenceItem[];
}

function text(value: unknown, fallback = '无信号'): string {
  const clean = String(value ?? '').replace(/\s+/g, ' ').trim();
  return clean || fallback;
}

function rowTitle(row: Row): string {
  return text(row.title || row.label || row.task_title || row.name || row.alert_key || row.platform, '未命名信号');
}

function rowMeta(row: Row, fallback: string): string {
  return text(row.source_label || row.source || row.status || row.severity || row.platform || row.updated_at, fallback);
}

function agentStatus(snapshot: MissionControlSnapshot): { active: number; total: number; label: string } {
  const agentRows = rows(snapshot.agentsStatus.agents);
  const active = agentRows.filter((agent) => String(agent.status) === 'active').length;
  return {
    active,
    total: agentRows.length,
    label: agentRows.length ? `${active}/${agentRows.length} 在线` : '数据不足',
  };
}

function missionStats(snapshot: MissionControlSnapshot) {
  const taskRows = rows(snapshot.tasksStatus.tasks);
  const backlogSummary = record(snapshot.recommendationBacklog.summary);
  const missingFeedback = num(backlogSummary.missing_feedback_rows);
  const competitorTiers = record(snapshot.competitorDashboard.tier_counts);
  const riskCount = num(competitorTiers.avoid) + num(competitorTiers.caution) + snapshot.alerts.length;
  const official = officialTotals(snapshot.officialMatrix);
  const agents = agentStatus(snapshot);
  const actionCount = taskRows.length + missingFeedback + riskCount + snapshot.failedSections.length;
  return [
    {
      label: '今日关键动作',
      value: actionCount ? compact(actionCount) : '暂无可执行候选',
      meta: taskRows.length ? 'dashboard/tasks' : '无任务候选',
      tone: 'green',
    },
    {
      label: '业务进度',
      value: official.posts ? `${compact(official.posts)} 内容` : '数据不足',
      meta: official.views ? `${compact(official.views)} 曝光` : '官方矩阵待补齐',
      tone: 'blue',
    },
    {
      label: '风险 / 阻塞',
      value: riskCount ? compact(riskCount) : '无信号',
      meta: snapshot.alerts.length ? `${snapshot.alerts.length} alerts` : 'competitor/alerts',
      tone: riskCount ? 'red' : 'green',
    },
    {
      label: '智能 / 系统状态',
      value: snapshot.source === 'real' ? '真实 API' : '部分数据',
      meta: agents.label,
      tone: snapshot.source === 'real' ? 'green' : 'amber',
    },
  ];
}

function kpiEvidence(item: KpiCard, scopeLabel: string, snapshot: MissionControlSnapshot): EvidenceItem[] {
  return [
    { label: '当前口径', source: 'scope selector', value: scopeLabel },
    { label: item.label, source: item.meta, value: `${item.value} · ${item.status}` },
    { label: '快照状态', source: 'mission-control-v2', value: snapshot.failedSections.length ? snapshot.failedSections.join(' / ') : '真实 API' },
  ];
}

function evidenceFromRows(source: string, sourceRows: Row[], limit = 5): EvidenceItem[] {
  return sourceRows.slice(0, limit).map((row, index) => ({
    label: rowTitle(row) || `证据 ${index + 1}`,
    source,
    value: rowMeta(row, text(row.count || row.value || row.score || row.created_at, 'source row')),
  }));
}

function buildIntelligenceItems(snapshot: MissionControlSnapshot, tab: IntelligenceTab, kpis: KpiCard[]): IntelligenceItem[] {
  const taskRows = rows(snapshot.tasksStatus.tasks);
  const backlogSummary = record(snapshot.recommendationBacklog.summary);
  const missingFeedback = num(backlogSummary.missing_feedback_rows);
  const competitorTiers = record(snapshot.competitorDashboard.tier_counts);
  const riskCount = num(competitorTiers.avoid) + num(competitorTiers.caution) + snapshot.alerts.length;
  const platforms = buildPlatforms(snapshot);

  if (tab === 'platform') {
    if (!platforms.length) return [];
    return platforms.map((platform, index) => ({
      id: `platform-${platform.label}`,
      title: platform.label,
      value: compact(platform.value),
      meta: platform.meta,
      tone: index === 0 ? 'green' : 'blue',
      evidence: [
        { label: '平台排名', source: 'official-channel-matrix / kol-summary', value: `${platform.label} · ${platform.meta}` },
        { label: '数值', source: 'dashboard platform rows', value: compact(platform.value) },
      ],
    }));
  }

  if (tab === 'market') {
    return snapshot.brandSignals.slice(0, 4).map((row, index) => ({
      id: `market-${index}`,
      title: rowTitle(row),
      value: text(row.brand || row.platform || row.signal_type, '市场信号'),
      meta: rowMeta(row, 'brand-signals'),
      tone: index === 0 ? 'violet' : 'blue',
      evidence: evidenceFromRows('brand-signals', [row]),
    }));
  }

  if (tab === 'competitor') {
    if (!riskCount) return [];
    return [
      {
        id: 'competitor-risk',
        title: '竞品风险',
        value: compact(riskCount),
        meta: 'avoid / caution / alerts',
        tone: riskCount > 5 ? 'red' : 'amber',
        evidence: [
          { label: 'avoid', source: 'competitors-dashboard', value: compact(num(competitorTiers.avoid)) },
          { label: 'caution', source: 'competitors-dashboard', value: compact(num(competitorTiers.caution)) },
          { label: 'alerts', source: 'alerts', value: compact(snapshot.alerts.length) },
        ],
      },
    ];
  }

  const today = [
    taskRows.length ? {
      id: 'today-tasks',
      title: '今日动作候选',
      value: compact(taskRows.length),
      meta: 'dashboard/tasks',
      tone: 'green' as const,
      evidence: evidenceFromRows('dashboard/tasks', taskRows),
    } : null,
    missingFeedback ? {
      id: 'today-feedback',
      title: '推荐反馈缺口',
      value: compact(missingFeedback),
      meta: 'recommendation-feedback-backlog',
      tone: 'amber' as const,
      evidence: [{ label: 'missing_feedback_rows', source: 'recommendation-feedback-backlog', value: compact(missingFeedback) }],
    } : null,
    riskCount ? {
      id: 'today-risk',
      title: '风险 / 阻塞',
      value: compact(riskCount),
      meta: 'competitors + alerts',
      tone: 'red' as const,
      evidence: evidenceFromRows('alerts', snapshot.alerts).concat([
        { label: 'competitor caution/avoid', source: 'competitors-dashboard', value: compact(riskCount) },
      ]),
    } : null,
    snapshot.failedSections.length ? {
      id: 'today-system',
      title: '系统待复核',
      value: compact(snapshot.failedSections.length),
      meta: 'partial API',
      tone: 'amber' as const,
      evidence: snapshot.failedSections.map((section) => ({ label: section, source: 'mission snapshot', value: 'API 未返回完整数据' })),
    } : null,
  ].filter(Boolean) as IntelligenceItem[];

  return today.length ? today : [{
    id: 'today-empty',
    title: '暂无可执行候选',
    value: '--',
    meta: kpis.some((item) => !item.pending) ? '已有 KPI，无新增动作' : '数据不足',
    tone: 'blue',
    evidence: [{ label: 'dashboard/tasks', source: 'dashboard/tasks', value: '0 rows' }],
  }];
}

function ScopeSelector({ scope, onChange }: { scope: DashboardScope; onChange: (scope: DashboardScope) => void }) {
  return (
    <div className="mission-scope-selector" role="group" aria-label="KPI 数据范围">
      {scopeOptions.map((option) => (
        <button
          key={option.key}
          type="button"
          className={scope === option.key ? 'is-active' : ''}
          onClick={() => onChange(option.key)}
          title={option.detail}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function MissionMetricCard({ item, onEvidence }: { item: KpiCard; onEvidence: () => void }) {
  return (
    <button className={`mission-metric-card is-${item.tone}${item.pending ? ' is-pending' : ''}`} type="button" onClick={onEvidence}>
      <span>{item.status}</span>
      <small>{item.label}</small>
      <strong>{item.value}</strong>
      <em>{item.meta}</em>
    </button>
  );
}

function kpiStatusLabel(kpis: KpiCard[]): string {
  const ready = kpis.filter((item) => !item.pending).length;
  if (!ready) return '全部待接入';
  if (ready === kpis.length) return '真实数据完整';
  return `${ready}/${kpis.length} 有真实信号`;
}

function EvidencePanel({ title, rows: evidenceRows, onClose }: { title: string; rows: EvidenceItem[]; onClose: () => void }) {
  return (
    <aside className="mission-evidence-panel" aria-label="证据">
      <header>
        <span>Evidence</span>
        <button type="button" onClick={onClose}>×</button>
      </header>
      <h3>{title}</h3>
      {evidenceRows.map((item, index) => (
        <div className="mission-evidence-row" key={`${item.source}-${item.label}-${index}`}>
          <b>{item.label}</b>
          <span>{item.source}</span>
          <p>{item.value}</p>
        </div>
      ))}
    </aside>
  );
}

export function MissionControlV2Page({
  apiToken,
  userName = 'Viltrox 成员',
  userRole = '营销运营',
  userAvatar,
  windowDays = 30,
  onSelectPage,
  onSignOut,
}: MissionControlV2PageProps) {
  const [snapshot, setSnapshot] = useState<MissionControlSnapshot>(EMPTY_MISSION_CONTROL_SNAPSHOT);
  const [loading, setLoading] = useState(false);
  const [scope, setScope] = useState<DashboardScope>('official');
  const [tab, setTab] = useState<IntelligenceTab>('today');
  const [trendSegment, setTrendSegment] = useState<TrendSegment>('曝光量');
  const [reportOpen, setReportOpen] = useState(false);
  const [evidence, setEvidence] = useState<{ title: string; rows: EvidenceItem[] } | null>(null);

  useEffect(() => {
    if (!apiToken) {
      setSnapshot(EMPTY_MISSION_CONTROL_SNAPSHOT);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    fetchMissionControlSnapshot(apiToken, windowDays)
      .then((next) => {
        if (!cancelled) setSnapshot(next);
      })
      .catch(() => {
        if (!cancelled) setSnapshot({ ...EMPTY_MISSION_CONTROL_SNAPSHOT, source: 'partial', failedSections: ['mission-control-v2'] });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, windowDays]);

  const stats = useMemo(() => missionStats(snapshot), [snapshot]);
  const kpis = useMemo(() => buildKpis(snapshot, scope), [scope, snapshot]);
  const scopeLabel = scopeOptions.find((option) => option.key === scope)?.label || '官方 18 号';
  const intelligenceItems = useMemo(() => buildIntelligenceItems(snapshot, tab, kpis), [kpis, snapshot, tab]);
  const regions = useMemo(() => buildRegions(snapshot), [snapshot]);
  const trend = useMemo(() => buildTrend(snapshot.trendRows, trendSegment), [snapshot.trendRows, trendSegment]);
  const contentRows = useMemo(() => latestContent(snapshot).slice(0, 5), [snapshot]);

  const openEvidence = useCallback((title: string, evidenceRows: EvidenceItem[]) => {
    setEvidence({ title, rows: evidenceRows.length ? evidenceRows : [{ label: title, source: 'mission-control-v2', value: '数据不足' }] });
  }, []);

  return (
    <div className="mission-v2">
      <V2ShellTopbar
        apiToken={apiToken}
        pageTitle="管理主控 V2"
        subtitle="关键结果 / 风险 / 任务"
        userName={userName}
        userRole={userRole}
        userAvatar={userAvatar}
        tasks={rows(snapshot.tasksStatus.tasks)}
        alerts={snapshot.alerts}
        onReportClick={() => setReportOpen(true)}
        onSelectPage={onSelectPage}
        onSignOut={onSignOut}
      />

      <section className="mission-v2__top">
        <article className="mission-command-card">
          <div className="mission-command-card__head">
            <div>
              <span>{loading ? '正在读取真实 API' : dateLabel(snapshot.loadedAt)}</span>
              <h1>全球影响情报中枢</h1>
              <p>关键结果 / 风险 / 任务</p>
            </div>
            <ScopeSelector scope={scope} onChange={setScope} />
          </div>
          <div className="mission-stat-grid">
            {stats.map((item) => (
              <div className={`mission-stat is-${item.tone}`} key={item.label}>
                <i />
                <span>{item.label}</span>
                <strong>{item.value}</strong>
                <em>{item.meta}</em>
              </div>
            ))}
          </div>
        </article>

        <aside className="mission-intelligence-panel">
          <header>
            <div>
              <span>智能层 · 只读</span>
              <h2>{snapshot.source === 'real' ? '真实信号' : '部分信号'}</h2>
            </div>
            <b>{snapshot.failedSections.length ? `${snapshot.failedSections.length} API 待复核` : '证据可查'}</b>
          </header>
          <div className="mission-intelligence-tabs">
            {intelligenceTabs.map((item) => (
              <button key={item.key} type="button" className={tab === item.key ? 'is-active' : ''} onClick={() => setTab(item.key)}>
                {item.label}
              </button>
            ))}
          </div>
          <div className="mission-intelligence-list">
            {intelligenceItems.length ? intelligenceItems.map((item) => (
              <button className={`mission-intel-card is-${item.tone}`} type="button" key={item.id} onClick={() => openEvidence(item.title, item.evidence)}>
                <span>{item.meta}</span>
                <strong>{item.title}</strong>
                <b>{item.value}</b>
                <em>{item.evidence.length ? `${item.evidence.length} 证据` : '证据'}</em>
              </button>
            )) : (
              <div className="mission-empty-state">暂无可执行候选</div>
            )}
          </div>
        </aside>
      </section>

      <section className="mission-kpi-section" aria-label={`${scopeLabel} KPI`}>
        <header className="mission-kpi-strip-head">
          <div>
            <span>KPI 数据范围</span>
            <strong>{scopeLabel}</strong>
          </div>
          <b>{kpiStatusLabel(kpis)}</b>
        </header>
        <div className="mission-kpi-grid">
          {kpis.map((item) => (
            <MissionMetricCard
              key={item.label}
              item={item}
              onEvidence={() => openEvidence(item.label, kpiEvidence(item, scopeLabel, snapshot))}
            />
          ))}
        </div>
      </section>

      <section className="mission-v2__lower">
        <article className="mission-panel">
          <header><span>全球 KOL 分布</span><b>{regions.length ? `${regions.length} 地区` : '数据不足'}</b></header>
          <div className="mission-region-list">
            {regions.length ? regions.slice(0, 7).map((region) => (
              <div className="mission-region-row" key={region.code}>
                <span>{countryFlag(region.code)} {region.label}</span>
                <b>{compact(region.count)}</b>
                <i style={{ width: `${Math.max(6, region.share)}%` }} />
              </div>
            )) : <div className="mission-empty-state">数据不足</div>}
          </div>
        </article>

        <article className="mission-panel mission-trend-panel">
          <header>
            <span>曝光量趋势</span>
            <div className="mission-segmented">
              {trendSegments.map((segment) => <button key={segment} type="button" className={trendSegment === segment ? 'is-active' : ''} onClick={() => setTrendSegment(segment)}>{segment}</button>)}
            </div>
          </header>
          <svg viewBox="0 0 540 240" role="img" aria-label={`${trendSegment}趋势`}>
            <path d={trend.areaPath} className="mission-trend-area" />
            <path d={trend.path} className="mission-trend-line" />
            <circle cx={trend.pointX} cy={trend.pointY} r="5" />
          </svg>
          <div className="mission-trend-tip"><b>{trend.tipValue}</b><span>{trend.tipDate}</span></div>
        </article>

        <article className="mission-panel">
          <header><span>智能层战情室</span><b>{contentRows.length ? '真实内容' : '无信号'}</b></header>
          <div className="mission-content-list">
            {contentRows.length ? contentRows.map((row, index) => {
              const url = contentUrl(row);
              return (
                <a key={`${contentTitle(row)}-${index}`} href={url || undefined} target="_blank" rel="noreferrer" className={!url ? 'is-disabled' : ''}>
                  <strong>{contentTitle(row)}</strong>
                  <span>{rowMeta(row, 'recent-content')}</span>
                </a>
              );
            }) : <div className="mission-empty-state">无信号</div>}
          </div>
        </article>
      </section>

      {evidence ? <EvidencePanel title={evidence.title} rows={evidence.rows} onClose={() => setEvidence(null)} /> : null}
      <V2ReportPanel
        open={reportOpen}
        onClose={() => setReportOpen(false)}
        snapshot={snapshot}
        alerts={snapshot.alerts}
        kpis={kpis}
        scopeLabel={scopeLabel}
        scope={scope}
        windowDays={windowDays}
        generatedBy={userName}
        apiToken={apiToken}
      />
    </div>
  );
}

export default MissionControlV2Page;
