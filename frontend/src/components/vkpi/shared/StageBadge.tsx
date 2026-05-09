import type { VkpiProjectStage } from '../vkpiTypes';
import { stageLabels } from './vkpiConstants';

export function StageBadge({ stage }: { stage: VkpiProjectStage }) {
  return <span className={`vkpi-stage-badge is-${stage}`}>{stageLabels[stage] ?? stage}</span>;
}
