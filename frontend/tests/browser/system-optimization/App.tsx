import React, { useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { domMax, LazyMotion } from "framer-motion";
import { ActionInboxPanel } from "../../../src/components/vkpi/cockpit/components/ActionInboxPanel";
import { GtmKpiBand, SignalsBody } from "../../../src/components/vkpi/cockpit/pages/GtmCommandBoardPage.modules";
import { CampaignPlanReadiness } from "../../../src/components/vkpi/pages/CampaignPlanReadiness";
import { CampaignPlanPresentation } from "../../../src/components/vkpi/pages/CampaignPlanPresentation";
import { I18nContext, makeT } from "../../../src/components/vkpi/cockpit/lib/i18n";
import { KpiTruthFixture } from "./KpiTruthFixture";
import { FIXTURE_TOKEN, SCENARIOS, getSnapshot, inboxSnapshot, marketSummary, planningOutput, planningReviewOutput, resetScenario, setReadFailure, subscribe, type Scenario } from "./fixture-state";
import "../../../src/styles/global.css";
import "../../../src/styles/tokens.css";
import "../../../src/styles/type-scale.css";
import "../../../src/styles/theme-alias.css";
import "../../../src/styles/ds-viz.css";
import "./fixture.css";

function Fixture() {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot);
  const scenario = SCENARIOS[snapshot.scenario];
  const summary = marketSummary();
  return <main className="fixture-shell">
    <header className="fixture-warning">
      <strong>合成数据 · 本地浏览器验收夹具</strong>
      <span>真实组件 / 假接口 / 仅内存状态。不是线上截图，不证明部署、平台连接或营销效果。</span>
    </header>
    <section className="fixture-controls" aria-label="合成场景控制">
      <label>选择验收场景 <select value={snapshot.scenario} onChange={(event) => resetScenario(event.target.value as Scenario)}>
        {Object.entries(SCENARIOS).map(([key, entry]) => <option key={key} value={key}>{entry.title}</option>)}
      </select></label>
      <button type="button" onClick={() => resetScenario(snapshot.scenario)}>重置合成场景</button>
      <p>{scenario.instruction}</p>
      {snapshot.scenario === "read_failure" ? <button type="button" onClick={() => setReadFailure(!snapshot.readsFail)}>
        {snapshot.readsFail ? "恢复合成读取（再手动刷新组件）" : "模拟读取失败（再手动刷新组件）"}
      </button> : null}
    </section>
    {snapshot.scenario === "kpi_truth" ? <KpiTruthFixture key={snapshot.epoch} /> : snapshot.scenario === "plan_review" ? <section className="fixture-panel" aria-label="真实规划审阅组件 · 合成数据">
      <h2>CampaignPlanPresentation · 合成规划草案</h2>
      <CampaignPlanReadiness output={planningReviewOutput} />
      <CampaignPlanPresentation output={planningReviewOutput} />
      <details className="fixture-raw-plan" aria-label="合成规划原始数据">
        <summary>查看原始规划 JSON</summary>
        <pre>{JSON.stringify(planningReviewOutput, null, 2)}</pre>
      </details>
    </section> : <>
    <section className="fixture-panel" aria-label="真实 GTM KPI 组件 · 合成数据">
      <h2>GtmKpiBand · 合成快照</h2>
      <GtmKpiBand summary={summary} inbox={inboxSnapshot()} inboxError="" />
    </section>
    <div className="fixture-columns">
      <section className="fixture-panel" aria-label="真实市场信号组件 · 合成数据">
        <h2>SignalsBody · 合成来源状态</h2>
        <SignalsBody summary={summary} />
        <p className="fixture-note">{summary.weekly_signals.sources_note}</p>
        {snapshot.scenario === "plan_gap" ? <CampaignPlanReadiness output={planningOutput} /> : null}
      </section>
      <section aria-label="真实 Action Inbox 组件 · 合成数据">
        <ActionInboxPanel key={snapshot.epoch} apiToken={FIXTURE_TOKEN} heading="合成数据 · 行动收件箱" limit={6} />
      </section>
    </div>
    </>}
    <details className="fixture-panel" aria-label="合成隔离回执" open>
      <summary>隔离回执：内存调用 {snapshot.events.length} 条 / 拒绝未知调用 {snapshot.denied} 次</summary>
      <p className="fixture-note">这里只记录假实现函数名；不记录 token、真实数据或请求正文。浏览器 Network 不应出现业务 API 或供应商请求。</p>
      <ol className="fixture-log">{snapshot.events.map((entry, index) => <li key={`${snapshot.epoch}-${index}`}>{entry}</li>)}</ol>
    </details>
    <footer className="fixture-note">本夹具不修改前端生产 dist，不登录，不写业务数据库，不产生供应商费用。所有数字均为合成样例。</footer>
  </main>;
}

export function mountFixture() {
  createRoot(document.getElementById("root")!).render(<I18nContext.Provider value={{ lang: "zh", setLang: () => {}, t: makeT("zh") }}><LazyMotion features={domMax}><Fixture /></LazyMotion></I18nContext.Provider>);
}
