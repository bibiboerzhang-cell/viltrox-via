import { Package, RefreshCw } from 'lucide-react';
import type { VkpiProjectRow } from '../../vkpiTypes';
import { Avatar } from '../../shared/Avatar';
import { stageIndex, type TrackingState } from '../../../../domains/projects';

interface LiveLogisticsBannerProps {
  rows: VkpiProjectRow[];
  trackingForRow: (row: VkpiProjectRow) => TrackingState;
}

function trackingStatus(row: VkpiProjectRow, tracking: TrackingState) {
  if (tracking.delivered || stageIndex(row.stage) >= stageIndex('received')) return '已签收';
  if (stageIndex(row.stage) >= stageIndex('shipped')) return '在途';
  return '待追踪';
}

export function LiveLogisticsBanner({ rows, trackingForRow }: LiveLogisticsBannerProps) {
  const cards = rows
    .map((row) => ({ row, tracking: trackingForRow(row) }))
    .filter(({ row, tracking }) => tracking.no && !row.isFakeTracking);

  return (
    <section className="vkpi-live-logistics" aria-label="实时物流">
      <header>
        <div>
          <span><Package size={13} /> 实时物流 · {cards.length} 个快递</span>
          <p>真实 tracking 单号 · 排除占位/假单号 · 状态按当前阶段推断</p>
        </div>
        <em title="真刷新功能将在视频 URL 每日刷新 job 接入后启用">
          <RefreshCw size={10} /> 状态 · 每日刷新待接入
        </em>
      </header>

      {cards.length ? (
        <div className="vkpi-live-logistics-grid">
          {cards.map(({ row, tracking }) => {
            const status = trackingStatus(row, tracking);
            return (
              <article className="vkpi-live-logistics-card" key={row.id}>
                <Avatar name={row.kolName || row.kolHandle} src={row.kolAvatar} size="sm" />
                <div>
                  <strong>{row.kolHandle || row.kolName}</strong>
                  <small>{tracking.courier || row.trackingCarrier || '待识别快递'} · {tracking.no}</small>
                </div>
                <span className={status === '已签收' ? 'is-delivered' : status === '在途' ? 'is-moving' : 'is-pending'}>
                  {status}
                </span>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="vkpi-live-logistics-empty">暂无物流单号</div>
      )}
    </section>
  );
}
