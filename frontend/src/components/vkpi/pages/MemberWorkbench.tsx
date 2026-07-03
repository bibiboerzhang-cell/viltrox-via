// C7-B1 成员个人工作台(最小可用):我的 KOL 数 / 我的项目 / 我负责的官号 / 我的提醒。
// 全部复用已有数据源,不新建重后端:
//   - 我的 KOL:/api/admin/vkpi/my-kol/aggregate 的 kpi_summary(成员永远只拿自己的);
//   - 我的项目 / 我的提醒:props.data(dashboard summary,后端已按 effective_staff_id 隔离);
//   - 我负责的官号:C7-A3 分配表(useChannelAssignments 共享快照,零额外请求)。
// 仅员工视图(viewMode=employee)在工作页顶部渲染,可折叠并本地记忆。
import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import type { VkpiDashboardData, VkpiPageKey } from '../vkpiTypes';
import { useChannelAssignments } from './channels/ChannelAssignmentControl';
import './memberWorkbench.css';

const AGG_STALE_MS = 60_000;
const COLLAPSE_KEY = 'vkpi:member-workbench:collapsed';
// 项目「已收尾」阶段口径:其余一律算进行中(与看板阶段字典一致,不另造状态)
const DONE_STAGES = new Set(['measured', 'closed', 'lost', 'released', 'cancelled']);

type MyKolStats = { favorites: number; claimed: number; inProject: number };

let aggSnapshot: { key: string; fetchedAt: number; stats: MyKolStats } | null = null;

function readCollapsed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === '1';
  } catch {
    return false;
  }
}

function writeCollapsed(value: boolean) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(COLLAPSE_KEY, value ? '1' : '0');
  } catch {
    // localStorage 不可用时静默:仅丢失折叠记忆,不影响功能
  }
}

// MY KOL 聚合(仅取 kpi_summary 计数),模块级缓存 60s,页面切换不重复拉。
function useMyKolStats(apiToken?: string) {
  const [stats, setStats] = useState<MyKolStats | null>(aggSnapshot ? aggSnapshot.stats : null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!apiToken) return;
    const key = apiToken.slice(-10);
    if (aggSnapshot && aggSnapshot.key === key && Date.now() - aggSnapshot.fetchedAt < AGG_STALE_MS) {
      setStats(aggSnapshot.stats);
      return;
    }
    let cancelled = false;
    import('../../../services/vkpi/kol-api')
      .then(({ getMyKolAggregate }) => getMyKolAggregate(apiToken))
      .then((resp) => {
        const kpi = (resp.kpi_summary || {}) as Record<string, number>;
        const next: MyKolStats = {
          favorites: Number(kpi.favorites_count || 0),
          claimed: Number(kpi.claimed_count || 0),
          inProject: Number(kpi.in_project_count || 0),
        };
        aggSnapshot = { key, fetchedAt: Date.now(), stats: next };
        if (!cancelled) setStats(next);
      })
      .catch((requestError) => {
        if (!cancelled) setError(requestError instanceof Error ? requestError.message : 'MY KOL 聚合加载失败');
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  return { stats, error };
}

export function MemberWorkbench({
  data,
  apiToken,
  userName,
  onSelectPage,
}: {
  data: VkpiDashboardData;
  apiToken?: string;
  userName?: string;
  onSelectPage?: (page: VkpiPageKey) => void;
}) {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const { stats, error: aggError } = useMyKolStats(apiToken);
  const { assignments, meStaffId, loading: assignmentsLoading } = useChannelAssignments(apiToken);

  const toggleCollapsed = () =>
    setCollapsed((value) => {
      const next = !value;
      writeCollapsed(next);
      return next;
    });

  // 我负责的官号:分配表里 staff_id = 我 的行(official 口径由后端 metadata 判定)
  const myChannels = useMemo(() => {
    if (meStaffId == null) return [];
    return assignments.filter((row) => Number(row.staff_id) === Number(meStaffId) && row.official !== false);
  }, [assignments, meStaffId]);

  const projectsTotal = data.projects.length;
  const projectsActive = useMemo(
    () => data.projects.filter((project) => !DONE_STAGES.has(String(project.stage || '').toLowerCase())).length,
    [data.projects],
  );
  const alertsCount = data.alerts.length;
  const topAlert = data.alerts[0];

  const kolValue = stats ? stats.favorites + stats.claimed : null;
  const goto = (page: VkpiPageKey) => onSelectPage?.(page);

  return (
    <section className="vkpi-member-workbench" aria-label="成员个人工作台">
      <header>
        <div>
          <h2>我的工作台</h2>
          <p>{userName ? `${userName} · ` : ''}只看我名下的数据（与后端 effective_staff_id 口径一致）</p>
        </div>
        <button type="button" aria-expanded={!collapsed} onClick={toggleCollapsed}>
          {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          {collapsed ? '展开' : '收起'}
        </button>
      </header>
      {collapsed ? null : (
        <div className="vkpi-member-workbench__grid">
          <button type="button" className="vkpi-member-workbench__card" onClick={() => goto('channels')}>
            <span>我的 KOL</span>
            <b>{kolValue == null ? '—' : kolValue}</b>
            <em>{stats ? `关注 ${stats.favorites} · 认领 ${stats.claimed} · 在项目 ${stats.inProject}` : aggError || '聚合加载中…'}</em>
          </button>
          <button type="button" className="vkpi-member-workbench__card" onClick={() => goto('projects')}>
            <span>我的项目</span>
            <b>{projectsActive}</b>
            <em>进行中 {projectsActive} · 共 {projectsTotal} 个</em>
          </button>
          <button type="button" className="vkpi-member-workbench__card" onClick={() => goto('channels')}>
            <span>我负责的官号</span>
            <b>{assignmentsLoading && !assignments.length ? '—' : myChannels.length}</b>
            <em>
              {myChannels.length
                ? myChannels
                    .slice(0, 3)
                    .map((row) => row.display_name || row.handle || `#${row.channel_id}`)
                    .join(' · ') + (myChannels.length > 3 ? ` 等 ${myChannels.length} 个` : '')
                : '暂无分配（由管理层在矩阵页指派）'}
            </em>
          </button>
          <button type="button" className="vkpi-member-workbench__card" onClick={() => goto('command')}>
            <span>我的提醒</span>
            <b>{alertsCount}</b>
            <em>{topAlert ? topAlert.label : '暂无待处理提醒'}</em>
          </button>
        </div>
      )}
    </section>
  );
}
