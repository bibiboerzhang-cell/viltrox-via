import { platformDisplay, safeNumber } from '../../shared/vkpiDataUtils';
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
        <div className="vkpi-table-wrap">
          <table className="vkpi-table">
            <thead><tr><th>平台</th><th>红人 / Handle</th><th>来源产品</th><th>内容</th><th>链接</th><th>播放</th><th>评分</th><th>操作</th></tr></thead>
            <tbody>
              {suggestions.length ? suggestions.map((row) => (
                <tr key={String(row.id)}>
                  <td>{platformDisplay(row.platform)}</td>
                  <td>{String(row.channel_name || row.handle || '-')}</td>
                  <td>{String(row.source_product_sku || '-')}</td>
                  <td>{String(row.source_video_title || '-')}</td>
                  <td><LinkActions row={row} /></td>
                  <td>{numberFormatter.format(safeNumber(row.source_view_count))}</td>
                  <td>{String(row.score || '-')}</td>
                  <td>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.id, 'claim')}>认领</button>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.id, 'create_project')}>建项目+短链</button>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.id, 'dismiss')}>忽略</button>
                  </td>
                </tr>
              )) : <tr><td className="vkpi-table-empty" colSpan={8}>暂无真实建议联系。平台未配置或没有抓取结果时不会显示假 KOL。</td></tr>}
            </tbody>
          </table>
        </div>
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
