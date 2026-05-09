import type { VkpiKolDetail, VkpiKolProfile, VkpiProjectRow } from '../vkpiTypes';
import { Avatar } from '../shared/Avatar';
import { DetailList } from '../shared/DetailList';
import { PlatformPill } from '../shared/PlatformPill';
import { StageBadge } from '../shared/StageBadge';
import { numberFormatter } from '../shared/vkpiFormatters';
import { coerceProjectStage, formatMoneyCents, profileToKolDetail, safeNumber, textValue } from '../shared/vkpiDataUtils';
import { platformLabels } from '../shared/vkpiConstants';

export function KolProfileDrawer({
  profile,
  fallbackKol,
  fallbackProject,
  viewMode,
  loading,
  error,
  onSelectProject,
  onClose,
}: {
  profile: VkpiKolProfile | null;
  fallbackKol: VkpiKolDetail;
  fallbackProject?: VkpiProjectRow;
  viewMode: 'manager' | 'employee';
  loading?: boolean;
  error?: string;
  onSelectProject?: (project: VkpiProjectRow) => void;
  onClose: () => void;
}) {
  const fallbackDetail: VkpiKolDetail = {
    ...fallbackKol,
    id: fallbackProject?.kolId || fallbackKol.id,
    name: fallbackProject?.kolName || fallbackKol.name,
    handle: fallbackProject?.kolHandle || fallbackKol.handle,
    platform: fallbackProject?.platform || fallbackKol.platform,
    avatar: fallbackProject?.kolAvatar || fallbackKol.avatar,
    claimOwner: fallbackProject?.ownerName || fallbackKol.claimOwner,
  };
  const kol = profileToKolDetail(profile, fallbackDetail, fallbackProject);
  const showFinancials = viewMode === 'manager';
  const projects = profile?.projects || [];
  const links = profile?.links || [];
  const linkClicks = profile?.link_clicks || [];
  const linkOrders = profile?.link_orders || [];
  const linkSummary = profile?.link_summary || {};
  const messages = profile?.messages || [];
  const contentPosts = profile?.content_posts || profile?.posts || [];
  const sales = profile?.sales_attributions || [];
  const costsRows = profile?.costs || [];
  const kpiLedger = profile?.kpi_ledger || [];
  const kpiSummary = profile?.kpi_summary || [];
  const recommendations = profile?.recommendations || [];
  const recommendationOutcomes = profile?.recommendation_outcomes || [];
  const claimHistory = profile?.claim_history || [];
  const timeline = profile?.activity_timeline || [];
  const auditEvents = profile?.audit_events || [];
  const summary = profile?.summary || {};
  return (
    <aside className="vkpi-evidence-drawer vkpi-project-detail-drawer vkpi-kol-profile-drawer" role="dialog" aria-label="KOL Profile">
      <header>
        <div>
          <span>KOL Profile</span>
          <h2>{kol.name}</h2>
          <small>{kol.handle} · {platformLabels[kol.platform] || kol.platform} · 负责人 {kol.claimOwner || '-'}</small>
        </div>
        <button type="button" onClick={onClose}>×</button>
      </header>
      <div className="vkpi-evidence-list">
        {loading ? <div className="vkpi-empty-state">正在加载真实 KOL 档案...</div> : null}
        {error ? <div className="vkpi-empty-state">{error}</div> : null}
        <article>
          <div><strong>账号概览</strong><span>{kol.scanStatus || '未抓取'}</span></div>
          <div className="vkpi-profile-card vkpi-profile-card--drawer">
            <Avatar name={kol.name} src={kol.avatar} size="lg" />
            <div>
              <h3>{kol.name}</h3>
              <p><PlatformPill platform={kol.platform} /> <span>{kol.profileUrl || kol.handle}</span></p>
            </div>
          </div>
          <div className="vkpi-profile-stats">
            <div><strong>{kol.subscribersLabel}</strong><span>粉丝</span></div>
            <div><strong>{kol.videosLabel}</strong><span>内容</span></div>
            <div><strong>{kol.engagementLabel}</strong><span>互动率</span></div>
          </div>
          <div className="vkpi-profile-stats vkpi-profile-stats--secondary">
            <div><strong>{kol.avgViewsLabel || '-'}</strong><span>平均播放</span></div>
            <div><strong>{kol.totalLikesLabel || '-'}</strong><span>总点赞</span></div>
            <div><strong>{kol.accountScoreLabel || '-'}</strong><span>评分</span></div>
          </div>
        </article>
        <article>
          <div><strong>用户画像 / 联系建议</strong><span>{kol.priorityLabel || '待评估'}</span></div>
          <p>{kol.personaLabel || '待判断'} · 适配产品：{kol.productFitSummary || '-'}</p>
          <em>{kol.personaReason || kol.recommendedAction || '暂无真实评估报告。'}</em>
        </article>
        <article>
          <div><strong>联系方式</strong><span>{kol.contactEmail || kol.contactPhone || kol.contactLinks?.length ? '已抓取' : '待补录'}</span></div>
          {kol.contactEmail ? <p>邮箱：{kol.contactEmail}</p> : null}
          {kol.contactPhone ? <p>电话 / WhatsApp：{kol.contactPhone}</p> : null}
          {kol.profileUrl ? <p>主页：{kol.profileUrl}</p> : null}
          {kol.contactLinks?.length ? <em>{kol.contactLinks.slice(0, 5).map((link) => `${link.label}: ${link.value}`).join(' · ')}</em> : null}
          {!kol.contactEmail && !kol.contactPhone && !kol.profileUrl && !kol.contactLinks?.length ? <p>暂未抓到公开联系方式；不会用假邮箱填充。</p> : null}
        </article>
        <article>
          <div><strong>Profile 汇总</strong><span>真实接口</span></div>
          <p>
            项目 {numberFormatter.format(safeNumber(summary.project_count || projects.length))}
            {' · '}
            消息 {numberFormatter.format(safeNumber(summary.message_count || messages.length))}
            {' · '}
            内容 {numberFormatter.format(safeNumber(summary.published_content_count || contentPosts.length))}
            {' · '}
            短链 {numberFormatter.format(links.length)}
            {' · '}
            KPI 证据 {numberFormatter.format(safeNumber(summary.kpi_source_count || kpiLedger.length))}
          </p>
          <em>
            销售 {formatMoneyCents(summary.revenue_cents)}
            {showFinancials ? ` · 成本 ${formatMoneyCents(summary.cost_cents)}` : ''}
            {showFinancials ? ` · 推荐 ${numberFormatter.format(safeNumber(summary.recommendation_count || recommendations.length))}` : ''}
          </em>
        </article>
        <DetailList title="项目历史" rows={projects} empty="暂无项目历史。">
          {(row) => {
            const projectId = textValue(row.id, '');
            const stage = coerceProjectStage(row.stage);
            const projectForOpen = fallbackProject && projectId === fallbackProject.id ? fallbackProject : undefined;
            return (
              <article key={`kol-project-${projectId || textValue(row.project_uid, 'project')}`}>
                <div><strong>{textValue(row.project_name || row.campaign, '未命名项目')}</strong><span>{textValue(row.updated_at || row.created_at, '-')}</span></div>
                <p>产品：{textValue(row.product_sku, '-')} · <StageBadge stage={stage} /></p>
                <em>负责人 {textValue(row.staff_name || row.assigned_staff_id, '-')} · 平台 {textValue(row.platform, '-')}</em>
                {projectForOpen && onSelectProject ? <button className="vkpi-mini-button" type="button" onClick={() => onSelectProject(projectForOpen)}>打开项目详情</button> : null}
              </article>
            );
          }}
        </DetailList>
        <DetailList title="Claim 历史" rows={claimHistory} empty="暂无 claim 历史。">
          {(row) => (
            <article key={`claim-${String(row.id || row.claimed_at || Math.random())}`}>
              <div><strong>{textValue(row.staff_name || row.staff_email || row.staff_id, '负责人')}</strong><span>{textValue(row.status, '-')}</span></div>
              <p>认领 {textValue(row.claimed_at, '-')} · 释放 {textValue(row.released_at, '-')}</p>
              <em>{textValue(row.metadata_json, '')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="近期内容" rows={contentPosts} empty="暂无真实内容数据。">
          {(row) => (
            <article key={`kol-content-${String(row.id || row.post_url || Math.random())}`}>
              <div><strong>{textValue(row.title || row.caption || row.post_url, '内容')}</strong><span>{textValue(row.published_at || row.created_at, '-')}</span></div>
              <p>{textValue(row.post_url, '-')}</p>
              <em>播放 {numberFormatter.format(safeNumber(row.views))} · 点赞 {numberFormatter.format(safeNumber(row.likes))} · 评论 {numberFormatter.format(safeNumber(row.comments))}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="消息记录" rows={messages} empty="暂无消息证据。">
          {(row) => (
            <article key={`kol-message-${String(row.id || row.created_at || Math.random())}`}>
              <div><strong>{textValue(row.source, 'manual')}</strong><span>{textValue(row.captured_at || row.created_at, '-')}</span></div>
              <p>{textValue(row.snippet || row.body, '无内容')}</p>
              <em>{textValue(row.direction, '-')} · {textValue(row.evidence_url, '无证据链接')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="短链" rows={links} empty="暂无短链。">
          {(row) => (
            <article key={`kol-link-${String(row.id || row.slug || Math.random())}`}>
              <div><strong>{textValue(row.slug, '短链')}</strong><span>{textValue(row.status, '-')}</span></div>
              <p>{textValue(row.destination_url, '-')}</p>
              <em>有效点击 {numberFormatter.format(safeNumber(row.valid_click_count || row.click_count))} · 销售 {formatMoneyCents(row.revenue_cents)}</em>
            </article>
          )}
        </DetailList>
        <article>
          <div><strong>短链汇总</strong><span>KOL 维度</span></div>
          <p>
            短链 {numberFormatter.format(safeNumber(linkSummary.link_count || links.length))}
            {' · '}点击 {numberFormatter.format(safeNumber(linkSummary.click_count))}
            {' · '}有效 {numberFormatter.format(safeNumber(linkSummary.valid_click_count))}
            {' · '}机器人 {numberFormatter.format(safeNumber(linkSummary.bot_click_count))}
          </p>
          <em>订单 {numberFormatter.format(safeNumber(linkSummary.order_count || linkOrders.length))} · 销售 {formatMoneyCents(linkSummary.revenue_cents)}</em>
        </article>
        <DetailList title="短链点击证据" rows={linkClicks} empty="暂无点击证据。">
          {(row) => (
            <article key={`kol-click-${String(row.id || row.event_id || Math.random())}`}>
              <div><strong>{textValue(row.slug || row.event_id, 'click')}</strong><span>{textValue(row.clicked_at, '-')}</span></div>
              <p>{textValue(row.referrer, '无 referrer')}</p>
              <em>{textValue(row.device_type, '-')} · {safeNumber(row.is_bot) ? 'bot' : 'valid'} · {textValue(row.country_code, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="短链订单证据" rows={linkOrders} empty="暂无短链订单证据。">
          {(row) => (
            <article key={`kol-link-order-${String(row.shopify_order_snapshot_id || row.source_ref || row.attribution_id || Math.random())}`}>
              <div><strong>{textValue(row.order_name || row.order_number || row.source_ref, '订单')}</strong><span>{formatMoneyCents(row.revenue_cents)}</span></div>
              <p>{textValue(row.slug, '-')} · {textValue(row.financial_status || row.confidence, '-')}</p>
              <em>{textValue(row.processed_at || row.occurred_at, '-')} · Hash {textValue(row.raw_payload_hash, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="销售归因" rows={sales} empty="暂无销售归因。">
          {(row) => (
            <article key={`kol-sale-${String(row.id || row.source_ref || Math.random())}`}>
              <div><strong>{formatMoneyCents(row.revenue_cents)}</strong><span>{textValue(row.source_platform, '-')}</span></div>
              <p>{textValue(row.source_ref || row.order_id, '-')}</p>
              {row.order_snapshot ? (
                <p className="vkpi-detail-subline">
                  Shopify {textValue((row.order_snapshot as Record<string, unknown>).order_name || (row.order_snapshot as Record<string, unknown>).order_number, '-')}
                  {' · '}
                  {textValue((row.order_snapshot as Record<string, unknown>).financial_status, '-')}
                  {' · '}
                  {textValue((row.order_snapshot as Record<string, unknown>).refund_status, 'no refund')}
                </p>
              ) : null}
              <em>{textValue(row.confidence, '-')} · {textValue(row.occurred_at || row.created_at, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="KPI 工作量证据" rows={kpiLedger} empty="暂无 KPI ledger 证据。">
          {(row) => (
            <article key={`kol-kpi-${String(row.id || row.source_ref || row.metric_key || Math.random())}`}>
              <div><strong>{textValue(row.metric_key, 'kpi')}</strong><span>{textValue(row.ledger_date || row.created_at, '-')}</span></div>
              <p>数值 {numberFormatter.format(safeNumber(row.metric_value))} · 项目 {textValue(row.project_name || row.project_id, '-')}</p>
              <em>{textValue(row.source_type, '-')} · {textValue(row.source_ref, '无来源 ref')} · {textValue(row.confidence, '-')}</em>
            </article>
          )}
        </DetailList>
        <DetailList title="KPI 汇总" rows={kpiSummary} empty="暂无 KPI 汇总。">
          {(row) => (
            <article key={`kol-kpi-summary-${String(row.metric_key || Math.random())}`}>
              <div><strong>{textValue(row.metric_key, 'kpi')}</strong><span>{numberFormatter.format(safeNumber(row.row_count))} 条</span></div>
              <p>累计 {numberFormatter.format(safeNumber(row.total_value))}</p>
              <em>{textValue(row.latest_ledger_date, '-')} · {textValue(row.latest_source_ref, '无来源 ref')}</em>
            </article>
          )}
        </DetailList>
        {showFinancials ? (
          <DetailList title="推荐 / Outcome 证据" rows={recommendationOutcomes.length ? recommendationOutcomes : recommendations} empty="暂无推荐 outcome 证据。">
            {(row) => (
              <article key={`kol-rec-${String(row.id || row.recommendation_uid || row.pool_uid || Math.random())}`}>
                <div><strong>{textValue(row.recommendation_uid || row.pool_uid || row.handle, 'recommendation')}</strong><span>{textValue(row.model_version || row.strategy_version || row.recommendation_status, '-')}</span></div>
                <p>
                  排名 {numberFormatter.format(safeNumber(row.rank))}
                  {' · '}分数 {numberFormatter.format(safeNumber(row.score))}
                  {' · '}GMV {formatMoneyCents(row.attributed_gmv_cents)}
                  {' · '}ROI {textValue(row.computed_roi, '-')}
                </p>
                <em>
                  shortlist {safeNumber(row.was_shortlisted) ? '是' : '否'}
                  {' · '}claim {safeNumber(row.was_claimed) ? '是' : '否'}
                  {' · '}publish {safeNumber(row.content_published) ? '是' : '否'}
                </em>
              </article>
            )}
          </DetailList>
        ) : null}
        {showFinancials ? (
          <DetailList title="成本记录" rows={costsRows} empty="暂无成本记录。">
            {(row) => (
              <article key={`kol-cost-${String(row.id || row.source_ref || Math.random())}`}>
                <div><strong>{formatMoneyCents(row.amount_cents)}</strong><span>{textValue(row.cost_type, '-')}</span></div>
                <p>{textValue(row.note, '无备注')}</p>
                <em>{textValue(row.status, '-')} · {textValue(row.incurred_at || row.created_at, '-')}</em>
              </article>
            )}
          </DetailList>
        ) : null}
        {showFinancials ? (
          <DetailList title="业务审计" rows={auditEvents} empty="暂无业务审计记录。">
            {(row) => (
              <article key={`kol-audit-${String(row.id || row.created_at || Math.random())}`}>
                <div><strong>{textValue(row.action_type, 'audit')}</strong><span>{textValue(row.created_at, '-')}</span></div>
                <p>{textValue(row.detail, '-')}</p>
                <em>{textValue(row.target_type, '-')} · {textValue(row.target_id, '-')}</em>
              </article>
            )}
          </DetailList>
        ) : null}
        <DetailList title="时间线 / 审计摘要" rows={timeline} empty="暂无时间线。">
          {(row) => (
            <article key={`kol-timeline-${String(row.id || row.created_at || Math.random())}`}>
              <div><strong>{textValue(row.action || row.event_type || row.type, '事件')}</strong><span>{textValue(row.at || row.created_at || row.occurred_at, '-')}</span></div>
              <p>{textValue(row.label || row.detail || row.note || row.description, '-')}</p>
              <em>{textValue(row.source || row.target_type, '-')}</em>
            </article>
          )}
        </DetailList>
      </div>
    </aside>
  );
}
