import { platformDisplay } from '../../shared/vkpiDataUtils';
import { numberFormatter } from '../../shared/vkpiFormatters';
import { PlatformLogo } from './PlatformLogo';
import { displayCount } from './myKolMatrixData';
import type { PlatformEntryMetric, PlatformFilter } from './myKolMatrixTypes';

interface MyKolPlatformCardsProps {
  activePlatform: PlatformFilter;
  metrics: PlatformEntryMetric[];
  onSelect: (platform: PlatformFilter) => void;
  viewLabel: string;
}

export function MyKolPlatformCards({ activePlatform, metrics, onSelect, viewLabel }: MyKolPlatformCardsProps) {
  return (
    <div className="vkpi-my-kol-platforms" aria-label="平台入口">
      {metrics.map((entry) => (
        <button
          className={`vkpi-my-kol-platform-card${activePlatform === entry.platform ? ' is-active' : ''}`}
          key={entry.platform}
          onClick={() => onSelect(entry.platform)}
          type="button"
        >
          <PlatformLogo platform={entry.platform} label={platformDisplay(entry.platform)} size="small" />
          <strong>{platformDisplay(entry.platform)}</strong>
          <small>{numberFormatter.format(entry.kolCount)} KOL</small>
          <div>
            <span><b>{numberFormatter.format(entry.projectCount)}</b>项目</span>
            <span><b>{displayCount(entry.views)}</b>{viewLabel}</span>
          </div>
        </button>
      ))}
    </div>
  );
}
