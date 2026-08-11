import React from "react";
import { ModuleCard } from "./MarketVoicePage.modules";
import { MODULE_SOURCES } from "./AutonomyDrivePage.modules";
import { ActionInboxPanel } from "../components/ActionInboxPanel";
import { WeeklyScorecardPanel } from "../components/WeeklyScorecardPanel";
import { MissReviewPanel } from "../components/MissReviewPanel";
import { ShadowEvalPanel } from "../components/ShadowEvalPanel";
import { AgentLoopPanel } from "../components/AgentLoopPanel";

// These evidence/review panels are non-blocking enhancements on an already-lazy
// board. Keep them behind their own runtime boundaries so
// their validation/API stacks are not pulled back into the application shell.
const ActionResultReviewQueue = React.lazy(() => import("../components/ActionResultReviewQueue").then((module) => ({
  default: module.ActionResultReviewQueue,
})));
const PredictionLedgerPanel = React.lazy(() => import("../components/PredictionLedgerPanel").then((module) => ({
  default: module.PredictionLedgerPanel,
})));
const OutreachTruthReviewQueue = React.lazy(() => import("../components/OutreachTruthReviewQueue").then((module) => ({
  default: module.OutreachTruthReviewQueue,
})));

class ReviewPanelErrorBoundary extends React.Component<
  { children: React.ReactNode; name: string },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div
        role="alert"
        aria-label={`${this.props.name} 暂不可用`}
        className="my-2 rounded border border-amber-500/20 bg-amber-500/[0.06] p-2 text-[9px] text-amber-200"
      >
        <div>{this.props.name} 暂时加载失败；其他自治模块仍可继续使用。</div>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-1 text-sky-300 underline underline-offset-2"
        >
          刷新后重试
        </button>
      </div>
    );
  }
}

export function ReviewPanelBoundary({ children, name }: {
  children: React.ReactNode;
  name: string;
}) {
  return (
    <ReviewPanelErrorBoundary name={name}>
      <React.Suspense fallback={<div className="py-2 text-[9px] text-muted">复核证据组件加载中…</div>}>
        {children}
      </React.Suspense>
    </ReviewPanelErrorBoundary>
  );
}

// 自治驾照 · 复杂区块 embeds 包装族(MyKolBoardPage.embeds 同款手法)。
//   六件自取数区块零改动收编:审批流(ActionInboxPanel,Dashboard 同款真身)+
//   旧 AutonomyBoardPage 同屏五件(预测台账 / 周度记分卡 / 低命中复盘 / 影子评测 /
//   智能体闭环)—— 旧组件文件绝不改,只用包装容器的 Tailwind 任意变体选择器压平
//   旧卡壳、隐藏与新卡头重复的 icon+标题(真数徽 / 刷新钮 / 折叠 chevron /
//   dry-run 串跑钮等功能控件全部保留可见)。
//   对手三层:①旧组件自带类(px-5 py-3 border-b / rounded-lg border bg-*);
//   ②cockpit-reference.css 换肤层([class*="bg-white/"] → var(--ds-card) !important,
//   特异性 (0,3,0));③ActionInboxPanel 的 m.div(framer-motion,类仍是静态)。
//   容器统一挂 vkpi-embed + data-embed,变体写成 [&.vkpi-embed[data-embed]>…] →
//   (0,3,1)+!,三层全稳压(金样板同注释)。
//   诚实态:台账 / 记分卡 / 闭环三件按旧约「接口失败整块安静缺席」→ 卡体如实留白,
//   口径已登记 MODULE_SOURCES(SrcChip 可查),不在卡面装数据。
// 红线:本文件零直连网络(取数在旧组件内部);绝不写 viltrox_fit_score / rule_v0;
//   新增颜色全 token 零写死色;零 token色+opacity 修饰类;数据缺席 = 诚实缺席。

const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

const EMBED = "vkpi-embed";

// ActionInboxPanel:m.div 外壳 rounded-xl border bg-panel p-4 backdrop-blur 压平;
// 头部左组的 icon + h3 大标题隐藏(与新卡头双写),条数徽 / 范围徽 / 刷新保留。
const INBOX_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!rounded-none [&.vkpi-embed[data-embed]>div]:!border-0",
  "[&.vkpi-embed[data-embed]>div]:!bg-transparent [&.vkpi-embed[data-embed]>div]:!p-0",
  "[&.vkpi-embed[data-embed]>div]:!backdrop-blur-none",
  "[&>div>div:first-child>div:first-child>svg]:hidden [&>div>div:first-child>div:first-child>h3]:hidden",
].join(" ");

// SectionFold 三件(台账 / 记分卡 / 闭环):外壳 px-5 py-3 border-b 压平;折叠头
// button 里的 icon + 标题 span 隐藏(徽章 span 与 chevron 保留,折叠功能不丢)。
const FOLD_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!border-0 [&.vkpi-embed[data-embed]>div]:!px-0 [&.vkpi-embed[data-embed]>div]:!py-0",
  "[&>div>button>svg:first-child]:hidden [&>div>button>span:first-of-type]:hidden",
].join(" ");

// 卡壳两件(低命中复盘 / 影子评测):rounded-lg border bg-* p-* 压平;头部行
// icon + 标题 span 隐藏(失败计数徽 / 低命中徽 / 刷新钮保留)。
const CARD_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!rounded-none [&.vkpi-embed[data-embed]>div]:!border-0",
  "[&.vkpi-embed[data-embed]>div]:!bg-transparent [&.vkpi-embed[data-embed]>div]:!p-0",
  "[&.vkpi-embed[data-embed]>div]:!px-0 [&.vkpi-embed[data-embed]>div]:!py-0",
  "[&>div>div:first-child>svg:first-child]:hidden [&>div>div:first-child>span:first-of-type]:hidden",
].join(" ");

/* ============ 审批流 · 今日建议(vkpi_action_inbox 状态机真身:
   通过 / 稍后 / 忽略 / 执行 + 执行台账回读,Dashboard 同款零重造) ============ */
export function ApprovalsEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="审批流 · 今日建议" srcLabel={src("approvals").label} srcRows={src("approvals").rows}>
      {apiToken ? (
        <div data-embed="approvals" className={`${EMBED} ${INBOX_TRIM}`}>
          <ActionInboxPanel apiToken={apiToken} limit={8} />
          <ReviewPanelBoundary name="执行结果复核">
            <ActionResultReviewQueue apiToken={apiToken} />
          </ReviewPanelBoundary>
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

/* ============ 预测台账(升降的证据底座;接口失败 = 安静缺席,口径在 SrcChip) ============ */
export function LedgerEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="预测台账" srcLabel={src("ledger").label} srcRows={src("ledger").rows}>
      {apiToken ? (
        <div data-embed="ledger" className={`${EMBED} ${FOLD_TRIM}`}>
          <ReviewPanelBoundary name="预测台账">
            <PredictionLedgerPanel apiToken={apiToken} />
          </ReviewPanelBoundary>
          <ReviewPanelBoundary name="外联真值复核">
            <OutreachTruthReviewQueue apiToken={apiToken} />
          </ReviewPanelBoundary>
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

/* ============ 周度记分卡(命中率周曲线 + 待对答案催办红条) ============ */
export function ScorecardEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="周度记分卡" srcLabel={src("scorecard").label} srcRows={src("scorecard").rows}>
      {apiToken ? (
        <div data-embed="scorecard" className={`${EMBED} ${FOLD_TRIM}`}>
          <WeeklyScorecardPanel apiToken={apiToken} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

/* ============ 低命中复盘(失败原因聚类 + 入记忆;palette 备选) ============ */
export function MissEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="低命中复盘" srcLabel={src("miss").label} srcRows={src("miss").rows}>
      {apiToken ? (
        <div data-embed="miss" className={`${EMBED} ${CARD_TRIM}`}>
          <MissReviewPanel apiToken={apiToken} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

/* ============ 影子评测(赢旧版才上线;palette 备选) ============ */
export function ShadowEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="影子评测" srcLabel={src("shadow").label} srcRows={src("shadow").rows}>
      {apiToken ? (
        <div data-embed="shadow" className={`${EMBED} ${CARD_TRIM}`}>
          <ShadowEvalPanel apiToken={apiToken} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

/* ============ 智能体闭环(六步链串跑留痕,卡内 dry-run 串跑钮) ============ */
export function LoopEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="闭环串跑" srcLabel={src("loop").label} srcRows={src("loop").rows}>
      {apiToken ? (
        <div data-embed="loop" className={`${EMBED} ${FOLD_TRIM}`}>
          <AgentLoopPanel apiToken={apiToken} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}
