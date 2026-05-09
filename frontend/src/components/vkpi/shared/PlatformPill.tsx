import type { VkpiPlatform } from '../vkpiTypes';
import { platformClass, platformLabels } from './vkpiConstants';
import { PlatformDot } from './PlatformDot';

export function PlatformPill({ platform }: { platform: VkpiPlatform }) {
  return <span className={`vkpi-platform-pill ${platformClass[platform] ?? platformClass.Other}`}><PlatformDot platform={platform} />{platformLabels[platform] || platform}</span>;
}
