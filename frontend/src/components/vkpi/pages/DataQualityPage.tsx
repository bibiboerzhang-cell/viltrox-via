import { useEffect, useState } from 'react';
import {
  actOnDataQualityIssue,
  DataQualitySummaryCards,
  listBrandSignals,
  reviewBrandSignal,
  type DataQualityAction,
  useDataQualitySummary,
} from '../../../domains/data-quality';
import { SeverityBadge } from '../shared/SeverityBadge';
import { PageShell } from './PageShell';

interface DataQualityPageProps {
  apiToken?: string;
  viewMode: 'manager' | 'employee';
}

function textValue(value: unknown, fallback = '') {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function formatDateTime(value: unknown) {
  const text = textValue(value);
  if (!text) return '-';
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function brandRoleLabel(value: unknown) {
  const role = textValue(value).toLowerCase();
  if (role === 'self') return 'Viltrox';
  if (role === 'competitor') return '竞品';
  return role || '-';
}

function brandSignalTypeLabel(value: unknown) {
  const type = textValue(value).toLowerCase();
  if (type === 'mention_viltrox') return '提到 Viltrox';
  if (type === 'show_product') return '产品 / SKU';
  if (type === 'comment_mention') return '评论提及';
  if (type === 'mention_competitor') return '提到竞品';
  return type || '-';
}

function brandSignalSeverity(signal: Record<string, unknown>) {
  const strength = textValue(signal.signal_strength).toLowerCase();
  const role = textValue(signal.brand_role).toLowerCase();
  if (strength === 'high' && role === 'competitor') return 'critical';
  if (strength === 'high') return 'high';
  if (strength === 'medium') return 'medium';
  return 'low';
}

function brandSignalActionLabel(action: 'contact' | 'ignore' | 'flag' | 'compete') {
  if (action === 'contact') return '联系';
  if (action === 'ignore') return '忽略';
  if (action === 'flag') return '标记';
  return '竞品处理';
}

function brandSignalEvidence(signal: Record<string, unknown>) {
  const raw = textValue(signal.evidence_json);
  if (!raw) return textValue(signal.post_uid || signal.source_id, '-');
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return textValue(parsed.match_text || parsed.match_context || parsed.match_source || signal.post_uid || signal.source_id, '-');
  } catch {
    return raw.slice(0, 120);
  }
}

export function DataQualityPage({ apiToken, viewMode }: DataQualityPageProps) {
  const [brandSignals, setBrandSignals] = useState<Array<Record<string, unknown>>>([]);
  const [brandSignalCount, setBrandSignalCount] = useState(0);
  const [brandSignalSchemaReady, setBrandSignalSchemaReady] = useState(true);
  const [brandSignalStatus, setBrandSignalStatus] = useState<'new' | 'reviewed' | 'all'>('new');
  const [brandSignalRole, setBrandSignalRole] = useState<'all' | 'self' | 'competitor'>('all');
  const [brandSignalType, setBrandSignalType] = useState('');
  const [brandSignalLoading, setBrandSignalLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [message, setMessage] = useState('');
  const {
    errorMessage,
    loading: qualityLoading,
    quality,
    refresh,
    setQuality,
  } = useDataQualitySummary({ enabled: viewMode === 'manager', token: apiToken, limit: 200 });
  const loading = qualityLoading || actionLoading;

  const refreshBrandSignals = async () => {
    if (!apiToken || viewMode !== 'manager') return;
    setBrandSignalLoading(true);
    setMessage('');
    try {
      const response = await listBrandSignals(apiToken, {
        status: brandSignalStatus,
        brandRole: brandSignalRole === 'all' ? '' : brandSignalRole,
        signalType: brandSignalType,
        limit: 100,
      });
      setBrandSignals(response.signals || []);
      setBrandSignalCount(Number(response.total_count || response.count || 0));
      setBrandSignalSchemaReady(response.schema_ready !== false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '品牌信号读取失败');
    } finally {
      setBrandSignalLoading(false);
    }
  };

  const actionReason: Record<DataQualityAction, string> = {
    resolve: '管理层已复核处理',
    ignore: '管理层确认暂不处理',
    assign: '已指派当前管理层跟进',
    rerun: '请求重新检查该问题',
    evidence: '已补充证据说明',
    reopen: '重新打开复核',
  };

  const actionMessage: Record<DataQualityAction, string> = {
    resolve: '问题已标记为已处理。',
    ignore: '问题已忽略。',
    assign: '问题已记录指派。',
    rerun: '已记录重新检查请求。',
    evidence: '已记录补充证据动作。',
    reopen: '问题已重新打开。',
  };

  const actOnIssue = async (issueId: string, action: DataQualityAction) => {
    if (!apiToken) return;
    if (action === 'resolve' || action === 'ignore') {
      const confirmed = window.confirm(action === 'resolve'
        ? '确认将该数据质量问题标记为已处理？该动作会写入审计记录。'
        : '确认忽略该数据质量问题？该动作会写入审计记录, 后续可用“重新打开”恢复。');
      if (!confirmed) return;
    }
    setActionLoading(true);
    setMessage('');
    try {
      await actOnDataQualityIssue(apiToken, issueId, action, actionReason[action], { ui_action: action });
      setMessage(actionMessage[action]);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '数据质量操作失败');
    } finally {
      setActionLoading(false);
    }
  };

  const actOnBrandSignal = async (signalId: unknown, action: 'contact' | 'ignore' | 'flag' | 'compete') => {
    if (!apiToken || !signalId) return;
    setBrandSignalLoading(true);
    setMessage('');
    try {
      await reviewBrandSignal(apiToken, String(signalId), { action });
      setMessage(`品牌信号已标记为${brandSignalActionLabel(action)}。`);
      await refreshBrandSignals();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '品牌信号处理失败');
    } finally {
      setBrandSignalLoading(false);
    }
  };

  useEffect(() => { void refreshBrandSignals(); }, [apiToken, viewMode, brandSignalStatus, brandSignalRole, brandSignalType]);

  if (viewMode !== 'manager') {
    return (
      <PageShell title="数据质量" description="需要授权后才能查看全局数据质量、内部成本、审计和预算信息。">
        <section className="vkpi-card"><div className="vkpi-empty-state">你当前无权限。</div></section>
      </PageShell>
    );
  }

  const issues = quality?.issues || [];
  return (
    <PageShell title="数据质量 / 可信度检查" description="检查未匹配销售、缺订单快照、缺内容证据、缺成本、异常短链、指标来源和红人联系方式。">
      <DataQualitySummaryCards
        apiTokenAvailable={Boolean(apiToken)}
        loading={qualityLoading}
        onRefresh={() => void refresh()}
        quality={quality}
      />
      {message || errorMessage ? <div className="vkpi-inline-message">{message || errorMessage}</div> : null}
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header">
          <div><h2>Viltrox / 竞品信号</h2><span>{brandSignalSchemaReady ? `${brandSignalCount} 条` : '未建表'}</span></div>
          <div className="vkpi-table-actions">
            <select value={brandSignalStatus} onChange={(event) => setBrandSignalStatus(event.target.value as typeof brandSignalStatus)} aria-label="信号状态">
              <option value="new">未处理</option>
              <option value="reviewed">已处理</option>
              <option value="all">全部</option>
            </select>
            <select value={brandSignalRole} onChange={(event) => setBrandSignalRole(event.target.value as typeof brandSignalRole)} aria-label="品牌角色">
              <option value="all">全部品牌</option>
              <option value="self">Viltrox</option>
              <option value="competitor">竞品</option>
            </select>
            <select value={brandSignalType} onChange={(event) => setBrandSignalType(event.target.value)} aria-label="信号类型">
              <option value="">全部类型</option>
              <option value="mention_viltrox">提到 Viltrox</option>
              <option value="show_product">产品 / SKU</option>
              <option value="comment_mention">评论提及</option>
              <option value="mention_competitor">提到竞品</option>
            </select>
            <button className="vkpi-button vkpi-button--small" type="button" disabled={brandSignalLoading || !apiToken} onClick={() => void refreshBrandSignals()}>
              {brandSignalLoading ? '读取中' : '刷新'}
            </button>
          </div>
        </div>
        <div className="vkpi-table-wrap">
          <table className="vkpi-table">
            <thead><tr><th>强度</th><th>品牌</th><th>平台</th><th>证据</th><th>时间</th><th>处理</th></tr></thead>
            <tbody>
              {!brandSignalSchemaReady ? (
                <tr><td className="vkpi-table-empty" colSpan={6}>品牌信号表还没有创建；先运行 brand signal 扫描或迁移。</td></tr>
              ) : brandSignals.length ? brandSignals.map((signal) => (
                <tr key={String(signal.id || signal.signal_uid)}>
                  <td><SeverityBadge severity={brandSignalSeverity(signal)} /></td>
                  <td>
                    <strong>{textValue(signal.brand_name || signal.brand || '-')}</strong><br />
                    <small>{brandSignalTypeLabel(signal.signal_type)} · {brandRoleLabel(signal.brand_role)}</small>
                  </td>
                  <td>
                    {textValue(signal.platform || '-')}<br />
                    <small>{textValue(signal.kol_entity_uid || signal.source_table || '-')}</small>
                  </td>
                  <td>
                    {signal.post_url ? <a href={String(signal.post_url)} target="_blank" rel="noreferrer">打开原帖</a> : '-'}
                    <br />
                    <small>{brandSignalEvidence(signal)}</small>
                  </td>
                  <td>{formatDateTime(signal.detected_at || signal.published_at)}</td>
                  <td>
                    <div className="vkpi-table-actions vkpi-data-quality-actions">
                      <button className="vkpi-button vkpi-button--small" type="button" disabled={brandSignalLoading} onClick={() => void actOnBrandSignal(signal.id, 'contact')}>联系</button>
                      <button className="vkpi-button vkpi-button--small vkpi-button--ghost" type="button" disabled={brandSignalLoading} onClick={() => void actOnBrandSignal(signal.id, 'flag')}>标记</button>
                      <button className="vkpi-button vkpi-button--small vkpi-button--ghost" type="button" disabled={brandSignalLoading} onClick={() => void actOnBrandSignal(signal.id, 'compete')}>竞品处理</button>
                      <button className="vkpi-button vkpi-button--small vkpi-button--ghost" type="button" disabled={brandSignalLoading} onClick={() => void actOnBrandSignal(signal.id, 'ignore')}>忽略</button>
                    </div>
                  </td>
                </tr>
              )) : (
                <tr><td className="vkpi-table-empty" colSpan={6}>{brandSignalLoading ? '正在读取品牌信号...' : '当前筛选下没有品牌信号。'}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
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
                      <details className="vkpi-row-action-menu">
                        <summary>处理动作</summary>
                        <div className="vkpi-dq-action-menu">
                          <div className="vkpi-dq-action-group">
                            <span>处理</span>
                            <button className="vkpi-button vkpi-button--small" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'resolve')}>已处理</button>
                            <button className="vkpi-button vkpi-button--small vkpi-button--ghost" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'ignore')}>忽略</button>
                          </div>
                          <div className="vkpi-dq-action-group">
                            <span>分派</span>
                            <button className="vkpi-button vkpi-button--small vkpi-button--ghost" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'assign')}>指派当前管理层</button>
                          </div>
                          <div className="vkpi-dq-action-group">
                            <span>补救</span>
                            <button className="vkpi-button vkpi-button--small vkpi-button--ghost" title="当前只记录重检请求, 不会自动修复数据。" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'rerun')}>记录重检</button>
                            <button className="vkpi-button vkpi-button--small vkpi-button--ghost" title="当前只记录补证据动作, 证据上传仍需到项目/红人详情里完成。" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'evidence')}>记录补证据</button>
                            <button className="vkpi-button vkpi-button--small vkpi-button--ghost" type="button" disabled={loading} onClick={() => void actOnIssue(issue.id, 'reopen')}>重新打开</button>
                          </div>
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
