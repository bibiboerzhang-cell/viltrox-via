import React from "react";
import { ChevronDown, LayoutGrid, Maximize2, Search, SlidersHorizontal, Star, X } from "lucide-react";
import { SmartKolInputPanel } from "../components/SmartKolInputPanel";
import { TaskProgressBoard } from "../components/TaskProgressBoard";
import { MarketCoverageCard } from "../components/MarketCoverageCard";
import { KPIBar } from "../components/KPIBar";
import { KOLTable } from "../components/KOLTable";
import { KolRecommendationCards } from "../components/KolRecommendationCards";
import { FilterBar } from "../components/FilterBar";
import { SearchProgressBar } from "../components/SearchProgressBar";
import { CANDIDATE_KIND_INFO } from "../data/candidateKindInfo";
import type { Row } from "./KolPoolBoardPage.actions";

// KOL 池 · 内嵌模块收编包装族(smart/lanes/coverage/kinds/table 五件 + 卡片流区段;
//   金样板 = MyKolBoardPage.embeds / KolProfileBoardPage.embeds 非侵入收编手法 1:1)。
//   手法:**绝不改 components/ 旧组件文件**(SmartKolInputPanel 一族刚接触达二段闸,
//   行为一根手指不动),只用包装容器的 Tailwind 任意变体选择器隐藏重复标题块、
//   压平旧卡壳;功能控件与真读数(找达人输入框/模式徽/历史条/发现闸折叠计数/
//   任务泳道「只看我的」/速度灯/覆盖卡「查看全部」/缺口「去发现」)全部保留可见。
//   旧组件自带写死色属零改动豁免区(收编不重涂)。
//   容器挂 vkpi-embed 类 + data-embed 属性,变体写成 [&.vkpi-embed[data-embed]>…]
//   → 特异性 (0,3,1)+!,稳压换肤层 !important(金样板同注释)。
// 红线:本文件零新增网络逻辑(SmartKolInputPanel/TaskProgressBoard 自取,其余纯展示
//   吃 page 层数据);纯展示绝不写 fit 分;新增样式全 token 零写死色;零 opacity 修饰类。

const EMBED = "vkpi-embed";

/* ---- SmartKolInputPanel:section 壳(rounded border bg p-2.5)压平;头行里
   icon span + h2「找达人」隐藏(卡头标题接手),副题句 + Video/Profile/查找 徽保留。 ---- */
const SMART_TRIM = [
  "[&.vkpi-embed[data-embed]>section]:!rounded-none [&.vkpi-embed[data-embed]>section]:!border-0",
  "[&.vkpi-embed[data-embed]>section]:!bg-transparent [&.vkpi-embed[data-embed]>section]:!p-0",
  "[&>section>div:first-child>div:first-child>span:first-child]:hidden",
  "[&>section>div:first-child_h2]:hidden",
].join(" ");

/* ---- TaskProgressBoard:外壳(rounded-xl border bg-card px-3 py-3 shadow)压平;
   头行左侧 icon+「任务进度」块隐藏,右侧「只看我的」/活跃计数/速度灯全保留。 ---- */
const LANES_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!rounded-none [&.vkpi-embed[data-embed]>div]:!border-0",
  "[&.vkpi-embed[data-embed]>div]:!bg-transparent [&.vkpi-embed[data-embed]>div]:!px-0",
  "[&.vkpi-embed[data-embed]>div]:!py-0 [&.vkpi-embed[data-embed]>div]:!shadow-none",
  "[&>div>div:first-child>div:first-child]:hidden",
].join(" ");

/* ---- MarketCoverageCard:外壳(rounded-xl border bg backdrop-blur)压平;头行里
   icon 盒 + h3「估算受众覆盖」隐藏,「· N 国 · N 缺口」读数与「查看全部」钮保留;
   头/行区横向 padding 收敛对齐卡体。 ---- */
const COVERAGE_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!rounded-none [&.vkpi-embed[data-embed]>div]:!border-0",
  "[&.vkpi-embed[data-embed]>div]:!bg-transparent [&.vkpi-embed[data-embed]>div]:!backdrop-blur-none",
  "[&>div>div:first-child]:!px-0 [&>div>div:nth-child(2)]:!px-0",
  "[&>div>div:first-child>div>div:first-child]:hidden [&>div>div:first-child>div>h3]:hidden",
].join(" ");

/* ---- KOLTable:外壳(rounded-xl border bg backdrop-blur)压平(卡壳由 ModuleCard 接手);
   表头/虚拟滚动/点行开抽屉全数原样。 ---- */
const TABLE_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!rounded-none [&.vkpi-embed[data-embed]>div]:!border-0",
  "[&.vkpi-embed[data-embed]>div]:!bg-transparent [&.vkpi-embed[data-embed]>div]:!backdrop-blur-none",
].join(" ");

/* ============ 五件包装(SmartKolInputPanel/TaskProgressBoard 取数自持;其余吃 page 层) ============ */

export function SmartEmbed({
  apiToken,
  accountId,
  searchMode,
  onSearchModeChange,
  onRecallItems,
  onOpenRecallItem,
  onOpenProfile,
}: {
  apiToken: string;
  accountId: string | number | null;
  searchMode: string;
  onSearchModeChange?: (mode: "balanced" | "precision" | "discovery") => void;
  onRecallItems: (items: any[]) => void;
  onOpenRecallItem: (item: any) => void;
  onOpenProfile: (result: any) => void;
}) {
  return (
    <div data-embed="smart" className={`${EMBED} ${SMART_TRIM}`}>
      <SmartKolInputPanel
        apiToken={apiToken}
        accountId={accountId}
        searchMode={searchMode}
        onSearchModeChange={onSearchModeChange}
        onRecallItems={onRecallItems}
        onOpenRecallItem={onOpenRecallItem}
        onOpenProfile={onOpenProfile}
      />
    </div>
  );
}

export function LanesEmbed({ apiToken }: { apiToken: string }) {
  return (
    <div data-embed="lanes" className={`${EMBED} ${LANES_TRIM}`}>
      <TaskProgressBoard apiToken={apiToken} />
    </div>
  );
}

export function CoverageEmbed({ items, onGoDiscover }: { items: Row[]; onGoDiscover: (country: string) => void }) {
  return (
    <div data-embed="coverage" className={`${EMBED} ${COVERAGE_TRIM}`}>
      <MarketCoverageCard items={items} onGoDiscover={onGoDiscover} />
    </div>
  );
}

export function KindBarEmbed({
  items,
  activeKindFilter,
  onCardClick,
  onTotalClick,
}: {
  items: Row[];
  activeKindFilter: string;
  onCardClick: (key: string) => void;
  onTotalClick: () => void;
}) {
  // KPIBar 本体是裸 grid(无壳无标题),原样内嵌;卡文案/推导口径属旧组件豁免区,
  // 真实口径已登记 MODULE_SOURCES.kinds(SrcChip 可查)。
  return (
    <div data-embed="kinds" className={EMBED}>
      <KPIBar items={items} activeKindFilter={activeKindFilter} onCardClick={onCardClick} onTotalClick={onTotalClick} />
    </div>
  );
}

export function TableEmbed({
  items,
  onRowClick,
  selectedItemId,
  myList,
}: {
  items: Row[];
  onRowClick: (item: Row) => void;
  selectedItemId?: unknown;
  myList: Set<unknown>;
}) {
  return (
    <div data-embed="table" className={`${EMBED} ${TABLE_TRIM}`}>
      <KOLTable items={items} onRowClick={onRowClick} selectedItemId={selectedItemId} myList={myList} />
    </div>
  );
}

/* ============ 卡片流区段(旧页「推荐 · 卡片流」section 平移:结果计数 + 模式配额徽 +
   生效筛选 chips + 全量大窗入口(kinds 撤出默认后总数卡的接棒者)+ 筛选·排序次级展开
   (FilterBar 原样)+ SearchProgressBar + 卡片流)。行内快捷动作(收藏/入项目,
   不用先开抽屉)经 onToggleFavorite/onAddToProject 透传给卡片(缺回调=按钮不摆)。
   区段头是页级新作 JSX → 全 token;FilterBar/卡片流本体旧组件加性扩展零行为改动。 ============ */

export interface RecsFilterState {
  search: string;
  country: string;
  audienceType: string;
  trendLevel: string;
  sortBy: string;
  hasViltrox: boolean;
  hasCompetitor: boolean;
  searchMode: string;
  kindFilter: string;
  myListFilter: boolean;
}

export function RecsSection({
  items,
  poolTotal,
  filteredBase,
  apiToken,
  myList,
  onOpenItem,
  avatarFor,
  filters,
  setters,
  kindCounts,
  filtersOpen,
  setFiltersOpen,
  onOpenPoolModal,
  onToggleFavorite,
  onAddToProject,
}: {
  items: Row[];
  poolTotal: number;
  filteredBase: Row[];
  apiToken: string;
  myList: Set<unknown>;
  onOpenItem: (item: Row) => void;
  avatarFor: (item: Row) => string;
  filters: RecsFilterState;
  setters: {
    setSearch: (v: string) => void;
    setCountry: (v: string) => void;
    setAudienceType: (v: string) => void;
    setTrendLevel: (v: string) => void;
    setSortBy: (v: string) => void;
    setHasViltrox: (v: boolean) => void;
    setHasCompetitor: (v: boolean) => void;
    setSearchMode: (v: string) => void;
    setKindFilter: (v: string) => void;
    setMyListFilter: (v: boolean) => void;
  };
  kindCounts: Record<string, number>;
  filtersOpen: boolean;
  setFiltersOpen: React.Dispatch<React.SetStateAction<boolean>>;
  /** 全量池表大窗入口(kinds 撤出默认布局后,总数卡开大窗的接棒入口) */
  onOpenPoolModal?: () => void;
  /** 行内快捷收藏(缺回调=卡面不摆按钮) */
  onToggleFavorite?: (item: Row) => void;
  /** 行内快捷入项目(缺回调=卡面不摆按钮) */
  onAddToProject?: (item: Row) => void;
}) {
  const anyFilterActive = Boolean(
    filters.search || filters.country || filters.audienceType || filters.trendLevel ||
    filters.hasViltrox || filters.hasCompetitor || filters.kindFilter || filters.myListFilter ||
    filters.sortBy !== "v6_fit",
  );
  return (
    <div>
      <div className="mb-2.5 flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-[10.5px] text-muted">
          <span className="inline-flex items-center gap-1.5 text-ink-2">
            <Search size={10} className="text-accent" />
            <span>结果</span>
            <span className="font-medium tabular-nums text-ink">{items.length}</span>
            <span className="text-muted">/{poolTotal}</span>
          </span>
          {onOpenPoolModal ? (
            <button
              type="button"
              onClick={onOpenPoolModal}
              aria-label="打开全量池表大窗"
              title="全量池表大窗(搜索 + 平台分组)"
              className="inline-flex items-center gap-1 rounded-[6px] border border-line px-1.5 py-0.5 text-[9.5px] text-muted transition-colors hover:border-accent hover:text-ink"
            >
              <Maximize2 size={9} />
              <span>全部 {poolTotal.toLocaleString()}</span>
            </button>
          ) : null}
          {/* 与找达人工作台同一状态：目标固定 30 人，显示业务分桶配额。 */}
          <span
            className="rounded-[6px] border border-line px-1.5 py-0.5 text-[9.5px] text-muted"
            title="当前搜索策略的目标配额:核心垂直 / 拓展 / 探索；严格筛选不足会明确标注短缺"
          >
            {filters.searchMode === "balanced" ? "平衡 18/9/3 · 目标30" : filters.searchMode === "precision" ? "垂直优先 24/5/1 · 目标30" : "拓展 15/12/3 · 目标30"}
          </span>
          {filters.kindFilter ? (
            <span className="inline-flex items-center gap-1 rounded-[6px] border border-accent bg-accent-soft px-1.5 py-0.5 text-[9.5px] text-accent">
              {filters.kindFilter === "existing" ? "已有库" : filters.kindFilter === "new" ? "新发现" : ((CANDIDATE_KIND_INFO as any)[filters.kindFilter]?.short || filters.kindFilter)}
              <button type="button" onClick={() => setters.setKindFilter("")} className="transition-colors hover:text-ink" aria-label="清除分类筛选">
                <X size={9} />
              </button>
            </span>
          ) : null}
          {filters.myListFilter ? (
            <span className="inline-flex items-center gap-1 rounded-[6px] border border-warn bg-warn-soft px-1.5 py-0.5 text-[9.5px] text-warn">
              <Star size={9} style={{ fill: "var(--ds-warn)" }} /> 我的列表
              <button type="button" onClick={() => setters.setMyListFilter(false)} className="transition-colors hover:text-ink" aria-label="清除我的列表筛选">
                <X size={9} />
              </button>
            </span>
          ) : null}
        </div>
        {/* 筛选条收敛为次级展开:一键展开完整 FilterBar(功能原样,同时作用于卡片流与表格)。 */}
        <button
          type="button"
          onClick={() => setFiltersOpen((open) => !open)}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-line bg-card px-2.5 py-1.5 text-[10.5px] text-muted transition-colors hover:text-ink"
        >
          <SlidersHorizontal size={11} />
          <span>{filtersOpen ? "收起筛选 · 排序" : "筛选 · 排序"}</span>
          {anyFilterActive ? <span className="h-1.5 w-1.5 rounded-full bg-accent" title="有筛选/排序生效中" /> : null}
          <ChevronDown size={12} className={`transition-transform ${filtersOpen ? "rotate-180" : ""}`} />
        </button>
      </div>
      {filtersOpen ? (
        <div data-embed="filters" className={EMBED}>
          <FilterBar
            search={filters.search}
            setSearch={setters.setSearch}
            country={filters.country}
            setCountry={setters.setCountry}
            audienceType={filters.audienceType}
            setAudienceType={setters.setAudienceType}
            trendLevel={filters.trendLevel}
            setTrendLevel={setters.setTrendLevel}
            sortBy={filters.sortBy}
            setSortBy={setters.setSortBy}
            hasViltrox={filters.hasViltrox}
            setHasViltrox={setters.setHasViltrox}
            hasCompetitor={filters.hasCompetitor}
            setHasCompetitor={setters.setHasCompetitor}
            searchMode={filters.searchMode}
            setSearchMode={setters.setSearchMode}
            kindFilter={filters.kindFilter}
            setKindFilter={setters.setKindFilter}
            kindCounts={kindCounts}
            myListFilter={filters.myListFilter}
            setMyListFilter={setters.setMyListFilter}
            myListCount={myList.size}
          />
          {(filters.search || filters.kindFilter || filters.myListFilter) ? (
            <SearchProgressBar items={filteredBase} searchActive={!!filters.search} />
          ) : null}
        </div>
      ) : null}
      <div className="mt-1 flex items-center gap-1.5 text-[9.5px] text-muted">
        <LayoutGrid size={10} className="text-accent" />
        <span>点卡片打开详情抽屉;收藏 / 联系 / 合作方案草案都在抽屉里</span>
      </div>
      <div className="mt-2" data-embed="cards">
        <KolRecommendationCards
          items={items}
          apiToken={apiToken}
          myList={myList}
          onOpenItem={onOpenItem}
          avatarFor={avatarFor}
          onToggleFavorite={onToggleFavorite}
          onAddToProject={onAddToProject}
        />
      </div>
    </div>
  );
}
