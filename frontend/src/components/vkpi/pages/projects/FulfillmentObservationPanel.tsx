// 履约后半链最小可视面板(additive,只读 + 人工复核)。
// 列「观察窗口」(starts/ends/status/匹配帖)与「已匹配/候选内容帖」(content_url/状态);
// 候选可一键人工复核 matched/rejected,matched 会触发后端回填窗口 matched_content_post_id。
// 红线对齐后端:零 fit 写、零自动裁决;复核只置 content_posts.status + 回填窗口行。
import { useCallback, useEffect, useState } from 'react';
import {
  advanceContentPostsToRetrospective,
  listContentPosts,
  listObservationWindows,
  reviewContentPost,
  type VkpiContentPost,
  type VkpiObservationWindow,
} from '../../../../services/vkpi/fulfillment-api';

interface FulfillmentObservationPanelProps {
  projectId: string;
  apiToken?: string;
}

const CELL: React.CSSProperties = { padding: '4px 8px', verticalAlign: 'top' };
const HEAD_ROW: React.CSSProperties = { textAlign: 'left', opacity: 0.7 };

function shortDate(value: string | null): string {
  if (!value) return '—';
  const s = String(value);
  return s.length >= 10 ? s.slice(0, 10) : s;
}

function statusTone(status: string): string {
  switch (status) {
    case 'matched':
    case 'retrospective_ready':
      return 'var(--vkpi-success, #16a34a)';
    case 'rejected':
    case 'content_missing':
      return 'var(--vkpi-danger, #dc2626)';
    case 'needs_review':
    case 'scanning':
      return 'var(--vkpi-warning, #d97706)';
    default:
      return 'inherit';
  }
}

export function FulfillmentObservationPanel({ projectId, apiToken }: FulfillmentObservationPanelProps) {
  const [windows, setWindows] = useState<VkpiObservationWindow[]>([]);
  const [posts, setPosts] = useState<VkpiContentPost[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState<number | null>(null);
  const [advanceBusy, setAdvanceBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const reload = useCallback(async () => {
    if (!apiToken) {
      setError('缺少登录凭证,无法加载履约观测。');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const [winRes, postRes] = await Promise.all([
        listObservationWindows(apiToken, projectId, 'all'),
        listContentPosts(apiToken, projectId, 'all'),
      ]);
      setWindows(winRes.items ?? []);
      setPosts(postRes.items ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载履约观测失败。');
    } finally {
      setLoading(false);
    }
  }, [apiToken, projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const onReview = useCallback(
    async (postId: number, action: 'matched' | 'rejected') => {
      if (!apiToken) return;
      setBusyId(postId);
      setNotice('');
      try {
        const res = await reviewContentPost(apiToken, postId, action);
        if (action === 'matched' && res.observation_window_id) {
          setNotice(`已确认匹配并回填观察窗口 #${res.observation_window_id}。`);
        } else if (action === 'matched') {
          setNotice('已确认匹配(未找到对应活动观察窗口,可手动核对)。');
        } else {
          setNotice('已标记为否决。');
        }
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : '复核失败。');
      } finally {
        setBusyId(null);
      }
    },
    [apiToken, reload],
  );

  const onAdvance = useCallback(async () => {
    if (!apiToken) return;
    setAdvanceBusy(true);
    setNotice('');
    try {
      const res = await advanceContentPostsToRetrospective(apiToken, projectId);
      const advanced = Number((res as { advanced_count?: number }).advanced_count ?? 0);
      setNotice(advanced > 0 ? `已推进 ${advanced} 条已匹配内容进复盘并排队聚合。` : '无已匹配内容可推进。');
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : '推进复盘失败。');
    } finally {
      setAdvanceBusy(false);
    }
  }, [apiToken, projectId, reload]);

  const matchedCount = posts.filter((p) => p.status === 'matched').length;

  return (
    <section
      className="vkpi-fulfillment-observation-panel"
      style={{ marginTop: 16, padding: 16, border: '1px solid var(--vkpi-border, #e2e8f0)', borderRadius: 8 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <h3 style={{ margin: 0 }}>履约观测(观察窗口 + 内容帖)</h3>
          <p style={{ margin: '4px 0 0', fontSize: 'var(--ds-fs-13)', opacity: 0.75 }}>
            物流签收后开的「等内容」窗口与扫到/手录的内容帖。候选经人工复核 matched 后回填窗口并可进复盘。
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="button" disabled={loading} onClick={() => void reload()}>
            {loading ? '加载中…' : '刷新'}
          </button>
          <button
            className="is-primary"
            type="button"
            disabled={advanceBusy || matchedCount === 0}
            onClick={() => void onAdvance()}
            title={matchedCount === 0 ? '无 matched 内容帖可推进' : '把已匹配内容推进复盘并排队聚合'}
          >
            {advanceBusy ? '推进中…' : `推进复盘(${matchedCount})`}
          </button>
        </div>
      </div>

      {error ? <p style={{ marginTop: 12, color: 'var(--vkpi-danger, #dc2626)', fontSize: 'var(--ds-fs-13)' }}>{error}</p> : null}
      {notice ? <p style={{ marginTop: 12, color: 'var(--vkpi-success, #16a34a)', fontSize: 'var(--ds-fs-13)' }}>{notice}</p> : null}

      <div style={{ marginTop: 16 }}>
        <h4 style={{ margin: '0 0 8px' }}>观察窗口({windows.length})</h4>
        {windows.length ? (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--ds-fs-13)' }}>
            <thead>
              <tr style={HEAD_ROW}>
                <th style={CELL}>#</th>
                <th style={CELL}>派单</th>
                <th style={CELL}>KOL</th>
                <th style={CELL}>开始</th>
                <th style={CELL}>结束</th>
                <th style={CELL}>状态</th>
                <th style={CELL}>已匹配帖</th>
              </tr>
            </thead>
            <tbody>
              {windows.map((w) => (
                <tr key={w.id} style={{ borderTop: '1px solid var(--vkpi-border, #e2e8f0)' }}>
                  <td style={CELL}>{w.id}</td>
                  <td style={CELL}>{w.assignment_id ?? '—'}</td>
                  <td style={CELL}>{w.kol_pool_id ?? '—'}</td>
                  <td style={CELL}>{shortDate(w.starts_at)}</td>
                  <td style={CELL}>{shortDate(w.ends_at)}</td>
                  <td style={{ ...CELL, color: statusTone(w.status) }}>{w.status}</td>
                  <td style={CELL}>{w.matched_content_post_id ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p style={{ fontSize: 'var(--ds-fs-13)', opacity: 0.7 }}>暂无观察窗口(无 delivered shipment 或未开窗)。</p>
        )}
      </div>

      <div style={{ marginTop: 20 }}>
        <h4 style={{ margin: '0 0 8px' }}>内容帖({posts.length})</h4>
        {posts.length ? (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--ds-fs-13)' }}>
            <thead>
              <tr style={HEAD_ROW}>
                <th style={CELL}>#</th>
                <th style={CELL}>平台</th>
                <th style={CELL}>内容链接</th>
                <th style={CELL}>曝光</th>
                <th style={CELL}>状态</th>
                <th style={CELL}>操作</th>
              </tr>
            </thead>
            <tbody>
              {posts.map((p) => {
                const isCandidate = p.status === 'candidate' || p.status === 'needs_review';
                return (
                  <tr key={p.id} style={{ borderTop: '1px solid var(--vkpi-border, #e2e8f0)' }}>
                    <td style={CELL}>{p.id}</td>
                    <td style={CELL}>{p.platform || '—'}</td>
                    <td style={{ ...CELL, maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {p.content_url ? (
                        <a href={p.content_url} target="_blank" rel="noopener noreferrer">
                          {p.content_url}
                        </a>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td style={CELL}>{p.view_count ?? 0}</td>
                    <td style={{ ...CELL, color: statusTone(p.status) }}>{p.status}</td>
                    <td style={CELL}>
                      {isCandidate ? (
                        <span style={{ display: 'inline-flex', gap: 6 }}>
                          <button
                            type="button"
                            disabled={busyId === p.id}
                            onClick={() => void onReview(p.id, 'matched')}
                          >
                            {busyId === p.id ? '…' : '确认匹配'}
                          </button>
                          <button
                            type="button"
                            disabled={busyId === p.id}
                            onClick={() => void onReview(p.id, 'rejected')}
                          >
                            否决
                          </button>
                        </span>
                      ) : (
                        <span style={{ opacity: 0.6 }}>—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p style={{ fontSize: 'var(--ds-fs-13)', opacity: 0.7 }}>暂无内容帖(无窗口/无扫到/手录内容)。</p>
        )}
      </div>
    </section>
  );
}
