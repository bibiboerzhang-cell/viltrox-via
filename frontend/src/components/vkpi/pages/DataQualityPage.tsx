import { useEffect, useState } from 'react';
import { actOnDataQualityIssue, getDataQuality, type VkpiDataQualityAction } from '../../../services/vkpi.ui-api';
import type { VkpiDataQualityResponse } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { InfoBlock } from '../shared/InfoBlock';
import { SeverityBadge } from '../shared/SeverityBadge';
import { PageShell } from './PageShell';

interface DataQualityPageProps {
  apiToken?: string;
  viewMode: 'manager' | 'employee';
}

export function DataQualityPage({ apiToken, viewMode }: DataQualityPageProps) {
  const [quality, setQuality] = useState<VkpiDataQualityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

  const refresh = async () => {
    if (!apiToken) return;
    setLoading(true);
    setMessage('');
    try {
      setQuality(await getDataQuality(apiToken, 200));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '数据质量检查失败');
    } finally {
      setLoading(false);
    }
  };

  const actionReason: Record<VkpiDataQualityAction, string> = {
    resolve: '管理层已复核处理',
    ignore: '管理层确认暂不处理',
    assign: '已指派当前管理层跟进',
    rerun: '请求重新检查该问题',
    evidence: '已补充证据说明',
    reopen: '重新打开复核',
  };

  const actionMessage: Record<VkpiDataQualityAction, string> = {
    resolve: '问题已标记为已处理。',
    ignore: '问题已忽略。',
    assign: '问题已记录指派。',
    rerun: '已记录重新检查请求。',
    evidence: '已记录补充证据动作。',
    reopen: '问题已重新打开。',
  };

  const actOnIssue = async (issueId: string, action: VkpiDataQualityAction) => {
    if (!apiToken) return;
    setLoading(true);
    setMessage('');
    try {
      await actOnDataQualityIssue(apiToken, issueId, action, actionReason[action], { ui_action: action });
      setMessage(actionMessage[action]);
      setQuality(await getDataQuality(apiToken, 200));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '数据质量操作失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, [apiToken]);

  if (viewMode !== 'manager') {
    return (
      <PageShell title="数据质量" description="员工视角不显示全局数据质量、内部成本、审计和预算信息。">
        <section className="vkpi-card"><div className="vkpi-empty-state">该页面只对管理层开放。</div></section>
      </PageShell>
    );
  }

  const issues = quality?.issues || [];
  const summary = quality?.summary || {};
  return (
    <PageShell title="数据质量 / 可信度检查" description="检查未匹配销售、缺订单快照、缺内容证据、缺成本、异常短链、指标来源和红人联系方式。">
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="检查状态" />
          <InfoBlock label="问题总数" value={String(quality?.total_count ?? 0)} tone={(quality?.total_count || 0) ? 'warn' : 'good'} />
          <InfoBlock label="高优先级" value={String(summary.high || 0)} tone={summary.high ? 'warn' : 'good'} />
          <InfoBlock label="中优先级" value={String(summary.medium || 0)} />
          <button className="vkpi-button vkpi-button--primary" type="button" disabled={loading || !apiToken} onClick={() => void refresh()}>
            {loading ? '正在检查' : '重新检查'}
          </button>
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="检查口径" />
          <p className="vkpi-summary-text">只读取真实业务表，不生成假问题；问题用于复核，不会阻塞当前业务操作。</p>
          <InfoBlock label="最近检查" value={quality?.generated_at || '-'} />
        </section>
      </section>
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header">
          <div><h2>问题队列</h2><span>{issues.length} 条</span></div>
        </div>
        <div className="vkpi-table-wrap">
          <table className="vkpi-table">
            <thead><tr><th>级别</th><th>问题</th><th>对象</th><th>项目</th><th>员工</th><th>说明</th><th>操作</th></tr></thead>
            <tbody>
              {issues.length ? issues.map((issue) => (
                <tr key={issue.id}>
                  <td><SeverityBadge severity={issue.severity} /></td>
                  <td><strong>{issue.title}</strong><br /><small>{issue.issue_type}</small></td>
                  <td>{issue.entity_type} #{issue.entity_id || '-'}</td>
                  <td>{issue.project_id || '-'}</td>
                  <td>{issue.staff_id || '-'}</td>
                  <td>{issue.detail || '-'}</td>
                  <td>
                    <div className="vkpi-table-actions vkpi-data-quality-actions">
                      <button className="vkpi-button vkpi-button--small" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'resolve')}>已处理</button>
                      <details className="vkpi-row-action-menu">
                        <summary>更多</summary>
                        <div>
                          <button className="vkpi-button vkpi-button--small vkpi-button--ghost" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'assign')}>指派</button>
                          <button className="vkpi-button vkpi-button--small vkpi-button--ghost" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'rerun')}>重检</button>
                          <button className="vkpi-button vkpi-button--small vkpi-button--ghost" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'evidence')}>补证据</button>
                          <button className="vkpi-button vkpi-button--small vkpi-button--ghost" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'ignore')}>忽略</button>
                        </div>
                      </details>
                    </div>
                  </td>
                </tr>
              )) : (
                <tr><td className="vkpi-table-empty" colSpan={7}>{loading ? '正在读取真实数据质量检查...' : '当前没有发现数据质量问题。'}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </PageShell>
  );
}
