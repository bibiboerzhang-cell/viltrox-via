import React from "react";
import { PencilLine } from "lucide-react";
import { AnimatePresence } from "framer-motion";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { ContactModal } from "../components/modals/ContactModal";
import { KolPoolAllModal } from "../components/modals/KolPoolAllModal";
import type { ContactState } from "../lib/kolContacts";
import { ErrorCard, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { MODULE_SOURCES, NeedsBody, PoolKpiBand, QuickProjectModal } from "./KolPoolBoardPage.modules";
import { DiscoveryFunnelBody, PoolFitDistBody, PoolPlatformBody } from "./KolPoolBoardPage.charts";
import {
  CoverageEmbed,
  KindBarEmbed,
  LanesEmbed,
  RecsSection,
  SmartEmbed,
  TableEmbed,
} from "./KolPoolBoardPage.embeds";
import {
  computeKindCounts,
  dataAsOfFrom,
  deepAnalyzedStats,
  filterPoolItems,
  sortedPoolItems,
  useNeedsAnalysis,
  usePoolDrawer,
  usePoolFavorites,
  usePoolSummary,
  useQuickAddToProject,
  useRecallAvatars,
  useSmartOpeners,
  weekNewCount,
  type Row,
} from "./KolPoolBoardPage.actions";

const KOLDetailDrawer = React.lazy(() =>
  import("../components/KOLDetailDrawer").then((module) => ({ default: module.KOLDetailDrawer })),
);

// KOL 池 → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage 五件套 +
//   KolProfileBoardPage / LaunchPadBoardPage / GtmCommandBoardPage 1:1 同构)。
//   旧 KOLPoolPage.tsx 保留不删(回滚垫);功能块零丢失映射:
//   页头状态 pills(真实 API / V6 Fit 只读 / 数据截至)→ pagehead 徽 + SrcChip 口径;
//   KPIBar 五卡(总数开大窗/分类筛选/均 Fit/播放汇总/待补全灰卡)→ kinds 模块(原件内嵌,
//   点击行为原样;2026-07-12 去重复:与 kpiK 带信息重复 → 降 palette 备选「经典指标条」,
//   默认布局撤出;独有信息接棒:分类点击筛选=FilterBar kindFilter 保留原样,总数大窗
//   入口=卡片流工具行「全部 N」钮,均 Fit=「Fit 分布」直方替代,播放汇总=卡面均播 chip
//   + 备选卡保留);智能入口 SmartKolInputPanel(URL/建档/视频分析/语义召回 + 触达二段闸
//   折叠计数 + 历史条 + 发现流勾选收藏/生成话术/立项草案)→ smart 模块(embeds 包装,
//   行为一根手指未动);待分析折叠区(needs-analysis + 批量入队)→ needs 模块真身;
//   估算受众覆盖 MarketCoverageCard(缺口「去发现」联动 country+探索模式)→ coverage 模块;
//   推荐卡片流 + FilterBar 次级展开 + SearchProgressBar → recs 模块(筛选状态 page 层共享);
//   表格视图 KOLTable → table 模块(palette 备选,旧页默认收起 → 默认布局不含);
//   Pool 总数大窗 KolPoolAllModal / 详情抽屉 KOLDetailDrawer(收藏/联系/补全/翻译/评论/
//   合作方案草案 Skill 全在抽屉内,零改动)/ ContactModal → 页级 overlay 原样;
//   收藏管线(候选先建档再收藏)/ 跨页事件(vkpi:open-kol-pool-item / -search)/
//   召回·账号结果开抽屉 → KolPoolBoardPage.actions.ts hooks 平移。
//   新增(板块页范式要素):KPI 带四卡真值(在池总数 / 本周新发现 / 已深析 /
//   低触达暂不推荐 —— 2026-07-12 本地实测口径见 MODULE_SOURCES)+ 任务泳道模块
//   (TaskProgressBoard 内嵌,批量深析/找达人任务的真实执行序)。
//   新增(2026-07-12 图形密度波,金样板 = MarketVoicePage.charts / MyKolBoardPage.charts
//   同构):图形三件 fitDist(全池 Fit 十分位直方 + 未评分桶,前端只读分桶)/
//   platDist(平台条形,前端聚合)/ funnel(发现转化近 30 天四段,
//   summary.discovery_funnel_30d,段缺席诚实灰行);行内快捷动作(收藏/入项目,
//   不用先开抽屉;入项目=既有 projects 列表 + addKolsToProject 幂等端点)。
//   数据源(全真,零编造):items = CockpitApp kolPoolRows(workspace 分页取尽);
//   kol-pool/favorites · needs-analysis · summary(低触达计数 + 漏斗) · detail-bundle;
//   smart/lanes 内嵌组件自取。
// 切换(一行,待用户发令,不改 CockpitApp):CockpitApp.tsx 「kol-pool」分支
//   e(KOLPoolPage, {...}) → e(KolPoolBoardPage, {...})(props 同形:items/loading/error/
//   apiToken/staff)。回滚 = 改回原行。
// 红线:纯展示 + 既有写端点(收藏/入队/建档),绝不写 viltrox fit 分 / 不触 rule_v0;
//   颜色全 token 零写死色;零 opacity 修饰类;卡面零技术术语(表名/端点只进 SrcChip);
//   诚实空态;时间绝对戳;布局只走本机 storageKey,不传 apiToken 给
//   EditableDashboardBoard(其账户级持久化写死 dashboard_layout_v1 键,传了会覆写
//   Dashboard 布局 —— 金样板同注释)。

// v1→v2(2026-07-12):kinds 撤出默认 + 图形三件进默认;bump 键让默认变更盖过本机残留布局。
const STORAGE_KEY = "vkpi-kol-pool-layout-v2";

// 默认布局(12 列 · 默认简六行):kpiK(12) → smart(12) → fitDist(4)+platDist(4)+funnel(4)
// → recs(8)+needs(4) → coverage(12) → lanes(12);
// kinds(经典指标条)/table = palette 备选(旧页五卡与表格默认收起的等价物)
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiK", span: 12 },
  { moduleKey: "smart", span: 12 },
  { moduleKey: "fitDist", span: 4 },
  { moduleKey: "platDist", span: 4 },
  { moduleKey: "funnel", span: 4 },
  { moduleKey: "recs", span: 8 },
  { moduleKey: "needs", span: 4 },
  { moduleKey: "coverage", span: 12 },
  { moduleKey: "lanes", span: 12 },
];

// demo .ph-b:pagehead 药丸徽(金样板同款)
const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

export function KolPoolBoardPage({
  items: sourceItems = [],
  loading = false,
  error = "",
  apiToken = "",
  staff = [],
  currentUser,
  embeddedModuleKey,
}: {
  items?: Row[];
  loading?: boolean;
  error?: string;
  apiToken?: string;
  staff?: Row[];
  currentUser?: { id?: string | number; name?: string; email?: string };
  embeddedModuleKey?: string;
}) {
  const poolItems = React.useMemo(() => (Array.isArray(sourceItems) ? sourceItems : []), [sourceItems]);
  const [editing, setEditing] = React.useState(false);

  /* ---------- 筛选状态(旧页同名 state 平移;recs/kinds/table 三模块共享一份) ---------- */
  const [search, setSearch] = React.useState("");
  const [country, setCountry] = React.useState("");
  const [audienceType, setAudienceType] = React.useState("");
  const [trendLevel, setTrendLevel] = React.useState("");
  const [sortBy, setSortBy] = React.useState("v6_fit");
  const [hasViltrox, setHasViltrox] = React.useState(false);
  const [hasCompetitor, setHasCompetitor] = React.useState(false);
  const [searchMode, setSearchMode] = React.useState("balanced"); // balanced | precision | discovery
  const [kindFilter, setKindFilter] = React.useState("");
  const [myListFilter, setMyListFilter] = React.useState(false);
  const [filtersOpen, setFiltersOpen] = React.useState(false);
  const [poolModalOpen, setPoolModalOpen] = React.useState(false);
  const contactSessionRef = React.useRef({ token: apiToken, userId: String(currentUser?.id ?? "anonymous"), generation: 0 });
  const currentContactUserId = String(currentUser?.id ?? "anonymous");
  if (contactSessionRef.current.token !== apiToken || contactSessionRef.current.userId !== currentContactUserId) {
    contactSessionRef.current = {
      token: apiToken,
      userId: currentContactUserId,
      generation: contactSessionRef.current.generation + 1,
    };
  }
  const contactSessionGeneration = contactSessionRef.current.generation;
  const [contactState, setContactState] = React.useState<{
    generation: number;
    item: Row;
    contactState: ContactState;
  } | null>(null);
  const contactItem = contactState?.generation === contactSessionGeneration ? contactState.item : null;
  const contactGenerationRef = React.useRef(contactSessionGeneration);
  React.useEffect(() => {
    if (contactGenerationRef.current === contactSessionGeneration) return;
    contactGenerationRef.current = contactSessionGeneration;
    setContactState(null);
  }, [contactSessionGeneration]);

  /* ---------- 动作 hooks(actions.ts,旧页逻辑 1:1) ---------- */
  const { rememberRecallItems, avatarForItem, mergeAvatarSeed } = useRecallAvatars();
  const {
    selectedItem,
    setSelectedItem,
    selectedDetailBundle,
    detailLoading,
    detailError,
    openItem,
    reloadDetail,
    closeDrawer,
  } = usePoolDrawer(apiToken, avatarForItem, mergeAvatarSeed, currentUser?.id ?? null);
  const { myList, toggleMyList, syncState: favoriteSyncState, syncError: favoriteSyncError } = usePoolFavorites(
    apiToken,
    currentUser?.id ?? null,
    selectedItem,
    setSelectedItem,
    avatarForItem,
  );
  const { openRecallItem, openProfileItem } = useSmartOpeners(apiToken, poolItems, openItem, mergeAvatarSeed);
  const needs = useNeedsAnalysis(apiToken);
  const { lowReachHidden, funnel30d, summaryLoading } = usePoolSummary(apiToken);
  const quickProject = useQuickAddToProject(apiToken);

  // 顶栏全局搜索接真:消费 pending 关键词 → 填入本地筛选并展开筛选条
  // (vkpi:open-kol-pool-item 同款 localStorage+event 管道)。挂载即消费一次 + 监听事件。
  React.useEffect(() => {
    const consumeSearch = () => {
      try {
        const pending = window.localStorage.getItem("vkpi:pending-kolpool-search");
        if (!pending) return;
        window.localStorage.removeItem("vkpi:pending-kolpool-search");
        setSearch(pending);
        setFiltersOpen(true);
      } catch { /* localStorage 不可用忽略 */ }
    };
    consumeSearch();
    window.addEventListener("vkpi:open-kol-pool-search", consumeSearch);
    return () => window.removeEventListener("vkpi:open-kol-pool-search", consumeSearch);
  }, []);

  /* ---------- 派生(旧页 filteredBase/kindCounts/items + KPI 真值) ---------- */
  const dataAsOf = React.useMemo(() => dataAsOfFrom(poolItems), [poolItems]);
  const filteredBase = React.useMemo(
    () => filterPoolItems(poolItems, { search, country, audienceType, trendLevel, hasViltrox, hasCompetitor }),
    [poolItems, search, country, audienceType, trendLevel, hasViltrox, hasCompetitor],
  );
  const kindCounts = React.useMemo(() => computeKindCounts(filteredBase), [filteredBase]);
  const items = React.useMemo(
    () => sortedPoolItems(filteredBase, { kindFilter, myListFilter, myList, sortBy }),
    [filteredBase, kindFilter, myListFilter, myList, sortBy],
  );
  const weekNew = React.useMemo(() => weekNewCount(poolItems), [poolItems]);
  const deepStats = React.useMemo(() => deepAnalyzedStats(poolItems), [poolItems]);

  /* ---------- 卡头 props(金样板 cardProps 同构) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows };
  };
  const asOfRows: Array<[string, string]> = dataAsOf
    ? [["数据截至", `${dataAsOf}(全池 max last_seen_at · 日刷新恢复前诚实陈旧优于假新鲜)`]]
    : [];

  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载 KOL 池数据。
    </PendingCard>
  );
  const poolGate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (loading && poolItems.length === 0) return <div className="py-6 text-center text-[12px] text-muted">KOL 池读取中…</div>;
    if (error && poolItems.length === 0) return <ErrorCard title="KOL 池读取失败" text={String(error)} />;
    if (poolItems.length === 0) return <div className="py-6 text-center text-[12px] text-muted">暂无入池 KOL —— 用「找达人」搜第一批。</div>;
    return null;
  };

  /* ---------- 模块 body ---------- */

  const renderKpiBand = () => (
    <ModuleCard {...cardProps("kpiK", "池子指标带", poolItems.length ? poolItems.length.toLocaleString() : undefined, asOfRows)}>
      {!apiToken ? noTokenCard : (
        <PoolKpiBand
          total={poolItems.length}
          weekNew={weekNew}
          deepKols={deepStats.kols}
          deepRows={deepStats.rows}
          lowReachHidden={lowReachHidden}
          loading={loading && poolItems.length === 0}
        />
      )}
    </ModuleCard>
  );

  const renderSmart = () => (
    <ModuleCard {...cardProps("smart", "找达人")}>
      {!apiToken ? noTokenCard : (
        <SmartEmbed
          apiToken={apiToken}
          accountId={currentUser?.id ?? null}
          searchMode={searchMode}
          onSearchModeChange={setSearchMode}
          onRecallItems={rememberRecallItems}
          onOpenRecallItem={openRecallItem}
          onOpenProfile={openProfileItem}
        />
      )}
    </ModuleCard>
  );

  const renderRecs = () => (
    <ModuleCard {...cardProps("recs", "推荐 · 卡片流", poolItems.length ? `${items.length}` : undefined)}>
      {poolGate() ?? (
        <RecsSection
          items={items}
          poolTotal={poolItems.length}
          filteredBase={filteredBase}
          apiToken={apiToken}
          myList={myList}
          onOpenItem={(item) => void openItem(item)}
          avatarFor={avatarForItem}
          filters={{ search, country, audienceType, trendLevel, sortBy, hasViltrox, hasCompetitor, searchMode, kindFilter, myListFilter }}
          setters={{ setSearch, setCountry, setAudienceType, setTrendLevel, setSortBy, setHasViltrox, setHasCompetitor, setSearchMode, setKindFilter, setMyListFilter }}
          kindCounts={kindCounts}
          filtersOpen={filtersOpen}
          setFiltersOpen={setFiltersOpen}
          onOpenPoolModal={() => setPoolModalOpen(true)}
          onToggleFavorite={(item) => toggleMyList(item.id)}
          onAddToProject={(item) => quickProject.openFor(item)}
        />
      )}
    </ModuleCard>
  );

  /* ---------- 图形三件(金样板 charts 同构;fitDist/platDist 前端只读聚合,funnel 吃 summary) ---------- */

  const renderFitDist = () => (
    <ModuleCard {...cardProps("fitDist", "Fit 分布", poolItems.length ? poolItems.length.toLocaleString() : undefined)}>
      {poolGate() ?? <PoolFitDistBody items={poolItems} />}
    </ModuleCard>
  );

  const renderPlatDist = () => (
    <ModuleCard {...cardProps("platDist", "平台分布", poolItems.length ? poolItems.length.toLocaleString() : undefined)}>
      {poolGate() ?? <PoolPlatformBody items={poolItems} />}
    </ModuleCard>
  );

  const renderFunnel = () => (
    <ModuleCard
      {...cardProps(
        "funnel",
        "发现转化 · 近30天",
        funnel30d && Number.isFinite(Number(funnel30d.discovered)) ? Number(funnel30d.discovered).toLocaleString() : undefined,
      )}
    >
      {!apiToken ? noTokenCard : <DiscoveryFunnelBody funnel={funnel30d} loading={summaryLoading} />}
    </ModuleCard>
  );

  const renderKinds = () => (
    <ModuleCard {...cardProps("kinds", "经典指标条", poolItems.length ? kindCounts.total.toLocaleString() : undefined)}>
      {poolGate() ?? (
        <KindBarEmbed
          items={poolItems}
          activeKindFilter={kindFilter}
          onCardClick={(key) => setKindFilter(kindFilter === key ? "" : key)}
          onTotalClick={() => setPoolModalOpen(true)}
        />
      )}
    </ModuleCard>
  );

  const renderNeeds = () => (
    <ModuleCard
      {...cardProps(
        "needs",
        "待深析",
        needs.needsItems.length ? `${needs.needsItems.length}${needs.needsItems.length >= 50 ? "+" : ""}` : undefined,
      )}
    >
      {!apiToken ? noTokenCard : (
        <NeedsBody
          items={needs.needsItems}
          loading={needs.needsLoading}
          busy={needs.needsBusy}
          msg={needs.needsMsg}
          onRunBatch={needs.runNeedsBatch}
        />
      )}
    </ModuleCard>
  );

  const renderLanes = () => (
    <ModuleCard {...cardProps("lanes", "任务进度")}>
      {!apiToken ? noTokenCard : <LanesEmbed apiToken={apiToken} />}
    </ModuleCard>
  );

  const renderCoverage = () => (
    <ModuleCard {...cardProps("coverage", "估算受众覆盖")}>
      {poolGate() ?? (
        <CoverageEmbed
          items={poolItems}
          onGoDiscover={(c) => {
            setCountry(c);
            setSearchMode("discovery");
            setFiltersOpen(true);
          }}
        />
      )}
    </ModuleCard>
  );

  const renderTable = () => (
    <ModuleCard {...cardProps("table", "表格视图", poolItems.length ? `${items.length} 行` : undefined)}>
      {poolGate() ?? (
        <div>
          <div className="mb-2 text-[9.5px] text-muted">点行打开详情抽屉;筛选 · 排序在「推荐 · 卡片流」模块展开,对表格同步生效</div>
          <TableEmbed items={items} onRowClick={(item) => void openItem(item)} selectedItemId={selectedItem?.id} myList={myList} />
        </div>
      )}
    </ModuleCard>
  );

  /* ---------- 模块注册表(palette 全量可选;kinds/table 为 palette 备选不进默认布局) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiK", label: "池子指标带", description: "在池总数 / 本周新发现 / 已深析 / 低触达暂不推荐(请求时点真值)", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "smart", label: "找达人", description: "贴链接看资料 · 描述需求找人 · 结果勾选收藏/话术/立项(内嵌)", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 13, minHeight: 6, maxHeight: 34, render: renderSmart },
    { key: "recs", label: "推荐 · 卡片流", description: "池内候选卡片 + 筛选·排序次级展开 · 行内收藏/入项目 · 点卡开抽屉", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 18, minHeight: 8, maxHeight: 36, render: renderRecs },
    { key: "fitDist", label: "Fit 分布", description: "全池匹配分十分位直方 + 未评分诚实桶(只读)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 4, maxHeight: 16, render: renderFitDist },
    { key: "platDist", label: "平台分布", description: "全池按平台条形(前端聚合,与总览口径同径)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 4, maxHeight: 16, render: renderPlatDist },
    { key: "funnel", label: "发现转化 · 近30天", description: "发现 → 自动入库 → 已深析 → 已收藏 四段(同窗计数)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 4, maxHeight: 16, render: renderFunnel },
    { key: "lanes", label: "任务进度", description: "搜索/分析任务真实泳道 + 排队区 · 失败可重试(内嵌)", category: "实时模块", defaultSpan: 12, minSpan: 4, defaultHeight: 12, minHeight: 6, maxHeight: 28, render: renderLanes },
    { key: "needs", label: "待深析", description: "有视频没分析的 KOL 清单 · 一键全部分析", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 12, minHeight: 5, maxHeight: 24, render: renderNeeds },
    { key: "coverage", label: "估算受众覆盖", description: "estimated_country_reach 代理信号 · 多国可重复计数 · 配置缺口一键去发现", category: "业务板块", defaultSpan: 12, minSpan: 4, defaultHeight: 10, minHeight: 5, maxHeight: 22, render: renderCoverage },
    // ↓ palette 备选(不进默认布局)
    { key: "kinds", label: "经典指标条", description: "旧版五卡(总数开大窗/分类筛选/均 Fit/播放汇总)· 独有信息已由筛选条/工具行/Fit 分布接棒", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 14, render: renderKinds },
    { key: "table", label: "表格视图", description: "全列表格 · 与卡片流同一份筛选排序 · 点行开抽屉", category: "业务板块", defaultSpan: 12, minSpan: 6, defaultHeight: 14, minHeight: 6, maxHeight: 32, render: renderTable },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="KOL Pool" />;
  }

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 真值药丸徽(KOL 数 / 数据截至)+ 实时辉光点 + 编辑布局 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">KOL 池</span>
          {poolItems.length > 0 && <span className={PH_BADGE}>{poolItems.length.toLocaleString()} KOL</span>}
          {dataAsOf && (
            <span className={PH_BADGE} title="全池最近一次抓取时间(max last_seen_at)。日刷新恢复前数据停更,诚实陈旧优于假新鲜。">
              数据截至 {dataAsOf}
            </span>
          )}
          {loading && <span className={PH_BADGE}>KOL 池读取中…</span>}
          {favoriteSyncState === "loading" && <span className={PH_BADGE}>收藏同步中…</span>}
          {favoriteSyncState === "error" && (
            <span className={PH_BADGE} title={favoriteSyncError || "收藏状态暂无法确认"}>收藏未同步</span>
          )}
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span
              className={`h-[5px] w-[5px] rounded-full ${!apiToken || error || favoriteSyncState === "error" ? "bg-warn" : "bg-good"}`}
              style={{ boxShadow: `0 0 var(--ds-glow-radius, 0px) ${!apiToken || error || favoriteSyncState === "error" ? "var(--ds-warn)" : "var(--ds-good)"}` }}
            />
            {!apiToken ? "未连接" : error || favoriteSyncState === "error" ? "部分未同步" : loading || favoriteSyncState === "loading" ? "同步中" : "已同步"}
          </span>
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

      {error && poolItems.length === 0 && (
        <div className="mb-3">
          <ErrorCard title="KOL 池读取失败" text={String(error)} />
        </div>
      )}
      {favoriteSyncState === "error" && (
        <div role="alert" className="mb-3 rounded-lg border border-warn bg-warn-soft px-3 py-2 text-[10.5px] text-warn">
          收藏状态暂无法确认：{favoriteSyncError || "请稍后重试或刷新页面"}。当前空星标不代表服务端没有收藏。
        </div>
      )}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 页级 overlay(旧页 AnimatePresence 区原样:大窗 / 详情抽屉 / 联系弹窗) */}
      <AnimatePresence>
        {poolModalOpen && (
          <KolPoolAllModal
            key="kol-pool-all-modal"
            items={poolItems}
            myList={myList}
            selectedItemId={selectedItem?.id}
            onClose={() => setPoolModalOpen(false)}
            onRowClick={(item) => {
              void openItem(item);
              window.setTimeout(() => setPoolModalOpen(false), 0);
            }}
          />
        )}
        {selectedItem && (
          <React.Suspense fallback={null}>
            <KOLDetailDrawer
              key={`kol-detail-${selectedItem.id || selectedItem.handle || "selected"}`}
              item={selectedItem}
              detailBundle={selectedDetailBundle}
              apiToken={apiToken}
              detailLoading={detailLoading}
              detailError={detailError}
              onClose={closeDrawer}
              inMyList={myList.has(selectedItem.id)}
              onToggleMyList={toggleMyList}
              onContact={(it: Row, revealedContactState: ContactState) => setContactState({
                generation: contactSessionGeneration,
                item: it,
                contactState: revealedContactState,
              })}
              staff={staff}
              onReloadDetail={reloadDetail}
            />
          </React.Suspense>
        )}
        {contactItem && (
          <ContactModal
            key={`kol-contact-${contactSessionGeneration}-${contactItem.id || contactItem.handle || "selected"}`}
            item={contactItem}
            apiToken={apiToken}
            currentUser={currentUser}
            sessionGeneration={contactSessionGeneration}
            initialContactState={contactState?.contactState}
            onClose={() => setContactState(null)}
          />
        )}
        {quickProject.target && (
          <QuickProjectModal
            key="kol-pool-quick-project"
            item={quickProject.target}
            projects={quickProject.projects}
            projectsError={quickProject.projectsError}
            busy={quickProject.busy}
            msg={quickProject.msg}
            onConfirm={(projId) => void quickProject.confirm(projId)}
            onClose={quickProject.close}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
