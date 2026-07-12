import React from "react";
import { ModuleCard } from "./MarketVoicePage.modules";
import {
  MODULE_SOURCES,
  OfficialMatrixModule,
  TeamMatrixModule,
  useTeamStaffCards,
  type MatrixState,
} from "./MyKolBoardPage.modules";
import { DailyDigestCard } from "../../pages/myKol/DailyDigestCard";
import { RiskIndexPanel } from "../../pages/myKol/RiskIndexPanel";
import { ContributionRollupPanel } from "../../pages/myKol/ContributionRollupPanel";
import type { VkpiDashboardData } from "../../vkpiTypes";

// MY KOL · 【M6】内嵌模块卡头收编包装族(digest/team/official/risk/rollup 五件)。
//   问题:M1 起五个内嵌模块是「ModuleCard 里塞旧组件」,旧组件自带的大标题(每日学习/
//   团队矩阵/官方账号矩阵/KOL 风险指数/KOL 贡献度聚合)与新卡头标题双写,自带卡壳
//   (border/bg/shadow/padding)在 ds-mod 卡内造成卡中卡。
//   手法 = 非侵入收编:**绝不改 pages/myKol 旧组件文件**,只用包装容器的 Tailwind
//   任意变体选择器(arbitrary variants)隐藏重复标题块、压平旧卡壳;功能控件
//   (digest 窗口切换钮 / 矩阵统计 chips·分页·收起钮 / risk 已析读数·刷新 /
//   rollup 刷新)全部保留可见。旧组件用内联样式处(risk/rollup)选择器带 !(important)
//   才能压过 style=,是此处 ! 的唯一正当理由。
//   卡头真短计数(cnt)诚实口径:
//     team    = useTeamStaffCards().cards.length(与旧头「负责人」chip 同源真数);
//     official= matrix.accountCount(official-matrix account_count 真值);
//     rollup  = 窗口天数 —— 包装层持窗(旧组件 windowDays 只是初始值,故隐藏其内部
//               select,由包装层 chips 控窗 + key 重挂载整卡重取,徽数=真实取数窗口);
//     digest  = 不接(窗口天数是内嵌组件内部 state,无外部口 —— 不摆会说谎的徽,
//               真窗口切换钮保留在卡内,口径记 MODULE_SOURCES.digest);
//     risk    = 不接(已析/总在组件内部 state,无外部口;旧头右侧「已分析 X/Y(%)」
//               真读数保留可见,口径记 MODULE_SOURCES.risk)。
//   SrcChip rows = MODULE_SOURCES 同一注册表(真实表名/行数与页主体对齐,零第二份)。
// 红线:纯展示零网络(取数在旧组件/page 层);绝不写 fit 分/rule_v0;颜色全 token
//   零写死色;零 opacity 修饰类;数据缺席=诚实缺席(cnt 拿不到真数就不渲染)。

const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

/* ---- 收编容器选择器(Tailwind 任意变体) ----
   对手有三层:①旧组件自带类/内联样式;②cockpit-reference.css 换肤层
   (.vkpi-page-stage… [class*="bg-white/"] / .vkpi-page-stage--my-kol :is(.mykol-panel…)
   全是 !important,特异性最高 (0,3,0));③risk/rollup 的 style=(内联)。
   故容器统一挂 vkpi-embed 类 + data-embed 属性,变体写成
   [&.vkpi-embed[data-embed]>…] → (0,3,1)+ 且带 !,三层全稳压(不赌样式表顺序)。 */
const EMBED = "vkpi-embed";

// DailyDigestCard:section 自带 mt-4/rounded-xl/border/bg/px-4/py-3;换肤层把
// bg-white/ 重映射成 var(--ds-card)!important → 卡中卡,必须一并压平。
// 标题行只藏 icon + 「每日学习」span,范围说明 span 与窗口切换钮保留。
const DIGEST_TRIM = [
  "[&.vkpi-embed[data-embed]>section]:!mt-0 [&.vkpi-embed[data-embed]>section]:!rounded-none",
  "[&.vkpi-embed[data-embed]>section]:!border-0 [&.vkpi-embed[data-embed]>section]:!bg-transparent",
  "[&.vkpi-embed[data-embed]>section]:!px-0 [&.vkpi-embed[data-embed]>section]:!py-0",
  "[&>section>div:first-child>svg]:hidden [&>section>div:first-child>span:first-of-type]:hidden",
].join(" ");

// TeamMatrix / OfficialMatrix:.mykol-panel 卡壳(margin/border/渐变 bg/shadow/圆角;
// 换肤层再糊一层 var(--ds-card)+shadow !important)整壳压平;.mykol-section-head
// 首个 div = h2 大标题块整块隐藏(chips/分页/收起钮住第二个 div,保留);
// 头部左右 padding 收敛对齐卡体。
const MATRIX_TRIM = [
  "[&.vkpi-embed[data-embed]>.mykol-panel]:!mt-0 [&.vkpi-embed[data-embed]>.mykol-panel]:!rounded-none",
  "[&.vkpi-embed[data-embed]>.mykol-panel]:!border-0 [&.vkpi-embed[data-embed]>.mykol-panel]:!bg-none",
  "[&.vkpi-embed[data-embed]>.mykol-panel]:!bg-transparent [&.vkpi-embed[data-embed]>.mykol-panel]:!shadow-none",
  "[&_.mykol-section-head]:px-0 [&_.mykol-section-head]:pt-0",
  "[&_.mykol-section-head>div:first-child]:hidden",
].join(" ");

// RiskIndexPanel / ContributionRollupPanel:section/卡壳全内联样式(! 压 style=);
// header 首个 div = h2+副题块(无内联 display,普通 hidden 即可)。
const PANEL_TRIM = [
  "[&.vkpi-embed[data-embed]>section]:!m-0 [&.vkpi-embed[data-embed]>section]:!rounded-none",
  "[&.vkpi-embed[data-embed]>section]:!border-0 [&.vkpi-embed[data-embed]>section]:!bg-transparent",
  "[&.vkpi-embed[data-embed]>section]:!p-0",
  "[&>section>header>div:first-child]:hidden",
].join(" ");

// rollup 追加:内部窗口 label+select 隐藏(包装层持窗,双控会打架);刷新钮保留。
const ROLLUP_TRIM = `${PANEL_TRIM} [&>section>header>div:last-child>label]:hidden [&>section>header>div:last-child>select]:hidden`;

const ROLLUP_WINDOWS = [30, 90, 180, 365];

/* ============ 五件包装 ============ */

export function DigestEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="每日学习摘要" srcLabel={src("digest").label} srcRows={src("digest").rows}>
      {apiToken ? (
        <div data-embed="digest" className={`${EMBED} ${DIGEST_TRIM}`}>
          <DailyDigestCard apiToken={apiToken} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

export function TeamEmbed({
  apiToken,
  data,
  matrix,
  noToken,
}: {
  apiToken: string;
  data?: VkpiDashboardData;
  matrix: MatrixState;
  noToken: React.ReactNode;
}) {
  const { cards, pendingCount } = useTeamStaffCards(data, matrix);
  return (
    <ModuleCard
      title="团队矩阵"
      cnt={cards.length ? `${cards.length} 负责人` : undefined}
      srcLabel={src("team").label}
      srcRows={src("team").rows}
    >
      {apiToken ? (
        <div data-embed="team" className={`${EMBED} ${MATRIX_TRIM}`}>
          <TeamMatrixModule cards={cards} pendingCount={pendingCount} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

export function OfficialEmbed({
  apiToken,
  matrix,
  noToken,
}: {
  apiToken: string;
  matrix: MatrixState;
  noToken: React.ReactNode;
}) {
  return (
    <ModuleCard
      title="官方账号矩阵"
      cnt={matrix.accountCount ? `${matrix.accountCount} 账号` : undefined}
      srcLabel={src("official").label}
      srcRows={src("official").rows}
    >
      {apiToken ? (
        <div data-embed="official" className={`${EMBED} ${MATRIX_TRIM}`}>
          <OfficialMatrixModule apiToken={apiToken} matrix={matrix} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

export function RiskEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  return (
    <ModuleCard title="KOL 风险指数" srcLabel={src("risk").label} srcRows={src("risk").rows}>
      {apiToken ? (
        <div data-embed="risk" className={`${EMBED} ${PANEL_TRIM}`}>
          <RiskIndexPanel apiToken={apiToken} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}

export function RollupEmbed({
  apiToken,
  viewMode,
  noToken,
}: {
  apiToken: string;
  viewMode: "manager" | "employee";
  noToken: React.ReactNode;
}) {
  // 包装层持窗:days 既是卡头徽也是旧组件 windowDays 初始值;key 重挂载 = 整卡按新窗重取,
  // 徽数与真实取数窗口永远一致(内部 select 已藏,无第二只手改窗)。
  const [days, setDays] = React.useState(90);
  return (
    <ModuleCard
      title="贡献度聚合"
      cnt={`${days} 天`}
      srcLabel={src("rollup").label}
      srcRows={[...src("rollup").rows, ["窗口", `${days} 天 · 包装层持窗(切换即整卡重挂载重取)`]]}
    >
      {apiToken ? (
        <div>
          <div className="mb-1.5 flex flex-wrap items-center justify-end gap-1.5">
            {ROLLUP_WINDOWS.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                className={`rounded-full border px-2 py-0.5 text-[9.5px] transition-colors ${
                  days === d ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-ink"
                }`}
              >
                {d} 天
              </button>
            ))}
          </div>
          <div data-embed="rollup" className={`${EMBED} ${ROLLUP_TRIM}`}>
            <ContributionRollupPanel key={days} apiToken={apiToken} viewMode={viewMode} windowDays={days} />
          </div>
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}
