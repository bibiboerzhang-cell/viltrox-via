import {
  AlertCircle,
  BadgeCheck,
  Clock,
  FileText,
  ImageIcon,
  Loader2,
  Package,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
  Upload,
  Video,
} from 'lucide-react';
import type { VkpiProjectRow, VkpiProjectStage } from '../../vkpiTypes';
import { Avatar } from '../../shared/Avatar';
import { nextProjectStage, shortDateTime } from '../../shared/vkpiDataUtils';
import { formatLargeNum, formatMoneyShort, PROJECT_STAGE_COLOR, PROJECT_STAGE_FLOW } from './projectDeliverableStyle';
import { stageIndex, type ScreenshotTarget, type TrackingState } from '../../../../domains/projects';

interface KolStageTimelineProps {
  evidenceCount: number;
  movingRowId: string;
  onMoveRowStage: (row: VkpiProjectRow) => void | Promise<void>;
  onOpenScreenshotModal: (target: ScreenshotTarget) => void;
  onOpenStageActionModal: (row: VkpiProjectRow, action: 'stalled' | 'lost' | 'released' | 'cancelled') => void;
  row: VkpiProjectRow;
  tracking: TrackingState;
}

function stageKeyAt(index: number): VkpiProjectStage {
  return PROJECT_STAGE_FLOW[index]?.key as VkpiProjectStage;
}

function cleanHandle(value: string) {
  return String(value || '')
    .trim()
    .replace(/^@+/, '')
    .replace(/^https?:\/\/[^/]+\//i, '')
    .replace(/^@+/, '')
    .replace(/\/(videos|reels|posts)?\/?$/i, '')
    .replace(/[?#].*$/, '')
    .replace(/\/+$/, '');
}

function kolProfileUrl(row: VkpiProjectRow) {
  const explicit = String(row.kolProfileUrl || '').trim();
  if (/^https?:\/\//i.test(explicit)) return explicit;
  const handle = cleanHandle(row.kolHandle || row.kolName);
  if (!handle || handle === '-') return '';
  if (row.platform === 'YouTube') return `https://www.youtube.com/@${handle}`;
  if (row.platform === 'TikTok') return `https://www.tiktok.com/@${handle}`;
  if (row.platform === 'Instagram') return `https://www.instagram.com/${handle}`;
  if (row.platform === 'X') return `https://x.com/${handle}`;
  if (row.platform === 'Facebook') return `https://www.facebook.com/${handle}`;
  return '';
}

function evidenceChip(stage: string, row: VkpiProjectRow, tracking: TrackingState, evidenceCount: number) {
  if (stage === 'agreed') {
    return {
      icon: FileText,
      kind: 'contract',
      label: row.cost ? `条款/费用 ${formatMoneyShort(row.cost)}` : '条款待归档',
      tone: 'bg-purple-500/10 text-purple-300',
    };
  }
  if (stage === 'shipped') {
    return {
      icon: Package,
      kind: 'tracking',
      label: tracking.no ? `${tracking.courier || '快递'} · ${tracking.no}` : '物流待补',
      tone: 'bg-cyan-500/10 text-cyan-300',
    };
  }
  if (stage === 'published') {
    return {
      icon: Video,
      kind: 'video',
      label: evidenceCount ? `${evidenceCount} 条内容 · ${formatLargeNum(row.views)} 播放` : '发布内容待同步',
      tone: 'bg-emerald-500/10 text-emerald-300',
    };
  }
  return {
    icon: ImageIcon,
    kind: 'screenshot',
    label: `${stage === 'contacted' ? '联系' : stage === 'replied' ? '回复' : '阶段'}证据`,
    tone: 'bg-cyan-500/10 text-cyan-300',
  };
}

export function KolStageTimeline({
  evidenceCount,
  movingRowId,
  onMoveRowStage,
  onOpenScreenshotModal,
  onOpenStageActionModal,
  row,
  tracking,
}: KolStageTimelineProps) {
  const currentIdx = Math.max(0, Math.min(stageIndex(row.stage), PROJECT_STAGE_FLOW.length - 1));
  const currentStage = PROJECT_STAGE_FLOW[currentIdx];
  const headerColor = PROJECT_STAGE_COLOR[currentStage.key] || '#94a3b8';
  const canAdvance = Boolean(nextProjectStage(row.stage));
  const profileUrl = kolProfileUrl(row);

  return (
    <div className="rounded-xl border border-white/[0.06] bg-black/40 p-4 mt-2">
      <div className="flex items-center gap-3 mb-4 pb-3 border-b border-white/[0.05]">
        {profileUrl ? (
          <a className="vkpi-timeline-avatar-link" href={profileUrl} target="_blank" rel="noreferrer" title="打开平台主页">
            <Avatar name={row.kolName || row.kolHandle || 'KOL'} src={row.kolAvatar} size="sm" />
          </a>
        ) : (
          <div className="w-9 h-9 rounded-full flex items-center justify-center text-[12px] font-bold text-white shrink-0" style={{ background: `linear-gradient(135deg,${headerColor},${headerColor}88)` }}>
            {(row.kolName || row.kolHandle || '?').charAt(0).toUpperCase()}
          </div>
        )}
        <div className="flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            {profileUrl ? (
              <a className="text-[13px] font-semibold text-white hover:text-purple-200 transition-colors" href={profileUrl} target="_blank" rel="noreferrer">
                {row.kolName || row.kolHandle || 'KOL'}
              </a>
            ) : (
              <div className="text-[13px] font-semibold text-white">{row.kolName || row.kolHandle || 'KOL'}</div>
            )}
            <span className="text-[10px] text-slate-500">{row.kolHandle || '-'}</span>
            <span className="text-[10px] text-slate-600">·</span>
            <span className="text-[10px] text-slate-400">{row.platform}</span>
            {row.ownerName ? <span className="text-[9.5px] px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-300">负责人 {row.ownerName}</span> : null}
          </div>
          <div className="text-[10.5px] text-slate-400 mt-0.5">
            当前状态: <span className="font-medium" style={{ color: headerColor }}>{currentIdx + 1}. {currentStage.label}</span>
            {' '}· 加入 {shortDateTime(row.startedAt || row.createdAt || row.latestMessageAt)}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-right">
            <div className="text-[10px] text-slate-500 mb-1">{currentIdx + 1} / 9 阶段</div>
            <div className="w-32 h-1.5 rounded-full bg-white/[0.05] overflow-hidden">
              <div className="h-full rounded-full transition-all duration-500" style={{ width: `${((currentIdx + 1) / 9) * 100}%`, background: headerColor }} />
            </div>
          </div>
          <button
            className="text-[10px] text-purple-200 hover:text-white px-2.5 py-1 rounded bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/40 flex items-center gap-1 font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={!canAdvance || movingRowId === row.id}
            onClick={() => void onMoveRowStage(row)}
            type="button"
          >
            <Upload size={10} />
            {movingRowId === row.id ? '推进中' : '推进'}
          </button>
          <button
            className="text-[10px] text-amber-200 hover:text-white px-2.5 py-1 rounded bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/30 flex items-center gap-1 font-medium"
            onClick={() => onOpenStageActionModal(row, 'stalled')}
            type="button"
          >
            <AlertCircle size={10} />停滞
          </button>
        </div>
      </div>

      <div className="space-y-2">
        {PROJECT_STAGE_FLOW.map((stage, idx) => {
          const isDone = idx < currentIdx;
          const isCurrent = idx === currentIdx;
          const isPending = idx > currentIdx;
          const color = PROJECT_STAGE_COLOR[stage.key] || '#64748b';
          const chip = evidenceChip(stage.key, row, tracking, evidenceCount);
          const ChipIcon = chip.icon;
          const stageKey = stageKeyAt(idx);

          return (
            <div
              className="rounded-lg p-3 flex items-start gap-3"
              key={stage.key}
              style={{
                background: isCurrent ? `${color}10` : isDone ? 'rgba(255,255,255,0.012)' : 'rgba(255,255,255,0.005)',
                border: isCurrent ? `1px solid ${color}50` : isDone ? '1px solid rgba(255,255,255,0.04)' : '1px solid rgba(255,255,255,0.02)',
              }}
            >
              <div className="flex flex-col items-center pt-0.5 shrink-0">
                <div
                  className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0"
                  style={{
                    background: isDone ? `${color}20` : isCurrent ? color : 'rgba(255,255,255,0.04)',
                    color: isDone ? color : isCurrent ? '#fff' : '#64748b',
                    border: isDone ? `1px solid ${color}50` : isCurrent ? 'none' : '1px solid rgba(255,255,255,0.06)',
                  }}
                >
                  {isDone ? <BadgeCheck size={14} /> : isCurrent ? <Clock size={12} /> : idx + 1}
                </div>
                {idx < PROJECT_STAGE_FLOW.length - 1 ? <div className="w-px h-3 mt-1" style={{ background: isDone ? `${color}30` : 'rgba(255,255,255,0.05)' }} /> : null}
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2 mb-0.5 flex-wrap">
                  <div className="flex items-center gap-2 flex-wrap">
                    <div className="text-[12px] font-semibold" style={{ color: isPending ? '#64748b' : '#fff' }}>{idx + 1}. {stage.label}</div>
                    {isCurrent ? <span className="text-[9px] px-1.5 py-0.5 rounded font-medium" style={{ background: `${color}20`, color }}>进行中</span> : null}
                    {isDone ? <span className="text-[9px] text-slate-500">{shortDateTime(row.latestMessageAt || row.updatedAt)}</span> : null}
                  </div>
                  {isCurrent && !['discovery', 'measured'].includes(stage.key) ? (
                    <button
                      className="text-[10px] text-purple-200 hover:text-white px-2.5 py-1 rounded bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/40 flex items-center gap-1 font-medium"
                      onClick={() => onOpenScreenshotModal({ row, from: idx, to: idx + 1, stage: stageKey })}
                      type="button"
                    >
                      <Upload size={10} />
                      {stage.key === 'agreed' ? '上传合同' : stage.key === 'shipped' ? '录入快递' : stage.key === 'published' ? '录入视频' : '上传证据'}
                    </button>
                  ) : null}
                  {isCurrent && stage.key === 'discovery' ? (
                    <button className="text-[10px] text-purple-200 px-2.5 py-1 rounded bg-purple-500/20 border border-purple-500/40 flex items-center gap-1 font-medium" type="button">
                      <Send size={10} />联系 KOL
                    </button>
                  ) : null}
                  {isCurrent && stage.key === 'measured' ? (
                    <span className="text-[10px] text-cyan-300 px-2.5 py-1 rounded bg-cyan-500/15 border border-cyan-500/30 flex items-center gap-1.5">
                      <RefreshCw size={10} className="animate-spin" />自动采集中
                    </span>
                  ) : null}
                  {isPending ? (
                    <button className="text-[10px] text-slate-600 px-2 py-0.5 rounded border border-white/[0.05] opacity-50 cursor-not-allowed" type="button">
                      <Plus size={10} className="inline mr-0.5" />等待上一阶段
                    </button>
                  ) : null}
                </div>

                {(isDone || isCurrent) && stage.key !== 'measured' ? (
                  <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                    <span className={`text-[10px] px-2 py-0.5 rounded flex items-center gap-1 ${chip.tone}`}>
                      <ChipIcon size={10} />{chip.label}
                    </span>
                    {tracking.delivered && stage.key === 'shipped' ? <span className="text-[9px] px-1 rounded bg-emerald-500/20 text-emerald-300">已签收</span> : null}
                  </div>
                ) : null}

                {(isDone || isCurrent) ? (
                  <div className="mt-1.5 flex items-start gap-1.5 px-2 py-1 rounded" style={{ background: isCurrent ? 'rgba(168,85,247,0.05)' : 'rgba(255,255,255,0.012)' }}>
                    <Sparkles size={10} className="shrink-0 mt-0.5" style={{ color: isCurrent ? '#a855f7' : '#64748b' }} />
                    <div className="text-[10.5px] leading-relaxed" style={{ color: isCurrent ? '#c4b5fd' : '#94a3b8' }}>
                      {stage.key === 'published'
                        ? `${evidenceCount} 条证据，当前曝光 ${formatLargeNum(row.views)}。`
                        : stage.key === 'shipped'
                          ? tracking.no ? '物流信息已记录，等待或确认到货。' : '已进入发货节点，物流单号待补齐。'
                          : '阶段证据随真实项目行同步。'}
                    </div>
                  </div>
                ) : null}

                {isCurrent && stage.key === 'measured' ? (
                  <div className="mt-2 rounded-lg bg-cyan-500/[0.04] border border-cyan-500/20 p-2.5">
                    <div className="text-[10.5px] font-medium text-cyan-200 mb-2 flex items-center gap-1.5">
                      <RefreshCw size={10} className="animate-spin" />自动数据采集 · 无需手动操作
                    </div>
                    <div className="space-y-1.5">
                      <div className="flex items-center gap-2 text-[10px]">
                        {evidenceCount ? <BadgeCheck size={11} className="text-emerald-400 shrink-0" /> : <Loader2 size={11} className="text-cyan-400 animate-spin shrink-0" />}
                        <div className="flex-1" style={{ color: evidenceCount ? '#86efac' : '#94a3b8' }}>
                          <span className="font-medium">Apify 视频数据</span>
                          {evidenceCount ? ` · ${formatLargeNum(row.views)} 播放 · ${evidenceCount} 条证据` : ' · 等待视频可达'}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 text-[10px]">
                        <AlertCircle size={11} className="text-amber-400 shrink-0" />
                        <div className="flex-1 text-amber-300"><span className="font-medium">Shopify 归因</span> · 缺归因 URL · 跳过</div>
                      </div>
                    </div>
                    <div className="mt-2 pt-2 border-t border-cyan-500/15 flex items-center justify-between">
                      <span className="text-[10px] text-slate-400">{evidenceCount ? '✓ 视频数据已就绪 · 可归档' : 'Apify 平均 5-15 分钟返回'}</span>
                      <span className="text-[10px] text-cyan-300">{evidenceCount ? '1 / 2 完成' : '0 / 2 完成'}</span>
                    </div>
                  </div>
                ) : null}

                {isPending ? <div className="text-[10.5px] text-slate-600 mt-0.5">等待上一阶段完成后自动开放</div> : null}
              </div>
            </div>
          );
        })}
      </div>
      <div className="vkpi-stage-action-row">
        <button type="button" onClick={() => onOpenStageActionModal(row, 'lost')}>标记流失</button>
        <button type="button" onClick={() => onOpenStageActionModal(row, 'released')}>释放回 Pool</button>
        <button type="button" onClick={() => onOpenStageActionModal(row, 'cancelled')}>取消该 KOL</button>
      </div>
    </div>
  );
}
