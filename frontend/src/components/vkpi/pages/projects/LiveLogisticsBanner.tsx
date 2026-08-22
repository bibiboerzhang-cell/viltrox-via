import React from 'react';
import { Package, RefreshCw } from 'lucide-react';
import type { VkpiProjectRow } from '../../vkpiTypes';
import { Avatar } from '../../shared/Avatar';
import { stageIndex, type TrackingState } from '../../../../domains/projects';

interface LiveLogisticsBannerProps {
  rows: VkpiProjectRow[];
  trackingForRow: (row: VkpiProjectRow) => TrackingState;
}

// 免费查单(2026-06-12 裁令:不买 API):承运商官网直达,识别不了落 17track 免费聚合页。
// 程序抓 Google/官网结果页不做(反爬+脆),一键外链即用户"Google 搜单号"的零成本等价物。
function freeTrackingUrl(carrier: string, trackingNumber: string) {
  const normalized = String(carrier || '').toLowerCase();
  const encoded = encodeURIComponent(trackingNumber);
  if (normalized.includes('dhl')) return `https://www.dhl.com/us-en/home/tracking/tracking-express.html?submit=1&tracking-id=${encoded}`;
  if (normalized.includes('fedex')) return `https://www.fedex.com/fedextrack/?trknbr=${encoded}`;
  if (normalized.includes('ups')) return `https://www.ups.com/track?tracknum=${encoded}`;
  if (normalized.includes('sf') || normalized.includes('顺丰')) return `https://www.sf-express.com/chn/sc/waybill/waybill-detail/${encoded}`;
  return `https://t.17track.net/zh-cn#nums=${encoded}`;
}

function trackingStatus(row: VkpiProjectRow, tracking: TrackingState) {
  // 17track 同步过的真实状态优先(metadata.shipping.status);否则按阶段推断
  if (row.trackingStatus && !['shipped'].includes(String(row.trackingStatus).toLowerCase())) return String(row.trackingStatus);
  if (tracking.delivered || stageIndex(row.stage) >= stageIndex('received')) return '已签收';
  if (stageIndex(row.stage) >= stageIndex('shipped')) return '在途';
  return '待追踪';
}

export function LiveLogisticsBanner({ rows, trackingForRow, onSyncTracking }: LiveLogisticsBannerProps & { onSyncTracking?: () => Promise<string> }) {
  const [syncState, setSyncState] = React.useState<'idle' | 'busy' | 'done'>('idle');
  const [syncMsg, setSyncMsg] = React.useState('');
  const syncNow = async () => {
    if (!onSyncTracking || syncState === 'busy') return;
    setSyncState('busy');
    try {
      const message = await onSyncTracking();
      setSyncMsg(message);
      setSyncState('done');
    } catch (error) {
      setSyncMsg(error instanceof Error ? error.message : '同步发起失败');
      setSyncState('idle');
    }
  };
  const cards = rows
    .map((row) => ({ row, tracking: trackingForRow(row) }))
    .filter(({ row, tracking }) => tracking.no && !row.isFakeTracking);

  return (
    <section className="vkpi-live-logistics" aria-label="实时物流">
      <header>
        <div>
          <span><Package size={13} /> 实时物流 · {cards.length} 个快递</span>
          <p>真实 tracking 单号 · 排除占位/假单号 · 已同步的显 17track 轨迹,其余按阶段推断</p>
        </div>
        {onSyncTracking ? (
          <button
            type="button"
            onClick={() => void syncNow()}
            disabled={syncState === 'busy'}
            style={{ fontSize: 'var(--ds-fs-10)', color: syncState === 'done' ? '#86efac' : '#93c5fd', background: 'rgba(59,130,246,0.10)', border: '1px solid rgba(59,130,246,0.3)', borderRadius: 6, padding: '3px 8px', cursor: 'pointer' }}
            title={syncMsg || '经 17track 拉取真实轨迹(队列「物流同步」可见)'}
          >
            <RefreshCw size={10} /> {syncState === 'busy' ? '发起中…' : syncState === 'done' ? '已入队 ✓' : '同步状态(17track)'}
          </button>
        ) : (
          <em title="配置 VKPI_17TRACK_TOKEN 后启用真实轨迹同步">
            <RefreshCw size={10} /> 状态 · 阶段推断
          </em>
        )}
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
                  <small>
                    {tracking.courier || row.trackingCarrier || '待识别快递'} · {tracking.no}
                    {' '}
                    <a href={freeTrackingUrl(tracking.courier || row.trackingCarrier || '', tracking.no)} target="_blank" rel="noreferrer" style={{ color: '#67e8f9' }}>查单 ↗</a>
                  </small>
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
