import type { VkpiProjectRow, VkpiStaffMember } from '../vkpiTypes';
import { Avatar } from '../shared/Avatar';
import { PlatformPill } from '../shared/PlatformPill';
import { StageBadge } from '../shared/StageBadge';
import { currencyFormatter, numberFormatter } from '../shared/vkpiFormatters';
import { shortDateTime } from '../shared/vkpiDataUtils';

const messageSourceLabels: Record<string, string> = {
  DM: '私信',
  Email: '邮件',
  'Comment reply': '评论回复',
  'Manual note': '手动备注',
  'No reply': '未回复',
};

export function ProjectTable({
  projects,
  selectedProjectId,
  viewMode,
  onSelectProject,
  onOpenKolProfile,
  onOpenStaffProfile,
  onDeleteProject,
}: {
  projects: VkpiProjectRow[];
  selectedProjectId?: string;
  viewMode: 'manager' | 'employee';
  onSelectProject: (project: VkpiProjectRow) => void;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onDeleteProject?: (project: VkpiProjectRow) => void | Promise<void>;
}) {
  const showFinancials = viewMode === 'manager';
  const columnCount = showFinancials ? 8 : 7;
  return (
    <div className="vkpi-table-wrap">
      <table className="vkpi-table">
        <thead>
          {showFinancials ? (
            <tr>
              <th>员工</th>
              <th>KOL</th>
              <th>项目</th>
              <th>阶段</th>
              <th>数据</th>
              <th>销售 / 成本</th>
              <th>耗时</th>
              <th />
            </tr>
          ) : (
            <tr>
              <th>KOL</th>
              <th>项目</th>
              <th>阶段</th>
              <th>数据</th>
              <th>本周销售</th>
              <th>耗时</th>
              <th />
            </tr>
          )}
        </thead>
        <tbody>
          {projects.length ? (
            projects.map((project) => {
              const hasProjectKolCount = typeof project.kolCount === 'number' && project.kolCount > 0;
              return (
                <tr
                  key={project.id}
                  className={selectedProjectId === project.id ? 'is-selected' : undefined}
                  onClick={() => onSelectProject(project)}
                  title="点击查看员工、KOL、项目、消息、短链和归因详情"
                >
                {showFinancials ? (
                  <td>
                    <button
                      className={`vkpi-owner-cell vkpi-owner-cell--button vkpi-owner-cell--strong ${project.ownerId && onOpenStaffProfile ? 'is-clickable' : ''}`}
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        if (project.ownerId && onOpenStaffProfile) void onOpenStaffProfile(project.ownerId, { name: project.ownerName, avatarUrl: project.ownerAvatar });
                      }}
                      title={project.ownerId && onOpenStaffProfile ? '打开员工详情' : undefined}
                    >
                      <Avatar name={project.ownerName} src={project.ownerAvatar} size="xs" />
                      <span>{project.ownerName}</span>
                    </button>
                  </td>
                ) : null}
                <td>
                  <button
                    className={`vkpi-kol-cell vkpi-kol-cell-button ${onOpenKolProfile ? 'is-clickable' : ''}`}
                    type="button"
                    onClick={(event) => {
                      if (!onOpenKolProfile) return;
                      event.stopPropagation();
                      void onOpenKolProfile(project);
                    }}
                    title={onOpenKolProfile ? '打开 KOL 完整档案' : undefined}
                  >
                    <Avatar name={project.kolName} src={project.kolAvatar} />
                    <div>
                      <strong>{hasProjectKolCount && project.kolCount! > 1 ? `${numberFormatter.format(project.kolCount!)} KOL` : project.kolName}</strong>
                      <span>{hasProjectKolCount ? `${numberFormatter.format(project.kolWithEvidence || 0)} 有证据 · ${numberFormatter.format(project.evidenceCount || 0)} 条内容` : project.kolHandle}</span>
                      <PlatformPill platform={project.platform} />
                    </div>
                  </button>
                </td>
                <td>
                  <div className="vkpi-project-primary">
                    <strong>{project.campaign || '未命名项目'}</strong>
                    <span>{messageSourceLabels[project.latestMessageSource] || project.latestMessageSource} · {shortDateTime(project.latestMessageAt)}</span>
                  </div>
                </td>
                <td><StageBadge stage={project.stage} /></td>
                <td>
                  <div className="vkpi-metric-stack">
                    <span>播 {project.views ? numberFormatter.format(project.views) : '—'}</span>
                    <small>{hasProjectKolCount ? `KOL ${numberFormatter.format(project.kolCount!)} · 有证据 ${numberFormatter.format(project.kolWithEvidence || 0)}` : `点 ${project.clicks == null ? '—' : numberFormatter.format(project.clicks)} · 单 ${project.orders == null ? '—' : numberFormatter.format(project.orders)}`}</small>
                  </div>
                </td>
                <td>
                  <div className="vkpi-metric-stack">
                    <span>{currencyFormatter.format(project.gmv)}</span>
                    {showFinancials ? <small>成本 {currencyFormatter.format(project.cost)}</small> : null}
                  </div>
                </td>
                <td>
                  <div className="vkpi-time-stack">
                    <span>{project.totalDurationLabel || '—'}</span>
                    <small>阶段 {project.stageDurationLabel || '—'}</small>
                  </div>
                </td>
                <td className="vkpi-row-actions" onClick={(event) => event.stopPropagation()}>
                  <button className="vkpi-mini-button" type="button" onClick={() => onSelectProject(project)}>
                    详情
                  </button>
                  {onDeleteProject ? (
                    <button className="vkpi-mini-button vkpi-mini-button--danger" type="button" onClick={() => void onDeleteProject(project)}>
                      删除
                    </button>
                  ) : (
                    <span className="vkpi-row-arrow">›</span>
                  )}
                </td>
                </tr>
              );
            })
          ) : (
            <tr>
              <td className="vkpi-table-empty" colSpan={columnCount}>
                暂无 KOL 项目。请先在红人搜索中认领红人，再创建项目和短链。
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
