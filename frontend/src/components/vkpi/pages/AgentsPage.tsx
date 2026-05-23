import { useEffect, useMemo, useState } from 'react';
import { getDashboardAgentsInbox, type VkpiAgentInboxItem } from '../../../services/vkpi.ui-api';
import type { WorkspacePageProps } from './WorkspacePage';

const agentFilters = [
  { id: '', label: '全部' },
  { id: 'brief', label: '简报' },
  { id: 'recommendation', label: '推荐' },
  { id: 'evidence', label: '证据' },
  { id: 'sync_sentinel', label: '同步' },
  { id: 'brain', label: '脑层' },
  { id: 'market_intel', label: '市场' },
];

function formatTime(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function valueLabel(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') return value.toLocaleString('en-US');
  if (Array.isArray(value)) return `${value.length} 项`;
  if (typeof value === 'object') return JSON.stringify(value).slice(0, 80);
  return String(value);
}

function statusLabel(status: string): string {
  if (status === 'active') return '在线';
  if (status === 'warning') return '警告';
  return '空闲';
}

export function AgentsPage({ apiToken }: WorkspacePageProps) {
  const [agentId, setAgentId] = useState('');
  const [items, setItems] = useState<VkpiAgentInboxItem[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [total, setTotal] = useState(0);
  const [source, setSource] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    if (!apiToken) {
      setItems([]);
      setSelectedId('');
      setTotal(0);
      setSource('');
      setError('当前前端没有登录 token。');
      return () => { alive = false; };
    }
    setLoading(true);
    setError('');
    getDashboardAgentsInbox(apiToken, { limit: 50, agentId: agentId || undefined })
      .then((payload) => {
        if (!alive) return;
        const rows = Array.isArray(payload.items) ? payload.items : [];
        setItems(rows);
        setTotal(Number(payload.total || rows.length));
        setSource(String(payload.source || payload.ops_dir || 'runtime/ops'));
        setSelectedId((current) => (current && rows.some((item) => item.id === current) ? current : rows[0]?.id || ''));
      })
      .catch((err) => {
        if (!alive) return;
        setError(err instanceof Error ? err.message : 'Agent Inbox 加载失败');
        setItems([]);
        setSelectedId('');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, [apiToken, agentId]);

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) || items[0],
    [items, selectedId],
  );
  const summaryRows = selected?.details?.summary
    ? Object.entries(selected.details.summary).filter(([, value]) => value !== null && value !== undefined).slice(0, 10)
    : [];
  const nextSteps = selected?.details?.next_steps || [];

  return (
    <div className="vkpi-agents-shell">
      <section className="vkpi-agents-hero">
        <div>
          <span>V-KPI Agents</span>
          <h1>Agent Inbox</h1>
        </div>
        <div className="vkpi-agents-hero__meta">
          <b>{total}</b>
          <small>{source || 'runtime/ops'}</small>
        </div>
      </section>

      <section className="vkpi-agents-toolbar">
        <div className="vkpi-agents-tabs" aria-label="Agent filters">
          {agentFilters.map((filter) => (
            <button
              className={filter.id === agentId ? 'is-active' : ''}
              key={filter.id || 'all'}
              type="button"
              onClick={() => setAgentId(filter.id)}
            >
              {filter.label}
            </button>
          ))}
        </div>
        <span>{loading ? '读取中' : error ? '需要处理' : '已同步'}</span>
      </section>

      {error ? <div className="vkpi-inline-message is-error">{error}</div> : null}

      <section className="vkpi-agents-grid">
        <div className="vkpi-agents-list">
          {items.map((item) => (
            <button
              className={`vkpi-agent-inbox-item ${item.id === selected?.id ? 'is-active' : ''}`}
              key={item.id}
              type="button"
              onClick={() => setSelectedId(item.id)}
            >
              <span className={`vkpi-agent-status is-${item.status}`}>{statusLabel(item.status)}</span>
              <b>{item.title}</b>
              <small>{item.agent_name} · {formatTime(item.generated_at || item.last_run_at)}</small>
              <p>{item.summary}</p>
            </button>
          ))}
          {!items.length && !loading ? <div className="vkpi-agents-empty">暂无 runtime/ops 输出。</div> : null}
        </div>

        <aside className="vkpi-agent-detail">
          {selected ? (
            <>
              <div className="vkpi-agent-detail__head">
                <span className={`vkpi-agent-status is-${selected.status}`}>{statusLabel(selected.status)}</span>
                <h2>{selected.title}</h2>
                <p>{selected.artifact_name || selected.mode || '-'}</p>
              </div>
              <div className="vkpi-agent-detail__meta">
                <span><b>Agent</b>{selected.agent_name}</span>
                <span><b>Mode</b>{selected.mode || '-'}</span>
                <span><b>Generated</b>{formatTime(selected.generated_at || selected.last_run_at)}</span>
                <span><b>Passed</b>{selected.passed ? 'yes' : 'no'}</span>
              </div>
              <div className="vkpi-agent-summary-grid">
                {summaryRows.map(([key, value]) => (
                  <span key={key}><b>{key}</b>{valueLabel(value)}</span>
                ))}
              </div>
              <div className="vkpi-agent-next">
                <b>Next Steps</b>
                {nextSteps.length ? nextSteps.map((step) => <p key={step}>{step}</p>) : <p>-</p>}
              </div>
            </>
          ) : (
            <div className="vkpi-agents-empty">选择一个 Agent 输出。</div>
          )}
        </aside>
      </section>
    </div>
  );
}

export default AgentsPage;
