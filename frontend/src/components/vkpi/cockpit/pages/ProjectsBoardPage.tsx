import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { ErrorCard, LoadingLine, ModuleCard, PendingCard, type Row } from "./MarketVoicePage.modules";
import { ModuleProvModal } from "./MarketVoicePage.dialogs";
import { ContentSchedBody, PostRowLine } from "./LaunchPadBoardPage.ops";
import { PostDetailModal, PostListModal } from "./LaunchPadBoardPage.dialogs";
import { StagesBody } from "./LaunchPadBoardPage.ops";
import { useContentReview, usePublishActions } from "./LaunchPadBoardPage.actions";
import { getStagesSummary, listContentPosts } from "../../../../services/vkpi/launchBoard-api";
import {
  centsToUsd,
  fmtUsd2,
  listAllObservationWindows,
  listSalesAttributions,
  sumRevenueUsd,
} from "../../../../services/vkpi/projectsBoard-api";
import { getBoardSeries } from "../../../../services/vkpi/boardSeries-api";
import { useEndpoint, useProjectsCrudActions, useRetroAdvance, type RetroReceipt } from "./ProjectsBoardPage.actions";
import {
  AttributionBody,
  AttributionRowLine,
  CostsBody,
  ProjectsKpiBand,
  WindowRowLine,
  WindowsBody,
  buildCampaignGroupsLite,
  confidencePill,
  countInFlightKols,
  windowStatusPill,
} from "./ProjectsBoardPage.charts";
import { CampaignWallEmbed, DueEmbed, MODULE_SOURCES, PROV_TITLES } from "./ProjectsBoardPage.embeds";
import { MiniDetailModal, MiniListModal, ProjectCreateModal } from "./ProjectsBoardPage.dialogs";
import { ImportKolListModal, ProjectDetailError, ProjectDetailSkeleton, ProjectFocusBanner } from "../../pages/ProjectsPage.Sections";
import { PageShell } from "../../pages/PageShell";
import { ProjectDetailView } from "../../pages/projects/ProjectDetailView";
import { useProjectDetail } from "../../hooks/useProjectDetail";
import { useAuth } from "../../../../hooks/useAuth";
import { clearProjectFocus, readProjectFocus, type IntelligenceProjectFocusPayload } from "../../intelligence/intelligenceProjectFocus";
import { formatLocal } from "../../lib/timeLocal";
import type { ProjectsPageProps } from "../../pages/ProjectsPage.types";
import "../../pages/projects/projectBoard.css";

// Projects → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage 五件套
//   embeds 收编 + LaunchPadBoardPage 闭环动作 1:1 同构)。
//   旧 ProjectsPage 全功能零丢失搬家:
//   · 项目卡片墙(筛选/搜索/排序/重点星标/暂停跟进/健康度启发式四卡/新建/导入)=
//     CampaignWallEmbed 非侵入收编(旧组件零改动,营销文案按门面纪律隐藏);
//   · 履约待办 due-list = DueEmbed 收编,点行直开项目详情;
//   · 项目详情(9 阶段推进/参与 KOL/数据汇总/物料/费用/合同归档/复盘/时间轴/任务坞/
//     物流横幅/履约观测面板/编辑/取消/删除/加 KOL/合同生成/成本录入/导出)=
//     detailProjectId 分支整页渲染旧 ProjectDetailView(零改动,回滚垫=旧 ProjectsPage);
//   · 新建推广表单(launch 分支/SKU 目录带价)= ProjectCreateModal(dialogs,逻辑原样);
//   · 导入 KOL 名单 = ImportKolListModal 直接复用 + 匹配逻辑搬 actions。
//   新增看板模块(全真数据):KPI 带四卡(进行中项目/在项 KOL/本窗发布/归因销售)/
//   履约阶段漏斗(BarRow 归一读侧)/ 内容匹配(行内 ✓/✕ 复核 + 推进复盘闭环)/
//   观察窗口 / 归因销售 / 成本概览(palette)。
//   数据源(全真,零编造):
//     props.data/filteredProjects(CockpitApp /dashboard 行)         —— 卡片墙/KPI 分组/成本
//     GET /api/admin/vkpi/projects/deliverable-stages-summary        —— 阶段漏斗(归一读侧)
//     GET /api/admin/vkpi/projects/content-posts?status=all          —— 内容匹配 + 本窗发布
//     GET /api/admin/vkpi/projects/observation-windows?status=all    —— 观察窗口
//     GET /api/admin/vkpi/attribution                                —— 归因销售(美分→美元带单测)
//     GET /api/admin/vkpi/projects/due-list                          —— 履约待办(内嵌自取)
//   闭环动作(端点真实返回才置绿,gone 不写 ✓):PATCH content-posts 复核 /
//   POST publish approve|reschedule|remind / POST advance-retrospective 推进复盘。
// 红线:纯展示 + 白名单动作端点,绝不写 viltrox fit 分 / 不触 rule_v0;颜色全 token
//   零写死色;禁 opacity 修饰类;卡面零内部术语(口径全进 SrcChip/溯源弹窗);时间绝对
//   时间戳(存 UTC 按浏览器时区);金额美分→美元唯一换算点带单测(历史 100 倍缺陷);
//   布局只走本机 storageKey,不传 apiToken 给 EditableDashboardBoard(其账户级持久化
//   写死 dashboard_layout_v1 键 —— 金样板同注释)。

const STORAGE_KEY = "vkpi-projects-layout-v1";

// 默认布局(12 列 · 默认简四行):kpiP(12) → due(8)+funnel(4) → wall(12)
// → content(8)+windows(4);palette 备选:attr / costs
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiP", span: 12 },
  { moduleKey: "dueP", span: 8 },
  { moduleKey: "funnelP", span: 4 },
  { moduleKey: "wallP", span: 12 },
  { moduleKey: "contentP", span: 8 },
  { moduleKey: "windowsP", span: 4 },
];

const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

export function ProjectsBoardPage({
  data,
  filteredProjects,
  selectedProjectId,
  selectedProject,
  openProjectId,
  onConsumeOpenProject,
  viewMode,
  onOpenKolProfile,
  onOpenStaffProfile,
  onLookupKol,
  onCreateProject,
  onUpdateProject,
  onMoveProjectStage,
  onDeleteProject,
  onAddProjectCost,
  onUpsertProjectTerms,
  onAddProjectShipment,
  onUploadEvidenceFile,
  onSelectPage,
  onToggleView,
  onRefreshData,
  apiToken,
  embeddedModuleKey,
}: ProjectsPageProps) {
  // 履约待办卡片消费 admin 端点,token 优先用页面下传的 apiToken,缺失时回退到 useAuth(旧页同款)。
  const { token: authToken } = useAuth();
  const dueListToken = apiToken || authToken || undefined;
  const token = apiToken || "";

  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);
  const [message, setMessage] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [importOpen, setImportOpen] = React.useState(false);
  const [detailProjectId, setDetailProjectId] = React.useState<string | null>(null);
  const [projectFocus, setProjectFocus] = React.useState<IntelligenceProjectFocusPayload | null>(null);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  // 行模块弹窗状态(全量列表 / 单条详情连续翻)
  const [postIdx, setPostIdx] = React.useState<number | null>(null);
  const [postListOpen, setPostListOpen] = React.useState(false);
  const [windowIdx, setWindowIdx] = React.useState<number | null>(null);
  const [windowListOpen, setWindowListOpen] = React.useState(false);
  const [attrIdx, setAttrIdx] = React.useState<number | null>(null);
  const [attrListOpen, setAttrListOpen] = React.useState(false);

  React.useEffect(() => {
    if (openProjectId) {
      setDetailProjectId(openProjectId);
      // 消费即回执:宿主(CockpitApp)清空 openLegacyProjectId 管道,
      // 同一项目二次 ⌘K/泳道点开时 id 再变 → effect 再触发(此前永不清空 = 死点击)。
      onConsumeOpenProject?.();
    }
  }, [openProjectId, onConsumeOpenProject]);

  // 智能中心 / 红人搜索焦点(旧页同款:读一次即清,命中即开详情)
  React.useEffect(() => {
    const focus = readProjectFocus();
    if (!focus) return;
    clearProjectFocus();
    setProjectFocus(focus);
    setMessage(focus.summary);
  }, []);
  React.useEffect(() => {
    if (!projectFocus) return;
    const target = filteredProjects.find(
      (project) => (projectFocus.projectId && project.id === projectFocus.projectId) || project.campaign === projectFocus.projectName,
    );
    if (target) setDetailProjectId(target.id);
  }, [filteredProjects, projectFocus]);

  // 旧页写动作原样搬家(actions 层)
  const crud = useProjectsCrudActions({ apiToken, data, onLookupKol, onDeleteProject, onRefreshData, setMessage, setBusy });

  // 闭环动作 hooks(端点真实返回才置绿;version 自增 → 重拉服务器真数)
  const review = useContentReview(token);
  const publish = usePublishActions(token);
  const retro = useRetroAdvance(token);

  /* ---------- 只读端点(逐源独立装载,单源失败不拖累) ---------- */
  const stagesResp = useEndpoint(
    token,
    reloadTick + review.version + retro.version,
    React.useCallback((t: string) => getStagesSummary(t), []),
  );
  const postsResp = useEndpoint(
    token,
    reloadTick + review.version + retro.version,
    React.useCallback((t: string) => listContentPosts(t, "all"), []),
  );
  const windowsResp = useEndpoint(
    token,
    reloadTick + review.version,
    React.useCallback((t: string) => listAllObservationWindows(t, "all"), []),
  );
  const attrResp = useEndpoint(
    token,
    reloadTick,
    React.useCallback((t: string) => listSalesAttributions(t), []),
  );
  // KPI 卡趋势线(挂账迸发①):board-series 按日真序列;失败 = KPI 卡照旧 spempty 诚实虚线
  const kpiSeriesResp = useEndpoint(
    token,
    reloadTick + review.version + retro.version,
    React.useCallback((t: string) => getBoardSeries(t, { board: "projects" }), []),
  );

  const posts = React.useMemo(() => (Array.isArray(postsResp.data?.items) ? (postsResp.data!.items as Row[]) : null), [postsResp.data]);
  const windows = React.useMemo(() => (Array.isArray(windowsResp.data?.items) ? (windowsResp.data!.items as Row[]) : null), [windowsResp.data]);
  const attributions = React.useMemo(
    () => (Array.isArray(attrResp.data?.attributions) ? (attrResp.data!.attributions as Row[]) : null),
    [attrResp.data],
  );
  // 阶段分布:同 stage 不同 stage_status 合并计数(后端已读侧归一词表;LaunchPad 同口径)
  const stagesAgg = React.useMemo(() => {
    const raw = stagesResp.data?.stages;
    if (!Array.isArray(raw)) return null;
    const merged = new Map<string, number>();
    raw.forEach((r) => merged.set(String(r.stage), (merged.get(String(r.stage)) || 0) + (Number(r.n) || 0)));
    return [...merged.entries()].map(([stage, n]) => ({ stage, n })).sort((a, b) => b.n - a.n);
  }, [stagesResp.data]);
  const stagesTotal = Number(stagesResp.data?.total) || 0;

  /* ---------- KPI 四数(全真;源失败 = pending 带原因,绝不编数) ---------- */
  const groups = React.useMemo(() => buildCampaignGroupsLite(filteredProjects), [filteredProjects]);
  const activeProjects = token ? groups.filter((group) => group.status === "进行中").length : null;
  const inFlightKols = token ? countInFlightKols(groups) : null;
  const posted30d = React.useMemo(() => {
    if (!posts) return null;
    const cutoff = Date.now() - 30 * 24 * 3600 * 1000;
    return posts.filter((p) => {
      const t = Date.parse(String(p.published_at || ""));
      return Number.isFinite(t) && t >= cutoff;
    }).length;
  }, [posts]);
  const revenueUsd = attributions ? sumRevenueUsd(attributions) : null;
  const srcNote = (error: string) => (error ? `读取失败:${error}` : "读取中…");

  const postStatus = React.useCallback((p: Row) => String(review.reviewed[Number(p.id)] ?? p.status ?? ""), [review.reviewed]);

  // 推进复盘目标:matched 帖(含本会话复核覆盖)按项目分组 → 每项目一枚真动作键
  const retroTargets = React.useMemo(() => {
    const byProject = new Map<string, { projectId: string; projectName: string; matched: number }>();
    (posts || []).forEach((p) => {
      if (postStatus(p) !== "matched") return;
      const key = String(p.project_id ?? "");
      if (!key) return;
      const cur = byProject.get(key) || { projectId: key, projectName: String(p.project_name || `项目 #${key}`), matched: 0 };
      cur.matched += 1;
      byProject.set(key, cur);
    });
    return [...byProject.values()];
  }, [posts, postStatus]);

  /* ---------- 闸(金样板同构) ---------- */
  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载项目跟进数据。
    </PendingCard>
  );
  const opsGate = (value: unknown, error: string, name: string): React.ReactNode | null => {
    if (!token) return noTokenCard;
    if (error) return <ErrorCard title={`${name} 读取失败`} text={error} />;
    if (!value) return <LoadingLine text="读取中…" />;
    return null;
  };

  /* ---------- 卡头 props(SrcChip hover 卡 + 点击溯源弹窗;动态口径进 extraRows) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const mergedRowsRef = React.useRef<Record<string, Array<[string, string]>>>({});
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    mergedRowsRef.current[key] = rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows, onOpenSrc: () => setProvKey(key) };
  };

  /* ---------- 模块 body ---------- */

  const renderKpiBand = () => {
    const extraRows: Array<[string, string]> = [
      ...(postsResp.error ? ([["本窗发布源", `读取失败:${postsResp.error}`]] as Array<[string, string]>) : []),
      ...(attrResp.error ? ([["归因源", `读取失败:${attrResp.error}`]] as Array<[string, string]>) : []),
      ...(revenueUsd != null ? ([["归因合计", `${fmtUsd2(revenueUsd)}(${attributions?.length ?? 0} 行 Σ revenue_cents ÷ 100 · 按本人可见行合计,非全表)`]] as Array<[string, string]>) : []),
      [
        "趋势线",
        "board-series?board=projects 按日真序列(30 天窗):进行中项目←新建项目/日、在项 KOL←阶段推进事件/日(两条为关联指标,不挂环比药丸)、本窗发布←发布帖/日(与卡面同口径,环比药丸同源)、归因销售←按日归因金额",
      ],
      ...(kpiSeriesResp.error
        ? ([["趋势线源", `读取失败:${kpiSeriesResp.error}(卡面虚线如实,不编时序)`]] as Array<[string, string]>)
        : []),
    ];
    return (
      <ModuleCard {...cardProps("kpiP", "项目指标带", groups.length ? `${groups.length} 组` : undefined, extraRows)}>
        {!token ? (
          noTokenCard
        ) : (
          <ProjectsKpiBand
            activeProjects={activeProjects}
            activeNote="dashboard 项目行未就绪"
            inFlightKols={inFlightKols}
            kolsNote="dashboard 项目行未就绪"
            posted30d={posted30d}
            postedNote={srcNote(postsResp.error)}
            revenueUsd={revenueUsd}
            revenueNote={srcNote(attrResp.error)}
            boardSeries={kpiSeriesResp.data ?? null}
          />
        )}
      </ModuleCard>
    );
  };

  const renderFunnel = () => (
    <ModuleCard {...cardProps("funnelP", "履约阶段漏斗", stagesAgg ? stagesTotal.toLocaleString() : undefined)}>
      {opsGate(stagesAgg, stagesResp.error, "deliverable-stages-summary") ?? <StagesBody stages={stagesAgg || []} total={stagesTotal} />}
    </ModuleCard>
  );

  const renderDue = () => (
    <DueEmbed apiToken={dueListToken} daysOverdue={7} onOpenProject={(projectId) => setDetailProjectId(projectId)} onOpenSrc={() => setProvKey("dueP")} noToken={noTokenCard} />
  );

  const renderWall = () => (
    <CampaignWallEmbed
      cnt={groups.length ? `${groups.length} 组` : undefined}
      onOpenSrc={() => setProvKey("wallP")}
      projects={filteredProjects}
      selectedProjectId={selectedProjectId}
      viewMode={viewMode}
      onOpenProjectDetail={(project) => setDetailProjectId(project.id)}
      onOpenStaffProfile={onOpenStaffProfile}
      onOpenCreateProject={onCreateProject ? () => setCreateOpen(true) : undefined}
      onOpenImportKols={apiToken && filteredProjects.length > 0 ? () => setImportOpen(true) : undefined}
      onSetFollowStatus={apiToken ? crud.setProjectFollowStatus : undefined}
      onSetProjectStar={apiToken ? crud.setProjectStar : undefined}
    />
  );

  const renderContent = () => (
    <ModuleCard
      {...cardProps(
        "contentP",
        "内容匹配",
        posts ? `${posts.length}` : undefined,
        posts ? [["视角", String(postsResp.data?.note || "own-only 由服务端裁剪")]] : [],
      )}
    >
      {opsGate(posts, postsResp.error, "content-posts") ?? (
        <div>
          <ContentSchedBody items={posts || []} total={posts?.length || 0} review={review} onOpen={(i) => setPostIdx(i)} onOpenAll={() => setPostListOpen(true)} />
          <RetroAdvanceStrip targets={retroTargets} busyProjectId={retro.busyProjectId} receipts={retro.receipts} error={retro.error} onAdvance={retro.advance} />
        </div>
      )}
    </ModuleCard>
  );

  const renderWindows = () => {
    const matchedN = (windows || []).filter((w) => String(w.status) === "matched").length;
    return (
      <ModuleCard
        {...cardProps(
          "windowsP",
          "观察窗口",
          windows ? `${windows.length}` : undefined,
          windows ? [["状态分布", `已匹配 ${matchedN} / 观察中 ${(windows || []).filter((w) => String(w.status) === "scanning").length} / 共 ${windows.length}`]] : [],
        )}
      >
        {opsGate(windows, windowsResp.error, "observation-windows") ?? (
          <WindowsBody items={windows || []} total={windows?.length || 0} onOpen={(i) => setWindowIdx(i)} onOpenAll={() => setWindowListOpen(true)} />
        )}
      </ModuleCard>
    );
  };

  const renderAttr = () => (
    <ModuleCard
      {...cardProps(
        "attrP",
        "归因销售",
        attributions ? `${attributions.length}` : undefined,
        revenueUsd != null ? [["合计", `${fmtUsd2(revenueUsd)}(Σ revenue_cents ÷ 100 · 按本人可见行,非全表)`]] : [],
      )}
    >
      {opsGate(attributions, attrResp.error, "attribution") ?? (
        <AttributionBody items={attributions || []} total={attributions?.length || 0} onOpen={(i) => setAttrIdx(i)} onOpenAll={() => setAttrListOpen(true)} />
      )}
    </ModuleCard>
  );

  const renderCosts = () => {
    const priced = groups.filter((group) => group.cost != null && (group.cost as number) > 0).length;
    return (
      <ModuleCard {...cardProps("costP", "成本概览", groups.length ? `${priced}/${groups.length} 组` : undefined)}>
        {!token ? noTokenCard : <CostsBody groups={groups} />}
      </ModuleCard>
    );
  };

  /* ---------- 模块注册表(palette 全量可选;默认简四行,高度贴内容 1 格=22px+14px gap) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiP", label: "项目指标带", description: "进行中项目 / 在项 KOL / 本窗发布 / 归因销售 · 四源真值", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "dueP", label: "履约待办", description: "已签收满 N 天未推进的项目 · 点行直开详情(内嵌)", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 5, maxHeight: 24, render: renderDue },
    { key: "funnelP", label: "履约阶段漏斗", description: "指派行按归一阶段条形 · 人次口径", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 4, maxHeight: 24, render: renderFunnel },
    { key: "wallP", label: "项目卡片墙", description: "筛选 / 搜索 / 排序 / 重点 / 暂停 / 新建 / 导入 · 全功能内嵌", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 24, minHeight: 8, maxHeight: 44, render: renderWall },
    { key: "contentP", label: "内容匹配", description: "观察窗口扫到的内容帖 · 行内 ✓/✕ 复核 + 推进复盘闭环", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 5, maxHeight: 26, render: renderContent },
    { key: "windowsP", label: "观察窗口", description: "签收后开的「等内容」窗口 · 状态/扫描/回填帖", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 4, maxHeight: 24, render: renderWindows },
    // ↓ palette 备选(不进默认布局)
    { key: "attrP", label: "归因销售", description: "订单归因行 · 金额美分→美元 · 置信徽", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 4, maxHeight: 20, render: renderAttr },
    { key: "costP", label: "成本概览", description: "项目组成本条形 Top · null=无账本链路如实", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 4, maxHeight: 20, render: renderCosts },
  ];

  /* ---------- 项目详情(旧 ProjectDetailView 整页零改动;骨架/错误三分支互斥) ---------- */
  const detailProject = React.useMemo(() => {
    if (!detailProjectId) return undefined;
    return filteredProjects.find((project) => project.id === detailProjectId);
  }, [detailProjectId, filteredProjects]);
  const projectDetailState = useProjectDetail({ apiToken, projectId: detailProjectId, fallbackProject: detailProject });

  // SKU / 产品名 → 单价(USD),供费用 tab 在 ledger 无产品成本行时做估算(旧页同款)
  const productUnitCosts = React.useMemo(() => {
    const map: Record<string, number> = {};
    data.productCosts
      .filter((item) => item.active !== false)
      .forEach((item) => {
        const cost = Number(item.unitCost) || 0;
        if (!cost) return;
        if (item.productSku) map[item.productSku] = cost;
        if (item.productName) map[item.productName.toLowerCase()] = cost;
      });
    return map;
  }, [data.productCosts]);

  const refreshProjectData = async () => {
    await projectDetailState.refresh();
    // dashboard 全量刷新转后台:详情已刷即解锁交互,不再阻塞等全站数据(旧页同款)。
    void onRefreshData?.();
  };

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="Projects" />;
  }

  if (detailProjectId) {
    const detailProjectForView = projectDetailState.project;
    const focusMatched = Boolean(
      projectFocus &&
        ((projectFocus.projectId && detailProjectId === projectFocus.projectId) ||
          (detailProjectForView && detailProjectForView.campaign === projectFocus.projectName) ||
          (detailProject && detailProject.campaign === projectFocus.projectName)),
    );
    return (
      <PageShell title="项目详情" description="按项目维度查看 KOL 阶段、数据、物流、证据和今日提醒。" hideHeading>
        {projectFocus && focusMatched ? <ProjectFocusBanner focus={projectFocus} matched onDismiss={() => setProjectFocus(null)} /> : null}
        {projectDetailState.loading && !projectDetailState.detail && !detailProjectForView ? (
          <ProjectDetailSkeleton onBack={() => setDetailProjectId(null)} />
        ) : null}
        {!projectDetailState.loading && projectDetailState.error && !detailProjectForView ? (
          <ProjectDetailError message={projectDetailState.notFound ? "项目不存在" : projectDetailState.error} onBack={() => setDetailProjectId(null)} />
        ) : null}
        {detailProjectForView ? (
          <ProjectDetailView
            key={detailProjectForView.id}
            apiToken={apiToken}
            detail={projectDetailState.detail}
            project={detailProjectForView}
            projects={filteredProjects.map((project) => (project.id === detailProjectForView.id ? detailProjectForView : project))}
            participatingRows={projectDetailState.participatingRows}
            assignmentPage={projectDetailState.assignmentPage}
            loadingMoreAssignments={projectDetailState.loadingMoreAssignments}
            assignmentLoadError={projectDetailState.assignmentLoadError}
            onLoadMoreAssignments={projectDetailState.loadMoreAssignments}
            costRows={projectDetailState.detail?.costs || []}
            productUnitCosts={productUnitCosts}
            staff={data.staffMembers}
            viewMode={viewMode}
            onBack={() => setDetailProjectId(null)}
            onOpenKolProfile={onOpenKolProfile}
            onOpenStaffProfile={onOpenStaffProfile}
            onMoveProjectStage={onMoveProjectStage}
            onUpdateProject={onUpdateProject}
            onAddProjectCost={onAddProjectCost}
            onUpsertProjectTerms={onUpsertProjectTerms}
            onAddProjectShipment={onAddProjectShipment}
            onUploadEvidenceFile={onUploadEvidenceFile}
            kolOptions={data.kolOptions}
            onLoadAvailableKols={apiToken ? crud.loadAvailableKols : undefined}
            onAddKolsToCampaign={apiToken ? crud.addKolsToCampaign : undefined}
            onProjectUpdated={refreshProjectData}
            onDeleteProject={onDeleteProject ? crud.deleteProjectRow : undefined}
            onAdvanceProjectKol={apiToken ? crud.advanceProjectKolRow : undefined}
            onUpdateProjectKolShipping={apiToken ? crud.updateProjectKolShippingRow : undefined}
            onSubmitProjectKolActionStub={apiToken ? crud.submitProjectKolStub : undefined}
            onSetFollowStatus={apiToken ? crud.setProjectFollowStatus : undefined}
            onSelectPage={onSelectPage}
            onToggleView={onToggleView}
          />
        ) : null}
        {message ? <div className="vkpi-inline-message">{message}</div> : null}
      </PageShell>
    );
  }

  const postItem = postIdx != null && posts ? posts[postIdx] : null;
  const windowItem = windowIdx != null && windows ? windows[windowIdx] : null;
  const attrItem = attrIdx != null && attributions ? attributions[attrIdx] : null;

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 项目组徽 + 实时辉光点 + 新建 + 刷新 + 编辑布局 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">项目跟进</span>
          {groups.length > 0 && <span className={PH_BADGE}>{groups.length} 项目组</span>}
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            实时
          </span>
          {onCreateProject ? (
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="flex items-center gap-1.5 rounded-xl border border-accent bg-accent-soft px-3 py-2 text-[12px] text-accent transition-colors hover:border-accent-hover"
            >
              ✦ 新建推广
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => {
              setReloadTick((tick) => tick + 1);
              if (onRefreshData) void onRefreshData();
            }}
            className="flex items-center gap-1.5 rounded-xl border border-line bg-card px-3 py-2 text-[12px] text-muted transition-colors hover:text-ink"
          >
            <RefreshCw size={13} />
            <span>刷新数据</span>
          </button>
          <button
            type="button"
            onClick={() => setEditing((value) => !value)}
            aria-pressed={editing}
            className={`vkpi-layout-edit-button flex items-center gap-1.5 ${editing ? "is-active" : ""}`}
          >
            <PencilLine size={13} />
            <span>{editing ? "完成布局" : "编辑布局"}</span>
          </button>
        </div>
      </div>

      {projectFocus ? <ProjectFocusBanner focus={projectFocus} matched={Boolean(detailProjectId)} onDismiss={() => setProjectFocus(null)} /> : null}
      {!token && <div className="mb-3">{noTokenCard}</div>}
      {message ? <div className="vkpi-inline-message">{message}</div> : null}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 新建推广(旧表单原样;成功文案/错误经 onMessage 落 inline message) */}
      {createOpen ? (
        <ProjectCreateModal
          data={data}
          apiToken={apiToken}
          defaultOwnerName={selectedProject?.ownerName || data.staffMembers[0]?.name || ""}
          onClose={() => setCreateOpen(false)}
          onCreateProject={onCreateProject}
          onMessage={setMessage}
        />
      ) : null}

      {/* 导入 KOL 名单(旧弹窗直接复用;成功才关,失败留在弹窗改名单) */}
      {importOpen ? (
        <ImportKolListModal
          projects={filteredProjects}
          selectedProject={selectedProject || filteredProjects[0]}
          busy={busy}
          onClose={() => setImportOpen(false)}
          onSubmit={async (project, rows) => {
            try {
              await crud.importKolRows(project, rows);
              setImportOpen(false);
            } catch {
              /* 失败文案已落 message,弹窗留在原地 */
            }
          }}
        />
      ) : null}

      {/* 内容匹配全量列表 + 单条详情(复核/审批动作,连续翻;LaunchPad 件复用) */}
      {postListOpen && posts && (
        <PostListModal total={posts.length} onClose={() => setPostListOpen(false)}>
          {posts.map((item, i) => (
            <PostRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={(idx) => setPostIdx(idx)} review={review} />
          ))}
        </PostListModal>
      )}
      {postItem && posts && (
        <PostDetailModal
          item={postItem}
          index={postIdx as number}
          total={posts.length}
          onNav={(i) => setPostIdx(Math.max(0, Math.min(posts.length - 1, i)))}
          onClose={() => {
            setPostIdx(null);
            review.clearError();
            publish.clearError();
          }}
          review={review}
          publish={publish}
        />
      )}

      {/* 观察窗口全量 + 详情连续翻 */}
      {windowListOpen && windows && (
        <MiniListModal title="观察窗口" total={windows.length} unit="个" onClose={() => setWindowListOpen(false)}>
          {windows.map((item, i) => (
            <WindowRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={(idx) => setWindowIdx(idx)} />
          ))}
        </MiniListModal>
      )}
      {windowItem && windows && (
        <MiniDetailModal
          title={String(windowItem.project_name || `项目 #${windowItem.project_id ?? "—"}`)}
          pill={windowStatusPill(String(windowItem.status || ""))}
          rows={[
            ["窗口", `#${windowItem.id}`],
            ["项目", `${String(windowItem.project_name || "—")}(#${windowItem.project_id ?? "—"})`],
            ["产品", String(windowItem.product_name || "—")],
            ["派单", windowItem.assignment_id != null ? `#${windowItem.assignment_id}` : "—"],
            ["KOL", windowItem.kol_pool_id != null ? `kol_pool #${windowItem.kol_pool_id}` : "—"],
            ["窗口起止", `${String(windowItem.starts_at || "—")} → ${String(windowItem.ends_at || "—")}(UTC 存)`],
            ["扫描", `×${Number(windowItem.scan_count) || 0} · 最近 ${formatLocal(windowItem.last_scan_at as string)}`],
            ["已匹配帖", windowItem.matched_content_post_id != null ? `内容帖 #${windowItem.matched_content_post_id}` : "—(等内容/未匹配)"],
          ]}
          index={windowIdx as number}
          total={windows.length}
          onNav={(i) => setWindowIdx(Math.max(0, Math.min(windows.length - 1, i)))}
          onClose={() => setWindowIdx(null)}
        />
      )}

      {/* 归因销售全量 + 详情连续翻(金额美分→美元) */}
      {attrListOpen && attributions && (
        <MiniListModal title="归因销售" total={attributions.length} unit="行" onClose={() => setAttrListOpen(false)}>
          {attributions.map((item, i) => (
            <AttributionRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={(idx) => setAttrIdx(idx)} />
          ))}
        </MiniListModal>
      )}
      {attrItem && attributions && (
        <MiniDetailModal
          title={String(attrItem.order_id || attrItem.source_ref || `归因 #${attrItem.id}`)}
          pill={confidencePill(String(attrItem.confidence || ""))}
          rows={[
            ["来源", `${String(attrItem.source_platform || "—")} · ${String(attrItem.source_ref || "—")}`],
            ["订单", String(attrItem.order_id || "—")],
            ["项目", attrItem.project_id != null ? `#${attrItem.project_id}` : "—(未挂项目)"],
            ["KOL / 负责人", `${attrItem.kol_id != null ? `kol #${attrItem.kol_id}` : "—"} · ${attrItem.staff_id != null ? `staff #${attrItem.staff_id}` : "—"}`],
            ["SKU", String(attrItem.product_sku || "—")],
            ["收入", `${fmtUsd2(centsToUsd(attrItem.revenue_cents))}(revenue_cents=${attrItem.revenue_cents ?? "—"})`],
            ["佣金", fmtUsd2(centsToUsd(attrItem.commission_cents))],
            ["归因模型", `${String(attrItem.attribution_model || "—")} · ${String(attrItem.confidence || "—")}`],
            ["发生于", `${formatLocal(attrItem.occurred_at as string)}(UTC 存 · 按浏览器时区显示)`],
            ["导入于", formatLocal(attrItem.imported_at as string)],
          ]}
          index={attrIdx as number}
          total={attributions.length}
          onNav={(i) => setAttrIdx(Math.max(0, Math.min(attributions.length - 1, i)))}
          onClose={() => setAttrIdx(null)}
        />
      )}

      {/* 模块溯源弹窗(SrcChip 点击) */}
      {provKey && (
        <ModuleProvModal
          title={PROV_TITLES[provKey] || provKey}
          caliber={mergedRowsRef.current[provKey] || srcOf(provKey).rows}
          onClose={() => setProvKey(null)}
        />
      )}
    </div>
  );
}

/* ============ 推进复盘条(履约闭环末棒;端点真实返回才显 ✓,gone 不写 ✓) ============ */
function RetroAdvanceStrip({
  targets,
  busyProjectId,
  receipts,
  error,
  onAdvance,
}: {
  targets: Array<{ projectId: string; projectName: string; matched: number }>;
  busyProjectId: string | null;
  receipts: Record<string, RetroReceipt>;
  error: string;
  onAdvance: (projectId: string) => void;
}) {
  if (targets.length === 0 && !error && Object.keys(receipts).length === 0) return null;
  return (
    <div className="mt-2">
      {targets.map((target) => {
        const receipt = receipts[target.projectId];
        const busyHere = busyProjectId === target.projectId;
        return (
          <div key={target.projectId} className="flex items-center gap-2 border-t border-line py-1.5 text-[11px]">
            <span className="min-w-0 flex-1 truncate text-ink-2">{target.projectName}</span>
            <span className="flex-none font-mono text-[9.5px] text-muted">已匹配 ×{target.matched}</span>
            {receipt ? (
              <span className="flex-none rounded-[5px] border border-good bg-good-soft px-1.5 py-px text-[9px] font-bold text-good" title={receipt.retrospectiveQueued ? "复盘聚合已排队" : "已推进(本次无新排队)"}>
                ✓ 已推进 {receipt.advancedCount} 条{receipt.retrospectiveQueued ? " · 聚合已排队" : ""}
              </span>
            ) : (
              <button
                type="button"
                disabled={busyProjectId != null}
                onClick={() => onAdvance(target.projectId)}
                title="把该项目已匹配内容帖推进复盘并排队聚合(POST advance-retrospective)"
                className="flex-none rounded-[5px] border border-line px-1.5 py-px text-[9px] text-muted transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent disabled:cursor-default"
              >
                {busyHere ? "推进中…" : "↦ 推进复盘"}
              </button>
            )}
          </div>
        );
      })}
      {error ? <div className="mt-1.5 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">推进复盘失败:{error}</div> : null}
    </div>
  );
}
