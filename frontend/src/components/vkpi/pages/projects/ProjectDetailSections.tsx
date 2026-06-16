// 纯重构:从 ProjectDetailView.tsx 抽出的纯展示子区块(只吃 props,零 state/effect/handler)。
// idiom 保真:沿用 JSX 写法。所有渲染逻辑逐字搬运,行为零变。state/逻辑/handler 全部留主函数,经 prop 下传。

import { PackageCheck } from 'lucide-react';
import type { VkpiProjectRow, VkpiProjectStage } from '../../vkpiTypes';
import { shortDateTime } from '../../shared/vkpiDataUtils';
import {
  detailTabs,
  formatMoney,
  formatNumber,
  formatRatio,
  isLostStage,
  parseDays,
  stageIndex,
  type DetailTab,
  type ProjectStatsSummary,
  type TaskItem,
} from '../../../../domains/projects';
import {
  healthBg,
  PROJECT_STAGE_COLOR,
  PROJECT_STAGE_FLOW,
  PROJECT_STATUS_COLOR,
  statusBg,
} from './projectDeliverableStyle';

interface HealthState {
  score: number;
  className: string;
  label: string;
}

interface BottleneckState {
  from: number;
  to: number;
  text: string;
}

// A. 返回栏 + 编辑按钮 + 大头部卡(健康度/负责人/操作按钮组/导出)
export function ProjectDetailHeaderCard({
  project,
  health,
  currentHealthColor,
  ownerInitial,
  campaignStatus,
  bottleneck,
  stats,
  viewMode,
  canEdit,
  canSetFollowStatus,
  showDelete,
  ownerProfileDisabled,
  onBack,
  onEdit,
  onToggleFollowStatus,
  onCancelProject,
  onDeleteProject,
  onAddKol,
  onGenerateContract,
  onOpenStaffProfile,
  onExportKols,
  canExport,
  onShare,
}: {
  project: VkpiProjectRow;
  health: HealthState;
  currentHealthColor: string;
  ownerInitial: string;
  campaignStatus: string;
  bottleneck: BottleneckState;
  stats: ProjectStatsSummary;
  viewMode: string;
  canEdit: boolean;
  canSetFollowStatus: boolean;
  showDelete: boolean;
  ownerProfileDisabled: boolean;
  onBack: () => void;
  onEdit: () => void;
  onToggleFollowStatus: () => void;
  onCancelProject: () => void;
  onDeleteProject: () => void;
  onAddKol: () => void;
  onGenerateContract: () => void;
  onOpenStaffProfile: () => void;
  onExportKols: () => void;
  canExport: boolean;
  onShare?: () => void;
}) {
  return (
    <>
      <div className="flex items-center gap-3">
        <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.02] text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white" type="button" onClick={onBack}>← 返回项目列表</button>
        <div className="flex-1" />
        <button className="px-3 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.02] text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white flex items-center gap-1.5" type="button" onClick={onEdit} disabled={!canEdit}>编辑项目</button>
      </div>

      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4 flex items-start gap-4">
        <div className="w-14 h-14 rounded-xl flex items-center justify-center shrink-0" style={{ background: healthBg(health.score), color: currentHealthColor }}>
          <PackageCheck size={26} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <h2 className="text-[20px] font-bold text-white">{project.campaign || '未命名推广'}</h2>
            <span className="text-[10.5px] px-2 py-0.5 rounded font-medium" style={{ background: statusBg(campaignStatus), color: PROJECT_STATUS_COLOR[campaignStatus] || PROJECT_STATUS_COLOR['已结束'] }}>{campaignStatus}</span>
            <span className="text-[10px] px-2 py-0.5 rounded bg-white/[0.04] text-slate-300 flex items-center gap-1">产品 {project.productName || project.productSku || '-'}</span>
          </div>
          <div className="text-[12px] text-slate-400 mb-2">{bottleneck.text}</div>
          <div className="flex items-center gap-3 flex-wrap">
            <button
              className="flex items-center gap-1.5"
              type="button"
              disabled={ownerProfileDisabled}
              onClick={onOpenStaffProfile}
            >
              <div className="w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white" style={{ background: '#a855f7' }}>{ownerInitial}</div>
              <span className="text-[10.5px] text-slate-400">负责人 {project.ownerName || '-'}</span>
            </button>
            <span className="text-[10px] text-slate-600">·</span>
            <span className="text-[10.5px] text-slate-400">最近更新 {shortDateTime(project.updatedAt || project.latestMessageAt)}</span>
            <span className="text-[10px] text-slate-600">·</span>
            <span className="text-[10.5px] text-slate-400">预算 {formatMoney(stats.cost)} · 销售 {formatMoney(stats.gmv)}</span>
            <span className="text-[10px] text-slate-600">·</span>
            <span className="text-[10.5px] text-slate-400">{viewMode === 'manager' ? '上市推广' : '我的跟进'}</span>
          </div>
          <div className="flex items-center gap-2 mt-3 flex-wrap">
            <button className="px-3 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.02] text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white" type="button" onClick={onEdit} disabled={!canEdit}>编辑</button>
            <button className="px-3 py-1.5 rounded-md border border-sky-400/30 bg-sky-400/10 text-[11px] text-sky-200 hover:bg-sky-400/15" type="button" onClick={onToggleFollowStatus} disabled={!canSetFollowStatus}>{project.followStatus === 'paused' ? '重新开启' : '暂停本轮跟进'}</button>
            <button className="px-3 py-1.5 rounded-md border border-red-500/30 bg-red-500/10 text-[11px] text-red-300 hover:bg-red-500/15" type="button" onClick={onCancelProject}>取消推广</button>
            {showDelete ? <button className="px-3 py-1.5 rounded-md border border-red-500/30 bg-red-500/10 text-[11px] text-red-300 hover:bg-red-500/15" type="button" onClick={onDeleteProject}>删除</button> : null}
            <button className="px-3 py-1.5 rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[11px] font-medium flex items-center gap-1.5" type="button" onClick={onAddKol}>+ 添加 KOL</button>
            <button className="px-3 py-1.5 rounded-md border border-purple-500/40 bg-purple-500/10 hover:bg-purple-500/20 text-purple-200 text-[11px] font-medium" type="button" onClick={onGenerateContract}>生成合同</button>
            {onShare ? <button className="px-3 py-1.5 rounded-md border border-cyan-400/30 bg-cyan-400/10 hover:bg-cyan-400/20 text-cyan-200 text-[11px] font-medium flex items-center gap-1.5" type="button" onClick={onShare}>👥 协作者</button> : null}
            <button
              className="px-3 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.02] text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white"
              type="button"
              disabled={!canExport}
              onClick={onExportKols}
            >导出 KOL 名单</button>
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-[10px] text-slate-500 mb-0.5">健康度</div>
          <div className="text-[40px] font-bold leading-none tabular-nums" style={{ color: currentHealthColor }}>{health.score}</div>
          <div className="mt-2 text-[10.5px] px-2 py-0.5 rounded font-medium inline-flex" style={{ background: healthBg(health.score), color: currentHealthColor }}>{health.label}</div>
        </div>
      </div>
    </>
  );
}

// B. 6 格 KPI
export function ProjectKpiGrid({
  stats,
  kolCount,
}: {
  stats: ProjectStatsSummary;
  kolCount: number;
}) {
  return (
    <div className="grid grid-cols-6 gap-2" aria-label="项目 KPI">
      {[
        ['参与 KOL', kolCount, '当前项目行'],
        ['总曝光', formatNumber(stats.views), '自动汇总'],
        ['已发布内容', stats.published, `发布率 ${stats.publishRate}%`],
        ['短链点击', formatNumber(stats.clicks), `${formatNumber(stats.orders)} 单`],
        ['归因销售', formatMoney(stats.gmv), '现有 GMV'],
        ['ROI', formatRatio(stats.roi), `成本 ${formatMoney(stats.cost)}`],
      ].map(([label, value, hint]) => (
        <div className="min-h-[68px] rounded-xl border border-white/[0.06] bg-white/[0.015] px-3 py-2.5" key={label}>
          <span className="block text-slate-500 text-[9.5px] font-medium leading-none">{label}</span>
          <strong className={`block mt-1.5 text-[21px] font-bold leading-none tabular-nums ${label === 'ROI' ? 'text-emerald-400' : 'text-white'}`}>{value}</strong>
          <em className="block mt-1 text-[9.5px] text-slate-500 not-italic leading-tight">{hint}</em>
        </div>
      ))}
    </div>
  );
}

// C. KOL 进度漏斗 + 流失列 + 瓶颈条
export function ProjectFunnel({
  rows,
  counts,
  tableStage,
  bottleneck,
  onSelectStage,
}: {
  rows: VkpiProjectRow[];
  counts: Map<number, number>;
  tableStage: string;
  bottleneck: BottleneckState;
  onSelectStage: (stage: VkpiProjectStage) => void;
}) {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-[14px] font-semibold text-white">KOL 进度漏斗</h3>
          <p className="text-[10.5px] text-slate-500 mt-0.5">项目内所有 KOL 在各阶段的实时分布</p>
        </div>
        <span
          className="text-[10px] px-2 py-0.5 rounded-full border border-emerald-500/20 bg-emerald-500/[0.08] text-emerald-300 inline-flex items-center gap-1 pointer-events-none select-none"
          title="真刷新功能将在视频 URL 每日刷新 job 接入后启用"
        >
          状态 · 每日刷新待接入
        </span>
      </div>
      <div className="grid grid-cols-10 gap-2">
        {PROJECT_STAGE_FLOW.map((stage, index) => {
          const stageNumber = index + 1;
          const stageKey = stage.key as VkpiProjectStage;
          // 末列"已关闭"剔除流失/取消(独立列展示,口径不再失真;扫描 #11)。
          const lostInStage = stageNumber === 9 ? rows.filter((row) => stageIndex(row.stage) === index && isLostStage(row.stage)).length : 0;
          const count = (counts.get(stageNumber) || 0) - lostInStage;
          const nextCount = counts.get(stageNumber + 1) || 0;
          const rate = count && stageNumber < 9 ? `${Math.round(Math.min(nextCount / count, 1) * 100)}%` : '—';
          const average = rows
            .filter((row) => stageIndex(row.stage) === index && !(stageNumber === 9 && isLostStage(row.stage)))
            .map((row) => parseDays(row.stageDurationLabel))
            .filter(Boolean);
          const avgDays = average.length ? `${Math.round(average.reduce((sum, day) => sum + day, 0) / average.length)} 天` : '-';
          const color = PROJECT_STAGE_COLOR[stage.key];
          const isActive = count > 0;
          const isSelected = tableStage === stage.key;
          return (
            <button
              aria-pressed={isSelected}
              className="rounded-lg p-3 text-center transition-all"
              key={stage.key}
              onClick={() => onSelectStage(stageKey)}
              style={{
                background: isSelected ? `${color}1f` : isActive ? `${color}12` : 'rgba(255,255,255,0.015)',
                border: isSelected ? `1px solid ${color}` : isActive ? `1px solid ${color}45` : '1px solid rgba(255,255,255,0.04)',
                boxShadow: isSelected ? `0 0 0 1px ${color}22, 0 12px 32px ${color}14` : undefined,
              }}
              title={`筛选 ${stageNumber}. ${stage.label}`}
              type="button"
            >
              <div className="text-[10px] text-slate-400 mb-1">{stageNumber}. {stage.label}</div>
              <div className="text-[28px] font-bold tabular-nums leading-none" style={{ color: isActive ? color : '#475569' }}>{count}</div>
              <div className="mt-1 text-[10px] text-slate-500">平均 {avgDays}</div>
              <div className="text-[10px] text-slate-500">→ {rate}</div>
            </button>
          );
        })}
        {(() => {
          const lostRows = rows.filter((row) => isLostStage(row.stage));
          const lostAvg = lostRows.map((row) => parseDays(row.stageDurationLabel)).filter(Boolean);
          const lostAvgDays = lostAvg.length ? `${Math.round(lostAvg.reduce((sum, day) => sum + day, 0) / lostAvg.length)} 天` : '-';
          return (
            <div
              className="rounded-lg p-3 text-center"
              style={{
                background: lostRows.length ? 'rgba(244,63,94,0.07)' : 'rgba(255,255,255,0.015)',
                border: lostRows.length ? '1px solid rgba(244,63,94,0.28)' : '1px solid rgba(255,255,255,0.04)',
              }}
              title="流失(churned)/取消(cancelled/lost/stalled)聚合,不计入主流程与『已关闭』"
            >
              <div className="text-[10px] text-slate-400 mb-1">✕ 流失/取消</div>
              <div className="text-[28px] font-bold tabular-nums leading-none" style={{ color: lostRows.length ? '#fb7185' : '#475569' }}>{lostRows.length}</div>
              <div className="mt-1 text-[10px] text-slate-500">平均 {lostAvgDays}</div>
              <div className="text-[10px] text-slate-500">独立口径</div>
            </div>
          );
        })()}
      </div>
      <div className="mt-3 rounded-xl border border-amber-400/25 bg-amber-400/10 text-amber-300 px-3 py-2 text-[11px] font-medium">当前瓶颈：<b>{bottleneck.from}→{bottleneck.to}</b> {bottleneck.text}</div>
    </div>
  );
}

// D. tabs 按钮条
export function ProjectTabsBar({
  activeTab,
  onSelectTab,
}: {
  activeTab: DetailTab;
  onSelectTab: (tab: DetailTab) => void;
}) {
  return (
    <div className="vkpi-campaign-tabs" aria-label="项目详情 tabs">
      {detailTabs.map((tab) => (
        <button key={tab} className={activeTab === tab ? 'is-active' : ''} type="button" onClick={() => onSelectTab(tab)}>
          {tab}
        </button>
      ))}
    </div>
  );
}

// F. 今日提醒 dock
export function ProjectTaskDock({
  taskReminderOpen,
  taskItems,
  reminderTasks,
  onJumpToTask,
  onMarkTaskFocus,
  onOpen,
  onDismiss,
}: {
  taskReminderOpen: boolean;
  taskItems: TaskItem[];
  reminderTasks: TaskItem[];
  onJumpToTask: (rowId?: string, tab?: DetailTab) => void;
  onMarkTaskFocus: (item: TaskItem) => void;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className={`vkpi-campaign-task-dock ${taskReminderOpen ? 'is-open' : 'is-collapsed'}`}>
      {taskReminderOpen ? (
        <div className="vkpi-campaign-task-panel">
          <div className="vkpi-campaign-task-panel-header">
            <h3>今日该做什么</h3>
            <button className="vkpi-campaign-task-close" type="button" onClick={onDismiss}>
              收起
            </button>
          </div>
          {taskItems.map((item) => (
            <button
              key={`${item.title}-${item.subtitle}`}
              type="button"
              onClick={() => onJumpToTask(item.rowId, item.tab)}
            >
              <span className={item.className}>{item.level}</span>
              <b>{item.title}</b>
              <small>{item.subtitle}</small>
            </button>
          ))}
          {taskItems[0] ? (
            <button
              className="vkpi-campaign-task-send"
              type="button"
              onClick={() => onMarkTaskFocus(taskItems[0])}
            >
              暂存提醒
            </button>
          ) : null}
        </div>
      ) : (
        <button className="vkpi-campaign-task-trigger" type="button" onClick={onOpen}>
          今日提醒
          {reminderTasks.length ? <span>{reminderTasks.length}</span> : null}
        </button>
      )}
    </div>
  );
}
