import type { VkpiPlatform } from '../vkpiTypes';
import { platformClass } from './vkpiConstants';

export function PlatformDot({ platform }: { platform: VkpiPlatform }) {
  return <i className={`vkpi-platform-dot ${platformClass[platform] ?? platformClass.Other}`} aria-hidden="true" />;
}
