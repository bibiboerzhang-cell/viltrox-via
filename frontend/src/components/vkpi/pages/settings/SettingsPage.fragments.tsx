import type React from 'react';
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

export function EmployeeSettingsView({ message, settingsError }: { message: string; settingsError: string }) {
  return (
    <PageShell title="个人设置">
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      {settingsError ? <div className="vkpi-inline-message is-error">{settingsError}</div> : null}
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="当前账号" />
          <InfoBlock label="界面" value="员工视角" />
          <InfoBlock label="数据范围" value="本人项目 / 本人短链 / 本人归因" />
          <InfoBlock label="头像" value="左下角上传真人头像" />
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="不可见项目" />
          <InfoBlock label="SKU 成本" value="管理层可见" />
          <InfoBlock label="员工授权" value="管理层可见" />
          <InfoBlock label="API Key" value="管理层可见" />
        </section>
      </section>
    </PageShell>
  );
}
