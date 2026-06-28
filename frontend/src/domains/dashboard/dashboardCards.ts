import type { IntelligenceCardModel } from '../../components/vkpi/intelligence/IntelligenceCard';
import { compact, dateLabel, num, record, rows } from './dashboardModel';
import type { KpiCard, Snapshot } from './dashboardModel';

export function buildCards(snapshot: Snapshot, kpis: KpiCard[], taskCount: number): IntelligenceCardModel[] {
  const backlogSummary = record(snapshot.recommendationBacklog.summary);
  const missingFeedback = num(backlogSummary.missing_feedback_rows);
  const competitorTiers = record(snapshot.competitorDashboard.tier_counts);
  const riskCount = num(competitorTiers.avoid) + num(competitorTiers.caution);
  const agentRows = rows(snapshot.agentsStatus.agents);
  const activeAgents = agentRows.filter((agent) => String(agent.status) === 'active').length;
  const signalCount = snapshot.brandSignals.length;
  const failedCount = snapshot.failedSections.length;
  const actionCount = (missingFeedback ? 1 : 0) + taskCount + riskCount + failedCount;
  return [
    {
      id: 'dashboard-summary',
      type: 'brief',
      priority: actionCount || riskCount ? 'high' : 'medium',
      status: 'open',
      title: `今日待处理 ${compact(actionCount)} 项`,
      summary: `推荐待反馈 ${compact(missingFeedback)} · 风险 ${compact(riskCount)} · Agent ${activeAgents}/${agentRows.length || 7}`,
      entityType: 'mission_control_summary',
      confidence: snapshot.source === 'real' ? 0.9 : snapshot.source === 'partial' ? 0.62 : 0.25,
      freshnessLabel: dateLabel(snapshot.loadedAt),
      sourceLabel: 'dashboard summary',
      evidence: [
        { label: '推荐待反馈', source: 'recommendation-feedback-backlog', value: `${compact(missingFeedback)} missing` },
        { label: '竞品风险', source: 'competitor-dashboard', value: `${compact(riskCount)} avoid/caution` },
        { label: '任务候选', source: 'dashboard/tasks', value: `${compact(taskCount)} tasks` },
        { label: '失败 API', source: 'premium snapshot', value: failedCount ? snapshot.failedSections.join(' / ') : '无' },
      ],
      actions: [{ label: '查看证据', kind: 'primary' }, { label: '进入智能中心', kind: 'secondary' }],
    },
    {
      id: 'dashboard-market',
      type: 'market',
      priority: signalCount ? 'medium' : 'low',
      status: 'open',
      title: signalCount ? `${compact(signalCount)} 条市场/品牌信号` : '市场信号待积累',
      summary: 'Google / Reddit / Trends 后续按预算和证据链小口接入；当前只展示既有站内信号。',
      entityType: 'market_signal',
      confidence: signalCount ? 0.72 : 0.25,
      freshnessLabel: dateLabel(snapshot.loadedAt),
      sourceLabel: 'brand-signals',
      evidence: snapshot.brandSignals.slice(0, 5).map((row, index) => ({ label: `信号 ${index + 1}`, source: String(row.platform || 'brand_signal'), value: String(row.title || row.match_context || row.reason || '-').slice(0, 160) })),
      actions: [{ label: '查看证据', kind: 'primary' }, { label: '进入数据分析', kind: 'secondary' }],
    },
    {
      id: 'dashboard-system',
      type: 'system',
      priority: failedCount ? 'high' : 'medium',
      status: failedCount ? 'blocked' : 'open',
      title: failedCount ? '系统状态需复核' : '系统状态正常',
      summary: `${activeAgents}/${agentRows.length || 7} Agent 在线 · ${snapshot.source === 'real' ? '真实 API' : snapshot.source}`,
      entityType: 'system_health',
      confidence: failedCount ? 0.45 : 0.84,
      freshnessLabel: dateLabel(snapshot.loadedAt),
      sourceLabel: 'premium dashboard health',
      evidence: [
        { label: 'KPI', source: 'cards', value: kpis.map((item) => `${item.label}:${item.status}`).join(' / ') },
        { label: '失败 API', source: 'premium snapshot', value: failedCount ? snapshot.failedSections.join(' / ') : '无' },
      ],
      actions: [{ label: '查看证据', kind: 'primary' }, { label: '打开 Agent Inbox', kind: 'secondary' }],
    },
  ];
}
