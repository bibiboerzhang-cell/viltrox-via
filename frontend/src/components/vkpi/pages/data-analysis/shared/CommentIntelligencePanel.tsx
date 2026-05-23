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
  if (raw === 'sampled_cached') return '缓存抽样';
  if (raw === 'cached_window') return '窗口缓存';
  if (raw === 'no_cached_comments') return '无缓存评论';
  if (raw === 'not_configured') return '未配置';
  return raw || '-';
}

function displayLabel(value: unknown): string {
  const raw = String(value || 'unknown');
  const labels: Record<string, string> = {
    positive: '正向',
    neutral: '中性',
    negative: '负向',
    mixed: '混合',
    advocate: '拥护',
    supportive: '支持',
    critical: '批评',
    hostile: '敌意',
    irrelevant: '无关',
    joy: '愉悦',
    surprise: '惊喜',
    curiosity: '好奇',
    frustration: '挫败',
    anger: '愤怒',
    sadness: '悲伤',
    disgust: '厌恶',
    fear: '担忧',
    unknown: '未知',
  };
  return labels[raw] || raw;
}

function timeLabel(value: unknown): string {
  const raw = String(value || '');
  if (!raw) return '-';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleString();
}

function DistributionList({
  title,
  rows,
}: {
  title: string;
  rows: Array<{ label?: string; display_name?: string; count?: number }>;
}) {
  const max = Math.max(1, ...rows.map((row) => Number(row.count || 0)));
  return (
    <div className="da-ci-distribution">
      <h4>{title}</h4>
      {rows.length ? rows.slice(0, 6).map((row) => {
        const count = Number(row.count || 0);
        const label = row.display_name || displayLabel(row.label);
        return (
          <div className="da-ci-bar" key={`${title}-${String(label)}`}>
            <span>{label}</span>
            <i><b style={{ width: `${Math.max(4, Math.round((count / max) * 100))}%` }} /></i>
            <strong>{compact(count)}</strong>
          </div>
        );
      }) : <p className="da-ci-muted">暂无分布数据</p>}
    </div>
  );
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
  const distributions = overview?.distributions || {};
  const rule = overview?.rule_v0 || {};
  const contract = rule.contract || overview?.comment_contract || {};
  const ruleCounts = rule.counts || {};
  const samples = Array.isArray(rule.samples) ? rule.samples : [];

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
          <span className="da-ci-readonly">只读 v0 · 不采集</span>
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
        <BigNumberCard
          title="Cached Sample"
          value={`${compact(contract.cached || 0)} / ${compact(contract.cap || overview?.coverage.sample_cap || 0)}`}
          delta={statusLabel(contract.status || overview?.coverage.sample_status)}
          tone={contract.cached ? 'positive' : 'neutral'}
        />
      </section>

      <section className="da-ci-distribution-grid">
        <DistributionList title="Sentiment" rows={distributions.sentiment || []} />
        <DistributionList title="Brand Attitude" rows={distributions.brand_attitude || []} />
        <DistributionList
          title="Top Pillars"
          rows={(distributions.pillars || []).map((row) => ({
            label: row.pillar_key,
            display_name: row.display_name,
            count: row.count,
          }))}
        />
        <DistributionList
          title="Rule v0"
          rows={[
            { label: 'questions', display_name: '问题', count: ruleCounts.questions || 0 },
            { label: 'opportunities', display_name: '机会', count: ruleCounts.opportunities || 0 },
            { label: 'issues', display_name: '问题点', count: ruleCounts.issues || 0 },
            { label: 'positive', display_name: '正向', count: ruleCounts.positive || 0 },
            { label: 'negative', display_name: '负向', count: ruleCounts.negative || 0 },
          ]}
        />
      </section>

      <div className="da-table-wrap da-ci-runs">
        <table className="da-table">
          <thead>
            <tr>
              <th>Cached Comment</th>
              <th>Rule</th>
              <th>Tags</th>
              <th>Source</th>
              <th>Fetched</th>
            </tr>
          </thead>
          <tbody>
            {samples.length ? samples.slice(0, 8).map((sample) => (
              <tr key={String(sample.comment_id || sample.external_comment_id || sample.text_excerpt)}>
                <td>{String(sample.text_excerpt || '-').slice(0, 160)}</td>
                <td>{displayLabel(sample.rule_sentiment)}</td>
                <td>{Array.isArray(sample.tags) ? sample.tags.join(' / ') || '-' : '-'}</td>
                <td>{String(sample.platform || '-')} · {String(sample.post_table || '-')} #{String(sample.post_id || '-')}</td>
                <td>{timeLabel(sample.fetched_at || sample.created_at)}</td>
              </tr>
            )) : (
              <tr>
                <td className="da-table-empty" colSpan={5}>
                  当前窗口没有缓存评论样本。这里不会自动抓取评论。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

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
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {recentRuns.length ? recentRuns.map((run) => {
              const runId = run.id || run.run_uid;
              return (
                <tr key={String(runId)}>
                  <td>{String(run.run_uid || run.id || '-')}</td>
                  <td>{String(run.post_table || 'industry_posts')} #{String(run.post_id || '-')}</td>
                  <td>{statusLabel(run.status)}</td>
                  <td>{String(run.triggered_by || '-')}</td>
                  <td>{timeLabel(run.started_at || run.created_at)}</td>
                  <td>{String(run.error_message || '-').slice(0, 80)}</td>
                  <td>只读</td>
                </tr>
              );
            }) : (
              <tr>
                <td className="da-table-empty" colSpan={7}>
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
