import type { VkpiProjectRow, VkpiStaffMember, VkpiStaffProfile } from '../vkpiTypes';
import { Avatar } from '../shared/Avatar';
import { DetailList } from '../shared/DetailList';
import { InfoBlock } from '../shared/InfoBlock';
import { currencyFormatter, numberFormatter } from '../shared/vkpiFormatters';
import { coerceProjectStage, platformDisplay, platformFromRaw, safeNumber, textValue } from '../shared/vkpiDataUtils';
import { stageLabels } from '../shared/vkpiConstants';

function auditActionLabel(value: string) {
  const labels: Record<string, string> = {
    claim: '认领', release: '释放', reassign: '转派', project_create: '创建项目', project_stage_change: '项目推进', project_delete: '删除项目',
    cost_add: '新增成本', cost_edit: '编辑成本', cost_approve: '审核成本', cost_void: '作废成本', manual_attribution: '手动归因',
    reconciliation_resolve: '归因复核', link_pause: '暂停短链', link_archive: '归档短链', export_download: '下载导出', kpi_rollup: 'KPI 计入',
    view_contact: '查看联系方式', view_message: '查看消息', view_financial: '查看财务', view_audit_log: '查看审计', view_kpi_detail: '查看 KPI 明细',
  };
  return labels[value] || value || '-';
}

export function StaffProfileDrawer({
  member,
  profile,
  loading,
  error,
  onSelectProject,
  onClose,
}: {
  member: VkpiStaffMember;
  profile: VkpiStaffProfile | null;
  loading?: boolean;
  error?: string;
  onSelectProject: (project: VkpiProjectRow) => void;
  onClose: () => void;
}) {
  const staff = profile?.staff || {};
  const summary = profile?.summary || {};
  const visibility = profile?.visibility || {};
  const projects = profile?.projects || [];
  const claims = profile?.claims || [];
  const links = profile?.links || [];
  const attributions = profile?.attributions || [];
  const costs = profile?.costs || [];
  const kpi = profile?.kpi_ledger || [];
  const kpiBreakdown = profile?.kpi_breakdown || {};
  const kpiGrouped = kpiBreakdown.grouped || [];
  const kpiSources = kpiBreakdown.source_rows || [];
  const recommendationKpiGrouped = kpiBreakdown.recommendation_grouped || [];
  const recommendationKpiSources = kpiBreakdown.recommendation_source_rows || [];
  const channels = profile?.channels || [];
  const audits = profile?.audit_events || [];
  const displayName = textValue(staff.staff_name || staff.name || member.name, member.name);
  const avatar = textValue(staff.avatar_url || member.avatarUrl, '');
  const employeeCode = textValue(staff.employee_code || member.employeeCode || staff.email || member.email, '-');
  const costsVisible = Boolean(visibility.costs_visible);
  return (
    <aside className="vkpi-evidence-drawer vkpi-staff-profile-drawer" role="dialog" aria-label="员工详情">
      <header>
        <div>
          <span>Staff Profile</span>
          <h2>{displayName}</h2>
          <small>{employeeCode} · {textValue(staff.email || member.email, '-')}</small>
        </div>
        <button type="button" onClick={onClose}>×</button>
      </header>
      <div className="vkpi-profile-card vkpi-profile-card--drawer">
        <Avatar name={displayName} src={avatar} size="lg" />
        <div>
          <h3>{displayName}</h3>
          <p>{textValue(staff.role || member.role, '-')} · {member.active ? '启用' : '停用'}</p>
          <span>{costsVisible ? '管理层可见成本和审计' : '当前视角隐藏内部成本'}</span>
        </div>
      </div>
      {loading ? <div className="vkpi-empty-state">正在加载员工详情...</div> : null}
      {error ? <div className="vkpi-empty-state">{error}</div> : null}
      <div className="vkpi-result-grid">
        <InfoBlock label="项目" value={numberFormatter.format(safeNumber(summary.project_count || summary.projects))} />
        <InfoBlock label="KOL" value={numberFormatter.format(safeNumber(summary.claim_count || summary.kol_claims))} />
        <InfoBlock label="短链" value={numberFormatter.format(safeNumber(summary.link_count || summary.links_created))} />
        <InfoBlock label="销售额" value={currencyFormatter.format(safeNumber(summary.profile_revenue_cents || summary.gmv_cents) / 100)} />
        {costsVisible ? <InfoBlock label="成本" value={currencyFormatter.format(safeNumber(summary.profile_cost_cents || summary.cost_cents) / 100)} /> : null}
        <InfoBlock label="工作量分" value={numberFormatter.format(safeNumber(summary.workload_score || summary.kpi_credit))} />
        <InfoBlock label="KPI 来源" value={numberFormatter.format(safeNumber(summary.kpi_source_count || kpiBreakdown.source_count))} />
        <InfoBlock label="推荐来源" value={numberFormatter.format(safeNumber(summary.recommendation_kpi_source_count || recommendationKpiSources.length))} />
      </div>
      <div className="vkpi-evidence-list">
        <DetailList title="KPI 来源汇总" rows={kpiGrouped.slice(0, 16)} empty="暂无 KPI 来源汇总。">
          {(row) => (
            <article key={`staff-kpi-group-${String(row.metric_key || Math.random())}`}>
              <div><strong>{textValue(row.metric_label || row.metric_key, '指标')}</strong><b>{numberFormatter.format(safeNumber(row.total_value))}</b></div>
              <p>来源 {numberFormatter.format(safeNumber(row.source_count))} 条 · {textValue(row.first_date, '-')} → {textValue(row.last_date, '-')}</p>
              <em>{String(row.is_recommendation_metric) === 'true' ? '来自产品分析推荐结果，可继续下钻到推荐 outcome。' : `confidence: ${textValue(row.confidence, '-')}`}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="推荐 KPI 来源" rows={recommendationKpiGrouped.slice(0, 12)} empty="暂无推荐类 KPI 来源。">
          {(row) => (
            <article key={`staff-rec-kpi-${String(row.metric_key || Math.random())}`}>
              <div><strong>{textValue(row.metric_label || row.metric_key, '推荐指标')}</strong><b>{numberFormatter.format(safeNumber(row.total_value))}</b></div>
              <p>来源 {numberFormatter.format(safeNumber(row.source_count))} 条 · {textValue(row.first_date, '-')} → {textValue(row.last_date, '-')}</p>
              <em>该指标来自 `vkpi_recommendation_outcomes`，不会重复计入主销售额 / 成本。</em>
            </article>
          )}
        </DetailList>
        <DetailList title="KPI Source Rows" rows={kpiSources.slice(0, 20)} empty="暂无 KPI source rows。">
          {(row) => {
            const evidence = (row.evidence || {}) as Record<string, unknown>;
            const sourceContext = (row.source_context || evidence.source_context || {}) as Record<string, unknown>;
            const entities = Array.isArray(sourceContext.entities) ? sourceContext.entities as Array<Record<string, unknown>> : [];
            return (
              <article key={`staff-kpi-source-${String(row.id || row.source_ref || Math.random())}`}>
                <div><strong>{textValue(row.metric_label || row.metric_key, '指标')}</strong><span>{numberFormatter.format(safeNumber(row.metric_value))}</span></div>
                <p>{textValue(row.project_name || row.project_id, '-')} · {textValue(row.kol_name || row.kol_id, '-')}</p>
                <em>{textValue(row.ledger_date || row.created_at, '-')} · {textValue(row.source_type, '-')} · {textValue(row.source_ref, '-')}</em>
                {entities.length ? <em>证据链：{entities.slice(0, 5).map((item) => `${textValue(item.type, 'entity')}#${textValue(item.id, '-')}`).join(' → ')}</em> : null}
                {sourceContext.shopify_order ? <em>Shopify：{textValue((sourceContext.shopify_order as Record<string, unknown>).order_name || (sourceContext.shopify_order as Record<string, unknown>).order_number, '-')}</em> : null}
                {evidence.recommendation_id ? <em>推荐 #{textValue(evidence.recommendation_id, '-')} · Outcome #{textValue(evidence.outcome_id, '-')} · Launch #{textValue(evidence.launch_id, '-')}</em> : null}
                {evidence.formula ? <em>公式：{textValue(evidence.formula, '-')}</em> : null}
                {Array.isArray(evidence.components) && evidence.components.length ? (
                  <em>组件：{evidence.components.slice(0, 4).map((item: Record<string, unknown>) => `${textValue(item.metric_label || item.metric_key, '指标')} ${numberFormatter.format(safeNumber(item.contribution))}`).join(' / ')}</em>
                ) : null}
              </article>
            );
          }}
        </DetailList>
        <DetailList title="员工项目" rows={projects} empty="该员工暂无项目。">
          {(row) => (
            <article key={`staff-project-${String(row.id || row.project_uid || Math.random())}`}>
              <div><strong>{textValue(row.project_name || row.project_uid, '项目')}</strong><span>{stageLabels[coerceProjectStage(row.stage)] || textValue(row.stage, '-')}</span></div>
              <p>KOL：{textValue(row.kol_name || row.kol_id, '-')} · 产品：{textValue(row.product_sku || row.product_name, '-')}</p>
              <em>{textValue(row.updated_at || row.created_at, '-')}</em>
              <button className="vkpi-mini-button" type="button" onClick={() => onSelectProject({
                id: String(row.id || ''),
                kolId: row.kol_id ? String(row.kol_id) : undefined,
                kolName: textValue(row.kol_name || row.kol_id, '未知 KOL'),
                kolHandle: textValue(row.kol_platform || row.platform, '-'),
                platform: platformFromRaw(row.kol_platform || row.platform),
                campaign: textValue(row.project_name || row.project_uid, '未命名项目'),
                stage: coerceProjectStage(row.stage),
                latestMessageAt: textValue(row.last_activity_at || row.updated_at, '-'),
                latestMessageSource: 'Manual note',
                views: safeNumber(row.total_views || row.views),
                clicks: null,
                orders: null,
                gmv: safeNumber(row.revenue_cents) / 100,
                cost: safeNumber(row.cost_cents) / 100,
                roi: null,
                ownerId: member.id,
                ownerName: displayName,
                ownerAvatar: avatar,
                updatedAt: textValue(row.updated_at || row.created_at, '-'),
              })}>打开项目详情</button>
            </article>
          )}
        </DetailList>
        <DetailList title="KOL 认领" rows={claims} empty="暂无 KOL 认领。">
          {(row) => (
            <article key={`staff-claim-${String(row.id || row.kol_id || Math.random())}`}>
              <div><strong>{textValue(row.kol_name || row.channel_name || row.kol_id, 'KOL')}</strong><span>{textValue(row.status, '-')}</span></div>
              <p>{platformDisplay(row.platform)} · 粉丝 {row.follower_count == null ? '待同步' : numberFormatter.format(safeNumber(row.follower_count))}</p>
              <em>{textValue(row.claimed_at || row.created_at, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="短链" rows={links} empty="暂无短链。">
          {(row) => (
            <article key={`staff-link-${String(row.id || row.slug || Math.random())}`}>
              <div><strong>{textValue(row.slug, 'link')}</strong><span>{textValue(row.status, '-')}</span></div>
              <p>{textValue(row.destination_url, '-')}</p>
              <em>有效点击 {numberFormatter.format(safeNumber(row.valid_click_count || row.click_count))} · KOL {textValue(row.kol_name || row.kol_id, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="销售归因" rows={attributions} empty="暂无销售归因。">
          {(row) => (
            <article key={`staff-attr-${String(row.id || row.source_ref || Math.random())}`}>
              <div><strong>{currencyFormatter.format(safeNumber(row.revenue_cents) / 100)}</strong><span>{textValue(row.source_platform, '-')}</span></div>
              <p>{textValue(row.order_name || row.order_number || row.source_ref, '-')}</p>
              <em>{textValue(row.confidence, '-')} · {textValue(row.occurred_at || row.imported_at || row.created_at, '-')}</em>
            </article>
          )}
        </DetailList>
        {costsVisible ? (
          <DetailList title="成本" rows={costs} empty="暂无成本。">
            {(row) => (
              <article key={`staff-cost-${String(row.id || row.source_ref || Math.random())}`}>
                <div><strong>{currencyFormatter.format(safeNumber(row.amount_cents) / 100)}</strong><span>{textValue(row.cost_type, '-')}</span></div>
                <p>{textValue(row.project_name || row.project_id, '-')} · {textValue(row.kol_name || row.kol_id, '-')}</p>
                <em>{textValue(row.status, '-')} · {textValue(row.incurred_at || row.created_at, '-')}</em>
              </article>
            )}
          </DetailList>
        ) : null}
        <DetailList title="KPI Ledger" rows={kpi.slice(0, 20)} empty="暂无 KPI Ledger。">
          {(row) => (
            <article key={`staff-kpi-${String(row.id || row.source_ref || Math.random())}`}>
              <div><strong>{textValue(row.metric_label || row.metric_key, '指标')}</strong><span>{numberFormatter.format(safeNumber(row.metric_value))}</span></div>
              <p>{textValue(row.project_name || row.project_id, '-')} · {textValue(row.kol_name || row.kol_id, '-')}</p>
              <em>{textValue(row.ledger_date || row.created_at, '-')} · {textValue(row.source_type, '-')}</em>
              {Array.isArray((row.source_context as Record<string, unknown> | undefined)?.entities) ? (
                <em>证据链：{((row.source_context as Record<string, unknown>).entities as Array<Record<string, unknown>>).slice(0, 4).map((item) => `${textValue(item.type, 'entity')}#${textValue(item.id, '-')}`).join(' → ')}</em>
              ) : null}
            </article>
          )}
        </DetailList>
        <DetailList title="平台账号" rows={channels} empty="暂无平台绑定。">
          {(row) => (
            <article key={`staff-channel-${String(row.id || row.account_handle || Math.random())}`}>
              <div><strong>{platformDisplay(row.platform)}</strong><span>{textValue(row.status, '-')}</span></div>
              <p>{textValue(row.account_display_name || row.account_handle, '-')}</p>
              <em>{textValue(row.last_sync_status || row.sync_status, '待同步')} · {textValue(row.updated_at, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="审计" rows={audits} empty={costsVisible ? '暂无审计事件。' : '当前视角不显示全局审计。'}>
          {(row) => (
            <article key={`staff-audit-${String(row.id || row.target_id || Math.random())}`}>
              <div><strong>{auditActionLabel(textValue(row.action_type, '-'))}</strong><span>{textValue(row.target_type, '-')} #{textValue(row.target_id, '-')}</span></div>
              <p>{textValue(row.detail, '-')}</p>
              <em>{textValue(row.created_at, '-')}</em>
            </article>
          )}
        </DetailList>
      </div>
    </aside>
  );
}
