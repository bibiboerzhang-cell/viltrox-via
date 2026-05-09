import type { VkpiProjectRow } from '../vkpiTypes';
import { primaryStageFlow, stageLabels } from './vkpiConstants';

export function ProjectFlowStepper({ project }: { project: VkpiProjectRow }) {
  const currentIndex = Math.max(0, primaryStageFlow.indexOf(project.stage));
  return (
    <div className="vkpi-flow-stepper" aria-label="项目流程">
      {primaryStageFlow.map((stage, index) => {
        const state = index < currentIndex ? 'done' : index === currentIndex ? 'current' : 'todo';
        return (
          <div className={`vkpi-flow-step is-${state}`} key={stage}>
            <i>{index + 1}</i>
            <span>{stageLabels[stage]}</span>
          </div>
        );
      })}
    </div>
  );
}
