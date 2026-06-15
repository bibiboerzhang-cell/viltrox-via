import type { CSSProperties } from 'react';
import { normalizeDiscoverPlatform, writeDiscoverFocus } from '../../intelligence/intelligenceDiscoveryFocus';
import { writeRepairRoadmapFocus } from '../../intelligence/intelligenceRepairFocus';
import {
  asList,
  asRecord,
  externalIntelligenceSources,
  numberText,
  numberValue,
  platformAdvicePlans,
  text,
  type ExternalIntelligenceSource,
  type PlatformAdvicePlan,
  type ReadinessTone,
  type Row,
} from './IntelligenceCenterModels';

function toneLabel(tone: ReadinessTone): string {
  if (tone === 'ready') return '已可读';
  if (tone === 'partial') return '部分就绪';
  return '待接入';
}

export function DailyPlanPanel({ plan }: { plan: Row }) {
  const summary = asRecord(plan.summary);
  const groups = asList(plan.groups);
  const sources = asList(plan.planned_sources);
  const plannedHttpCalls = numberValue(summary.planned_http_calls);
  const plannedItemLimit = numberValue(summary.planned_item_limit);
  const plannedGroupCount = numberValue(summary.planned_group_count);
  const blockedAutoRun = Boolean(summary.blocked_auto_run);
  return (
    <section className="vkpi-daily-plan-panel" aria-label="今日外部信号候选计划">
      <div className="vkpi-daily-plan-panel__head">
        <div>
          <span>今日外部信号计划</span>
          <h2>{groups.length ? '已生成候选来源，等待人工确认后再小口抓取' : '暂无候选来源'}</h2>
        </div>
        <em>{blockedAutoRun ? '不会自动执行' : '需复核策略'}</em>
      </div>
      <div className="vkpi-daily-plan-panel__metrics">
        <span><b>{plannedHttpCalls}</b><small>计划 HTTP</small></span>
        <span><b>{plannedItemLimit}</b><small>结果上限</small></span>
        <span><b>{plannedGroupCount}</b><small>候选分组</small></span>
        <span><b>$0</b><small>预估成本</small></span>
      </div>
      <div className="vkpi-daily-plan-panel__groups">
        {groups.slice(0, 4).map((group) => (
          <article key={text(group.source_group)}>
            <div>
              <b>{text(group.label)}</b>
              <strong>{text(group.priority)}</strong>
            </div>
            <p>{text(group.reason)}</p>
            <small>{numberText(group.planned_http_calls)} 次 HTTP · 最多 {numberText(group.planned_item_limit)} 条</small>
          </article>
        ))}
      </div>
      <div className="vkpi-daily-plan-panel__sources">
        {sources.slice(0, 8).map((source) => (
          <span key={text(source.source_key)}>
            <b>{text(source.source_key)}</b>
            <small>{text(source.provider)} · {text(source.source_type)}</small>
          </span>
        ))}
      </div>
    </section>
  );
}

export function IntelligenceSourceBoard() {
  const averageReadiness = Math.round(
    externalIntelligenceSources.reduce((total, source) => total + source.readiness, 0) / externalIntelligenceSources.length,
  );
  const readyCount = externalIntelligenceSources.filter((source) => source.tone === 'ready').length;
  const partialCount = externalIntelligenceSources.filter((source) => source.tone === 'partial').length;
  return (
    <section className="vkpi-intelligence-source-board" aria-label="智能层趋势源状态">
      <div className="vkpi-intelligence-source-board__hero">
        <div>
          <span>趋势源 / 视频智能 / 平台建议</span>
          <h2>智能层不只是等成员搜索，要主动发现 KOL、趋势、竞品和内容机会。</h2>
          <p>这里先把未来 Agent 的输入源、量化准备度和输出路径放出来；当前只读展示，不自动调用 Google、Reddit、Gemini、GPT 或 Claude。</p>
        </div>
        <div className="vkpi-intelligence-source-board__score">
          <b>{averageReadiness}%</b>
          <small>整体准备度</small>
          <i style={{ '--score': `${averageReadiness}%` } as CSSProperties} />
        </div>
      </div>

      <div className="vkpi-intelligence-source-flow" aria-label="智能层执行流">
        {['采集源', '去重归一', '视频理解', 'LLM 总结', '平台建议', '任务回流'].map((step, index) => (
          <span className={index < 2 ? 'is-done' : index === 2 ? 'is-active' : ''} key={step}>
            <i>{index + 1}</i>
            {step}
          </span>
        ))}
      </div>

      <div className="vkpi-intelligence-source-metrics">
        <span><b>{externalIntelligenceSources.length}</b><small>输入源规划</small></span>
        <span><b>{readyCount}</b><small>已可只读使用</small></span>
        <span><b>{partialCount}</b><small>部分就绪</small></span>
        <span><b>{platformAdvicePlans.length}</b><small>平台建议槽位</small></span>
      </div>

      <div className="vkpi-intelligence-source-grid">
        {externalIntelligenceSources.map((source) => (
          <article className={`vkpi-intelligence-source-card is-${source.tone}`} key={source.key}>
            <div>
              <b>{source.title}</b>
              <em>{source.statusLabel}</em>
            </div>
            <p>{source.scope}</p>
            <span>{source.cadence}</span>
            <small>{toneLabel(source.tone)}</small>
            <i style={{ '--score': `${source.readiness}%` } as CSSProperties} />
          </article>
        ))}
      </div>
    </section>
  );
}

export function PlatformAdviceMatrix() {
  return (
    <section className="vkpi-platform-advice-matrix" aria-label="平台发布建议矩阵">
      <div className="vkpi-platform-advice-matrix__head">
        <div>
          <span>Platform Advice Matrix</span>
          <h2>不同平台应该发什么，不靠感觉，后续要靠趋势、视频证据和 ROI 回流。</h2>
        </div>
        <em>中文运营视图 · 只读</em>
      </div>
      <div className="vkpi-platform-advice-matrix__grid">
        {platformAdvicePlans.map((plan) => (
          <article key={plan.key}>
            <div>
              <b>{plan.platform}</b>
              <strong>{plan.readiness}%</strong>
            </div>
            <p>{plan.bestFor}</p>
            <span><b>需要信号</b>{plan.signalNeed}</span>
            <span><b>输出</b>{plan.output}</span>
            <i style={{ '--score': `${plan.readiness}%` } as CSSProperties} />
          </article>
        ))}
      </div>
    </section>
  );
}

export function writeSourceRepairFocus(source: ExternalIntelligenceSource) {
  writeRepairRoadmapFocus({
    source: 'manual',
    key: source.key,
    title: source.title,
    statusLabel: source.statusLabel,
    statusTone: source.tone,
    readiness: `${source.readiness}%`,
    budgetPolicy: '先定义来源白名单、调用预算、失败状态和人工 ack，再允许进入自动化队列。',
    dataNeeded: `${source.scope} / ${source.evidence}`,
    output: `把 ${source.title} 转成可追溯 evidence refs，再进入 IntelligenceCard。`,
    gate: '必须有 source_url / captured_at / cost ledger / evidence drawer 回链。',
    actionLabel: '接入前检查',
    actionPage: 'intelligenceCenter',
  });
}

export function writePlatformRepairFocus(plan: PlatformAdvicePlan) {
  writeRepairRoadmapFocus({
    source: 'manual',
    key: `platform-advice-${plan.key}`,
    title: `${plan.platform} 平台建议`,
    statusLabel: '待接入趋势源',
    statusTone: 'planned',
    readiness: `${plan.readiness}%`,
    budgetPolicy: '先只读汇总已有内容和人工搜索；外部趋势、Gemini 和 LLM 必须走预算 ledger。',
    dataNeeded: plan.signalNeed,
    output: plan.output,
    gate: '必须能解释每条平台建议来自哪些帖子、视频、评论、趋势或 ROI 回流。',
    actionLabel: '平台建议接入前检查',
    actionPage: 'intelligenceCenter',
  });
}

export function writePlatformDiscoverFocus(plan: PlatformAdvicePlan) {
  writeDiscoverFocus({
    source: 'platform_advice_matrix',
    title: `${plan.platform} 红人发现任务`,
    summary: `${plan.bestFor} 需要信号：${plan.signalNeed}`,
    query: plan.query,
    platform: normalizeDiscoverPlatform(plan.platform),
    sourceLabel: '智能中心 / 平台建议矩阵',
  });
}
