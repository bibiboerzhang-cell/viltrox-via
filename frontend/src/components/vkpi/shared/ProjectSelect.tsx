import type { VkpiProjectRow } from '../vkpiTypes';

export function ProjectSelect({ projects, value, onChange, allowEmpty }: { projects: VkpiProjectRow[]; value: string; onChange: (value: string) => void; allowEmpty?: boolean }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {allowEmpty ? <option value="">不绑定项目</option> : <option value="">选择项目</option>}
      {projects.map((project) => <option key={project.id} value={project.id}>{project.campaign} · {project.kolName}</option>)}
    </select>
  );
}
