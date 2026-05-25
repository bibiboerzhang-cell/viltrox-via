import type { WorkspacePageProps } from './WorkspacePage';

export function RepairCenterPage({ viewMode }: WorkspacePageProps) {
  return (
    <section className="vkpi-page vkpi-repair-freeze">
      <div className="vkpi-section-card">
        <p className="vkpi-eyebrow">REPAIR CENTER V0 / FROZEN</p>
        <h1>修复中心已冻结</h1>
        <p>
          Repair Center v0 只保留为 dry-run / preview 原型，不再作为运营主线继续扩展。
          后续修复需求先走架构迁移、真实业务闭环和 800 行守卫。
        </p>
        <div className="vkpi-empty-state">
          <strong>当前模式：只读冻结</strong>
          <span>视图：{viewMode === 'manager' ? '管理' : '员工'}；不会写库、不会触发任务、不会调用外部 provider。</span>
        </div>
      </div>
    </section>
  );
}

export default RepairCenterPage;
