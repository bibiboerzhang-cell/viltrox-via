import type React from 'react';
import { useAuth } from '../../../../hooks/useAuth';
import { CardHeader } from '../../shared/CardHeader';
import { InfoBlock } from '../../shared/InfoBlock';
import { PageShell } from '../PageShell';
import { boolValue } from '../../../../domains/settings';

export const SETTINGS_MODULE_TITLES = {
  status: '当前状态',
  sku: 'SKU 录入',
  staff: '账号授权',
  funds: '资金管理',
  rules: '规则安排',
  scheduler: '定时任务 / Scheduler',
  preference: '个人偏好',
  notification: '通知配置',
} as const;

export type SettingsModuleKey = keyof typeof SETTINGS_MODULE_TITLES;

export function SettingsApiSkeletonGrid() {
  return (
    <div className="vkpi-settings-api-grid" aria-hidden="true">
      {['apify', 'openai', 'anthropic', 'google', 'resend', 'storage'].map((item) => (
        <article className="vkpi-settings-api-card vkpi-settings-api-card--skeleton" key={item}>
          <header>
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-pill" />
          </header>
          <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
          <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
        </article>
      ))}
    </div>
  );
}

export function SettingsProviderGrid({ providers }: { providers: Array<Record<string, unknown>> }) {
  return (
    <div className="vkpi-settings-api-grid">
      {providers.map((row) => {
        const configured = boolValue(row.configured, false);
        const ok = boolValue(row.ok, false);
        const keyMask = String(row.key_mask || '').trim();
        const status = String(row.latest_status || row.status || (ok ? 'healthy' : 'not_configured'));
        return (
          <article className={`vkpi-settings-api-card ${configured ? 'is-configured' : 'is-empty'}`} key={String(row.provider || row.label)}>
            <header>
              <strong>{String(row.label || row.provider || '-')}</strong>
              <span>{configured ? '已配置' : '未配置'}</span>
            </header>
            <p>{keyMask || '未读取到 key'}</p>
            <em>{status}</em>
          </article>
        );
      })}
    </div>
  );
}

export function SettingsLoadingStrip({ settingsLoading, catalogLoading }: { settingsLoading: boolean; catalogLoading: boolean }) {
  if (!settingsLoading && !catalogLoading) return null;
  const label = settingsLoading && catalogLoading
    ? '正在读取系统状态和 SKU 目录'
    : settingsLoading
      ? '正在读取 API / 权限 / 规则状态'
      : '正在读取 SKU 目录';
  return (
    <div className="vkpi-settings-loading-strip" aria-live="polite">
      <div>
        <strong>{label}</strong>
        <span>{settingsLoading ? '系统配置' : '系统配置已就绪'} · {catalogLoading ? '产品目录' : '产品目录已就绪'}</span>
      </div>
      <div className="vkpi-settings-loading-strip__bar" aria-hidden="true"><span /></div>
    </div>
  );
}

export function SettingsModule({
  children,
  moduleKey,
  open,
  subtitle,
  onToggle,
}: {
  children: React.ReactNode;
  moduleKey: SettingsModuleKey;
  open: boolean;
  subtitle: string;
  onToggle: () => void;
}) {
  return (
    <section className={`vkpi-settings-module ${open ? 'is-open' : 'is-collapsed'}`} key={moduleKey}>
      <button className="vkpi-settings-module__head" type="button" onClick={onToggle}>
        <span>{SETTINGS_MODULE_TITLES[moduleKey]}</span>
        <em>{subtitle}</em>
        <strong>{open ? '收起' : '展开'}</strong>
      </button>
      {open ? <div className="vkpi-settings-module__body">{children}</div> : null}
    </section>
  );
}

// 员工自助「我的权限」只读卡:展示自己的 {tab:level} 矩阵,改权限请联系管理层。
export function EmployeePermissionCard({ permissions }: { permissions?: Record<string, string> }) {
  const levelLabel = (level: string): string =>
    ({ none: "无权限", read: "只读", write: "可写", admin: "管理" }[String(level || "none").toLowerCase()] || "无权限");
  const levelColor = (level: string): string =>
    ({ none: "#64748b", read: "#3b82f6", write: "#10b981", admin: "#f59e0b" }[String(level || "none").toLowerCase()] || "#64748b");
  const keys = permissions ? Object.keys(permissions).sort() : [];
  return (
    <section className="vkpi-card vkpi-action-card">
      <CardHeader title="我的权限" />
      {!permissions || !keys.length ? (
        <InfoBlock label="权限" value="加载中 / 暂无明细" />
      ) : (
        keys.map((k) => (
          <div key={k} className="flex items-center justify-between py-0.5 text-[12px]">
            <span className="text-slate-400">{k}</span>
            <span className="rounded px-1.5 text-[11px] font-medium text-white" style={{ background: levelColor(permissions[k]) }}>
              {levelLabel(permissions[k])}
            </span>
          </div>
        ))
      )}
      <InfoBlock label="说明" value="只读;需更高权限请联系管理层" />
    </section>
  );
}

export function EmployeeSettingsView({ message, settingsError }: { message: string; settingsError: string }) {
  const { user } = useAuth();
  return (
    <PageShell title="个人设置">
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      {settingsError ? <div className="vkpi-inline-message is-error">{settingsError}</div> : null}
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="当前账号" />
          <InfoBlock label="界面" value="我的视角" />
          <InfoBlock label="数据范围" value="本人项目 / 本人短链 / 本人归因" />
          <InfoBlock label="头像" value="左下角上传真人头像" />
        </section>
        <EmployeePermissionCard permissions={user?.permissions as Record<string, string> | undefined} />
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="当前账号未开放" />
          <InfoBlock label="SKU 成本" value="需要授权" />
          <InfoBlock label="账号权限" value="需要授权" />
          <InfoBlock label="API Key" value="需要授权" />
        </section>
      </section>
    </PageShell>
  );
}
