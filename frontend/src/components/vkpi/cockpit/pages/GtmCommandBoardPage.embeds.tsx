import React from "react";
import { ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { MODULE_SOURCES } from "./GtmCommandBoardPage.modules";
import { HealthStrip } from "./GtmCommandPage.Command";
import { NorthStarGauges } from "../components/NorthStarGauges";
import { StrategySimPanel } from "../components/StrategySimPanel";
import { DealerFitPanel } from "../components/DealerFitPanel";
import { OfficialPlannerPanel } from "../components/OfficialPlannerPanel";
import { IndieSitePanel } from "../components/IndieSitePanel";
import { ChannelMixPanel } from "../components/ChannelMixPanel";
import type { GtmGoal } from "../../../../services/vkpi/gtmCommand-api";

// GTM Command · 复杂区块 embeds 包装族(MyKolBoardPage.embeds 同款手法)。
//   七件自取数复杂块零改动收编:HealthStrip / NorthStarGauges / StrategySimPanel /
//   Dealer / 官号计划 / 独立站 / 渠道组合 —— 旧组件文件绝不改,只用包装容器的
//   Tailwind 任意变体选择器压平旧卡壳、隐藏与新卡头重复的标题(功能控件/真数
//   徽全部保留可见)。介绍性副题一并隐藏(验收纪律:说明只进 SrcChip/tooltip),
//   其口径已逐条登记 MODULE_SOURCES,诚实信息不丢只挪位置。
//   对手三层:①旧组件自带类/内联样式;②cockpit-reference.css 换肤层
//   ([class*="bg-white/"] → var(--ds-card) !important,特异性 (0,3,0));
//   ③无内联 style= 大户。容器统一挂 vkpi-embed + data-embed,变体写成
//   [&.vkpi-embed[data-embed]>…] → (0,3,1)+!,三层全稳压(金样板同注释)。
// 红线:本文件零直连网络(取数在旧组件内部);纯读;不触 fit/rule_v0;
//   颜色全 token;零 opacity 修饰类;无 SKU 时 = 诚实 pending 卡,不摆空壳。

const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

const EMBED = "vkpi-embed";

// HealthStrip:外壳 rounded-xl border bg-panel px-3 py-1.5 压平;
// 首组「系统健康」icon+标题隐藏(与卡头双写),后台刷新脉冲点保留。
const STRIP_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!rounded-none [&.vkpi-embed[data-embed]>div]:!border-0",
  "[&.vkpi-embed[data-embed]>div]:!bg-transparent [&.vkpi-embed[data-embed]>div]:!px-0 [&.vkpi-embed[data-embed]>div]:!py-0",
  "[&>div>div:first-child>svg]:hidden [&>div>div:first-child>span:first-of-type]:hidden",
].join(" ");

// NorthStarGauges(strip 变体):外壳 rounded-2xl border bg-panel p-4 压平;
// 头部整块(icon+标题+「真库现查」注)隐藏 —— 注句已登记 MODULE_SOURCES.northstar;
// 底部「生成于 …(UTC)」绝对时间戳保留可见。
const GAUGE_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!rounded-none [&.vkpi-embed[data-embed]>div]:!border-0",
  "[&.vkpi-embed[data-embed]>div]:!bg-transparent [&.vkpi-embed[data-embed]>div]:!p-0 [&.vkpi-embed[data-embed]>div]:!shadow-none",
  "[&>div>div:first-child]:hidden",
].join(" ");

// StrategySimPanel:外壳 rounded-2xl border bg-white/[0.015] p-3(换肤层糊 !important)压平;
// 标题行 + 介绍副题两行隐藏(口径在 MODULE_SOURCES.sim);控件/结果全保留。
const SIM_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!rounded-none [&.vkpi-embed[data-embed]>div]:!border-0",
  "[&.vkpi-embed[data-embed]>div]:!bg-transparent [&.vkpi-embed[data-embed]>div]:!p-0",
  "[&>div>div:first-child]:hidden [&>div>div:nth-child(2)]:hidden",
].join(" ");

// 渠道四面板:外壳 px-5 py-3 border-b 压平;头部行里 icon + 标题 span 隐藏
// (类别徽/门店数/新鲜度点/官号帖计数等真数徽保留可见)。
const CHANNEL_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!border-0 [&.vkpi-embed[data-embed]>div]:!px-0 [&.vkpi-embed[data-embed]>div]:!py-0",
  "[&>div>div:first-child>svg]:hidden [&>div>div:first-child>span:first-of-type]:hidden",
].join(" ");

/* ============ 系统健康 / 北极星 / 模拟器 ============ */

export function HealthEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="系统健康" srcLabel={src("health").label} srcRows={src("health").rows}>
      {apiToken ? (
        <div data-embed="health" className={`${EMBED} ${STRIP_TRIM}`}>
          <HealthStrip apiToken={apiToken} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

export function NorthstarEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="90 天北极星" cnt="3 表盘" srcLabel={src("northstar").label} srcRows={src("northstar").rows}>
      {apiToken ? (
        <div data-embed="northstar" className={`${EMBED} ${GAUGE_TRIM}`}>
          <NorthStarGauges apiToken={apiToken} variant="strip" />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

export function SimEmbed({
  apiToken,
  skuHint,
  budgetHint,
  noToken,
}: {
  apiToken: string;
  /** summary.strategy_defaults 建议入口(真值;缺席不渲染行) */
  skuHint?: string;
  budgetHint?: number | null;
  noToken: React.ReactNode;
}) {
  const rows: Array<[string, string]> = skuHint
    ? [...src("sim").rows, ["建议入口", `${skuHint}${budgetHint != null ? ` · 预算参考 $${budgetHint}` : ""}`]]
    : src("sim").rows;
  return (
    <ModuleCard title="策略模拟器" srcLabel={src("sim").label} srcRows={rows}>
      {apiToken ? (
        <div>
          {skuHint ? (
            <div className="mb-2 text-[11px] text-muted">
              建议入口:{skuHint}
              {budgetHint != null ? ` · 预算参考 $${budgetHint}` : ""}
            </div>
          ) : null}
          <div data-embed="sim" className={`${EMBED} ${SIM_TRIM}`}>
            <StrategySimPanel apiToken={apiToken} />
          </div>
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

/* ============ 渠道四面板(自取数自判空;无 SKU = 诚实 pending) ============ */

function needSku(selectedSku: string): React.ReactNode | null {
  if (selectedSku) return null;
  return (
    <PendingCard>
      <b>未选 SKU</b> —— 页头选 SKU 生成作战预览后点亮。
    </PendingCard>
  );
}

export function DealerEmbed({ apiToken, selectedSku, noToken }: { apiToken: string; selectedSku: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="Dealer 适配" srcLabel={src("dealer").label} srcRows={src("dealer").rows}>
      {!apiToken
        ? noToken
        : needSku(selectedSku) ?? (
            <div data-embed="dealer" className={`${EMBED} ${CHANNEL_TRIM}`}>
              <DealerFitPanel apiToken={apiToken} sku={selectedSku} />
            </div>
          )}
    </ModuleCard>
  );
}

export function OfficialPlanEmbed({ apiToken, selectedSku, noToken }: { apiToken: string; selectedSku: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="官号内容计划" srcLabel={src("officialPlan").label} srcRows={src("officialPlan").rows}>
      {!apiToken ? (
        noToken
      ) : (
        <div data-embed="officialPlan" className={`${EMBED} ${CHANNEL_TRIM}`}>
          <OfficialPlannerPanel apiToken={apiToken} sku={selectedSku || undefined} />
        </div>
      )}
    </ModuleCard>
  );
}

export function IndieEmbed({ apiToken, selectedSku, noToken }: { apiToken: string; selectedSku: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="独立站承接" srcLabel={src("indie").label} srcRows={src("indie").rows}>
      {!apiToken
        ? noToken
        : needSku(selectedSku) ?? (
            <div data-embed="indie" className={`${EMBED} ${CHANNEL_TRIM}`}>
              <IndieSitePanel apiToken={apiToken} sku={selectedSku} />
            </div>
          )}
    </ModuleCard>
  );
}

export function MixEmbed({
  apiToken,
  selectedSku,
  budgetText,
  goal,
  noToken,
}: {
  apiToken: string;
  selectedSku: string;
  budgetText: string;
  goal: GtmGoal;
  noToken: React.ReactNode;
}) {
  const budget = Number(budgetText) > 0 ? Number(budgetText) : 3000;
  return (
    <ModuleCard title="渠道组合" srcLabel={src("mix").label} srcRows={src("mix").rows}>
      {!apiToken
        ? noToken
        : needSku(selectedSku) ?? (
            <div data-embed="mix" className={`${EMBED} ${CHANNEL_TRIM}`}>
              <ChannelMixPanel apiToken={apiToken} sku={selectedSku} budget={budget} goal={goal} />
            </div>
          )}
    </ModuleCard>
  );
}
