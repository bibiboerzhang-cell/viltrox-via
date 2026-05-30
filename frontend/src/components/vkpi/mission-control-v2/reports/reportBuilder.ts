import {
  compact,
  dateLabel,
  num,
  record,
  rows,
  type KpiCard,
  type Row,
  type Snapshot,
} from '../../../../domains/dashboard';

export interface V2ReportBuildInput {
  snapshot: Snapshot;
  alerts: Row[];
  kpis: KpiCard[];
  scopeLabel: string;
  windowDays: number;
  generatedBy: string;
}

function text(value: unknown, fallback = '无信号'): string {
  const clean = String(value ?? '').replace(/\s+/g, ' ').trim();
  return clean || fallback;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function sourceLine(input: V2ReportBuildInput): string {
  if (input.snapshot.source === 'real') return '数据源：真实 API';
  const failed = input.snapshot.failedSections.length ? input.snapshot.failedSections.join(' / ') : '无登录态或无信号';
  return `数据源：部分归因 / ${failed}`;
}

export function buildMissionReportMarkdown(input: V2ReportBuildInput): string {
  const generatedAt = dateLabel(input.snapshot.loadedAt);
  const taskRows = rows(input.snapshot.tasksStatus.tasks).slice(0, 6);
  const backlogSummary = record(input.snapshot.recommendationBacklog.summary);
  const missingFeedback = num(backlogSummary.missing_feedback_rows);
  const competitorTiers = record(input.snapshot.competitorDashboard.tier_counts);
  const riskCount = num(competitorTiers.avoid) + num(competitorTiers.caution);
  const agentRows = rows(input.snapshot.agentsStatus.agents);
  const activeAgents = agentRows.filter((agent) => String(agent.status) === 'active').length;
  const lines = [
    `# V-KPI 管理主控周报`,
    '',
    `- 生成时间：${generatedAt}`,
    `- 生成者：${input.generatedBy}`,
    `- 数据口径：${input.scopeLabel} / 近 ${input.windowDays} 天`,
    `- ${sourceLine(input)}`,
    '',
    `## KPI`,
    ...input.kpis.map((item) => `- ${item.label}：${item.value}（${item.status} · ${item.meta}）`),
    '',
    `## 今日判断`,
    `- 关键动作：${taskRows.length ? `${compact(taskRows.length)} 项` : '暂无可执行候选'}`,
    `- 风险 / 阻塞：${riskCount ? `${compact(riskCount)} 项` : '无信号'}`,
    `- 推荐反馈缺口：${missingFeedback ? `${compact(missingFeedback)} 条` : '无信号'}`,
    `- Agent 状态：${agentRows.length ? `${activeAgents}/${agentRows.length} 在线` : '数据不足'}`,
    '',
    `## 任务候选`,
    ...(taskRows.length
      ? taskRows.map((row, index) => `- ${index + 1}. ${text(row.title || row.label || row.task_title)} · ${text(row.source || row.source_label, 'dashboard/tasks')}`)
      : ['- 暂无可执行候选']),
    '',
    `## 告警`,
    ...(input.alerts.length
      ? input.alerts.slice(0, 8).map((row, index) => `- ${index + 1}. ${text(row.label || row.title || row.alert_key)} · ${text(row.severity || row.status, 'open')}`)
      : ['- 无信号']),
    '',
    `## 备注`,
    '- GMV / 订单量 / ROI 若显示待接入，表示当前没有 Shopify / 成本闭环真实数据。',
  ];
  return lines.join('\n');
}

export function buildMissionReportHtml(input: V2ReportBuildInput): string {
  const markdown = buildMissionReportMarkdown(input);
  const body = markdown
    .split('\n')
    .map((line) => {
      if (line.startsWith('# ')) return `<h1>${escapeHtml(line.slice(2))}</h1>`;
      if (line.startsWith('## ')) return `<h2>${escapeHtml(line.slice(3))}</h2>`;
      if (line.startsWith('- ')) return `<li>${escapeHtml(line.slice(2))}</li>`;
      return line ? `<p>${escapeHtml(line)}</p>` : '';
    })
    .join('\n')
    .replace(/(<li>.*<\/li>\n?)+/g, (match) => `<ul>${match}</ul>`);
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>V-KPI 管理主控周报</title>
  <style>
    body { margin: 40px; color: #101828; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    h1 { font-size: 28px; margin: 0 0 20px; }
    h2 { font-size: 18px; margin: 26px 0 10px; }
    ul { margin: 0 0 10px 20px; padding: 0; }
    li { margin: 7px 0; line-height: 1.55; }
    p { line-height: 1.55; }
  </style>
</head>
<body>${body}</body>
</html>`;
}
