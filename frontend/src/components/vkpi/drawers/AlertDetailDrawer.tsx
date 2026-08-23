import type { VkpiAlertDetail } from '../vkpiTypes';
import { alertRuleLabel, isKnownAlertRule } from '../../../domains/dashboard/alertRuleLabels';

function text(value: unknown, fallback = '-'): string {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

function formatConfidence(value: unknown): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return '-';
  return `${Math.round(num * 100)}%`;
}

function titleFromDetail(detail: VkpiAlertDetail | null): string {
  const alert = detail?.alert || {};
  return text(alert.title || alert.rule_key || alert.alert_key, 'Alert detail');
}

export function AlertDetailDrawer({
  detail,
  loading,
  error,
  onClose,
}: {
  detail: VkpiAlertDetail | null;
  loading?: boolean;
  error?: string;
  onClose: () => void;
}) {
  const alert = detail?.alert || {};
  const metadata = detail?.metadata || {};
  const post = detail?.post || null;
  const account = detail?.account || null;
  const comments = detail?.comments || [];
  const sourceSummary = detail?.sourceSummary || {};

  return (
    <aside className="vkpi-drawer" role="dialog" aria-label="Alert drilldown">
      <div className="vkpi-drawer__header">
        <div>
          <span className="vkpi-eyebrow">Alert Drilldown</span>
          <h2>{titleFromDetail(detail)}</h2>
          <p>{text(alert.body || alert.description, 'Source rows, post, account, and sentiment evidence.')}</p>
        </div>
        <button className="vkpi-icon-button" type="button" onClick={onClose}>×</button>
      </div>

      {loading ? <div className="vkpi-empty-state">正在加载告警证据...</div> : null}
      {error ? <div className="vkpi-empty-state">{error}</div> : null}

      {!loading && !error ? (
        <>
          <div className="vkpi-detail-grid">
            <div><span>Severity</span><strong>{text(alert.severity)}</strong></div>
            <div><span>Rule</span><strong>{isKnownAlertRule(alert.rule_key || sourceSummary.rule_key) ? `${alertRuleLabel(alert.rule_key || sourceSummary.rule_key)} · ${text(alert.rule_key || sourceSummary.rule_key)}` : text(alert.rule_key || sourceSummary.rule_key)}</strong></div>
            <div><span>Target</span><strong>{text(alert.target_type || sourceSummary.target_type)} #{text(alert.target_id || sourceSummary.target_id)}</strong></div>
            <div><span>Platform</span><strong>{text(metadata.platform || account?.platform || post?.platform)}</strong></div>
            <div><span>Flagged</span><strong>{text(metadata.flagged_comments || sourceSummary.comment_count || comments.length)}</strong></div>
            <div><span>Window</span><strong>{text(metadata.window_days)} days</strong></div>
          </div>

          <section className="vkpi-card vkpi-alert-detail-section">
            <div className="vkpi-card__header">
              <div><h2>Source Post</h2><span>{post ? 'industry_posts' : 'no post row'}</span></div>
            </div>
            {post ? (
              <div className="vkpi-detail-grid">
                <div><span>Title</span><strong>{text(post.title || post.caption)}</strong></div>
                <div><span>Post ID</span><strong>{text(post.id || post.platform_post_id)}</strong></div>
                <div><span>Likes</span><strong>{text(post.likes)}</strong></div>
                <div><span>Comments</span><strong>{text(post.comments)}</strong></div>
              </div>
            ) : <div className="vkpi-empty-state">没有找到绑定的帖子 source row。</div>}
          </section>

          <section className="vkpi-card vkpi-alert-detail-section">
            <div className="vkpi-card__header">
              <div><h2>Account</h2><span>{account ? 'industry_accounts' : 'no account row'}</span></div>
            </div>
            {account ? (
              <div className="vkpi-detail-grid">
                <div><span>Handle</span><strong>{text(account.handle || account.display_name)}</strong></div>
                <div><span>Account ID</span><strong>{text(account.id || account.platform_user_id)}</strong></div>
                <div><span>Project</span><strong>{text(account.project_id)}</strong></div>
                <div><span>Crawl</span><strong>{text(account.sync_status || account.crawl_status || account.crawl_enabled)}</strong></div>
              </div>
            ) : <div className="vkpi-empty-state">没有找到绑定的账号 source row。</div>}
          </section>

          <section className="vkpi-card vkpi-alert-detail-section">
            <div className="vkpi-card__header">
              <div><h2>Flagged Comments</h2><span>{comments.length} source rows</span></div>
            </div>
            {comments.length ? (
              <div className="vkpi-alert-source-list">
                {comments.map((comment) => (
                  <article key={text(comment.id || comment.external_comment_id)}>
                    <header>
                      <strong>{text(comment.author_handle, 'anonymous')}</strong>
                      <span>{text(comment.platform)} · {text(comment.created_at || comment.fetched_at)}</span>
                    </header>
                    <p>{text(comment.comment_text)}</p>
                    <footer>
                      <span>{text(comment.sentiment)} {formatConfidence(comment.sentiment_confidence)}</span>
                      <span>{text(comment.brand_attitude)} {formatConfidence(comment.brand_attitude_confidence)}</span>
                      <span>{text(comment.emotion)} {formatConfidence(comment.emotion_confidence)}</span>
                    </footer>
                  </article>
                ))}
              </div>
            ) : <div className="vkpi-empty-state">该告警暂未返回评论 source rows。</div>}
          </section>

          <section className="vkpi-card vkpi-alert-detail-section">
            <div className="vkpi-card__header">
              <div><h2>Raw Metadata</h2><span>audit context</span></div>
            </div>
            <pre className="vkpi-code-block">{JSON.stringify({ metadata, sourceSummary }, null, 2)}</pre>
          </section>
        </>
      ) : null}
    </aside>
  );
}
