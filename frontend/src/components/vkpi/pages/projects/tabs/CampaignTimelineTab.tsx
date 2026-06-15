import { useMemo } from 'react';
import { Activity, AlertCircle, Check } from 'lucide-react';
import { stageLabels } from '../../../shared/vkpiConstants';
import { ProjectTimeline } from '../../../v615-replica/components/ProjectTimeline';
import type { VkpiProjectDetail, VkpiProjectRow } from '../../../vkpiTypes';
import { PROJECT_STAGE_COLOR, PROJECT_STAGE_FLOW } from '../projectDeliverableStyle';
import { stageIndex } from '../../../../../domains/projects';

function timelineTimestamp(value: string) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function formatTimelineDate(value: string) {
  if (!value) return '—';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const hour = String(date.getHours()).padStart(2, '0');
  const minute = String(date.getMinutes()).padStart(2, '0');
  return `${month}/${day} ${hour}:${minute}`;
}

function timelineStage(row: VkpiProjectRow) {
  const index = Math.max(0, Math.min(PROJECT_STAGE_FLOW.length - 1, stageIndex(row.stage)));
  return PROJECT_STAGE_FLOW[index];
}

function timelineSpecial(row: VkpiProjectRow) {
  if (row.stage === 'lost' || row.stage === 'cancelled') return 'lost';
  if (row.stage === 'stalled') return 'stalled';
  return '';
}

function timelineField(event: Record<string, unknown>, key: string) {
  return String(event[key] ?? '').trim();
}

function timelineStageLabel(stage: string) {
  return (stageLabels as Record<string, string>)[stage] || stage || '—';
}

function timelineEventTypeLabel(type: string) {
  const labels: Record<string, string> = {
    created: '创建项目',
    deleted: '删除/取消',
    stage_change: '阶段变更',
    suggestion_project_created: '建议转项目',
  };
  return labels[type] || type || '事件';
}

function timelineEventColor(toStage: string, eventType: string) {
  if (eventType === 'deleted' || toStage === 'cancelled') return '#ef4444';
  if (eventType === 'created') return '#22c55e';
  const index = Math.max(0, Math.min(PROJECT_STAGE_FLOW.length - 1, stageIndex(toStage)));
  return PROJECT_STAGE_COLOR[PROJECT_STAGE_FLOW[index].key] || '#94a3b8';
}

export function CampaignTimelineTab({
  rows,
  events = [],
  apiToken,
  projectId,
}: {
  rows: VkpiProjectRow[];
  events?: VkpiProjectDetail['events'];
  apiToken?: string;
  projectId?: string;
}) {
  // W2 履约时间线:projectId 优先取传入,否则从 rows 派生(VkpiProjectRow.projectId)。
  // apiToken 若未透传则 ProjectTimeline 自动降级为只读阶段骨架(不报错)。
  const timelineProjectId = projectId || rows[0]?.projectId || '';
  const realEvents = useMemo(() => events
    .map((event, index) => {
      const fromStage = timelineField(event, 'from_stage');
      const toStage = timelineField(event, 'to_stage');
      const eventType = timelineField(event, 'event_type');
      const effectiveAt = timelineField(event, 'effective_at') || timelineField(event, 'created_at');
      return {
        id: timelineField(event, 'id') || `event-${index}`,
        date: formatTimelineDate(effectiveAt),
        timestamp: timelineTimestamp(effectiveAt),
        actor: timelineField(event, 'actor_staff_id') ? `Staff #${timelineField(event, 'actor_staff_id')}` : '系统/未知',
        fromStage,
        toStage,
        transition: `${timelineStageLabel(fromStage)} → ${timelineStageLabel(toStage)}`,
        eventType: timelineEventTypeLabel(eventType),
        note: timelineField(event, 'note'),
        source: [timelineField(event, 'source_ref_type'), timelineField(event, 'source_ref_id')].filter(Boolean).join(' #'),
        stageColor: timelineEventColor(toStage, eventType),
      };
    })
    .sort((a, b) => b.timestamp - a.timestamp || String(b.id).localeCompare(String(a.id))), [events]);

  const snapshot = useMemo(() => {
    const grouped = new Map<string, { label: string; color: string; count: number }>();
    rows.forEach((row) => {
      const stage = timelineStage(row);
      const special = timelineSpecial(row);
      const key = row.stage;
      const current = grouped.get(key) || {
        label: special === 'lost' ? '标记流失' : special === 'stalled' ? '标记停滞' : (stageLabels[row.stage] || stage.label),
        color: special === 'lost' ? '#ef4444' : special === 'stalled' ? '#fb923c' : PROJECT_STAGE_COLOR[stage.key],
        count: 0,
      };
      current.count += 1;
      grouped.set(key, current);
    });
    return Array.from(grouped.entries())
      .sort(([a], [b]) => stageIndex(a) - stageIndex(b))
      .map(([key, item]) => ({ key, ...item }));
  }, [rows]);

  return (
    <div className="p-4 space-y-3" aria-label="项目时间轴">
      {/* W2 履约时间线(只读):建→选→寄→签→观察→发布→复盘。渲染在真实事件流之上。 */}
      <ProjectTimeline apiToken={apiToken} projectId={timelineProjectId} />

      <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
        <Activity size={13} className="text-purple-300 mt-0.5 shrink-0" />
        <div className="text-[10.5px] text-slate-300">
          {realEvents.length} 条真实历史事件 · 来源 vkpi_project_stage_events · 按时间倒序
        </div>
      </div>

      {realEvents.length === 0 ? (
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
          <Activity size={24} className="text-slate-600 mx-auto mb-2" />
          <div className="text-[11.5px] text-slate-300">暂无真实历史事件</div>
          <div className="text-[10px] text-slate-500 mt-1">该项目尚未写入 stage_events；下方仅显示当前状态快照。</div>
        </div>
      ) : (
        <div className="space-y-2 relative">
          <div className="absolute left-3 top-2 bottom-2 w-px bg-white/[0.06]" />
          {realEvents.map((event) => (
            <div key={event.id} className="flex items-start gap-3 relative pl-1">
              <div
                className="w-5 h-5 rounded-full flex items-center justify-center shrink-0 z-10"
                style={{ background: event.stageColor || '#94a3b8', boxShadow: '0 0 0 3px #0a0a0d' }}
              >
                <Check size={10} className="text-white" />
              </div>
              <div className="flex-1 rounded-lg border border-white/[0.05] bg-white/[0.012] p-2.5">
                <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                  <span className="text-[10.5px] text-slate-400 tabular-nums font-mono">{event.date}</span>
                  <span className="text-[11px] text-white font-medium">{event.transition}</span>
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                    style={{ background: `${event.stageColor}20`, color: event.stageColor }}
                  >
                    {event.eventType}
                  </span>
                </div>
                <div className="text-[10px] text-slate-500 mt-1">{event.actor}{event.source ? ` · ${event.source}` : ''}</div>
                {event.note ? <div className="text-[10px] text-slate-400 mt-1 whitespace-pre-wrap">{event.note}</div> : null}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="rounded-lg border border-white/[0.06] bg-white/[0.012] p-3">
        <div className="flex items-center gap-2 mb-2">
          <AlertCircle size={12} className="text-slate-400" />
          <div className="text-[10.5px] text-slate-300">当前状态快照 · 非历史事件</div>
        </div>
        {snapshot.length === 0 ? (
          <div className="text-[10px] text-slate-500">暂无 KOL 当前状态。</div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {snapshot.map((item) => (
              <span
                key={item.key}
                className="text-[10px] px-2 py-1 rounded border"
                style={{ color: item.color, borderColor: `${item.color}40`, background: `${item.color}12` }}
              >
                {item.label} {item.count}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
