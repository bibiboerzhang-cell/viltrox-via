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

export function OutreachTables({ busy, suggestions, digestItems, digestDate, onUpdateSuggestion }: OutreachTablesProps) {
  return (
    <>
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header"><div><h2>建议联系</h2><span>{suggestions.length} 条</span></div></div>
        <div className="vkpi-table-wrap">
          <table className="vkpi-table">
            <thead><tr><th>平台</th><th>红人 / Handle</th><th>来源产品</th><th>内容</th><th>播放</th><th>评分</th><th>操作</th></tr></thead>
            <tbody>
              {suggestions.length ? suggestions.map((row) => (
                <tr key={String(row.id)}>
                  <td>{platformDisplay(row.platform)}</td>
                  <td>{String(row.channel_name || row.handle || '-')}</td>
                  <td>{String(row.source_product_sku || '-')}</td>
                  <td>{String(row.source_video_title || '-')}</td>
                  <td>{numberFormatter.format(safeNumber(row.source_view_count))}</td>
                  <td>{String(row.score || '-')}</td>
                  <td>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.id, 'claim')}>认领</button>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.id, 'create_project')}>建项目+短链</button>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.id, 'dismiss')}>忽略</button>
                  </td>
                </tr>
              )) : <tr><td className="vkpi-table-empty" colSpan={7}>暂无真实建议联系。平台未配置或没有抓取结果时不会显示假 KOL。</td></tr>}
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
            <thead><tr><th>排名</th><th>平台</th><th>红人 / Handle</th><th>来源产品</th><th>内容</th><th>播放</th><th>评分</th><th>买家画像</th><th>推荐理由</th><th>操作</th></tr></thead>
            <tbody>
              {digestItems.length ? digestItems.map((row) => (
                <tr key={String(row.suggestion_id || row.id)}>
                  <td>{String(row.rank || '-')}</td>
                  <td>{platformDisplay(row.platform)}</td>
                  <td>{String(row.channel_name || row.handle || '-')}</td>
                  <td>{String(row.source_product_sku || '-')}</td>
                  <td>{String(row.source_video_title || '-')}</td>
                  <td>{numberFormatter.format(safeNumber(row.source_view_count))}</td>
                  <td>{String(row.quality_score || row.score || '-')}</td>
                  <td>{String(row.buyer_profile || '-')}</td>
                  <td>{String(row.relevance_reason || '-')}</td>
                  <td>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.suggestion_id, 'claim')}>认领</button>
                    <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onUpdateSuggestion(row.suggestion_id, 'create_project')}>建项目+短链</button>
                  </td>
                </tr>
              )) : <tr><td className="vkpi-table-empty" colSpan={10}>今日还没有未联系 KOL 清单。每天 08:00 自动生成；也可以先配置产品监控后手动生成。</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
