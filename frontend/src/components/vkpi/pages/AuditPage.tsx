import { useEffect, useState } from 'react';
import { getAuditOverview } from '../../../services/vkpi.ui-api';
import type { VkpiAuditOverview } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { InfoBlock } from '../shared/InfoBlock';
import { PageShell } from './PageShell';

interface AuditPageProps {
  apiToken?: string;
  viewMode: 'manager' | 'employee';
}

function auditCategoryLabel(value: string) {
  const labels: Record<string, string> = {
    business: '业务动作',
    sensitive_access: '敏感访问',
    export: '导出',
    settings_change: '设置变更',
    attribution_adjust: '归因调整',
  };
  return labels[value] || value || '-';
}

function auditActionLabel(value: string) {
  const labels: Record<string, string> = {
    claim: '认领红人',
    release: '释放红人',
    reassign: '转派',
    project_create: '创建项目',
    project_stage_change: '项目推进',
    project_delete: '删除项目',
    link_pause: '暂停短链',
    link_archive: '归档短链',
    cost_add: '新增成本',
    cost_update: '更新成本',
    cost_approve: '批准成本',
    cost_void: '作废成本',
    kpi_rollup: 'KPI 计入',
    view_financial: '查看财务',
    view_export_history: '查看导出',
    view_audit_log: '查看审计',
    view_kpi_detail: '查看 KPI 明细',
  };
  return labels[value] || value || '-';
}

export function AuditPage({ apiToken, viewMode }: AuditPageProps) {
  const [audit, setAudit] = useState<VkpiAuditOverview | null>(null);
  const [category, setCategory] = useState('');
  const [days, setDays] = useState('7');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

  const loadAudit = async () => {
    if (!apiToken) return;
    setLoading(true);
    setMessage('');
    try {
      setAudit(await getAuditOverview(apiToken, { limit: 200, eventCategory: category, days: Number(days || 7) }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '审计记录读取失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadAudit(); }, [apiToken, category, days]);

  if (viewMode !== 'manager') {
    return (
      <PageShell title="审计" description="员工视角不显示全局审计、敏感访问、导出和设置变更记录。">
        <section className="vkpi-card"><div className="vkpi-empty-state">该页面只对管理层开放。</div></section>
      </PageShell>
    );
  }

  const summary = audit?.summary;
  const events = audit?.events || [];
  const categoryOptions = [
    { value: '', label: '全部' },
    { value: 'business', label: '业务动作' },
    { value: 'sensitive_access', label: '敏感访问' },
    { value: 'export', label: '导出' },
    { value: 'settings_change', label: '设置变更' },
    { value: 'attribution_adjust', label: '归因调整' },
  ];

  return (
    <PageShell title="审计 / 证据访问记录" description="管理层查看敏感访问、导出、设置变更、归因调整和业务动作，员工视角不可见。">
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="审计概览" />
          <InfoBlock label="敏感访问" value={String(summary?.sensitiveAccessCount ?? 0)} tone={(summary?.sensitiveAccessCount || 0) ? 'warn' : undefined} />
          <InfoBlock label="导出" value={String(summary?.exportCount ?? 0)} />
          <InfoBlock label="设置变更" value={String(summary?.settingsChangeCount ?? 0)} />
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="业务动作" />
          <InfoBlock label="业务审计" value={String(summary?.businessEventCount ?? 0)} />
          <InfoBlock label="归因调整" value={String(summary?.attributionAdjustmentCount ?? 0)} tone={(summary?.attributionAdjustmentCount || 0) ? 'warn' : undefined} />
          <InfoBlock label="当前列表" value={`${summary?.eventCount ?? events.length} 条`} />
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="筛选" />
          <label>时间范围
            <select value={days} onChange={(event) => setDays(event.target.value)}>
              <option value="1">近 1 天</option>
              <option value="7">近 7 天</option>
              <option value="30">近 30 天</option>
              <option value="90">近 90 天</option>
            </select>
          </label>
          <label>类型
            <select value={category} onChange={(event) => setCategory(event.target.value)}>
              {categoryOptions.map((option) => <option key={option.value || 'all'} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <button className="vkpi-button vkpi-button--primary" type="button" disabled={loading || !apiToken} onClick={() => void loadAudit()}>
            {loading ? '正在读取' : '刷新审计'}
          </button>
        </section>
      </section>
      {message ? <div className="vkpi-inline-message is-error">{message}</div> : null}
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header">
          <div><h2>审计事件</h2><span>{events.length} 条</span></div>
        </div>
        <div className="vkpi-table-wrap">
          <table className="vkpi-table">
            <thead><tr><th>时间</th><th>类型</th><th>动作</th><th>员工</th><th>对象</th><th>说明</th><th>IP</th></tr></thead>
            <tbody>
              {events.length ? events.map((event) => (
                <tr key={event.id}>
                  <td>{event.occurredAt || '-'}</td>
                  <td><span className="vkpi-stage-badge is-replied">{auditCategoryLabel(event.eventCategory)}</span></td>
                  <td><strong>{auditActionLabel(event.action)}</strong></td>
                  <td>{event.staffName || event.staffEmail || event.staffId || '-'}</td>
                  <td>{event.targetType || '-'} {event.targetId ? `#${event.targetId}` : ''}</td>
                  <td>{event.detail || '-'}</td>
                  <td>{event.ip || '-'}</td>
                </tr>
              )) : (
                <tr><td className="vkpi-table-empty" colSpan={7}>{loading ? '正在读取审计记录...' : '当前范围没有审计事件。'}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header">
          <div><h2>动作分布</h2><span>{summary?.byAction?.length || 0} 类</span></div>
        </div>
        <div className="vkpi-mini-grid">
          {(summary?.byAction || []).slice(0, 12).map((item) => <InfoBlock key={item.label} label={auditActionLabel(item.label)} value={String(item.count)} />)}
          {summary?.byAction?.length ? null : <div className="vkpi-empty-state">暂无动作统计。</div>}
        </div>
      </section>
    </PageShell>
  );
}
