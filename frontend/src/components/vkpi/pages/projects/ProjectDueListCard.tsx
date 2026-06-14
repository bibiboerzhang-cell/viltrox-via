import { useCallback, useEffect, useState } from 'react';
import { ClipboardList, RefreshCw } from 'lucide-react';
import { getProjectsDueList, type VkpiProjectDueListItem } from '../../../../services/vkpi/projects-api';

interface ProjectDueListCardProps {
  apiToken?: string;
  /** 逾期阈值(天):物流签收满 N 天仍未推进的项目进入待办。后端默认 7。 */
  daysOverdue?: number;
  /** 点击某条待办:把对应项目详情打开(只读卡片不写库,仅导航)。 */
  onOpenProject?: (projectId: string) => void;
}

const EMPTY_STATE_FALLBACK = '当前无逾期待观察项 / 物流签收数据接入后自动出现';

// 履约待办 / Due List —— 消费已真后端 GET /api/admin/vkpi/projects/due-list。
// 本轮只读:不做"标记完成 / 推进"动作;count=0 时如实显示后端 note,不造假行。
export function ProjectDueListCard({ apiToken, daysOverdue = 7, onOpenProject }: ProjectDueListCardProps) {
  const [items, setItems] = useState<VkpiProjectDueListItem[]>([]);
  const [count, setCount] = useState(0);
  const [note, setNote] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [loadedOnce, setLoadedOnce] = useState(false);

  const load = useCallback(async () => {
    if (!apiToken) return;
    setLoading(true);
    setError('');
    try {
      const response = await getProjectsDueList(apiToken, daysOverdue);
      setItems(Array.isArray(response.items) ? response.items : []);
      setCount(typeof response.count === 'number' ? response.count : (response.items?.length ?? 0));
      setNote(typeof response.note === 'string' ? response.note : '');
    } catch (err) {
      setError(err instanceof Error ? err.message : '履约待办加载失败');
      setItems([]);
      setCount(0);
    } finally {
      setLoading(false);
      setLoadedOnce(true);
    }
  }, [apiToken, daysOverdue]);

  useEffect(() => {
    void load();
  }, [load]);

  // 没有 token 时不渲染(看板未鉴权场景),避免出现无意义空壳。
  if (!apiToken) return null;

  const isEmpty = loadedOnce && !error && count === 0;
  const emptyNote = note && note.trim() ? note.trim() : EMPTY_STATE_FALLBACK;

  return (
    <section
      className="rounded-xl border border-white/[0.06] bg-white/[0.012] overflow-hidden mb-4"
      aria-label="履约待办"
    >
      <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-white/[0.04]">
        <div className="flex items-center gap-2 min-w-0">
          <ClipboardList className="w-4 h-4 text-amber-300/80 shrink-0" aria-hidden />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-medium text-slate-200">履约待办</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-400">
                已签收 ≥ {daysOverdue} 天未推进
              </span>
              {count > 0 ? (
                <span className="text-[10px] px-1.5 py-0.5 rounded border border-amber-300/40 bg-amber-300/15 text-amber-200">
                  {count}
                </span>
              ) : null}
            </div>
            <p className="text-[11px] text-slate-500 mt-0.5 truncate">
              物流签收满 {daysOverdue} 天仍未进入下一阶段的项目,只读观察清单。
            </p>
          </div>
        </div>
        <button
          type="button"
          className="text-[11px] px-2 py-1 rounded border border-white/[0.08] bg-white/[0.03] text-slate-400 hover:text-slate-200 hover:border-white/[0.14] transition-colors flex items-center gap-1 shrink-0 disabled:opacity-50"
          onClick={() => void load()}
          disabled={loading}
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} aria-hidden />
          {loading ? '刷新中' : '刷新'}
        </button>
      </header>

      <div className="px-4 py-3">
        {error ? (
          <div className="text-[12px] text-rose-300/90">
            {error}
          </div>
        ) : !loadedOnce && loading ? (
          <div className="text-[12px] text-slate-500">加载履约待办…</div>
        ) : isEmpty ? (
          <div className="text-[12px] text-slate-500 leading-relaxed">{emptyNote}</div>
        ) : (
          <ul className="flex flex-col gap-2">
            {items.map((item) => {
              const projectId = String(item.project_id);
              const days = typeof item.days_since_delivered === 'number' ? item.days_since_delivered : null;
              const assignments = typeof item.assignment_count === 'number' ? item.assignment_count : null;
              const clickable = Boolean(onOpenProject);
              return (
                <li
                  key={projectId}
                  className={`rounded-lg border border-white/[0.06] bg-white/[0.012] px-3 py-2 transition-colors ${
                    clickable ? 'cursor-pointer hover:bg-white/[0.025] hover:border-white/[0.10]' : ''
                  }`}
                  onClick={clickable ? () => onOpenProject?.(projectId) : undefined}
                  role={clickable ? 'button' : undefined}
                  tabIndex={clickable ? 0 : undefined}
                  onKeyDown={
                    clickable
                      ? (event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            onOpenProject?.(projectId);
                          }
                        }
                      : undefined
                  }
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-[12.5px] text-slate-200 font-medium truncate">
                        {item.project_name || `项目 #${projectId}`}
                      </div>
                      <div className="text-[11px] text-slate-500 truncate mt-0.5">
                        {item.product_name || '产品未填'}
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-amber-300/30 bg-amber-300/10 text-amber-200/90">
                        {days != null ? `已签收 ${days} 天` : '签收时间未知'}
                      </span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-400">
                        {assignments != null ? `${assignments} assignment` : 'assignment 未知'}
                      </span>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
