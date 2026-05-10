import { useEffect, useMemo, useState } from 'react';
import {
  getCommentIntelligenceOverview,
  type VkpiCommentIntelligenceOverview,
} from '../../../../../services/vkpi.ui-api';
import { BigNumberCard } from './BigNumberCard';
import { DaCard } from './DaCard';
import { EmptyState } from './EmptyState';

interface CommentIntelligencePanelProps {
  apiToken?: string;
  days?: number;
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${Math.round(value * 100)}%`;
}

function compact(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

function healthLabel(value: string): string {
  if (value === 'ok') return '正常';
  if (value === 'degraded') return '有失败';
  if (value === 'attention') return '需处理';
  return value || '未知';
}

function statusLabel(value: unknown): string {
  const raw = String(value || '');
  if (raw === 'ok') return '完成';
  if (raw === 'partial') return '部分完成';
  if (raw === 'fail') return '失败';
  if (raw === 'running') return '运行中';
  return raw || '-';
}

function timeLabel(value: unknown): string {
  const raw = String(value || '');
  if (!raw) return '-';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleString();
}

export function CommentIntelligencePanel({ apiToken, days = 7 }: CommentIntelligencePanelProps) {
  const [overview, setOverview] = useState<VkpiCommentIntelligenceOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    if (!apiToken) return;
    setLoading(true);
    setError('');
    try {
      const result = await getCommentIntelligenceOverview(apiToken, { days, recentLimit: 8 });
      setOverview(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : '评论智能概览加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [apiToken, days]);

  const recentRuns = useMemo(() => overview?.runs.recent || [], [overview]);

  if (!apiToken) {
    return (
      <DaCard title="Comment Intelligence Pipeline" eyebrow="评论智能" wide>
        <EmptyState title="登录后查看评论智能链路" body="这里显示评论采集、情感分析、内容支柱归类和最近处理记录。" />
      </DaCard>
    );
  }

  return (
    <DaCard
      title="Comment Intelligence Pipeline"
      eyebrow={`近 ${days} 天评论智能`}
      wide
      side={
        <div className="da-ci-actions">
          <span className={`da-ci-health da-ci-health--${overview?.health || 'unknown'}`}>
            {loading ? '刷新中' : healthLabel(String(overview?.health || 'unknown'))}
          </span>
          <button className="da-text-button" type="button" disabled={loading} onClick={() => void load()}>
            刷新
          </button>
        </div>
      }
    >
      {error ? <div className="da-ci-error">{error}</div> : null}
      <section className="da-detail-grid">
        <BigNumberCard
          title="Pipeline Runs"
          value={compact(overview?.runs.total || 0)}
          delta={overview?.runs.success_rate === null || overview?.runs.success_rate === undefined ? '暂无运行' : `成功率 ${pct(overview.runs.success_rate)}`}
          tone={overview?.health === 'ok' ? 'positive' : overview?.health ? 'negative' : 'neutral'}
        />
        <BigNumberCard
          title="Comments"
          value={compact(overview?.coverage.comments_total || 0)}
          delta={overview?.coverage.pending_sentiment ? `${overview.coverage.pending_sentiment} 条待情感` : '情感处理就绪'}
          tone={overview?.coverage.comments_total ? 'positive' : 'neutral'}
        />
        <BigNumberCard
          title="Sentiment"
          value={pct(overview?.coverage.sentiment_coverage)}
          delta="评论情感覆盖率"
          tone={overview?.coverage.sentiment_coverage ? 'positive' : 'neutral'}
        />
        <BigNumberCard
          title="Pillars"
          value={pct(overview?.coverage.comment_pillar_coverage)}
          delta="评论支柱归类覆盖率"
          tone={overview?.coverage.comment_pillar_coverage ? 'positive' : 'neutral'}
        />
        <BigNumberCard
          title="Post Pillars"
          value={pct(overview?.coverage.post_pillar_coverage)}
          delta={`${compact(overview?.coverage.posts_with_primary_pillar || 0)} / ${compact(overview?.coverage.posts_total || 0)} 帖子`}
          tone={overview?.coverage.post_pillar_coverage ? 'positive' : 'neutral'}
        />
      </section>

      <div className="da-table-wrap da-ci-runs">
        <table className="da-table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Post</th>
              <th>Status</th>
              <th>Trigger</th>
              <th>Started</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {recentRuns.length ? recentRuns.map((run) => (
              <tr key={String(run.id || run.run_uid)}>
                <td>{String(run.run_uid || run.id || '-')}</td>
                <td>{String(run.post_table || 'industry_posts')} #{String(run.post_id || '-')}</td>
                <td>{statusLabel(run.status)}</td>
                <td>{String(run.triggered_by || '-')}</td>
                <td>{timeLabel(run.started_at || run.created_at)}</td>
                <td>{String(run.error_message || '-').slice(0, 80)}</td>
              </tr>
            )) : (
              <tr>
                <td className="da-table-empty" colSpan={6}>
                  暂无评论智能运行记录。采集评论后会在这里显示处理状态。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </DaCard>
  );
}
