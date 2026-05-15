import { Avatar } from '../../shared/Avatar';
import { compactCount, platformDisplay, platformFromRaw, safeNumber } from '../../shared/vkpiDataUtils';
import { numberFormatter } from '../../shared/vkpiFormatters';

type Row = Record<string, unknown>;

interface OutreachTablesProps {
  busy: boolean;
  suggestions: Row[];
  digestItems: Row[];
  digestDate: string;
  onUpdateSuggestion: (id: unknown, action: 'claim' | 'dismiss' | 'create_project') => Promise<void>;
}

function externalUrl(value: unknown): string {
  const url = String(value || '').trim();
  return /^https?:\/\//i.test(url) ? url : '';
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = String(value ?? '').trim();
    if (text) return text;
  }
  return '';
}

function avatarUrl(row: Row): string {
  return externalUrl(row.avatar_url || row.profile_image_url || row.thumbnail_url || row.source_thumbnail_url);
}

function platformClass(value: unknown): string {
  return String(platformFromRaw(value)).toLowerCase().replace(/[^a-z0-9]+/g, '-');
}

function LinkActions({ row }: { row: Row }) {
  const profileUrl = externalUrl(row.profile_url);
  const sourceUrl = externalUrl(row.source_video_url);
  if (!profileUrl && !sourceUrl) return <span className="vkpi-help-text">无链接</span>;
  return (
    <div className="vkpi-button-row">
      {profileUrl ? <a className="vkpi-mini-button" href={profileUrl} rel="noreferrer" target="_blank">主页</a> : null}
      {sourceUrl ? <a className="vkpi-mini-button" href={sourceUrl} rel="noreferrer" target="_blank">原帖</a> : null}
    </div>
  );
}

function SuggestionCard({ row, busy, onUpdateSuggestion }: { row: Row; busy: boolean; onUpdateSuggestion: OutreachTablesProps['onUpdateSuggestion'] }) {
  const displayName = firstText(row.channel_name, row.display_name, row.handle, 'Unknown KOL');
  const handle = firstText(row.handle, row.platform_user_id);
  const title = firstText(row.source_video_title, row.bio, row.description, '暂无内容摘要');
  const product = firstText(row.source_product_sku, row.product_sku, '未关联产品');
  const platformLabel = platformDisplay(row.platform);
  const sourceViewCount = safeNumber(row.source_view_count);
  const score = firstText(row.score, row.quality_score, '-');

  return (
    <article className="vkpi-suggestion-card">
      <div className="vkpi-suggestion-card__top">
        <Avatar name={displayName} src={avatarUrl(row)} size="lg" />
        <div className="vkpi-suggestion-card__identity">
          <span className="vkpi-platform-pill">
            <span className={`vkpi-platform-dot is-${platformClass(row.platform)}`} />
            {platformLabel}
          </span>
          <strong>{displayName}</strong>
          {handle ? <small>@{handle.replace(/^@/, '')}</small> : <small>未写入 handle</small>}
        </div>
      </div>
      <div className="vkpi-suggestion-card__content">{title}</div>
      <div className="vkpi-suggestion-card__meta">
        <span><small>来源产品</small><strong>{product}</strong></span>
        <span><small>播放</small><strong>{sourceViewCount ? compactCount(sourceViewCount) : '-'}</strong></span>
        <span><small>评分</small><strong>{score}</strong></span>
      </div>
      <div className="vkpi-suggestion-card__footer">
        <LinkActions row={row} />
        <div className="vkpi-button-row">
          <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.id, 'claim')}>认领</button>
          <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.id, 'create_project')}>建项目</button>
          <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.id, 'dismiss')}>忽略</button>
        </div>
      </div>
    </article>
  );
}

function jsonRecord(value: unknown): Row {
  if (!value) return {};
  if (typeof value === 'object' && !Array.isArray(value)) return value as Row;
  if (typeof value !== 'string') return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Row : {};
  } catch {
    return {};
  }
}

function assignmentDisplay(row: Row): { label: string; detail: string; tone: string } {
  const metadata = jsonRecord(row.metadata_json);
  const reason = String(metadata.assignment_reason || '');
  const staffId = String(metadata.assignment_staff_id || '');
  if (!reason && !staffId) return { label: '-', detail: '未写入分配原因', tone: 'vkpi-chip--muted' };
  if (reason === 'fallback_round_robin') {
    return { label: '兜底轮询', detail: staffId ? `员工 #${staffId}` : '无负责人 ID', tone: 'vkpi-chip--warn' };
  }
  if (reason === 'metadata.responsible_staff_id') {
    return { label: '负责人导入', detail: staffId ? `员工 #${staffId}` : '负责人字段未匹配', tone: 'is-success' };
  }
  if (reason === 'metadata.created_by_staff_id') {
    return { label: '导入创建人', detail: staffId ? `员工 #${staffId}` : '创建人未匹配', tone: 'is-success' };
  }
  if (reason.startsWith('metadata.')) {
    return { label: '导入字段', detail: `${reason.replace('metadata.', '')}${staffId ? ` · #${staffId}` : ''}`, tone: 'is-success' };
  }
  return { label: '规则分配', detail: `${reason || 'unknown'}${staffId ? ` · #${staffId}` : ''}`, tone: 'vkpi-chip--muted' };
}

function AssignmentCell({ row }: { row: Row }) {
  const assignment = assignmentDisplay(row);
  return (
    <div>
      <span className={`vkpi-chip ${assignment.tone}`}>{assignment.label}</span>
      <br />
      <small className="vkpi-help-text">{assignment.detail}</small>
    </div>
  );
}

export function OutreachTables({ busy, suggestions, digestItems, digestDate, onUpdateSuggestion }: OutreachTablesProps) {
  return (
    <>
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header"><div><h2>建议联系</h2><span>{suggestions.length} 条</span></div></div>
        {suggestions.length ? (
          <div className="vkpi-suggestion-grid">
            {suggestions.map((row) => <SuggestionCard key={String(row.id)} row={row} busy={busy} onUpdateSuggestion={onUpdateSuggestion} />)}
          </div>
        ) : (
          <div className="vkpi-table-empty">暂无真实建议联系。平台未配置或没有抓取结果时不会显示假 KOL。</div>
        )}
      </section>
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header">
          <div><h2>今日前 100 条优质内容</h2><span>{digestDate || '今日'} · 未联系 KOL</span></div>
        </div>
        <div className="vkpi-table-wrap">
          <table className="vkpi-table">
            <thead><tr><th>排名</th><th>平台</th><th>红人 / Handle</th><th>分配</th><th>来源产品</th><th>内容</th><th>链接</th><th>播放</th><th>评分</th><th>买家画像</th><th>推荐理由</th><th>操作</th></tr></thead>
            <tbody>
              {digestItems.length ? digestItems.map((row) => (
                <tr key={String(row.suggestion_id || row.id)}>
                  <td>{String(row.rank || '-')}</td>
                  <td>{platformDisplay(row.platform)}</td>
                  <td>{String(row.channel_name || row.handle || '-')}</td>
                  <td><AssignmentCell row={row} /></td>
                  <td>{String(row.source_product_sku || '-')}</td>
                  <td>{String(row.source_video_title || '-')}</td>
                  <td><LinkActions row={row} /></td>
                  <td>{numberFormatter.format(safeNumber(row.source_view_count))}</td>
                  <td>{String(row.quality_score || row.score || '-')}</td>
                  <td>{String(row.buyer_profile || '-')}</td>
                  <td>{String(row.relevance_reason || '-')}</td>
                  <td>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.suggestion_id, 'claim')}>认领</button>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.suggestion_id, 'create_project')}>建项目+短链</button>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.suggestion_id, 'dismiss')}>忽略</button>
                  </td>
                </tr>
              )) : <tr><td className="vkpi-table-empty" colSpan={12}>今日还没有未联系 KOL 清单。每天 08:00 自动生成；也可以先配置产品监控后手动生成。</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
