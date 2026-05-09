import React from "react";
import { CardHeader } from "../../shared/CardHeader";
import { InfoBlock } from "../../shared/InfoBlock";
import { ProviderStatusTable } from "../../tables/ProviderStatusTable";

type Row = Record<string, unknown>;

export function ProviderHealthCard({
  providers,
  providerBusy,
  providerError,
  onReload,
  onProbe,
}: {
  providers: Row[];
  providerBusy: string;
  providerError: string;
  onReload: () => void;
  onProbe: (provider: string) => void;
}) {
  return (
    <section className="vkpi-card vkpi-table-card vkpi-action-card--wide vkpi-settings-switch-panel">
      <div className="vkpi-table-card__header">
        <div><h2>API 是否工作</h2><span>{providers.length} 个服务</span></div>
        <button className="vkpi-button" type="button" disabled={providerBusy === "all"} onClick={onReload}>{providerBusy === "all" ? "刷新中" : "刷新"}</button>
      </div>
      <ProviderStatusTable rows={providers} busyProvider={providerBusy} onProbe={onProbe} />
      {providerError ? <div className="vkpi-inline-message">{providerError}</div> : null}
    </section>
  );
}

export function StaffInviteCard({
  email,
  name,
  role,
  permission,
  busy,
  canInvite,
  onEmailChange,
  onNameChange,
  onRoleChange,
  onPermissionChange,
  onSubmit,
}: {
  email: string;
  name: string;
  role: string;
  permission: "none" | "read" | "write";
  busy: boolean;
  canInvite: boolean;
  onEmailChange: (value: string) => void;
  onNameChange: (value: string) => void;
  onRoleChange: (value: string) => void;
  onPermissionChange: (value: "none" | "read" | "write") => void;
  onSubmit: React.FormEventHandler;
}) {
  return (
    <section className="vkpi-card vkpi-action-card">
      <CardHeader title="授权账户" />
      <form className="vkpi-form-stack" onSubmit={onSubmit}>
        <input value={email} onChange={(event) => onEmailChange(event.target.value)} placeholder="员工邮箱，建议使用 @viltrox.com" />
        <input value={name} onChange={(event) => onNameChange(event.target.value)} placeholder="员工姓名 / 拼音 ID" />
        <select value={role} onChange={(event) => onRoleChange(event.target.value)}><option value="employee">员工 / 运营</option><option value="manager">管理层</option><option value="analyst">数据分析</option><option value="readonly">只读</option><option value="admin">管理员</option></select>
        <select value={permission} onChange={(event) => onPermissionChange(event.target.value as "none" | "read" | "write")}><option value="write">可操作 Viltrox Marketing</option><option value="read">只读 Viltrox Marketing</option><option value="none">无 Viltrox Marketing 权限</option></select>
        <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !canInvite}>发送邀请</button>
      </form>
    </section>
  );
}

export function ProductCostFormCard({
  costSku,
  costProductName,
  unitCostUsd,
  costNote,
  busy,
  canUpsert,
  onCostSkuChange,
  onCostProductNameChange,
  onUnitCostUsdChange,
  onCostNoteChange,
  onSubmit,
}: {
  costSku: string;
  costProductName: string;
  unitCostUsd: string;
  costNote: string;
  busy: boolean;
  canUpsert: boolean;
  onCostSkuChange: (value: string) => void;
  onCostProductNameChange: (value: string) => void;
  onUnitCostUsdChange: (value: string) => void;
  onCostNoteChange: (value: string) => void;
  onSubmit: React.FormEventHandler;
}) {
  return (
    <section className="vkpi-card vkpi-action-card">
      <CardHeader title="SKU 录入" />
      <form className="vkpi-form-stack" onSubmit={onSubmit}>
        <input value={costSku} onChange={(event) => onCostSkuChange(event.target.value)} placeholder="产品 SKU，例如 AF35-F1.2" />
        <input value={costProductName} onChange={(event) => onCostProductNameChange(event.target.value)} placeholder="产品名称，例如 AF 35mm F1.2" />
        <input value={unitCostUsd} onChange={(event) => onUnitCostUsdChange(event.target.value)} placeholder="镜头内部成本 USD" inputMode="decimal" />
        <input value={costNote} onChange={(event) => onCostNoteChange(event.target.value)} placeholder="备注（可选）" />
        <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !canUpsert}>保存 SKU</button>
      </form>
    </section>
  );
}

export function SystemSummaryCards({
  controlSummary,
  syncPolicy,
  youtubeKpi,
  claudeConfigured,
  claudeStatus,
}: {
  controlSummary: Row;
  syncPolicy: Row;
  youtubeKpi: Row;
  claudeConfigured: boolean;
  claudeStatus: string;
}) {
  return (
    <>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="总控状态" />
        <InfoBlock label="高成本开关" value={`${String(controlSummary.enabled_high_cost_controls ?? 0)} 个开启`} />
        <InfoBlock label="风险状态" value={String(controlSummary.risk_level || "controlled")} />
        <InfoBlock label="本月预算剩余" value={`$${String(controlSummary.budget_remaining_usd ?? 0)}`} />
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="08:00 同步策略" />
        <InfoBlock label="时间" value={`${String(syncPolicy.daily_sync_time || "08:00")} ${String(syncPolicy.timezone || "Asia/Shanghai")}`} />
        <InfoBlock label="每人候选" value={`${String(syncPolicy.candidate_limit_per_staff || 100)} 条`} />
        <InfoBlock label="候选限制" value={syncPolicy.only_uncontacted_kols ? "只推未联系 KOL" : "未限制"} />
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="YouTube KPI 预留" />
        <InfoBlock label="预留槽" value={youtubeKpi.reserved ? "已预留" : "未预留"} />
        <InfoBlock label="平台抓取" value={youtubeKpi.platform_enabled ? "开启" : "关闭"} />
        <InfoBlock label="API 状态" value={String(youtubeKpi.last_test_status || "not_configured")} />
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="AI 周报" />
        <InfoBlock label="Claude API" value={claudeConfigured ? claudeStatus : "未配置"} />
        <InfoBlock label="失败处理" value="自动 fallback 模板" />
        <InfoBlock label="记录方式" value="写入 LLM 调用审计" />
      </section>
    </>
  );
}
