import React from "react";
import type { DashboardModuleDefinition } from "./EditableDashboardBoard";

export function EmbeddedDashboardModule({
  modules,
  moduleKey,
  boardLabel,
}: {
  modules: DashboardModuleDefinition[];
  moduleKey: string;
  boardLabel: string;
}) {
  const definition = modules.find((module) => module.key === moduleKey);
  if (!definition) {
    return (
      <section className="ds-mod flex h-full min-h-[120px] items-center justify-center p-4 text-center text-[11px] text-muted">
        该模块当前对本账号不可见，或已从 {boardLabel} 注册表移除。
      </section>
    );
  }
  return <>{definition.render()}</>;
}
