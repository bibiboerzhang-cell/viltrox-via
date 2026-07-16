import type React from 'react';
import {
  PreferenceSettingsCard,
  NotificationSettingsCard,
  TeamPreferenceTable,
  TeamNotificationTable,
} from './SettingsPreferencePanels';
import { SettingsApiKeyPoolPanel, SettingsZoneHeader } from './SettingsPage.Sections';
import type { SettingsModuleKey } from './SettingsPage.fragments';
import { BudgetSettingsTable } from './SettingsControlPanels';
import { SettingsRulesPanel, type SettingsRulesTab } from './SettingsRulesPanel';
import { SettingsStaffPanel } from './SettingsStaffPanel';
import { SettingsSkuPanel } from './SettingsSkuPanel';
import { SettingsStatusPanel } from './SettingsStatusPanel';
import { SettingsSchedulerPanel } from './SettingsSchedulerPanel';
import { GoaffproConnectCard } from './GoaffproConnectCard';
import { SettingsBusinessIntegrationsPanel } from './SettingsBusinessIntegrationsPanel';
import { SystemHealthBar } from '../../cockpit/components/SystemHealthBar';
import type {
  VkpiProductCatalogItem,
  VkpiStaffMember,
} from '../../vkpiTypes';
import type {
  BackendBuildInfo,
  VkpiStaffActivationLinkResponse,
  VkpiStaffInviteCapabilities,
} from '../../../../domains/settings';

type Row = Record<string, unknown>;

type RenderModule = (
  key: SettingsModuleKey,
  subtitle: string,
  children: React.ReactNode,
) => React.ReactNode;

interface SettingsCompanyZoneProps {
  renderModule: RenderModule;
  apiToken?: string;
  busy: boolean;
  // status
  apiStatusText: string;
  apiStatusDetail: string;
  backendBuild: BackendBuildInfo | null;
  dailySync: Row | null;
  frontendAsset: string;
  kolRefresh: Row;
  kolRefreshActiveTasks: number;
  kolRefreshBatchConcurrency: number;
  kolRefreshBatchCount: number;
  kolRefreshBatchTargets: number;
  kolRefreshCold: number;
  kolRefreshGateEnabled: boolean;
  kolRefreshGateText: string;
  kolRefreshHot: number;
  kolRefreshMode: string;
  kolRefreshTotal: number;
  kolRefreshWarm: number;
  providers: Row[];
  settingsLoading: boolean;
  syncAck: Row | null;
  syncAckReason: string;
  syncErrors: number;
  syncFailureRate: number;
  syncGuardText: string;
  syncHealth: Row;
  syncLastRun: Row | null;
  syncLastRunStatus: string;
  syncRequested: number;
  syncTime: string;
  systemHealth: string;
  totalBudgetUsd: number;
  totalSpentUsd: number;
  versionCheckedAt: string;
  versionSummary: string;
  onReloadVersionStatus: () => void;
  onOpenBusinessArea?: (area: 'shopify' | 'dealers' | 'events' | 'gtmCommand') => void;
  onOpenCostModule: () => void;
  // sku
  skuCount: number;
  lensCount: number;
  lightingCount: number;
  adapterCount: number;
  costSku: string;
  costProductName: string;
  unitCostUsd: string;
  costNote: string;
  selectedCatalogProduct: VkpiProductCatalogItem | null;
  canUpsert: boolean;
  productCatalog: VkpiProductCatalogItem[];
  productCatalogLoading: boolean;
  productCatalogError: string;
  productSearch: string;
  onCostSkuChange: (value: string) => void;
  onCostProductNameChange: (value: string) => void;
  onUnitCostUsdChange: (value: string) => void;
  onCostNoteChange: (value: string) => void;
  onProductSearchChange: (value: string) => void;
  onSelectProduct: (product: VkpiProductCatalogItem) => void;
  onSubmitProductCost: React.FormEventHandler;
  // staff
  members: VkpiStaffMember[];
  selectedStaffId?: string | null;
  email: string;
  name: string;
  role: string;
  permission: 'none' | 'read' | 'write';
  permissionTemplate: string;
  canInvite: boolean;
  inviteMode: 'email' | 'manual_link';
  inviteCapabilities: VkpiStaffInviteCapabilities | null;
  inviteCapabilitiesError: string;
  activationLink: VkpiStaffActivationLinkResponse | null;
  activationCopied: boolean;
  onEmailChange: (value: string) => void;
  onNameChange: (value: string) => void;
  onRoleChange: (value: string) => void;
  onPermissionChange: (value: 'none' | 'read' | 'write') => void;
  onPermissionTemplateChange: (value: string) => void;
  onCopyActivationLink: () => void;
  onSubmitInvite: React.FormEventHandler;
  onSelectStaff: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void;
  // funds
  budgetSettings: Row[];
  rowEnabled: (row: Row, key?: string) => boolean;
  onSaveBudgetSetting: (event: React.FormEvent<HTMLFormElement>, row: Row) => void;
  // rules
  candidateLimitPerStaff: string;
  commentAlertSettings: Row;
  failureRateThresholdLabel: string;
  featureFlags: Row[];
  platformCrawl: Row[];
  rulesTab: SettingsRulesTab;
  syncTimezone: string;
  onRunMorningSync: () => void;
  onRulesTabChange: (tab: SettingsRulesTab) => void;
  onSaveCommentAlertSettings: (event: React.FormEvent<HTMLFormElement>) => void;
  onToggleFeatureFlag: (row: Row) => void;
  onTogglePlatformCrawl: (row: Row) => void;
  // scheduler
  schedulerTasks: Row[];
  schedulerStatus: Row;
  schedulerTaskTotal: number;
  schedulerTaskEnabled: number;
  onToggleSchedulerTask: (taskKey: string, enabled: boolean) => void;
  // apikeys
  apiKeyPool: Row[];
  keyDraft: { account_name: string; provider: string; key: string; daily_quota: string; enabled: boolean };
  onKeyDraftChange: (draft: { account_name: string; provider: string; key: string; daily_quota: string; enabled: boolean }) => void;
  onToggleApiKey: (row: Row) => void;
  onRemoveApiKey: (id: number) => void;
  onSaveApiKey: () => void;
}

// 公司管理区:从 SettingsPage 容器抽出的纯展示分区(只吃 props)。
// renderModule 即容器里的 renderSettingsModule 闭包,折叠/展开状态仍归容器。
export function SettingsCompanyZone(props: SettingsCompanyZoneProps) {
  const { renderModule } = props;
  return (
    <>
      {/* 系统健康条:2026-06-15 从 Dashboard 顶部迁移至此,主界面更干净;仅主管可见。 */}
      <div style={{ marginBottom: 12 }}>
        <SystemHealthBar apiToken={props.apiToken} />
      </div>
      <SettingsZoneHeader zone="company" title="公司管理" hint="仅公司账号可见 · 账号权限 / 预算 / 规则 / 定时 / API 状态" />
      {renderModule('status', `${props.apiStatusText} · 同步 ${props.syncTime} / ${props.syncGuardText} · KOL ${props.kolRefreshGateText} · ${props.systemHealth} · 版本 ${props.versionSummary}`, (
        <SettingsStatusPanel
          apiStatusDetail={props.apiStatusDetail}
          apiStatusText={props.apiStatusText}
          backendBuild={props.backendBuild}
          dailySync={props.dailySync}
          frontendAsset={props.frontendAsset}
          kolRefresh={props.kolRefresh}
          kolRefreshActiveTasks={props.kolRefreshActiveTasks}
          kolRefreshBatchConcurrency={props.kolRefreshBatchConcurrency}
          kolRefreshBatchCount={props.kolRefreshBatchCount}
          kolRefreshBatchTargets={props.kolRefreshBatchTargets}
          kolRefreshCold={props.kolRefreshCold}
          kolRefreshGateEnabled={props.kolRefreshGateEnabled}
          kolRefreshGateText={props.kolRefreshGateText}
          kolRefreshHot={props.kolRefreshHot}
          kolRefreshMode={props.kolRefreshMode}
          kolRefreshTotal={props.kolRefreshTotal}
          kolRefreshWarm={props.kolRefreshWarm}
          providers={props.providers}
          settingsLoading={props.settingsLoading}
          syncAck={props.syncAck}
          syncAckReason={props.syncAckReason}
          syncErrors={props.syncErrors}
          syncFailureRate={props.syncFailureRate}
          syncGuardText={props.syncGuardText}
          syncHealth={props.syncHealth}
          syncLastRun={props.syncLastRun}
          syncLastRunStatus={props.syncLastRunStatus}
          syncRequested={props.syncRequested}
          syncTime={props.syncTime}
          systemHealth={props.systemHealth}
          totalBudgetUsd={props.totalBudgetUsd}
          totalSpentUsd={props.totalSpentUsd}
          versionCheckedAt={props.versionCheckedAt}
          onReloadVersionStatus={props.onReloadVersionStatus}
        />
      ))}
      {renderModule('businessIntegrations', 'Shopify / Dealer / 库存 / 实际成本 / 销售归因 / 结果回传 / R2 · 只认真实成功证据', (
        <SettingsBusinessIntegrationsPanel
          apiToken={props.apiToken}
          onOpenArea={props.onOpenBusinessArea}
          onOpenCostModule={props.onOpenCostModule}
        />
      ))}
      {renderModule('sku', `${props.skuCount} 个 SKU · 镜头 ${props.lensCount} · 闪光灯 ${props.lightingCount} · 转接环 ${props.adapterCount}`, (
        <SettingsSkuPanel
          apiToken={props.apiToken}
          costSku={props.costSku}
          costProductName={props.costProductName}
          unitCostUsd={props.unitCostUsd}
          costNote={props.costNote}
          selectedCatalogProduct={props.selectedCatalogProduct}
          busy={props.busy}
          canUpsert={props.canUpsert}
          productCatalog={props.productCatalog}
          productCatalogLoading={props.productCatalogLoading}
          productCatalogError={props.productCatalogError}
          productSearch={props.productSearch}
          onCostSkuChange={props.onCostSkuChange}
          onCostProductNameChange={props.onCostProductNameChange}
          onUnitCostUsdChange={props.onUnitCostUsdChange}
          onCostNoteChange={props.onCostNoteChange}
          onProductSearchChange={props.onProductSearchChange}
          onSelectProduct={props.onSelectProduct}
          onSubmitProductCost={props.onSubmitProductCost}
        />
      ))}
      {renderModule('staff', `${props.members.length} 人 · 邀请 / 权限`, (
        <SettingsStaffPanel
          members={props.members}
          selectedStaffId={props.selectedStaffId}
          email={props.email}
          name={props.name}
          role={props.role}
          permission={props.permission}
          permissionTemplate={props.permissionTemplate}
          busy={props.busy}
          canInvite={props.canInvite}
          inviteMode={props.inviteMode}
          inviteCapabilities={props.inviteCapabilities}
          inviteCapabilitiesError={props.inviteCapabilitiesError}
          activationLink={props.activationLink}
          activationCopied={props.activationCopied}
          onEmailChange={props.onEmailChange}
          onNameChange={props.onNameChange}
          onRoleChange={props.onRoleChange}
          onPermissionChange={props.onPermissionChange}
          onPermissionTemplateChange={props.onPermissionTemplateChange}
          onCopyActivationLink={props.onCopyActivationLink}
          onSubmitInvite={props.onSubmitInvite}
          onSelectStaff={props.onSelectStaff}
        />
      ))}
      {renderModule('funds', `$${props.totalSpentUsd.toLocaleString('en-US')} / $${props.totalBudgetUsd.toLocaleString('en-US')}`, (
        <BudgetSettingsTable
          budgetSettings={props.budgetSettings}
          busy={props.busy}
          rowEnabled={props.rowEnabled}
          onSaveBudgetSetting={props.onSaveBudgetSetting}
        />
      ))}
      {renderModule('rules', '功能 / 抓取 / 告警 / 同步', (
        <SettingsRulesPanel
          apiToken={props.apiToken}
          busy={props.busy}
          candidateLimitPerStaff={props.candidateLimitPerStaff}
          commentAlertSettings={props.commentAlertSettings}
          failureRateThresholdLabel={props.failureRateThresholdLabel}
          featureFlags={props.featureFlags}
          platformCrawl={props.platformCrawl}
          rowEnabled={props.rowEnabled}
          rulesTab={props.rulesTab}
          syncGuardText={props.syncGuardText}
          syncTime={props.syncTime}
          syncTimezone={props.syncTimezone}
          onRunMorningSync={props.onRunMorningSync}
          onRulesTabChange={props.onRulesTabChange}
          onSaveCommentAlertSettings={props.onSaveCommentAlertSettings}
          onToggleFeatureFlag={props.onToggleFeatureFlag}
          onTogglePlatformCrawl={props.onTogglePlatformCrawl}
        />
      ))}
      {renderModule('scheduler', `${props.schedulerTaskTotal} 个任务 · ${props.schedulerTaskEnabled} 已开启 · 仅注册可见,执行链后续接`, (
        <SettingsSchedulerPanel
          tasks={props.schedulerTasks}
          status={props.schedulerStatus}
          busy={props.busy}
          onToggleTask={props.onToggleSchedulerTask}
        />
      ))}
      {renderModule('apikeys', `${props.apiKeyPool.length} 个账号 · 7月手动填轮转 · 密文存,前端不回显`, (
        <SettingsApiKeyPoolPanel
          apiKeyPool={props.apiKeyPool}
          keyDraft={props.keyDraft}
          busy={props.busy}
          onKeyDraftChange={props.onKeyDraftChange}
          onToggleApiKey={props.onToggleApiKey}
          onRemoveApiKey={props.onRemoveApiKey}
          onSaveApiKey={props.onSaveApiKey}
        />
      ))}
      {/* GOAFFPRO 联盟营销连接配置 —— 从 Shopify Hub「连接」页迁来(token/密钥归管理员在设置区管)。
          保存端点 vkpi:admin 权限,只 owner/admin 可存,放公司管理区正合适。 */}
      {renderModule('goaffpro', '连接 token / 状态 / affiliate 预览 · 管理员配置', (
        <GoaffproConnectCard apiToken={props.apiToken} />
      ))}
    </>
  );
}

interface SettingsPersonalZoneProps {
  renderModule: RenderModule;
  busy: boolean;
  apiToken?: string;
  // 个人偏好
  landingPage: string;
  dateRangeDefault: string;
  tableDensity: string;
  rowsPerPage: string;
  compactMode: boolean;
  rightPanelOpen: boolean;
  preferenceList: Array<Record<string, unknown>>;
  onLandingPageChange: (value: string) => void;
  onDateRangeDefaultChange: (value: string) => void;
  onTableDensityChange: (value: string) => void;
  onRowsPerPageChange: (value: string) => void;
  onCompactModeChange: (value: boolean) => void;
  onRightPanelOpenChange: (value: boolean) => void;
  onSubmitPreferences: React.FormEventHandler;
  // 通知配置
  emailEnabled: boolean;
  inAppEnabled: boolean;
  dailyDigestEnabled: boolean;
  weeklySummaryEnabled: boolean;
  stalledProjectEnabled: boolean;
  claimActivityEnabled: boolean;
  attributionAlertEnabled: boolean;
  costAlertEnabled: boolean;
  systemAlertEnabled: boolean;
  quietHoursStart: string;
  quietHoursEnd: string;
  notificationList: Array<Record<string, unknown>>;
  boolValue: (value: unknown, fallback?: boolean) => boolean;
  onEmailEnabledChange: (value: boolean) => void;
  onInAppEnabledChange: (value: boolean) => void;
  onDailyDigestEnabledChange: (value: boolean) => void;
  onWeeklySummaryEnabledChange: (value: boolean) => void;
  onStalledProjectEnabledChange: (value: boolean) => void;
  onClaimActivityEnabledChange: (value: boolean) => void;
  onAttributionAlertEnabledChange: (value: boolean) => void;
  onCostAlertEnabledChange: (value: boolean) => void;
  onSystemAlertEnabledChange: (value: boolean) => void;
  onQuietHoursStartChange: (value: string) => void;
  onQuietHoursEndChange: (value: string) => void;
  onSubmitNotifications: React.FormEventHandler;
}

// 个人偏好区:从 SettingsPage 容器抽出的纯展示分区(只吃 props)。
// renderModule 即容器里的 renderSettingsModule 闭包,折叠/展开状态仍归容器。
export function SettingsPersonalZone({
  renderModule,
  busy,
  apiToken,
  landingPage,
  dateRangeDefault,
  tableDensity,
  rowsPerPage,
  compactMode,
  rightPanelOpen,
  preferenceList,
  onLandingPageChange,
  onDateRangeDefaultChange,
  onTableDensityChange,
  onRowsPerPageChange,
  onCompactModeChange,
  onRightPanelOpenChange,
  onSubmitPreferences,
  emailEnabled,
  inAppEnabled,
  dailyDigestEnabled,
  weeklySummaryEnabled,
  stalledProjectEnabled,
  claimActivityEnabled,
  attributionAlertEnabled,
  costAlertEnabled,
  systemAlertEnabled,
  quietHoursStart,
  quietHoursEnd,
  notificationList,
  boolValue,
  onEmailEnabledChange,
  onInAppEnabledChange,
  onDailyDigestEnabledChange,
  onWeeklySummaryEnabledChange,
  onStalledProjectEnabledChange,
  onClaimActivityEnabledChange,
  onAttributionAlertEnabledChange,
  onCostAlertEnabledChange,
  onSystemAlertEnabledChange,
  onQuietHoursStartChange,
  onQuietHoursEndChange,
  onSubmitNotifications,
}: SettingsPersonalZoneProps) {
  return (
    <>
      <SettingsZoneHeader zone="personal" title="个人偏好" hint="每位成员管理自己的偏好与通知" />
      {renderModule('preference', `默认入口 ${landingPage} · 范围 ${dateRangeDefault} · 密度 ${tableDensity}`, (
        <>
          <PreferenceSettingsCard
            busy={busy}
            apiToken={apiToken}
            landingPage={landingPage}
            dateRangeDefault={dateRangeDefault}
            tableDensity={tableDensity}
            rowsPerPage={rowsPerPage}
            compactMode={compactMode}
            rightPanelOpen={rightPanelOpen}
            onLandingPageChange={onLandingPageChange}
            onDateRangeDefaultChange={onDateRangeDefaultChange}
            onTableDensityChange={onTableDensityChange}
            onRowsPerPageChange={onRowsPerPageChange}
            onCompactModeChange={onCompactModeChange}
            onRightPanelOpenChange={onRightPanelOpenChange}
            onSubmit={onSubmitPreferences}
          />
          <TeamPreferenceTable preferenceList={preferenceList} />
        </>
      ))}
      {renderModule('notification', `站内 ${inAppEnabled ? '开' : '关'} · 邮件 ${emailEnabled ? '开' : '关'} · v3 只存不发`, (
        <>
          <NotificationSettingsCard
            busy={busy}
            apiToken={apiToken}
            emailEnabled={emailEnabled}
            inAppEnabled={inAppEnabled}
            dailyDigestEnabled={dailyDigestEnabled}
            weeklySummaryEnabled={weeklySummaryEnabled}
            stalledProjectEnabled={stalledProjectEnabled}
            claimActivityEnabled={claimActivityEnabled}
            attributionAlertEnabled={attributionAlertEnabled}
            costAlertEnabled={costAlertEnabled}
            systemAlertEnabled={systemAlertEnabled}
            quietHoursStart={quietHoursStart}
            quietHoursEnd={quietHoursEnd}
            onEmailEnabledChange={onEmailEnabledChange}
            onInAppEnabledChange={onInAppEnabledChange}
            onDailyDigestEnabledChange={onDailyDigestEnabledChange}
            onWeeklySummaryEnabledChange={onWeeklySummaryEnabledChange}
            onStalledProjectEnabledChange={onStalledProjectEnabledChange}
            onClaimActivityEnabledChange={onClaimActivityEnabledChange}
            onAttributionAlertEnabledChange={onAttributionAlertEnabledChange}
            onCostAlertEnabledChange={onCostAlertEnabledChange}
            onSystemAlertEnabledChange={onSystemAlertEnabledChange}
            onQuietHoursStartChange={onQuietHoursStartChange}
            onQuietHoursEndChange={onQuietHoursEndChange}
            onSubmit={onSubmitNotifications}
          />
          <TeamNotificationTable notificationList={notificationList} boolValue={boolValue} />
        </>
      ))}
    </>
  );
}
