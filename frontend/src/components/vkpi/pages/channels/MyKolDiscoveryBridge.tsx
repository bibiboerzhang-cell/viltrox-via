import { platformDisplay } from '../../shared/vkpiDataUtils';
import { numberFormatter } from '../../shared/vkpiFormatters';
import type { PlatformEntryMetric, PlatformFilter } from './myKolMatrixTypes';

interface MyKolDiscoveryBridgeProps {
  filteredCount: number;
  metric?: PlatformEntryMetric;
  onDiscoverPlatform?: (platform: PlatformFilter) => void;
  platform: PlatformFilter;
}

export function MyKolDiscoveryBridge({ filteredCount, metric, onDiscoverPlatform, platform }: MyKolDiscoveryBridgeProps) {
  if (!onDiscoverPlatform) return null;
  return (
    <section className="vkpi-my-kol-discovery-bridge" aria-label="从我的 KOL 发现新候选">
      <div>
        <span>发现补位</span>
        <h3>{platform === 'all' ? '从全部平台找新 KOL' : `从 ${platformDisplay(platform)} 找相似 KOL`}</h3>
        <p>
          当前筛选内有 {numberFormatter.format(filteredCount)} 个账号；
          {metric ? `该平台已有 ${numberFormatter.format(metric.kolCount)} 个 KOL、${numberFormatter.format(metric.projectCount)} 个项目。` : '先选择平台，再按同平台内容方向补候选。'}
        </p>
      </div>
      <div className="vkpi-my-kol-discovery-bridge__flow">
        <span className="is-active">平台池</span>
        <span>KOL账号</span>
        <span>候选发现</span>
        <span>项目判断</span>
      </div>
      <button className="vkpi-button vkpi-button--primary" type="button" onClick={() => onDiscoverPlatform(platform)}>
        带入红人发现
      </button>
    </section>
  );
}
