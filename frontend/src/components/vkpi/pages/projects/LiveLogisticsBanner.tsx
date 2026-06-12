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
