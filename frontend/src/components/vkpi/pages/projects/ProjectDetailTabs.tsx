import { Avatar } from '../../shared/Avatar';
import { PlatformPill } from '../../shared/PlatformPill';
import { stageLabels } from '../../shared/vkpiConstants';
import type { VkpiProjectRow } from '../../vkpiTypes';
import {
  bottleneckForRows,
  cancelledStages,
  formatMoney,
  formatNumber,
  formatPercent,
  formatRatio,
  healthForRows,
  stageIndex,
  type ContractLine,
  type ExpenseLine,
  type ProjectAnalyticsSummary,
  type ProjectStatsSummary,
  type StageCostSummary,
} from './projectDetailModel';

export function CampaignContractsTab({
  rows,
  contractLines,
}: {
  rows: VkpiProjectRow[];
  contractLines: ContractLine[];
}) {
  const archiveNeeded = contractLines.filter((line) => line.statusLabel === '需归档').length;
  const termsPending = contractLines.filter((line) => line.statusLabel === '待确认条款').length;
  const notStarted = contractLines.filter((line) => line.statusLabel === '未触发').length;
  const reviewReady = contractLines.filter((line) => line.statusLabel === '待复核').length;
  const evidenceTotal = contractLines.reduce((sum, line) => sum + line.evidenceCount, 0);

  return (
    <div className="vkpi-campaign-contracts" aria-label="项目合同归档">
      <div className="vkpi-campaign-contracts-head">
        <div>
          <span>Contract archive center</span>
          <h3>合同归档</h3>
          <p>当前不新增合同表，先用项目阶段、成本和证据数量推导每个 KOL 的合同归档风险。</p>
        </div>
        <div>
          <strong>{archiveNeeded + reviewReady}</strong>
          <span>需要归档 / 复核</span>
        </div>
      </div>

      <div className="vkpi-campaign-contracts-totals">
        <div><span>全部 KOL</span><strong>{rows.length}</strong><em>当前项目行</em></div>
        <div><span>需归档</span><strong>{archiveNeeded}</strong><em>已合作后应补凭证</em></div>
        <div><span>待确认条款</span><strong>{termsPending}</strong><em>回复到合作前</em></div>
        <div><span>未触发</span><strong>{notStarted}</strong><em>尚未到合同节点</em></div>
        <div><span>证据截图</span><strong>{evidenceTotal}</strong><em>现有阶段证据</em></div>
      </div>

      <div className="vkpi-campaign-contracts-grid">
        <section className="vkpi-campaign-contract-card">
          <header>
            <div>
              <span>合同状态总览</span>
              <h4>按阶段推导，不伪造签约状态</h4>
            </div>
          </header>
          <div className="vkpi-campaign-contract-statuses">
            <div><b>{notStarted}</b><span>未触发</span></div>
            <div><b>{termsPending}</b><span>待确认条款</span></div>
            <div><b>{archiveNeeded}</b><span>需归档</span></div>
            <div><b>{reviewReady}</b><span>待复核</span></div>
          </div>
          <div className="vkpi-campaign-contract-alert">未接入 `campaign_contracts` 之前，这里不会提供假上传、假生成合同或假签署按钮；只显示真实项目行能推导出的待办。</div>
        </section>

        <section className="vkpi-campaign-contract-card">
          <header>
            <div>
              <span>模板库状态</span>
              <h4>下一步接口位置</h4>
            </div>
          </header>
          <div className="vkpi-campaign-contract-template">
            <div><strong>免费寄样 / 佣金模板</strong><span>未接入合同模板表</span></div>
            <div><strong>付费推广模板</strong><span>未接入合同模板表</span></div>
            <div><strong>长期合作模板</strong><span>未接入合同模板表</span></div>
          </div>
          <p>后续接入合同表后，这里再开放模板生成、已签版上传、条款 OCR 和归档导出。</p>
        </section>
      </div>

      <section className="vkpi-campaign-contract-card">
        <header>
          <div>
            <span>合同清单</span>
            <h4>KOL 级归档待办</h4>
          </div>
        </header>
        <div className="vkpi-campaign-contract-table">
          <div className="vkpi-campaign-contract-row is-head">
            <span>KOL</span>
            <span>平台</span>
            <span>阶段</span>
            <span>归档状态</span>
            <span>条款口径</span>
            <span>金额</span>
            <span>证据</span>
            <span>下一步</span>
          </div>
          {contractLines.map((line) => (
            <div className="vkpi-campaign-contract-row" key={line.id}>
              <span><b>{line.kolHandle || line.kolName}</b><small>{line.kolName || '-'}</small></span>
              <span><PlatformPill platform={line.platform} /></span>
              <span>{stageLabels[line.stage]}</span>
              <span className={line.statusClass}>{line.statusLabel}</span>
              <span>{line.contractType}</span>
              <span>{formatMoney(line.amount)}</span>
              <span>{line.evidenceCount}</span>
              <span>{line.nextAction}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

export function CampaignRetrospectiveTab({
  project,
  rows,
  stats,
  analytics,
  health,
  bottleneck,
  onCopy,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  analytics: ProjectAnalyticsSummary;
  health: ReturnType<typeof healthForRows>;
  bottleneck: ReturnType<typeof bottleneckForRows>;
  onCopy: (text: string, label: string) => Promise<void>;
}) {
  const bestPlatform = analytics.platformRows[0];
  const topKol = analytics.topRows[0];
  const pendingPublish = rows.filter((row) => stageIndex(row.stage) < stageIndex('published')).length;
  const missingCost = rows.filter((row) => !row.cost).length;
  const missingLinks = rows.filter((row) => !row.shopifyLink).length;
  const targetStatus = rows.length ? '目标字段未接入，当前只展示实际值' : '暂无 KOL';
  const highlightItems = [
    topKol ? `${topKol.kolHandle || topKol.kolName} 当前贡献 ${formatNumber(topKol.views)} 曝光，归因销售 ${formatMoney(topKol.gmv)}。` : '暂无 Top KOL，可先追加 KOL 或等待内容同步。',
    bestPlatform ? `${bestPlatform.platform} 是当前最高曝光平台，${bestPlatform.kolCount} 个 KOL 合计 ${formatNumber(bestPlatform.views)} 曝光。` : '暂无平台分布数据。',
    stats.roi != null ? `项目 ROI 为 ${formatRatio(stats.roi)}，成本 ${formatMoney(stats.cost)}，归因销售 ${formatMoney(stats.gmv)}。` : '成本或销售不足，ROI 暂不可判断。',
  ];
  const lessonItems = [
    `当前瓶颈在 ${bottleneck.from}→${bottleneck.to}：${bottleneck.text}`,
    pendingPublish ? `${pendingPublish} 个 KOL 还没到发布节点，收尾前需要逐个确认发布排期。` : '当前 KOL 均已到发布或后续节点。',
    missingLinks ? `${missingLinks} 个 KOL 缺 Shopify / 归因链接，后续销售归因会偏弱。` : '当前 KOL 都有归因链接。',
    missingCost ? `${missingCost} 个 KOL 缺成本记录，费用 tab 的 ROI 仍需复核。` : '当前 KOL 都有成本记录。',
  ].slice(0, 4);
  const retrospectiveText = [
    `${project.campaign || '未命名推广'} · 复盘草稿`,
    `健康度：${health.score} / ${health.label}`,
    `参与 KOL：${rows.length}`,
    `已发布：${stats.published}，发布率 ${stats.publishRate}%`,
    `总曝光：${formatNumber(stats.views)}`,
    `短链点击：${formatNumber(stats.clicks || 0)}`,
    `归因订单：${formatNumber(stats.orders || 0)}`,
    `归因销售：${formatMoney(stats.gmv)}`,
    `成本：${formatMoney(stats.cost)}，ROI：${formatRatio(stats.roi)}`,
    '',
    '亮点：',
    ...highlightItems.map((item, index) => `${index + 1}. ${item}`),
    '',
    '风险 / 教训：',
    ...lessonItems.map((item, index) => `${index + 1}. ${item}`),
  ].join('\n');

  return (
    <div className="vkpi-campaign-retro" aria-label="项目复盘">
      <div className="vkpi-campaign-retro-head">
        <div>
          <span>Campaign retrospective</span>
          <h3>复盘</h3>
          <p>基于当前真实项目数据生成复盘草稿；不冒充 AI 报告，也不写入后端复盘表。</p>
        </div>
        <button type="button" onClick={() => void onCopy(retrospectiveText, '复盘草稿')}>复制复盘草稿</button>
      </div>

      <div className="vkpi-campaign-retro-score">
        <div>
          <span>健康度</span>
          <strong className={health.className}>{health.score}</strong>
          <em>{health.label}</em>
        </div>
        <div>
          <span>当前瓶颈</span>
          <strong>{bottleneck.from}→{bottleneck.to}</strong>
          <em>{bottleneck.text}</em>
        </div>
        <div>
          <span>复盘状态</span>
          <strong>草稿</strong>
          <em>{targetStatus}</em>
        </div>
      </div>

      <div className="vkpi-campaign-retro-grid">
        <section className="vkpi-campaign-retro-card is-wide">
          <header>
            <div>
              <span>KPI vs 当前实际</span>
              <h4>目标字段未接入前只看真实实际值</h4>
            </div>
          </header>
          <div className="vkpi-campaign-retro-kpis">
            <div><span>KOL</span><strong>{rows.length}</strong><em>目标未设置</em></div>
            <div><span>已发布</span><strong>{stats.published}</strong><em>{stats.publishRate}%</em></div>
            <div><span>曝光</span><strong>{formatNumber(stats.views)}</strong><em>自动汇总</em></div>
            <div><span>销售</span><strong>{formatMoney(stats.gmv)}</strong><em>{formatNumber(stats.orders || 0)} 单</em></div>
            <div><span>ROI</span><strong>{formatRatio(stats.roi)}</strong><em>成本 {formatMoney(stats.cost)}</em></div>
          </div>
        </section>

        <section className="vkpi-campaign-retro-card">
          <header>
            <div>
              <span>亮点</span>
              <h4>可以复用的经验</h4>
            </div>
          </header>
          <div className="vkpi-campaign-retro-list">
            {highlightItems.map((item) => <p key={item}>{item}</p>)}
          </div>
        </section>
      </div>

      <div className="vkpi-campaign-retro-grid">
        <section className="vkpi-campaign-retro-card">
          <header>
            <div>
              <span>风险 / 教训</span>
              <h4>下一步需要补齐</h4>
            </div>
          </header>
          <div className="vkpi-campaign-retro-list is-warning">
            {lessonItems.map((item) => <p key={item}>{item}</p>)}
          </div>
        </section>

        <section className="vkpi-campaign-retro-card">
          <header>
            <div>
              <span>平台结论</span>
              <h4>从分布里找下一轮预算方向</h4>
            </div>
          </header>
          <div className="vkpi-campaign-retro-platforms">
            {analytics.platformRows.slice(0, 4).map((item) => (
              <div key={item.platform}>
                <span><PlatformPill platform={item.platform as VkpiProjectRow['platform']} /></span>
                <b>{formatNumber(item.views)}</b>
                <em>{formatMoney(item.gmv)} · ROI {formatRatio(item.roi)}</em>
              </div>
            ))}
            {!analytics.platformRows.length ? <p>暂无平台数据。</p> : null}
          </div>
        </section>
      </div>

      <section className="vkpi-campaign-retro-card">
        <header>
          <div>
            <span>团队备注</span>
            <h4>后端复盘表未接入</h4>
          </div>
        </header>
        <div className="vkpi-campaign-retro-note">
          <p>这里暂时不放假“添加备注”按钮。后续接入 `campaign_retrospectives` 后，再开放团队备注、AI 生成、PDF 导出和管理层分享。</p>
        </div>
      </section>
    </div>
  );
}

export function CampaignAnalyticsTab({
  rows,
  stats,
  analytics,
  health,
}: {
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  analytics: ProjectAnalyticsSummary;
  health: ReturnType<typeof healthForRows>;
}) {
  const maxTimelineViews = Math.max(...analytics.timeline.map((item) => item.views), 1);
  const maxTopViews = Math.max(...analytics.topRows.map((row) => row.views || 0), 1);
  const activeRows = rows.filter((row) => !cancelledStages.has(row.stage));
  const pendingRows = rows.filter((row) => stageIndex(row.stage) < stageIndex('published'));

  return (
    <div className="vkpi-campaign-analytics" aria-label="项目数据汇总">
      <div className="vkpi-campaign-analytics-head">
        <div>
          <span>Campaign data cockpit</span>
          <h3>数据汇总</h3>
          <p>基于当前项目详情返回的 KOL 行、短链点击、曝光、成本和归因销售实时聚合。</p>
        </div>
        <div className={`vkpi-campaign-analytics-health ${health.className}`}>
          <span>健康度</span>
          <strong>{health.score}</strong>
          <em>{health.label}</em>
        </div>
      </div>

      <div className="vkpi-campaign-analytics-totals">
        <div>
          <span>总曝光</span>
          <strong>{formatNumber(stats.views)}</strong>
          <em>{rows.length ? `${formatNumber(Math.round(stats.views / rows.length))} / KOL` : '暂无 KOL'}</em>
        </div>
        <div>
          <span>总互动</span>
          <strong>{formatNumber(stats.clicks)}</strong>
          <em>互动率 {formatPercent(analytics.engagement)}</em>
        </div>
        <div>
          <span>归因订单</span>
          <strong>{formatNumber(stats.orders)}</strong>
          <em>已发布 {stats.published} / {rows.length}</em>
        </div>
        <div>
          <span>归因销售</span>
          <strong>{formatMoney(stats.gmv)}</strong>
          <em>ROI {formatRatio(stats.roi)}</em>
        </div>
      </div>

      <div className="vkpi-campaign-analytics-grid">
        <section className="vkpi-campaign-analytics-card is-wide">
          <header>
            <div>
              <span>7 天趋势</span>
              <h4>发布与曝光</h4>
            </div>
            <em>{stats.published} 条已发布内容</em>
          </header>
          <div className="vkpi-campaign-analytics-timeline">
            {analytics.timeline.map((point) => (
              <div key={point.dateKey}>
                <span style={{ height: `${Math.max(8, Math.round((point.views / maxTimelineViews) * 86))}px` }} />
                <strong>{formatNumber(point.views)}</strong>
                <em>{point.posts} 条</em>
                <small>{point.label}</small>
              </div>
            ))}
          </div>
        </section>

        <section className="vkpi-campaign-analytics-card">
          <header>
            <div>
              <span>执行状态</span>
              <h4>当前项目池</h4>
            </div>
          </header>
          <div className="vkpi-campaign-analytics-state">
            <div><b>{rows.length}</b><span>全部 KOL</span></div>
            <div><b>{activeRows.length}</b><span>有效推进</span></div>
            <div><b>{pendingRows.length}</b><span>待发布</span></div>
            <div><b>{stats.publishRate}%</b><span>发布率</span></div>
          </div>
        </section>
      </div>

      <div className="vkpi-campaign-analytics-grid">
        <section className="vkpi-campaign-analytics-card is-wide">
          <header>
            <div>
              <span>平台分布</span>
              <h4>按平台看 ROI 和曝光</h4>
            </div>
          </header>
          <div className="vkpi-campaign-platform-table">
            <div className="vkpi-campaign-platform-row is-head">
              <span>平台</span>
              <span>KOL</span>
              <span>曝光</span>
              <span>互动率</span>
              <span>归因$</span>
              <span>ROI</span>
            </div>
            {analytics.platformRows.map((item) => (
              <div className="vkpi-campaign-platform-row" key={item.platform}>
                <span><PlatformPill platform={item.platform as VkpiProjectRow['platform']} /></span>
                <span>{item.kolCount}</span>
                <span>{formatNumber(item.views)}</span>
                <span>{formatPercent(item.views ? (item.clicks / item.views) * 100 : 0)}</span>
                <span>{formatMoney(item.gmv)}</span>
                <span>{formatRatio(item.roi)}</span>
              </div>
            ))}
            {!analytics.platformRows.length ? <div className="vkpi-campaign-analytics-empty">暂无平台数据。</div> : null}
          </div>
        </section>

        <section className="vkpi-campaign-analytics-card">
          <header>
            <div>
              <span>Top KOL</span>
              <h4>贡献排行</h4>
            </div>
          </header>
          <div className="vkpi-campaign-top-kols">
            {analytics.topRows.map((row) => (
              <div key={row.id}>
                <Avatar name={row.kolName || row.kolHandle} src={row.kolAvatar} size="sm" />
                <div>
                  <strong>{row.kolHandle || row.kolName}</strong>
                  <span>{formatNumber(row.views)} 曝光 · {formatMoney(row.gmv)}</span>
                  <i style={{ width: `${Math.max(6, Math.round(((row.views || 0) / maxTopViews) * 100))}%` }} />
                </div>
              </div>
            ))}
            {!analytics.topRows.length ? <div className="vkpi-campaign-analytics-empty">暂无 KOL 数据。</div> : null}
          </div>
        </section>
      </div>
    </div>
  );
}

export function CampaignFinanceTab({
  rows,
  stats,
  expenseLines,
  stageCosts,
}: {
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  expenseLines: ExpenseLine[];
  stageCosts: StageCostSummary[];
}) {
  const recordedLines = expenseLines.filter((line) => line.status === 'recorded');
  const missingLines = expenseLines.filter((line) => line.status === 'missing');
  const grossProfit = stats.gmv * 0.38;
  const netContribution = grossProfit - stats.cost;
  const costCoverage = rows.length ? Math.round((recordedLines.length / rows.length) * 100) : 0;
  const maxStageCost = Math.max(...stageCosts.map((item) => item.amount), 1);

  return (
    <div className="vkpi-campaign-finance" aria-label="项目费用">
      <div className="vkpi-campaign-finance-head">
        <div>
          <span>Campaign finance ledger</span>
          <h3>费用</h3>
          <p>当前先读取项目详情里的真实成本聚合；样品、物流、推广费拆分会在后续接入 cost ledger 明细后展开。</p>
        </div>
        <div>
          <strong>{formatPercent(costCoverage)}</strong>
          <span>成本登记覆盖率</span>
        </div>
      </div>

      <div className="vkpi-campaign-finance-totals">
        <div><span>已记录成本</span><strong>{formatMoney(stats.cost)}</strong><em>{recordedLines.length} 个 KOL 有成本记录</em></div>
        <div><span>归因销售</span><strong>{formatMoney(stats.gmv)}</strong><em>{formatNumber(stats.orders)} 单</em></div>
        <div><span>ROI</span><strong>{formatRatio(stats.roi)}</strong><em>销售 / 成本</em></div>
        <div><span>净贡献估算</span><strong className={netContribution >= 0 ? 'is-green' : 'is-red'}>{formatMoney(netContribution)}</strong><em>按 38% 毛利估算</em></div>
      </div>

      <div className="vkpi-campaign-finance-grid">
        <section className="vkpi-campaign-finance-card">
          <header>
            <div>
              <span>ROI 计算明细</span>
              <h4>用现有真实字段计算</h4>
            </div>
          </header>
          <div className="vkpi-campaign-roi-formula">
            <div><span>归因销售</span><strong>{formatMoney(stats.gmv)}</strong></div>
            <div><span>毛利估算 38%</span><strong>{formatMoney(grossProfit)}</strong></div>
            <div><span>已记录成本</span><strong>{formatMoney(stats.cost)}</strong></div>
            <div><span>净贡献</span><strong className={netContribution >= 0 ? 'is-green' : 'is-red'}>{formatMoney(netContribution)}</strong></div>
          </div>
          <p>公式：净贡献 = 归因销售 × 38% - 已记录成本；ROI = 归因销售 / 已记录成本。</p>
        </section>

        <section className="vkpi-campaign-finance-card">
          <header>
            <div>
              <span>成本完整性</span>
              <h4>缺口提示</h4>
            </div>
          </header>
          <div className="vkpi-campaign-finance-gaps">
            <div><b>{recordedLines.length}</b><span>已登记</span></div>
            <div><b>{missingLines.length}</b><span>未登记</span></div>
            <div><b>{formatMoney(rows.length ? stats.cost / rows.length : 0)}</b><span>均摊成本</span></div>
          </div>
          {missingLines.length ? (
            <div className="vkpi-campaign-finance-alert">{missingLines.length} 个 KOL 还没有成本记录，ROI 会偏高；建议后续从「成本台」或项目明细补登记。</div>
          ) : (
            <div className="vkpi-campaign-finance-ok">当前项目行都已有成本记录，可以继续核对凭证和审批状态。</div>
          )}
        </section>
      </div>

      <div className="vkpi-campaign-finance-grid">
        <section className="vkpi-campaign-finance-card is-wide">
          <header>
            <div>
              <span>阶段成本分布</span>
              <h4>看成本卡在哪个阶段</h4>
            </div>
          </header>
          <div className="vkpi-campaign-stage-costs">
            {stageCosts.map((item) => (
              <div key={item.stage}>
                <div>
                  <strong>{stageLabels[item.stage]}</strong>
                  <span>{item.count} 个 KOL · {formatMoney(item.amount)}</span>
                </div>
                <em><i style={{ width: `${Math.max(5, Math.round((item.amount / maxStageCost) * 100))}%` }} /></em>
              </div>
            ))}
          </div>
        </section>

        <section className="vkpi-campaign-finance-card">
          <header>
            <div>
              <span>费用口径</span>
              <h4>当前版本说明</h4>
            </div>
          </header>
          <div className="vkpi-campaign-finance-notes">
            <p>已接入：项目行 `cost`、`gmv`、`orders`、`roi`。</p>
            <p>未接入：样品成本、物流、现金推广、凭证审批的独立明细。</p>
            <p>后续接入 cost ledger 后，这里会拆成费用分类和凭证表。</p>
          </div>
        </section>
      </div>

      <section className="vkpi-campaign-finance-card">
        <header>
          <div>
            <span>KOL 费用明细</span>
            <h4>按当前项目行展开</h4>
          </div>
        </header>
        <div className="vkpi-campaign-expense-table">
          <div className="vkpi-campaign-expense-row is-head">
            <span>KOL</span>
            <span>平台</span>
            <span>阶段</span>
            <span>成本</span>
            <span>销售</span>
            <span>ROI</span>
            <span>状态</span>
          </div>
          {expenseLines.map((line) => (
            <div className="vkpi-campaign-expense-row" key={line.id}>
              <span><b>{line.kolHandle || line.kolName}</b><small>{line.kolName || '-'}</small></span>
              <span><PlatformPill platform={line.platform} /></span>
              <span>{stageLabels[line.stage]}</span>
              <span>{formatMoney(line.amount)}</span>
              <span>{formatMoney(line.revenue)}</span>
              <span>{formatRatio(line.roi)}</span>
              <span className={line.status === 'recorded' ? 'is-recorded' : 'is-missing'}>{line.status === 'recorded' ? '已登记' : '未登记'}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

export function CampaignMaterialsTab({
  project,
  rows,
  stats,
  onCopy,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  onCopy: (text: string, label: string) => Promise<void>;
}) {
  const platforms = Array.from(new Set(rows.map((row) => row.platform).filter(Boolean)));
  const productName = project.productName || project.campaign || '未设置';
  const productSku = project.productSku || '未设置';
  const marketplace = project.marketplace || '未设置';
  const projectLinks = rows
    .map((row) => row.shopifyLink)
    .filter((value): value is string => Boolean(value && value.trim()));
  const primaryLink = project.shopifyLink || projectLinks[0] || '';
  const briefText = [
    `推广：${project.campaign || '未命名推广'}`,
    `产品：${productName}`,
    `SKU：${productSku}`,
    `平台：${platforms.join(' / ') || project.platform || '-'}`,
    `市场 / 店铺：${marketplace}`,
    primaryLink ? `商品 / 归因链接：${primaryLink}` : '商品 / 归因链接：未设置',
    `参与 KOL：${rows.length}`,
    `当前曝光：${formatNumber(stats.views)}`,
    '',
    '发布要求：请按项目沟通内容执行；如有合同或 brief PDF，以归档文件为准。',
  ].join('\n');
  const distributionText = rows.map((row, index) => [
    `${index + 1}. ${row.kolHandle || row.kolName}`,
    row.platform,
    stageLabels[row.stage],
    row.shopifyLink ? `link=${row.shopifyLink}` : 'link=未设置',
  ].join(' · ')).join('\n');
  const readyRows = rows.filter((row) => stageIndex(row.stage) >= stageIndex('agreed'));
  const linkReadyRows = rows.filter((row) => Boolean(row.shopifyLink));
  const pendingLinkRows = rows.filter((row) => !row.shopifyLink);

  return (
    <div className="vkpi-campaign-materials" aria-label="项目物料">
      <div className="vkpi-campaign-materials-head">
        <div>
          <span>Campaign material hub</span>
          <h3>物料</h3>
          <p>先把当前项目已有字段整理成可发给 KOL 的 brief、商品链接和名单清单；文件上传库后续接入 campaign materials。</p>
        </div>
        <div>
          <strong>{linkReadyRows.length}/{rows.length}</strong>
          <span>链接就绪</span>
        </div>
      </div>

      <div className="vkpi-campaign-materials-grid">
        <section className="vkpi-campaign-material-card is-brief">
          <header>
            <div>
              <span>Campaign Brief</span>
              <h4>{project.campaign || '未命名推广'}</h4>
            </div>
            <button type="button" onClick={() => void onCopy(briefText, 'Campaign Brief')}>复制 Brief</button>
          </header>
          <div className="vkpi-campaign-brief-fields">
            <div><span>产品</span><strong>{productName}</strong></div>
            <div><span>SKU</span><strong>{productSku}</strong></div>
            <div><span>市场 / 店铺</span><strong>{marketplace}</strong></div>
            <div><span>平台</span><strong>{platforms.join(' / ') || project.platform || '-'}</strong></div>
          </div>
          <div className="vkpi-campaign-brief-text">
            {briefText.split('\n').map((line, index) => <p key={`${line}-${index}`}>{line || '\u00a0'}</p>)}
          </div>
        </section>

        <section className="vkpi-campaign-material-card">
          <header>
            <div>
              <span>发放状态</span>
              <h4>物料准备度</h4>
            </div>
          </header>
          <div className="vkpi-campaign-material-readiness">
            <div><b>{rows.length}</b><span>参与 KOL</span></div>
            <div><b>{readyRows.length}</b><span>已到合作/发货后</span></div>
            <div><b>{linkReadyRows.length}</b><span>已设置链接</span></div>
            <div><b>{pendingLinkRows.length}</b><span>待补链接</span></div>
          </div>
          {pendingLinkRows.length ? (
            <div className="vkpi-campaign-material-alert">{pendingLinkRows.length} 个 KOL 还没有归因链接。可在「参与 KOL」展开行里保存 Shopify 链接。</div>
          ) : (
            <div className="vkpi-campaign-material-ok">当前所有 KOL 都已有可用链接。</div>
          )}
        </section>
      </div>

      <div className="vkpi-campaign-materials-grid">
        <section className="vkpi-campaign-material-card">
          <header>
            <div>
              <span>共享素材库</span>
              <h4>文件区状态</h4>
            </div>
          </header>
          <div className="vkpi-campaign-material-library">
            <div><strong>Brief PDF</strong><span>未接入上传表</span></div>
            <div><strong>产品图 / 视频</strong><span>未接入上传表</span></div>
            <div><strong>Logo / LUT</strong><span>未接入上传表</span></div>
          </div>
          <p>这里不放假上传按钮。下一步接 `campaign_materials` 后再开放上传、下载和使用记录。</p>
        </section>

        <section className="vkpi-campaign-material-card is-wide">
          <header>
            <div>
              <span>KOL 发放清单</span>
              <h4>按当前项目行生成</h4>
            </div>
            <button type="button" onClick={() => void onCopy(distributionText, 'KOL 发放清单')}>复制清单</button>
          </header>
          <div className="vkpi-campaign-material-table">
            <div className="vkpi-campaign-material-row is-head">
              <span>KOL</span>
              <span>平台</span>
              <span>阶段</span>
              <span>链接</span>
            </div>
            {rows.map((row) => (
              <div className="vkpi-campaign-material-row" key={row.id}>
                <span><b>{row.kolHandle || row.kolName}</b><small>{row.kolName || '-'}</small></span>
                <span><PlatformPill platform={row.platform} /></span>
                <span>{stageLabels[row.stage]}</span>
                <span className={row.shopifyLink ? 'is-ready' : 'is-missing'}>{row.shopifyLink ? '已设置' : '未设置'}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
