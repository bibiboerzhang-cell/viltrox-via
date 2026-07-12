import React from "react";
import { ModuleCard } from "./MarketVoicePage.modules";
import {
  MODULE_SOURCES,
  OfficialMatrixModule,
  TeamMatrixModule,
  useTeamStaffCards,
  type MatrixState,
} from "./MyKolBoardPage.modules";
import { fmtZhCompact } from "./MyKolBoardPage.charts";
import { DailyDigestCard } from "../../pages/myKol/DailyDigestCard";
import { RiskIndexPanel } from "../../pages/myKol/RiskIndexPanel";
import { ContributionRollupPanel } from "../../pages/myKol/ContributionRollupPanel";
import { OfficialContentLayer } from "../../pages/myKol/OfficialContentLayer";
import { EmployeeKolLibrary } from "../../pages/myKol/MyKolPage.Sections";
import { PendingCard } from "./MarketVoicePage.modules";
import type { OfficialChannelAccount } from "../../pages/channels/channelTypes";
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

// 【截图bug 修正 2026-07-12】官号矩阵展开(平台卡网格 + 账号列表/内容层 drill 两栏)后
// 左半大空白/右侧挤压:旧 CSS 的 .mykol-drill-grid 两列固定最小宽(280px+360px)与
// .mykol-platform-grid 固定 6 列都按**视口** ≤1200px 的媒体查询降栏——内嵌进 8-span
// 模块卡后视口 >1200 永不触发,卡宽却只有 ~600-760px,固定列宽撑爆卡宽 → 布局偏移。
// 修法 = 容器自适应列(auto-fit 最小列宽):列数随**卡宽**收放,窄卡自动叠排、宽卡
// 恢复双栏,内容永远占满卡宽(span 4~12 全档安全);只挂在包装容器上,旧组件/旧页零改动。
const OFFICIAL_GRID_FIX = [
  "[&_.mykol-platform-grid]:!grid-cols-[repeat(auto-fit,minmax(148px,1fr))]",
  "[&_.mykol-drill-grid]:!grid-cols-[repeat(auto-fit,minmax(280px,1fr))]",
  "[&_.mykol-account-list]:min-w-0 [&_.mykol-content-layer]:min-w-0",
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

/* ============ 个人矩阵分组(官号三组之外的第四组):成员个人绑定账号 + 持有人名。
   数据 = official-matrix 增量 personal 字段(官号名单之外的账号行,不混入官号
   platforms —— K4 官号粉丝合计等聚合口径零污染);行点击开合 OfficialContentLayer,
   与官号同一条 channels/{id}/posts 内容链(channel_id 通用,零新链);
   0 账号 = 诚实空态,绑定入口指真实路径 设置 → 账号授权(成员账号授权)。
   红线:纯展示零网络(数据在 matrix hook / 内容层自取);颜色全 token 零写死色。 ============ */
export function PersonalMatrixGroup({ accounts, apiToken }: { accounts: OfficialChannelAccount[]; apiToken: string }) {
  const [openId, setOpenId] = React.useState<number | null>(null);
  if (accounts.length === 0) {
    return (
      <div className="mt-2.5 rounded-[9px] border border-dashed border-line px-3 py-2.5 text-[10.5px] leading-[1.7] text-muted">
        <b className="font-semibold text-ink-2">个人矩阵 · 0 账号</b> —— 暂无个人账号。成员在 设置 → 账号授权
        绑定个人账号后自动出现在这里,数据同步后即可查看帖子与播放数据。
      </div>
    );
  }
  const open = accounts.find((account) => account.id === openId) || null;
  return (
    <div className="mt-2.5">
      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">
        个人矩阵 <b className="font-mono text-ink-2">{accounts.length}</b>
      </div>
      {accounts.map((account) => {
        const active = openId === account.id;
        return (
          <button
            key={account.id}
            type="button"
            onClick={() => setOpenId((cur) => (cur === account.id ? null : account.id))}
            title={active ? "再点收起内容层" : "点击查看该账号的帖子与播放数据"}
            className={`flex w-full items-center gap-2 rounded-[8px] border px-2 py-1.5 text-left text-[11.5px] transition-colors ${
              active ? "border-accent bg-accent-soft" : "border-transparent hover:border-line hover:bg-card"
            }`}
          >
            <span className={`min-w-0 flex-1 truncate ${active ? "text-accent" : "text-ink-2"}`}>
              {account.displayName || account.handle || "个人账号"}
              <span className="ml-1.5 text-[9.5px] text-muted">{account.platformLabel || account.platform}{account.handle ? ` · ${account.handle}` : ""}</span>
            </span>
            <span className="flex-none rounded-[6px] border border-line px-1.5 py-px text-[9px] text-muted" title="持有人(账号授权归属)">
              {account.staffName || "未分配"}
            </span>
            <span
              className="w-[104px] flex-none text-right font-mono text-[9.5px] text-muted"
              title={
                account.viewsUnavailable
                  ? `粉丝 · 内容数 · 播放(${account.viewsUnavailableReason || "该平台无公开播放口径"},显 — 不当 0)`
                  : "粉丝 · 内容数 · 播放(最新快照)"
              }
            >
              {fmtZhCompact(account.followers)} 粉 · {account.postsCount} 帖 · {account.viewsUnavailable ? "—" : fmtZhCompact(account.totalViews)}
            </span>
          </button>
        );
      })}
      {open ? (
        <div className="mt-2">
          <OfficialContentLayer account={open} apiToken={apiToken} />
        </div>
      ) : null}
    </div>
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
        <div>
          <div data-embed="official" className={`${EMBED} ${MATRIX_TRIM} ${OFFICIAL_GRID_FIX}`}>
            <OfficialMatrixModule apiToken={apiToken} matrix={matrix} />
          </div>
          <PersonalMatrixGroup accounts={matrix.personalAccounts} apiToken={apiToken} />
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

/* ============ 经典视图 · KOL 库(palette 备选,不进默认布局) ============
   旧两栏库 EmployeeKolLibrary 整体内嵌保留不删 —— 默认体验统一走新版行式 KOL 库
   (library 模块),想用升级前两栏体的从 palette 添加本卡。组件自取 aggregate
   (与新库同源),旧组件文件零改动;MATRIX_TRIM 同套收编(压平卡壳 + 隐藏
   双写大标题,头部 chips/漏斗切换等功能控件全保留)。 ============ */
export function LibClassicEmbed({
  apiToken,
  data,
  viewMode,
  noToken,
  onRefreshData,
}: {
  apiToken: string;
  data?: VkpiDashboardData;
  viewMode: "manager" | "employee";
  noToken: React.ReactNode;
  onRefreshData?: () => void;
}) {
  return (
    <ModuleCard title="经典视图 · KOL 库" srcLabel={src("libclassic").label} srcRows={src("libclassic").rows}>
      {!apiToken ? (
        noToken
      ) : !data ? (
        <PendingCard>
          <b>主控数据未就绪</b> —— 经典视图依赖主控 projects/kolOptions,就绪后自动点亮。
        </PendingCard>
      ) : (
        <div data-embed="libclassic" className={`${EMBED} ${MATRIX_TRIM}`}>
          <EmployeeKolLibrary apiToken={apiToken} data={data} viewMode={viewMode} onRefreshData={onRefreshData} />
        </div>
      )}
    </ModuleCard>
  );
}
