import { platformDisplay } from '../../shared/vkpiDataUtils';

type Row = Record<string, unknown>;
type RecommendationAction = 'shortlist' | 'reject' | 'claim' | 'create_project';

interface RecommendationCandidateTableProps {
  busy: boolean;
  recommendations: Row[];
  readOnly?: boolean;
  onSelect: (row: Row) => void;
  onAction: (id: unknown, action: RecommendationAction) => void | Promise<void>;
}

export function RecommendationCandidateTable({
  busy,
  recommendations,
  readOnly = false,
  onSelect,
  onAction,
}: RecommendationCandidateTableProps) {
  return (
    <section className="vkpi-card vkpi-table-card">
      <div className="vkpi-table-card__header"><div><h2>产品推荐候选</h2><span>{recommendations.length} 条</span></div></div>
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead><tr><th>排名</th><th>平台</th><th>红人</th><th>分数</th><th>状态</th><th>模型</th><th>主 KOL</th><th>操作</th></tr></thead>
          <tbody>
            {recommendations.length ? recommendations.map((row) => (
              <tr key={String(row.id)}>
                <td>{String(row.rank || '-')}</td>
                <td>{platformDisplay(row.platform)}</td>
                <td><button className="vkpi-link-button" type="button" onClick={() => onSelect(row)}>{String(row.display_name || row.handle || '-')}</button></td>
                <td>{String(row.score || '-')}</td>
                <td>{String(row.status || '-')}</td>
                <td>rule_v0</td>
                <td>{row.linked_main_kol_id ? `#${String(row.linked_main_kol_id)}` : '未落库'}</td>
                <td>
                  {readOnly ? (
                    <span className="vkpi-help-text">Preview only</span>
                  ) : (
                    <>
                      <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onAction(row.id, 'shortlist')}>入选</button>
                      <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onAction(row.id, 'claim')}>认领</button>
                      <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onAction(row.id, 'create_project')}>建项目</button>
                      <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => void onAction(row.id, 'reject')}>忽略</button>
                    </>
                  )}
                </td>
              </tr>
            )) : <tr><td className="vkpi-table-empty" colSpan={8}>暂无推荐候选。先创建发布项目并导入真实 KOL 池。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
