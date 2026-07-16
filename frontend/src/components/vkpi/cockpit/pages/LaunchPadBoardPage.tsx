import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { EmptyLine, ErrorCard, LoadingLine, ModuleCard, PendingCard, type Row } from "./MarketVoicePage.modules";
import { ModuleProvModal } from "./MarketVoicePage.dialogs";
import {
  BudgetsBody,
  CoverageBody,
  ForecastBody,
  LaunchKpiBand,
  MODULE_SOURCES,
  PROV_TITLES,
  PlaybooksBody,
  ProductBody,
  RosterBody,
  ScheduleBody,
  SynergyBody,
  blockGate,
} from "./LaunchPadBoardPage.modules";
import { ApprovalsBody, ContentSchedBody, LaunchesBody, MaterialsBody, PostRowLine, StagesBody } from "./LaunchPadBoardPage.ops";
import {
  ApprovalDetailModal,
  MemberDetailModal,
  PostDetailModal,
  PostListModal,
  SynergyFullModal,
} from "./LaunchPadBoardPage.dialogs";
import { useContentReview, usePublishActions } from "./LaunchPadBoardPage.actions";
import {
  assembleLaunchPlan,
  fmtUsd,
  getPublishApprovals,
  getStagesSummary,
  listContentPosts,
  listProductLaunches,
  listSkus,
  mergeLaunchMembers,
  type SkuListItem,
} from "../../../../services/vkpi/launchBoard-api";
import { getBoardSeries } from "../../../../services/vkpi/boardSeries-api";

// 发射台 → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage 五件套 1:1 同构)。
//   旧 LaunchPadPage 全功能零丢失搬家:SKU 选择器 + 人数上限进 pagehead;一键全案六段
//   (产品概要/①名单/②预算/③排期/④打法/⑤官号协同/⑥覆盖 + 预测)各成模块,卡面一行
//   一条 + 成员详情弹窗合并六段一档(‹#n/N› 连续翻 + ↑↓);段状态分流 blockGate 原口径。
//   新增运营侧真数据模块:内容排期(vkpi_project_content_posts)/ 发布审批
//   (vkpi_publish_approvals · 迁移173)/ 发布计划(vkpi_product_launches)/ 履约阶段
//   (vkpi_project_kol_assignments 归一聚合)/ 物料覆盖(三表静态盘点,无读端点如实)。
//   KPI 带四卡 = 四源真计数(进行中计划/候选待核/已发内容/审批中),无按日序列端点 →
//   趋势位诚实虚线;闭环动作 = PATCH content-posts 复核 + POST /publish/approve|
//   reschedule|remind(actions hooks,端点真实返回才置绿)。
//   数据源(全真,零编造):
//     GET /api/admin/vkpi/sku/list                        —— SKU 选择器(SKU 360° 同源)
//     GET /api/admin/vkpi/launch/assemble                 —— 六段纯聚合(零 LLM,90s)
//     GET /api/admin/vkpi/publish/pending?status=all      —— 审批条目(0 行=已建未用如实)
//     GET /api/admin/vkpi/product-analysis/launches       —— 发布计划
//     GET /api/admin/vkpi/projects/content-posts?status=all —— 内容排期(own-only 服务端裁)
//     GET /api/admin/vkpi/projects/deliverable-stages-summary —— 履约阶段
//   动作成功(hooks version 自增)→ 重拉审批/内容/阶段端点,KPI 与列表读服务器真数。
// 红线:纯展示 + 白名单动作端点,绝不写 viltrox fit 分 / 不触 rule_v0;颜色全 token
//   零写死色;禁 opacity 修饰类;卡面零内部术语(口径全进 SrcChip/溯源弹窗);时间绝对
//   时间戳(存 UTC 按浏览器时区);布局只走本机 storageKey,不传 apiToken 给
//   EditableDashboardBoard(其账户级持久化写死 dashboard_layout_v1 键 —— 金样板同注释)。

const STORAGE_KEY = "vkpi-launchpad-layout-v1";

// 默认布局(12 列 · 五行):kpiL(12) → roster(8)+product(4) → schedule(8)+budgets(4)
// → playbooks(8)+forecast(4) → contentSched(8)+approvals(4);
// palette 备选:synergy / coverage / launches / stages / materials
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiL", span: 12 },
  { moduleKey: "roster", span: 8 },
  { moduleKey: "product", span: 4 },
  { moduleKey: "schedule", span: 8 },
  { moduleKey: "budgets", span: 4 },
  { moduleKey: "playbooks", span: 8 },
  { moduleKey: "forecast", span: 4 },
  { moduleKey: "contentSched", span: 8 },
  { moduleKey: "approvals", span: 4 },
];

const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

/** 只读端点装载(端点失败 = error 原文;version 自增 = 重拉服务器真数)。 */
function useEndpoint<T>(apiToken: string, version: number, fetcher: (token: string) => Promise<T>) {
  const [data, setData] = React.useState<T | null>(null);
  const [error, setError] = React.useState("");
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setError("");
    fetcher(apiToken)
      .then((res) => {
        if (alive) setData((res as T) ?? null);
      })
      .catch((err: any) => {
        if (alive) setError(String(err?.detail || err?.message || "读取失败"));
      });
    return () => {
      alive = false;
    };
  }, [apiToken, version, fetcher]);
  return { data, error };
}

export function LaunchPadBoardPage({ apiToken = "", onNavigate, embeddedModuleKey }: { apiToken?: string; onNavigate?: (navKey: string) => void; embeddedModuleKey?: string }) {
  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);

  /* ---------- SKU 选择器(旧页 300ms 防抖原样;/sku/list 与 SKU 360° 同源) ---------- */
  const [query, setQuery] = React.useState("");
  const [options, setOptions] = React.useState<SkuListItem[]>([]);
  const [dropdownOpen, setDropdownOpen] = React.useState(false);
  const [searching, setSearching] = React.useState(false);
  const [selectedSku, setSelectedSku] = React.useState("");
  const [maxRoster, setMaxRoster] = React.useState(8);

  React.useEffect(() => {
    if (!apiToken || !dropdownOpen) return;
    let alive = true;
    setSearching(true);
    const timer = window.setTimeout(() => {
      listSkus(apiToken, query)
        .then((res) => {
          if (alive) setOptions(Array.isArray(res.items) ? res.items : []);
        })
        .catch(() => {
          if (alive) setOptions([]);
        })
        .finally(() => {
          if (alive) setSearching(false);
        });
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [apiToken, query, dropdownOpen]);

  /* ---------- 一键全案(六段纯聚合;选 SKU / 换人数上限即重组) ---------- */
  const [plan, setPlan] = React.useState<Row | null>(null);
  const [planLoading, setPlanLoading] = React.useState(false);
  const [planError, setPlanError] = React.useState("");

  const loadPlan = React.useCallback(
    (sku: string, roster: number) => {
      if (!apiToken || !sku) return;
      setSelectedSku(sku);
      setDropdownOpen(false);
      setPlanLoading(true);
      setPlanError("");
      setPlan(null);
      assembleLaunchPlan(apiToken, sku, roster)
        .then((res) => setPlan(res && typeof res === "object" ? res : null))
        .catch((err: any) => setPlanError(String(err?.detail || err?.message || "加载失败")))
        .finally(() => setPlanLoading(false));
    },
    [apiToken],
  );

  /* ---------- 闭环动作 hooks(端点真实返回才置绿;version → 重拉服务器真数) ---------- */
  const publish = usePublishActions(apiToken);
  const review = useContentReview(apiToken);

  /* ---------- 运营侧四端点(逐源独立装载,单源失败不拖累) ---------- */
  const approvalsResp = useEndpoint(
    apiToken,
    reloadTick + publish.version,
    React.useCallback((t: string) => getPublishApprovals(t, "all"), []),
  );
  const postsResp = useEndpoint(
    apiToken,
    reloadTick + review.version,
    React.useCallback((t: string) => listContentPosts(t, "all"), []),
  );
  const launchesResp = useEndpoint(
    apiToken,
    reloadTick,
    React.useCallback((t: string) => listProductLaunches(t), []),
  );
  const stagesResp = useEndpoint(
    apiToken,
    reloadTick + review.version,
    React.useCallback((t: string) => getStagesSummary(t), []),
  );
  // KPI 卡趋势线(挂账迸发①):board-series 按日真序列;失败 = KPI 卡照旧 spempty 诚实虚线
  const kpiSeriesResp = useEndpoint(
    apiToken,
    reloadTick + review.version + publish.version,
    React.useCallback((t: string) => getBoardSeries(t, { board: "launchpad" }), []),
  );

  const posts = React.useMemo(() => (Array.isArray(postsResp.data?.items) ? (postsResp.data!.items as Row[]) : null), [postsResp.data]);
  const approvals = React.useMemo(
    () => (Array.isArray(approvalsResp.data?.items) ? (approvalsResp.data!.items as Row[]) : null),
    [approvalsResp.data],
  );
  const launches = React.useMemo(
    () => (Array.isArray(launchesResp.data?.launches) ? (launchesResp.data!.launches as Row[]) : null),
    [launchesResp.data],
  );
  // 阶段分布:同 stage 不同 stage_status 合并计数(后端已读侧归一词表)
  const stagesAgg = React.useMemo(() => {
    const raw = stagesResp.data?.stages;
    if (!Array.isArray(raw)) return null;
    const merged = new Map<string, number>();
    raw.forEach((r) => merged.set(String(r.stage), (merged.get(String(r.stage)) || 0) + (Number(r.n) || 0)));
    return [...merged.entries()].map(([stage, n]) => ({ stage, n })).sort((a, b) => b.n - a.n);
  }, [stagesResp.data]);
  const stagesTotal = Number(stagesResp.data?.total) || 0;

  const postStatus = React.useCallback((p: Row) => String(review.reviewed[Number(p.id)] ?? p.status ?? ""), [review.reviewed]);

  /* ---------- KPI 四数(全服务器真数;源失败 = pending 带原因,绝不编数) ---------- */
  const launchesActive = launches ? launches.filter((l) => String(l.status) === "active").length : null;
  const candidateCount = posts ? posts.filter((p) => postStatus(p) === "candidate").length : null;
  const postedCount = stagesAgg ? stagesAgg.find((s) => s.stage === "content_posted")?.n ?? 0 : null;
  const approvalsAvailable = approvalsResp.data ? approvalsResp.data.available !== false : null;
  const approvalsPending =
    approvals && approvalsAvailable ? approvals.filter((a) => String(a.status || "pending") === "pending").length : null;

  const srcNote = (error: string) => (error ? `读取失败:${error}` : "读取中…");

  /* ---------- 成员合并(六段 → 一人一档;详情弹窗连续翻数据面) ---------- */
  const members = React.useMemo(() => mergeLaunchMembers(plan), [plan]);
  const product: Row = plan?.product || {};
  const roster: Row = plan?.roster || {};
  const budgets: Row = plan?.budgets || {};
  const budgetTotal: Row = budgets.total || {};
  const schedule: Row = plan?.schedule || {};
  const playbooks: Row = plan?.playbooks || {};
  const synergy: Row = plan?.official_synergy || {};
  const coverage: Row = plan?.coverage || {};
  const forecast: Row = plan?.forecast || {};
  const coverageReady = coverage.status === "ready" || coverage.status === "ok";

  /* ---------- 弹窗状态 ---------- */
  const [memberIdx, setMemberIdx] = React.useState<number | null>(null);
  const [postIdx, setPostIdx] = React.useState<number | null>(null);
  const [approvalIdx, setApprovalIdx] = React.useState<number | null>(null);
  const [postListOpen, setPostListOpen] = React.useState(false);
  const [synergyOpen, setSynergyOpen] = React.useState(false);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  const gotoMember = React.useCallback((i: number) => setMemberIdx((cur) => (i >= 0 ? i : cur)), []);

  /* ---------- 闸(金样板同构:未登录 / 加载 / 端点失败 / 未选 SKU 各给诚实态) ---------- */
  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载发射台数据。
    </PendingCard>
  );
  const planGate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (planError) return <ErrorCard title="launch/assemble 读取失败" text={planError} />;
    if (planLoading) return <LoadingLine text="全案组装中(候选打分 + 逐人聚合)…" />;
    if (!selectedSku) return <EmptyLine text="页头搜索并选择 SKU → 生成全案。" />;
    if (!plan) return <LoadingLine text="全案组装中…" />;
    return null;
  };
  const opsGate = (data: unknown, error: string, name: string): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (error) return <ErrorCard title={`${name} 读取失败`} text={error} />;
    if (!data) return <LoadingLine text="读取中…" />;
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
      ...(launchesResp.error ? ([["发布计划源", `读取失败:${launchesResp.error}`]] as Array<[string, string]>) : []),
      ...(postsResp.error ? ([["内容候选源", `读取失败:${postsResp.error}`]] as Array<[string, string]>) : []),
      ...(stagesResp.error ? ([["已发内容源", `读取失败:${stagesResp.error}`]] as Array<[string, string]>) : []),
      ...(approvalsResp.error ? ([["审批源", `读取失败:${approvalsResp.error}`]] as Array<[string, string]>) : []),
      ...(approvalsAvailable === false
        ? ([["审批源", `后端未上线:${String(approvalsResp.data?.reason || "迁移173未应用")}`]] as Array<[string, string]>)
        : []),
      [
        "趋势线",
        "board-series?board=launchpad 按日真序列(30 天窗):内容候选待核←新候选帖/日(vkpi_project_content_posts 入库即候选)、审批中←新审批行/日(vkpi_publish_approvals,表未建诚实空);两条为关联指标,卡面大数是当前待办存量 → 不挂环比药丸",
      ],
      ...(kpiSeriesResp.error
        ? ([["趋势线源", `读取失败:${kpiSeriesResp.error}(卡面虚线如实,不编时序)`]] as Array<[string, string]>)
        : []),
    ];
    return (
      <ModuleCard {...cardProps("kpiL", "发布指标带", undefined, extraRows)}>
        {!apiToken ? (
          noTokenCard
        ) : (
          <LaunchKpiBand
            launchesActive={launchesActive}
            launchesNote={srcNote(launchesResp.error)}
            candidates={candidateCount}
            candidatesNote={srcNote(postsResp.error)}
            posted={postedCount}
            postedNote={srcNote(stagesResp.error)}
            approvalsPending={approvalsPending}
            approvalsNote={
              approvalsAvailable === false ? `后端未上线:${String(approvalsResp.data?.reason || "迁移173未应用")}` : srcNote(approvalsResp.error)
            }
            boardSeries={kpiSeriesResp.data ?? null}
          />
        )}
      </ModuleCard>
    );
  };

  const renderProduct = () => (
    <ModuleCard
      {...cardProps(
        "product",
        "产品概要",
        plan ? String(product.sku || selectedSku) : undefined,
        plan
          ? [
              ["生成于", `${String(plan.generated_at || "—")}(UTC)`],
              ["零 LLM", plan.llm_calls === false ? "已验证" : "—"],
            ]
          : [],
      )}
    >
      {planGate() ?? <ProductBody product={product} skuFocal={String(plan?.sku_focal || "")} />}
    </ModuleCard>
  );

  const renderRoster = () => {
    const extraRows: Array<[string, string]> = plan
      ? [
          ...(roster.basis?.resolved_query
            ? ([["匹配口径", `${roster.basis.resolved_query} → ${roster.basis.family || "—"}`]] as Array<[string, string]>)
            : []),
          ...(roster.selection_basis ? ([["选人依据", String(roster.selection_basis)]] as Array<[string, string]>) : []),
          ["人数上限", `${maxRoster} 人`],
        ]
      : [];
    return (
      <ModuleCard {...cardProps("roster", "KOL 名单", plan ? `${members.length} 人` : undefined, extraRows)}>
        {planGate() ??
          blockGate(
            members.length
              ? { status: "ready" }
              : { status: "empty", reason: roster.reason || roster.candidate_pool?.reason || "候选池为空(memory 证据不足)。" },
          ) ?? <RosterBody members={members} onOpenMember={gotoMember} />}
      </ModuleCard>
    );
  };

  const renderBudgets = () => {
    const extraRows: Array<[string, string]> =
      plan && budgetTotal.estimated_usd_p50 != null
        ? [["总额", `p50 ${fmtUsd(budgetTotal.estimated_usd_p50)} · 区间 ${fmtUsd(budgetTotal.estimated_usd_low)}–${fmtUsd(budgetTotal.estimated_usd_high)}`]]
        : [];
    return (
      <ModuleCard
        {...cardProps("budgets", "每人预算", plan && budgetTotal.estimated_usd_p50 != null ? fmtUsd(budgetTotal.estimated_usd_p50) : undefined, extraRows)}
      >
        {planGate() ?? blockGate(budgets) ?? <BudgetsBody members={members} total={budgetTotal} onOpenMember={gotoMember} />}
      </ModuleCard>
    );
  };

  const renderSchedule = () => (
    <ModuleCard
      {...cardProps(
        "schedule",
        "发布排期",
        plan && schedule.kickoff_date ? `kickoff ${schedule.kickoff_date}` : undefined,
        plan && schedule.kickoff_date ? [["kickoff", String(schedule.kickoff_date)]] : [],
      )}
    >
      {planGate() ?? blockGate(schedule) ?? <ScheduleBody members={members} onOpenMember={gotoMember} />}
    </ModuleCard>
  );

  const renderPlaybooks = () => (
    <ModuleCard {...cardProps("playbooks", "每人打法", plan ? `${members.filter((m) => m.playbook).length}` : undefined)}>
      {planGate() ?? blockGate(playbooks) ?? <PlaybooksBody members={members} onOpenMember={gotoMember} />}
    </ModuleCard>
  );

  const renderSynergy = () => {
    const items = Array.isArray(synergy.suggestions) ? synergy.suggestions : [];
    const extraRows: Array<[string, string]> =
      plan && synergy.status === "ready"
        ? [["命中", `官号 ${synergy.scanned_posts} 贴命中 ${synergy.matched_posts} 条(${synergy.scope === "sku_focal" ? "SKU 焦段" : "同品类"}口径)`]]
        : [];
    return (
      <ModuleCard {...cardProps("synergy", "官号协同", plan ? `${items.length} 条` : undefined, extraRows)}>
        {planGate() ??
          (String(synergy.status) === "generic" ? (
            <SynergyBody synergy={synergy} onOpenFull={() => setSynergyOpen(true)} />
          ) : (
            blockGate(synergy) ?? <SynergyBody synergy={synergy} onOpenFull={() => setSynergyOpen(true)} />
          ))}
      </ModuleCard>
    );
  };

  const renderCoverage = () => (
    <ModuleCard {...cardProps("coverage", "覆盖最大化", plan && coverageReady ? `${(coverage.selected || []).length} 选中` : undefined)}>
      {planGate() ?? blockGate(coverageReady ? { status: "ready" } : coverage) ?? <CoverageBody coverage={coverage} />}
    </ModuleCard>
  );

  const renderForecast = () => (
    <ModuleCard {...cardProps("forecast", "预测战绩", plan ? `${members.filter((m) => m.forecast).length} 人` : undefined)}>
      {planGate() ?? blockGate(forecast) ?? <ForecastBody members={members} onOpenMember={gotoMember} />}
    </ModuleCard>
  );

  const renderContentSched = () => (
    <ModuleCard
      {...cardProps(
        "contentSched",
        "内容排期",
        posts ? `${posts.length}` : undefined,
        posts ? [["视角", String(postsResp.data?.note || "own-only 由服务端裁剪")]] : [],
      )}
    >
      {opsGate(posts, postsResp.error, "content-posts") ?? (
        <ContentSchedBody
          items={posts || []}
          total={posts?.length || 0}
          review={review}
          onOpen={(i) => setPostIdx(i)}
          onOpenAll={() => setPostListOpen(true)}
        />
      )}
    </ModuleCard>
  );

  const renderApprovals = () => (
    <ModuleCard {...cardProps("approvals", "发布审批", approvals && approvalsAvailable ? `${approvalsPending ?? 0} 待审` : undefined)}>
      {opsGate(approvalsResp.data, approvalsResp.error, "publish/pending") ??
        (approvalsAvailable === false ? (
          <PendingCard>
            <b>审批后端未上线</b> —— {String(approvalsResp.data?.reason || "迁移173未应用")},上线后自动点亮。
          </PendingCard>
        ) : (
          <ApprovalsBody items={approvals || []} publish={publish} onOpen={(i) => setApprovalIdx(i)} />
        ))}
    </ModuleCard>
  );

  const renderLaunches = () => (
    <ModuleCard {...cardProps("launches", "发布计划", launches ? `${launches.length}` : undefined)}>
      {opsGate(launches, launchesResp.error, "product-analysis/launches") ?? <LaunchesBody launches={launches || []} />}
    </ModuleCard>
  );

  const renderStages = () => (
    <ModuleCard {...cardProps("stages", "履约阶段", stagesAgg ? stagesTotal.toLocaleString() : undefined)}>
      {opsGate(stagesAgg, stagesResp.error, "deliverable-stages-summary") ?? <StagesBody stages={stagesAgg || []} total={stagesTotal} />}
    </ModuleCard>
  );

  const renderMaterials = () => (
    <ModuleCard {...cardProps("materials", "物料覆盖", "3 源")}>
      <MaterialsBody />
    </ModuleCard>
  );

  /* ---------- 模块注册表(palette 全量可选;默认简五行,高度贴内容 1 格=22px+14px gap) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiL", label: "发布指标带", description: "进行中计划 / 候选待核 / 已发内容 / 审批中 · 四源真计数", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "roster", label: "KOL 名单", description: "候选打分排序 · 招牌拍法 + 焦段覆盖 · 点行成员详情", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 5, maxHeight: 24, render: renderRoster },
    { key: "product", label: "产品概要", description: "型号 / 类目 / 卡口 / 售价 / SKU 焦段", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 4, maxHeight: 16, render: renderProduct },
    { key: "schedule", label: "发布排期", description: "中位周期倒排 · 同周去重顺延 · 点行成员详情", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 5, maxHeight: 24, render: renderSchedule },
    { key: "budgets", label: "每人预算", description: "报价 p50 条形 · 不可估如实 · 点行成员详情", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 4, maxHeight: 20, render: renderBudgets },
    { key: "playbooks", label: "每人打法", description: "招牌拍法 top1 一行一条 · 全文进成员详情", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 4, maxHeight: 20, render: renderPlaybooks },
    { key: "forecast", label: "预测战绩", description: "预计播放 p50 条形 · p10–p90 / 互动率", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 4, maxHeight: 20, render: renderForecast },
    { key: "contentSched", label: "内容排期", description: "观察窗口扫到的内容 · 行内 ✓/✕ 复核 · 详情连续翻", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 5, maxHeight: 26, render: renderContentSched },
    { key: "approvals", label: "发布审批", description: "待审批条目 · 行内 ✓ 通过 · 重排 / 提醒", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 4, maxHeight: 20, render: renderApprovals },
    // ↓ palette 备选(不进默认布局)
    { key: "synergy", label: "官号协同", description: "官号帖命中建议 · 降级如实标", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 8, minHeight: 4, maxHeight: 20, render: renderSynergy },
    { key: "coverage", label: "覆盖最大化", description: "组合受众去重 · 淘汰名单", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 16, render: renderCoverage },
    { key: "launches", label: "发布计划", description: "计划行 · 状态 + 发布窗口", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 6, minHeight: 4, maxHeight: 16, render: renderLaunches },
    { key: "stages", label: "履约阶段", description: "指派行按归一阶段条形", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 4, maxHeight: 20, render: renderStages },
    { key: "materials", label: "物料覆盖", description: "三源盘点 · 静态标日期非实时", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16, render: renderMaterials },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="发射台" />;
  }

  const postItem = postIdx != null && posts ? posts[postIdx] : null;
  const approvalItem = approvalIdx != null && approvals ? approvals[approvalIdx] : null;
  const memberItem = memberIdx != null ? members[memberIdx] : null;

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + SKU 徽 + SKU 搜索 + 人数上限 + 刷新 + 编辑布局 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">发射台</span>
          {selectedSku && <span className={PH_BADGE}>{selectedSku}</span>}
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            实时
          </span>
          <div className="relative w-[240px] max-w-full">
            <input
              value={query}
              aria-label="搜索 SKU"
              onChange={(ev) => {
                setQuery(ev.target.value);
                setDropdownOpen(true);
              }}
              onFocus={() => setDropdownOpen(true)}
              onBlur={() => window.setTimeout(() => setDropdownOpen(false), 150)}
              placeholder="搜索 SKU / 型号…"
              className="w-full rounded-xl border border-line bg-card px-3 py-2 text-[12px] text-ink outline-none placeholder:text-muted focus:border-accent"
            />
            {dropdownOpen && (
              <div className="absolute z-30 mt-1 max-h-80 w-[320px] max-w-[80vw] overflow-auto rounded-xl border border-line bg-[var(--ds-overlay-surface)] shadow-ds">
                {searching && <div className="px-3 py-2 text-[11px] text-muted">搜索中…</div>}
                {!searching && options.length === 0 && <div className="px-3 py-2 text-[11px] text-muted">无匹配 SKU</div>}
                {!searching &&
                  options.map((opt) => (
                    <button
                      key={opt.sku}
                      type="button"
                      onMouseDown={(ev) => {
                        ev.preventDefault();
                        loadPlan(opt.sku, maxRoster);
                      }}
                      className="flex w-full items-center justify-between gap-2 border-b border-line px-3 py-2 text-left last:border-0 hover:bg-accent-soft"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[11.5px] text-ink">{opt.model_name || opt.marketing_name || opt.sku}</span>
                        <span className="block truncate font-mono text-[9.5px] text-muted">
                          {opt.sku}
                          {opt.mount ? ` · ${opt.mount}` : ""}
                          {opt.category_main ? ` · ${opt.category_main}` : ""}
                        </span>
                      </span>
                      <span className="flex-none font-mono text-[10px] text-muted">{opt.price_usd != null ? `$${opt.price_usd}` : ""}</span>
                    </button>
                  ))}
              </div>
            )}
          </div>
          <select
            value={maxRoster}
            aria-label="名单人数上限"
            onChange={(ev) => {
              const next = Number(ev.target.value) || 8;
              setMaxRoster(next);
              if (selectedSku) loadPlan(selectedSku, next);
            }}
            className="rounded-xl border border-line bg-card px-2 py-2 text-[12px] text-ink-2 outline-none focus:border-accent"
          >
            {[4, 6, 8, 10, 12].map((n) => (
              <option key={n} value={n}>
                {n} 人
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => {
              setReloadTick((tick) => tick + 1);
              if (selectedSku) loadPlan(selectedSku, maxRoster);
            }}
            className="flex items-center gap-1.5 rounded-xl border border-line bg-card px-3 py-2 text-[12px] text-muted transition-colors hover:text-ink"
          >
            <RefreshCw size={13} />
            <span>刷新数据</span>
          </button>
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            aria-pressed={editing}
            className={`vkpi-layout-edit-button flex items-center gap-1.5 ${editing ? "is-active" : ""}`}
          >
            <PencilLine size={13} />
            <span>{editing ? "完成布局" : "编辑布局"}</span>
          </button>
        </div>
      </div>

      {!apiToken && <div className="mb-3">{noTokenCard}</div>}
      {planError && (
        <div className="mb-3">
          <ErrorCard title="launch/assemble 读取失败" text={planError} />
        </div>
      )}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 成员全案详情:六段一档 + ‹#n/N› + ↑↓ 连续翻 + 打开 KOL 档案 */}
      {memberItem && (
        <MemberDetailModal
          member={memberItem}
          index={memberIdx as number}
          total={members.length}
          onNav={(i) => setMemberIdx(Math.max(0, Math.min(members.length - 1, i)))}
          onClose={() => setMemberIdx(null)}
          onNavigate={onNavigate}
        />
      )}

      {/* 内容排期全量列表(卡面 6 条之外在这里滚) */}
      {postListOpen && posts && (
        <PostListModal total={posts.length} onClose={() => setPostListOpen(false)}>
          {posts.map((item, i) => (
            <PostRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={(idx) => setPostIdx(idx)} review={review} />
          ))}
        </PostListModal>
      )}

      {/* 内容排期单条详情:复核 + 审批三动作 + 连续翻 */}
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

      {/* 审批条目详情:三动作 + 连续翻 */}
      {approvalItem && approvals && (
        <ApprovalDetailModal
          item={approvalItem}
          index={approvalIdx as number}
          total={approvals.length}
          onNav={(i) => setApprovalIdx(Math.max(0, Math.min(approvals.length - 1, i)))}
          onClose={() => {
            setApprovalIdx(null);
            publish.clearError();
          }}
          publish={publish}
        />
      )}

      {/* 官号协同全文 */}
      {synergyOpen && <SynergyFullModal synergy={synergy} onClose={() => setSynergyOpen(false)} />}

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
