import { useEffect, useMemo, useState } from 'react';
import { listProductRecommendationRuns, listProductRecommendations } from '../../../../services/vkpi/product-api';
import { InfoBlock } from '../../shared/InfoBlock';

type Row = Record<string, unknown>;

interface RecommendationRunReviewPanelProps {
  apiToken?: string;
  busy: boolean;
  onBusyChange: (busy: boolean) => void;
  onMessage: (message: string) => void;
  onRecommendationsChange: (rows: Row[]) => void;
  onRunLoaded: (run: Row | null) => void;
}

const strategyOptions = [
  { value: '', label: '全部策略' },
  { value: 'new_launch_match_v1', label: '新品匹配' },
  { value: 'kol_product_fit_v1', label: 'KOL 产品适配' },
  { value: 'project_next_action_v1', label: '项目下一步' },
];

function strategyLabel(value: unknown) {
  const raw = String(value || '');
  return strategyOptions.find((option) => option.value === raw)?.label || raw || '-';
}

function formatDate(value: unknown) {
  if (!value) return '-';
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function statusCountLabel(run: Row) {
  const counts = (run.recommendation_status_counts || {}) as Row;
  const entries = Object.entries(counts);
  if (!entries.length) return '-';
  return entries.map(([status, count]) => `${status}:${String(count)}`).join(' / ');
}

function filtersLabel(run: Row) {
  const filters = (run.filters || {}) as Row;
  const scenario = String(filters.scenario || filters.source_mode || '');
  const product = String(filters.product_query || filters.kol_query || filters.product_name || '');
  const source = scenario || product;
  return source || '-';
}

export function RecommendationRunReviewPanel({
  apiToken,
  busy,
  onBusyChange,
  onMessage,
  onRecommendationsChange,
  onRunLoaded,
}: RecommendationRunReviewPanelProps) {
  const [runs, setRuns] = useState<Row[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState('');
  const [selectedRunId, setSelectedRunId] = useState('');
  const [loading, setLoading] = useState(false);

  const selectedRun = useMemo(() => runs.find((run) => String(run.id) === selectedRunId) || null, [runs, selectedRunId]);

  const loadRuns = async () => {
    if (!apiToken) {
      setRuns([]);
      onRunLoaded(null);
      return;
    }
    setLoading(true);
    try {
      const response = await listProductRecommendationRuns(apiToken, {
        strategyVersion: selectedStrategy || undefined,
        limit: 30,
      });
      const rows = response.runs || [];
      setRuns(rows);
      if (selectedRunId && !rows.some((row) => String(row.id) === selectedRunId)) {
        setSelectedRunId('');
        onRunLoaded(null);
      }
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '推荐 run 读取失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadRuns();
  }, [apiToken, selectedStrategy]);

  const openRun = async (run: Row) => {
    if (!apiToken || !run.id) return;
    setSelectedRunId(String(run.id));
    onBusyChange(true);
    try {
      const response = await listProductRecommendations(apiToken, { runId: String(run.id), limit: 200 });
      const rows = response.recommendations || [];
      onRecommendationsChange(rows);
      onRunLoaded(run);
      onMessage(`已载入 ${rows.length} 条 ${strategyLabel(run.strategy_version)} preview 推荐。`);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '推荐 run 候选读取失败');
    } finally {
      onBusyChange(false);
    }
  };

  return (
    <section className="vkpi-card vkpi-table-card">
      <div className="vkpi-table-card__header">
        <div>
          <h2>推荐 Preview Run</h2>
          <span>{runs.length} 条最近运行</span>
        </div>
        <div className="vkpi-run-toolbar">
          <select value={selectedStrategy} onChange={(event) => setSelectedStrategy(event.target.value)}>
            {strategyOptions.map((option) => <option key={option.value || 'all'} value={option.value}>{option.label}</option>)}
          </select>
          <button className="vkpi-mini-button" type="button" disabled={loading || busy || !apiToken} onClick={() => void loadRuns()}>刷新</button>
        </div>
      </div>
      {selectedRun ? (
        <div className="vkpi-result-grid">
          <InfoBlock label="当前策略" value={strategyLabel(selectedRun.strategy_version)} />
          <InfoBlock label="Run ID" value={`#${String(selectedRun.id)}`} />
          <InfoBlock label="候选" value={String(selectedRun.candidate_count || 0)} />
          <InfoBlock label="推荐" value={String(selectedRun.recommendation_count || 0)} />
        </div>
      ) : null}
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead><tr><th>时间</th><th>策略</th><th>状态</th><th>候选</th><th>推荐</th><th>状态分布</th><th>来源</th><th>操作</th></tr></thead>
          <tbody>
            {runs.length ? runs.map((run) => (
              <tr key={String(run.id)}>
                <td>{formatDate(run.created_at)}</td>
                <td>{strategyLabel(run.strategy_version)}</td>
                <td>{String(run.status || '-')}</td>
                <td>{String(run.candidate_count || 0)}</td>
                <td>{String(run.recommendation_count || 0)}</td>
                <td>{statusCountLabel(run)}</td>
                <td>{filtersLabel(run)}</td>
                <td>
                  <button className="vkpi-mini-button" type="button" disabled={busy || loading} onClick={() => void openRun(run)}>
                    {String(run.id) === selectedRunId ? '已载入' : '查看候选'}
                  </button>
                </td>
              </tr>
            )) : <tr><td className="vkpi-table-empty" colSpan={8}>{loading ? '正在读取 preview runs...' : '暂无持久化 preview run。先从 P4 dry-run CLI 持久化一条 run。'}</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
