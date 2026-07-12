import React from "react";
import { ModuleCard } from "./MarketVoicePage.modules";
import { ProjectCampaignBoard } from "../../pages/projects/ProjectCampaignBoard";
import { ProjectDueListCard } from "../../pages/projects/ProjectDueListCard";
import type { VkpiProjectRow, VkpiStaffMember } from "../../vkpiTypes";

// Projects · 内嵌模块卡头收编包装族(MyKolBoardPage.embeds 同一手法)。
//   手法 = 非侵入收编:**绝不改 pages/projects 旧组件文件**,只用包装容器的 Tailwind
//   任意变体选择器隐藏营销文案/重复标题块、压平旧卡壳;功能控件(状态筛选/搜索/排序/
//   重点星标/暂停跟进/新建推广/导入名单/刷新钮/健康度四卡)全部保留可见。
//   卡头真短计数(cnt)诚实口径:
//     wall = 项目组数(page 层 buildCampaignGroupsLite 真分组,与卡片墙同口径);
//     due  = 不接(count 是内嵌组件内部 state,无外部口 —— 不摆会说谎的徽,
//            规则口径「已签收 ≥N 天未推进」记 MODULE_SOURCES.dueP)。
//   SrcChip rows = MODULE_SOURCES 唯一注册表(真实表名与页主体对齐,零第二份)。
// 红线:纯展示零网络(取数在旧组件/page 层);绝不写 fit 分/rule_v0;颜色全 token
//   零写死色;零 opacity 修饰类;数据缺席=诚实缺席(cnt 拿不到真数就不渲染)。

const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

/* ---- 收编容器选择器(MyKol embeds 同套:vkpi-embed 类 + data-embed 属性抬特异性) ---- */
const EMBED = "vkpi-embed";

// ProjectCampaignBoard:hero 左列营销文案(eyebrow span / h2 / p)按「门面零介绍文案」
// 隐藏;hero-buttons(重点项目开关/导入名单)与 health-grid(启发式四卡,卡面已如实标
// 「启发式」)保留;filter-bar / 卡片墙原样。
const WALL_TRIM = [
  "[&.vkpi-embed[data-embed]_.vkpi-project-hero>div:first-child>span]:hidden",
  "[&.vkpi-embed[data-embed]_.vkpi-project-hero>div:first-child>h2]:hidden",
  "[&.vkpi-embed[data-embed]_.vkpi-project-hero>div:first-child>p]:hidden",
].join(" ");

// ProjectDueListCard:section 自带 rounded/border/bg/mb 卡壳 → 压平(卡中卡);
// header 左块(标题「履约待办」+ 规则徽 + 副题)与新卡头双写 → 隐藏,刷新钮保留。
const DUE_TRIM = [
  "[&.vkpi-embed[data-embed]>section]:!mb-0 [&.vkpi-embed[data-embed]>section]:!rounded-none",
  "[&.vkpi-embed[data-embed]>section]:!border-0 [&.vkpi-embed[data-embed]>section]:!bg-transparent",
  "[&>section>header]:!px-0 [&>section>header]:!pt-0",
  "[&>section>header>div:first-child]:hidden",
  "[&>section>div]:!px-0",
].join(" ");

/* ============ 模块口径唯一注册表(label=真实表名;卡面零术语,口径全住这里) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiP: {
    label: "vkpi_projects · 归因/履约聚合",
    rows: [
      ["进行中项目", "vkpi_projects × assignments(dashboard 行)按状态分桶,与卡片墙同口径"],
      ["在项 KOL", "vkpi_project_kol_assignments Σ kolCount(规划中/进行中/收尾中组)"],
      ["本窗发布", "vkpi_project_content_posts.published_at ≥ 近 30 天(own-only 服务端裁)"],
      ["归因销售", "vkpi_sales_attributions Σ revenue_cents ÷ 100(美分→美元唯一换算点,带单测)"],
      ["趋势位", "无按日序列端点 → 诚实虚线零药丸(不编时序)"],
    ],
  },
  funnelP: {
    label: "vkpi_project_kol_assignments · 归一读侧",
    rows: [
      ["主表", "vkpi_project_kol_assignments(deliverable-stages-summary 读侧归一词表)"],
      ["合并", "同 stage 不同 stage_status 读侧合并计数"],
      ["口径", "指派行人次(非项目数);词表 discovered→…→closed/churned/cancelled"],
    ],
  },
  wallP: {
    label: "vkpi_projects + assignments",
    rows: [
      ["行来源", "/dashboard 项目行(vkpi_projects × vkpi_project_kol_assignments 聚合)"],
      ["分组", "同名 campaign 收拢一组;状态=paused/取消/终态/收尾/规划/进行分桶"],
      ["健康度", "前端启发式(发布占比/取消占比/阻塞加权),卡面已如实标「启发式」"],
      ["金额", "点击/订单/GMV/成本 null=无归因·账本链路显「—」,0=有链路真零"],
    ],
  },
  dueP: {
    label: "projects/due-list · vkpi_shipments",
    rows: [
      ["规则", "物流签收(delivered)满 N 天仍未推进的项目(默认 7 天)"],
      ["源表", "vkpi_shipments + vkpi_project_kol_assignments"],
      ["只读", "本卡不写库;点行打开项目详情推进"],
    ],
  },
  contentP: {
    label: "vkpi_project_content_posts",
    rows: [
      ["主表", "vkpi_project_content_posts(观察窗口扫到 + 手录内容帖)"],
      ["复核", "PATCH content-posts → matched/rejected(matched 回填窗口 matched_content_post_id)"],
      ["推进复盘", "POST /projects/{id}/content-posts/advance-retrospective(matched→retrospective_ready+排队聚合)"],
      ["纪律", "端点真实返回才置绿;非 ok / 未知形状不写 ✓"],
    ],
  },
  windowsP: {
    label: "vkpi_project_content_observation_windows",
    rows: [
      ["开窗", "物流签收(delivered)后自动开「等内容」观察窗口"],
      ["状态", "scanning / matched / expired 真状态原样"],
      ["回填", "matched_content_post_id ← 内容帖人工复核 matched"],
    ],
  },
  attrP: {
    label: "vkpi_sales_attributions",
    rows: [
      ["主表", "vkpi_sales_attributions(own-only 服务端裁剪)"],
      ["金额", "revenue_cents 美分存储 ÷ 100 展示(唯一换算点,带单测)"],
      ["置信", "confirmed / estimated / manual / unmatched 真值徽"],
    ],
  },
  costP: {
    label: "vkpi_cost_ledger(经 dashboard 行)",
    rows: [
      ["账本", "vkpi_cost_ledger → dashboard 项目行 cost 字段(项目组求和)"],
      ["口径", "null=无账本链路(不计入合计);0=有链路真零"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiP: "项目指标带",
  funnelP: "履约阶段漏斗",
  wallP: "项目卡片墙",
  dueP: "履约待办",
  contentP: "内容匹配",
  windowsP: "观察窗口",
  attrP: "归因销售",
  costP: "成本概览",
};

/* ============ 两件包装 ============ */

// 卡片墙数据来自 props(/dashboard 行),与旧页一致不设 token 闸(写动作缺 token 时
// 由调用点传 undefined → 按钮自禁用,旧组件行为原样)。
export function CampaignWallEmbed({
  cnt,
  onOpenSrc,
  ...board
}: {
  cnt?: React.ReactNode;
  onOpenSrc?: () => void;
  projects: VkpiProjectRow[];
  selectedProjectId?: string;
  viewMode: "manager" | "employee";
  onOpenProjectDetail: (project: VkpiProjectRow) => void;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onOpenCreateProject?: () => void;
  onOpenImportKols?: () => void;
  onSetFollowStatus?: (project: VkpiProjectRow, followStatus: "active" | "paused") => void | Promise<void>;
  onSetProjectStar?: (project: VkpiProjectRow, starred: boolean) => void | Promise<void>;
}) {
  return (
    <ModuleCard title="项目卡片墙" cnt={cnt} srcLabel={src("wallP").label} srcRows={src("wallP").rows} onOpenSrc={onOpenSrc}>
      <div data-embed="wall" className={`${EMBED} ${WALL_TRIM}`}>
        <ProjectCampaignBoard {...board} />
      </div>
    </ModuleCard>
  );
}

export function DueEmbed({
  apiToken,
  daysOverdue = 7,
  onOpenProject,
  onOpenSrc,
  noToken,
}: {
  apiToken?: string;
  daysOverdue?: number;
  onOpenProject: (projectId: string) => void;
  onOpenSrc?: () => void;
  noToken: React.ReactNode;
}) {
  return (
    <ModuleCard
      title="履约待办"
      srcLabel={src("dueP").label}
      srcRows={[...src("dueP").rows, ["当前阈值", `已签收 ≥ ${daysOverdue} 天未推进`]]}
      onOpenSrc={onOpenSrc}
    >
      {apiToken ? (
        <div data-embed="due" className={`${EMBED} ${DUE_TRIM}`}>
          <ProjectDueListCard apiToken={apiToken} daysOverdue={daysOverdue} onOpenProject={onOpenProject} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}
