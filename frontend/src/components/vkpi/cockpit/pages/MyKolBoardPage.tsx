import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { getMyKolAggregate, type VkpiMyKolAggregateResponse } from "../../../../services/vkpi/kol-api";
import { useOfficialChannelMatrix } from "../../pages/channels/useOfficialChannelMatrix";
import type { VkpiDashboardData, VkpiPageKey } from "../../vkpiTypes";
import { EmptyLine, ErrorCard, LoadingLine, ModuleCard, PendingCard, type Row } from "./MarketVoicePage.modules";
import { AnalysisActivityModule, KolLibraryModule, MODULE_SOURCES } from "./MyKolBoardPage.modules";
import { ContentWallModule } from "./MyKolBoardPage.content-wall";
import { WatchlistModule } from "./MyKolBoardPage.watchlist";
import { SkuPlayModule } from "./MyKolBoardPage.sku-play";
import {
  DigestEmbed,
  LensExposureEmbed,
  LibClassicEmbed,
  MetricTrackingEmbed,
  OfficialEmbed,
  RiskEmbed,
  RollupEmbed,
  TeamEmbed,
} from "./MyKolBoardPage.embeds";
import {
  ClaimsBody,
  ContactsBody,
  FitDistBody,
  FollowerTrendBody,
  FunnelBody,
  MyKolKpiBand,
  PlatDistBody,
  SharesBody,
  ViewsTopBody,
  pctText,
} from "./MyKolBoardPage.charts";
import {
  getMyKolBoardExt,
  mapLibraryRows,
  type LibraryFilter,
  type VkpiBoardExtGroup,
  type VkpiMyKolBoardExtResponse,
} from "../../../../services/vkpi/myKolBoard-api";

// MY KOL → 板块页范式改版 · M1 骨架 + M3 库弹窗化 + M4 图形真身
//   (金样板 = MarketVoicePage 板块页 1:1 同构)。
//   【M4】board-ext 七组聚合全量接线,M1 的 PendingCard 模块全部换真图形(图表件住
//   MyKolBoardPage.charts.tsx,图形语言 = MarketVoicePage.charts 同构):
//     funnel 合作漏斗(8 段条形 · 点段=过滤 library,库筛选状态提升到本层共享)/
//     fitdist Fit 直方(10 桶 + 未评分诚实桶,只读)/ platdist 平台条形(点行=库平台过滤)/
//     KPI 带 series+delta 接线(K1←pool_followers 缺快照日 null 断点如实 / K2←new_videos /
//     K4←official_followers;K3 保持 kol_views 点时实测口径,无 series 诚实虚线)/
//     palette 六备选真身:viewsTop 播放榜 / contacts 联系方式覆盖 / followerTrend 双线 /
//     claims 我的认领 / shares 共享池。无实时来源的旧「数据覆盖」静态盘点已下线。
//   【M3→M4】library:V 筛选升级 board-ext v_content.v_kol_ids 名单精确过滤(Set 查找;
//   truncated / 名单缺席均如实降级标注);弹窗族住 MyKolBoardPage.dialogs.tsx 不动;
//   旧 KOL Pool 抽屉(vkpi:open-kol-pool-item 事件)保留不动,两入口并存。
//   页壳:pagehead(标题 + 视角/KOL 数药丸徽 + 实时辉光点 + 编辑布局钮)+
//   EditableDashboardBoard(模块注册表 + 默认布局六行 + palette 备选)。
//   数据源(全真,零编造):
//     GET /api/admin/vkpi/my-kol/aggregate        —— K1/K2 现值(kpi_summary)+ 库行 + claims
//     GET /api/admin/vkpi/my-kol/board-ext        —— 七组聚合(单组失败逐组诚实降级)
//     GET /api/marketing/channels/official-matrix —— K4 官号粉丝现值(最新快照)
//     metrics prop(CockpitApp dashboardRuntime)  —— K3 兜底(board-ext kol_views 优先,
//       复用主控已拉数据,零触发 /dashboard 的 metric_lineage 隐藏写入)。
//   模块落地:digest/team/official/risk/rollup 仍内嵌 pages/myKol 现有组件(零重写);
//   【M6】五件走 MyKolBoardPage.embeds 包装族:卡头真短计数 + 旧组件双标题/卡壳
//   非侵入收编(包装容器选择器,旧组件文件零改动)。【M5】library 详情档案卡
//   「打开 KOL 档案 →」= sessionStorage["vkpi:kol-profile-id"] + vkpi:open-kol-profile
//   事件管道(MarketVoice jumpIdentity 同口径,CockpitApp 既有监听切页)。
//   用户裁决②=A:risk/rollup 管理层专属 —— 员工视角注册表直接不出现(默认布局自动少两块),
//   不是 403 卡。旧 MyKolPage.tsx 保留不删(回滚垫);跨页事件管道(vkpi:open-mykol-kol /
//   vkpi:open-kol-pool-item 等)签名不变,内嵌组件自带监听照常工作。
//   【闭环数据补刀 2026-07-12】旧两栏库右栏真数据零丢失接回新版(分区族住
//   MyKolBoardPage.libdetail):库行采集数据列(视频 N·播放合计·深析 n/N,
//   /kol-pool/{id}/videos 同源可见行懒取)+ 「我的收藏」快捷 chip(kol-pool/favorites
//   名单)+ 详情弹窗分区(档案→追踪链 GOAFFPRO→合作项目结果→V 内容五 tabs→动作排);
//   新增 activity 分析动态小模组(task-queue/compact 泳道同源)+ libclassic 经典视图
//   (旧两栏库 palette 备选不删,默认体验统一新版行式库)。
//   【内容墙 2026-07-12】contentWall 模块(span8,library 下一行,viewsTop 上默认作
//   旁柱):收藏集最近采集视频缩略图网格,数据 = board-ext recent_videos 增量组
//   (封顶 60;单 KOL 视图改走 /kol-pool/{id}/videos 同源);缩略图 = 创意库同款
//   毒缓存自愈链(best_thumbnail);布局键 bump v1→v2。
// 红线:纯展示,绝不写 viltrox fit 分 / 不触 rule_v0;颜色全 token 零写死色;发光只走
//   既有 ds-* 类(自带 reduced-motion 降级);端点失败 = 诚实错误卡,board-ext 单组
//   error → 单卡 ErrorCard / empty → 如实空。布局只走本机
//   storageKey,不传 apiToken 给 EditableDashboardBoard(其账户级持久化写死
//   dashboard_layout_v1 键,传了会覆写 Dashboard 布局 —— 金样板同注释)。

// v1→v2(2026-07-12):默认布局插入「内容墙」行(contentWall 8 + viewsTop 4),
// 旧本机布局不含新模块 → bump 键让全员看到新默认(手动改过布局的本机记忆一并重置)。
// v2→v3(2026-08-22 车道 L4):默认布局插入「数值跟进」(8)+「镜头出镜」(4)一行
// (被追踪视频实测曲线与 7d/30d 增量 · 深析内容按镜头汇总),同前例 bump 键让全员看到。
// v3→v4(2026-08-23 波 C·C3):默认布局在团队行后插入「观察清单」(12)——按分组跟进
// 重点 KOL 的追踪/深析进度(用户原话「给某些 kol 增加一个观察列表跟进进度」),同前例 bump 键。
// v4→v5(2026-08-23 波 D·C):观察清单行后插入「单品播放数据」(12)——被「数据关注」
// 登记的视频按单品(SKU)聚合播放/增量总览,同前例 bump 键让全员看到新默认。
const STORAGE_KEY = "vkpi-my-kol-layout-v5";
const MY_KOL_PAGE_SIZE = 50;

// 默认布局(12 列 · 设计单定稿七行;2026-07-12 两刀:team 12→8 腾出 span4 给
// 「分析动态」小模组;library 下一行加「内容墙」(收藏集最近采集视频网格)+
// 「播放 Top 视频」从 palette 上默认作旁柱):
// kpiM(12) → digest(8)+funnel(4) → team(8)+activity(4) → library(8)+fitdist(4)
// → contentWall(8)+viewsTop(4) → official(8)+platdist(4)
// → risk(8)+rollup(4)(manager-only,员工视角自动少一行)
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiM", span: 12 },
  { moduleKey: "digest", span: 8 },
  { moduleKey: "funnel", span: 4 },
  { moduleKey: "team", span: 8 },
  { moduleKey: "activity", span: 4 },
  { moduleKey: "watchlist", span: 12 },
  { moduleKey: "skuPlay", span: 12 },
  { moduleKey: "library", span: 8 },
  { moduleKey: "fitdist", span: 4 },
  { moduleKey: "contentWall", span: 8 },
  { moduleKey: "viewsTop", span: 4 },
  { moduleKey: "metricTracking", span: 8 },
  { moduleKey: "lensExposure", span: 4 },
  { moduleKey: "official", span: 8 },
  { moduleKey: "platdist", span: 4 },
  { moduleKey: "risk", span: 8 },
  { moduleKey: "rollup", span: 4 },
];

// demo .ph-b:pagehead 药丸徽(金样板同款)
const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

interface MyKolBoardPageProps {
  apiToken?: string;
  viewMode: "manager" | "employee";
  data?: VkpiDashboardData;
  userName?: string;
  userRole?: string;
  onRefreshData?: () => void;
  onSelectPage?: (page: VkpiPageKey) => void;
  /** CockpitApp dashboardRuntime.metrics(normalizeDashboardMetrics 产物);K3 取 exposure.all */
  metrics?: Row[];
  embeddedModuleKey?: string;
}

export function MyKolBoardPage({
  apiToken = "",
  viewMode,
  data,
  userName,
  onRefreshData,
  onSelectPage,
  metrics,
  embeddedModuleKey,
}: MyKolBoardPageProps) {
  const isManager = viewMode === "manager";
  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);

  React.useEffect(() => {
    const refresh = () => setReloadTick((tick) => tick + 1);
    window.addEventListener("vkpi:favorites-changed", refresh);
    window.addEventListener("vkpi:projects-changed", refresh);
    return () => {
      window.removeEventListener("vkpi:favorites-changed", refresh);
      window.removeEventListener("vkpi:projects-changed", refresh);
    };
  }, []);

  // K1/K2 + 徽章 KOL 数:统一只读聚合端点(员工 own-only 由后端 scope 强制)
  const [agg, setAgg] = React.useState<VkpiMyKolAggregateResponse | null>(null);
  const [aggLoading, setAggLoading] = React.useState(false);
  const [aggError, setAggError] = React.useState("");
  const [aggLoadingMore, setAggLoadingMore] = React.useState(false);
  const [aggLoadMoreError, setAggLoadMoreError] = React.useState("");

  // K4 官号粉丝 + team/official 模块共用一份矩阵(hook 自带 30s 内存缓存 + localStorage SWR)
  const matrix = useOfficialChannelMatrix(apiToken || undefined);

  // 【M4】board-ext 七组聚合(KPI 带时序/漏斗/直方/平台/覆盖/播放榜/V 名单全吃这口;
  // 端点失败 = 各图形卡 ErrorCard,单组 error/empty 由 extGate 逐组诚实降级)
  const [ext, setExt] = React.useState<VkpiMyKolBoardExtResponse | null>(null);
  const [extError, setExtError] = React.useState("");
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setExtError("");
    getMyKolBoardExt(apiToken, { days: 30 })
      .then((res) => {
        if (alive) setExt(res && typeof res === "object" ? res : null);
      })
      .catch((err: unknown) => {
        const detail = (err as { detail?: unknown; message?: unknown }) || {};
        if (alive) setExtError(String(detail.detail || detail.message || "读取失败"));
      });
    return () => {
      alive = false;
    };
  }, [apiToken, reloadTick]);

  // 【M4】库筛选状态提升到 page 层:library 卡面 chips / 全量弹窗 / 合作漏斗点段 /
  // 平台分布点行共用同一份(与 M3 共享筛选状态)
  const [libFilter, setLibFilter] = React.useState<LibraryFilter>({ vOnly: false, platform: "", query: "", stage: null });

  // 库口径(与 board-ext scope 语义对齐):员工恒 own-only(服务端硬闸);
  // 管理层缺省 scope=team 全团队收藏集(负责人下拉跨看单人仍走 ?staff_id=,在库模块内)。
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setAggLoading(true);
    setAggError("");
    getMyKolAggregate(apiToken, {
      ...(isManager ? { scope: "team" as const } : {}),
      mode: "summary",
      favoritesLimit: MY_KOL_PAGE_SIZE,
    })
      .then((res) => {
        if (alive) setAgg(res && typeof res === "object" ? res : null);
      })
      .catch((err: unknown) => {
        const detail = (err as { detail?: unknown; message?: unknown }) || {};
        if (alive) setAggError(String(detail.detail || detail.message || "加载失败"));
      })
      .finally(() => {
        if (alive) setAggLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, isManager, reloadTick]);

  const loadMoreFavorites = React.useCallback(async () => {
    const cursor = agg?.pool_favorites_page?.next_cursor;
    if (!apiToken || !cursor || aggLoadingMore) return;
    setAggLoadingMore(true);
    setAggLoadMoreError("");
    try {
      const next = await getMyKolAggregate(apiToken, {
        ...(isManager ? { scope: "team" as const } : {}),
        mode: "summary",
        favoritesLimit: MY_KOL_PAGE_SIZE,
        favoritesCursor: cursor,
      });
      setAgg((current) => {
        if (!current) return next;
        const existing = current.pool_favorites || [];
        const seen = new Set(existing.map((row) => String(row.kol_pool_id ?? row.id ?? "")));
        const merged = [...existing];
        for (const row of next.pool_favorites || []) {
          const key = String(row.kol_pool_id ?? row.id ?? "");
          if (key && seen.has(key)) continue;
          if (key) seen.add(key);
          merged.push(row);
        }
        return { ...current, ...next, pool_favorites: merged };
      });
    } catch (err) {
      const detail = (err as { detail?: unknown; message?: unknown }) || {};
      setAggLoadMoreError(String(detail.detail || detail.message || "更多 KOL 加载失败"));
    } finally {
      setAggLoadingMore(false);
    }
  }, [agg?.pool_favorites_page?.next_cursor, aggLoadingMore, apiToken, isManager]);

  const kpi = (agg?.kpi_summary || null) as Record<string, number> | null;
  const kolCount = kpi && Number.isFinite(Number(kpi.favorites_count)) ? Number(kpi.favorites_count) : null;

  // K3:主控 evidence_metrics 注入(exposure.all = SUM view_count 点时实测)。
  // 未注入/主控未就绪 → KpiCard 诚实 pending,绝不本地编数。
  const exposure = React.useMemo(() => {
    const metric = (metrics || []).find((item) => String((item as Row).id) === "exposure") as Row | undefined;
    const cell = (metric?.data as Row | undefined)?.all as Row | undefined;
    const value = cell && typeof cell.value === "number" && Number.isFinite(cell.value) ? (cell.value as number) : null;
    return { value, trend: cell ? String(cell.trend || "") : "" };
  }, [metrics]);

  // K4:official-matrix 各平台最新快照 Σ followers(0 账号 ≠ 0 粉丝 → 空矩阵走 pending)
  const officialFollowers = React.useMemo(() => {
    if (matrix.platforms.length === 0) return null;
    return matrix.platforms.reduce((sum, platform) => sum + (Number(platform.totalFollowers) || 0), 0);
  }, [matrix.platforms]);

  // 【M3】库行模型:aggregate.pool_favorites(已全量下发)+ claims 认领桥 → KolLibraryRow
  const libraryRows = React.useMemo(
    () => mapLibraryRows(agg?.pool_favorites, agg?.claims),
    [agg?.pool_favorites, agg?.claims],
  );
  const staffOptions = React.useMemo(
    () => (data?.staffMembers || []).map((member) => ({ id: member.id, name: member.name })),
    [data?.staffMembers],
  );
  const vKolCount = React.useMemo(() => {
    const value = Number(ext?.v_content?.v_kol_count);
    return String(ext?.v_content?.status || "") === "ready" && Number.isFinite(value) ? value : null;
  }, [ext?.v_content]);

  // 【M4】V 名单精确过滤:v_content.v_kol_ids → Set(去重升序名单;未就绪 = null →
  // library 内降级为已挂项目近似并如实标注;truncated 原样透出后端说明)
  const vKolIds = React.useMemo(() => {
    const group = ext?.v_content;
    if (String(group?.status || "") !== "ready" || !Array.isArray(group?.v_kol_ids)) return null;
    return new Set(group.v_kol_ids.map((id) => Number(id)).filter((id) => Number.isFinite(id) && id > 0));
  }, [ext?.v_content]);
  const vIdsTruncated = Boolean(ext?.v_content?.v_kol_ids_truncated);
  const vIdsNote = String(ext?.v_content?.v_kol_ids_note || "");

  // board-ext kpi_series 组(ready 才喂 KPI 带;error/缺席 → 四卡 spempty 诚实虚线)
  const kpiSeriesGroup = React.useMemo(() => {
    const group = ext?.kpi_series;
    return group && String(group.status || "") === "ready" ? group : null;
  }, [ext?.kpi_series]);
  const kolViews = kpiSeriesGroup?.kol_views;

  /* ---------- 卡头 props(金样板 cardProps 同构;本刀 SrcChip hover 口径卡起步,
     点击溯源弹窗随 M2/M3 dialogs 刀补——不借市场之声 GENERIC_CHAIN,链口径不同不冒充) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows, readableBody: true };
  };

  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载 MY KOL 数据。
    </PendingCard>
  );

  const kpiGate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (aggLoading && !agg) return <LoadingLine text="MY KOL 聚合读取中…" />;
    if (aggError) return <ErrorCard title="my-kol/aggregate 读取失败" text={aggError} />;
    if (!agg) return <LoadingLine text="MY KOL 聚合读取中…" />;
    return null;
  };

  /* ---------- board-ext 逐组 gate(金样板 extGate 同构:端点错 → ErrorCard,
     组缺席 → PendingCard,组 error/empty → 诚实降级;null = 放行画图) ---------- */
  const extGate = (group?: VkpiBoardExtGroup): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (extError) return <ErrorCard title="board-ext 读取失败" text={extError} />;
    if (!ext) return <LoadingLine text="看板聚合读取中…" />;
    if (!group) {
      return (
        <PendingCard>
          <b>该组字段缺席</b> —— board-ext 未返回本组,接通后自动点亮(不摆假图)。
        </PendingCard>
      );
    }
    if (String(group.status) === "error") return <ErrorCard title="该组聚合失败" text={String(group.reason || "未知原因")} />;
    if (String(group.status) === "empty") return <EmptyLine text={String(group.reason || "窗口内无本组数据。")} />;
    return null;
  };
  const basisRows = (group?: VkpiBoardExtGroup): Array<[string, string]> =>
    group && typeof group.basis === "string" ? [["后端口径", group.basis]] : [];

  /* ---------- 模块 body ---------- */

  const renderKpiBand = () => {
    const extraRows: Array<[string, string]> = agg
      ? [
          ["窗口", `${Number(agg.window_days) || 30} 天(aggregate 口径)`],
          ["视角", isManager ? "管理层 · 全团队收藏集(scope=team 服务端聚合)" : `${userName || "成员"} · own-only(服务端裁剪)`],
          ...(kolViews?.fill_rate != null
            ? ([["播放覆盖", `${pctText(kolViews.fill_rate)}% 实测填充(view_count 非空 / 全 evidence)`]] as Array<[string, string]>)
            : exposure.trend
              ? ([["播放覆盖", exposure.trend]] as Array<[string, string]>)
              : []),
          ...(extError ? ([["board-ext", `读取失败:${extError} —— 时序/环比药丸缺席不编数`]] as Array<[string, string]>) : []),
        ]
      : [];
    return (
      <ModuleCard {...cardProps("kpiM", "KOL 指标带", kolCount != null ? kolCount.toLocaleString() : undefined, extraRows)}>
        {kpiGate() ?? (
          <MyKolKpiBand
            kpi={kpi}
            kpiSeries={kpiSeriesGroup}
            exposureValue={exposure.value}
            exposureNote={
              extError
                ? "board-ext 读取失败且主控指标未注入 —— 点时实测读数缺席不编数"
                : "点时实测读数待接(board-ext kol_views / 主控注入均未就绪)"
            }
            officialFollowers={officialFollowers}
            officialNote={
              matrix.error && matrix.platforms.length === 0
                ? "official-matrix 读取失败"
                : matrix.loading
                  ? "官号矩阵读取中…"
                  : "暂无官号账号(账号目录 0 行)"
            }
          />
        )}
      </ModuleCard>
    );
  };

  // 【M6】五内嵌模块 → embeds 包装族(卡头真短计数 + 双标题非侵入收编;旧组件零改动)
  const renderDigest = () => <DigestEmbed apiToken={apiToken} noToken={noTokenCard} />;
  const renderTeam = () => <TeamEmbed apiToken={apiToken} data={data} matrix={matrix} noToken={noTokenCard} />;
  const renderOfficial = () => <OfficialEmbed apiToken={apiToken} matrix={matrix} noToken={noTokenCard} />;
  const renderRisk = () => <RiskEmbed apiToken={apiToken} noToken={noTokenCard} />;
  const renderRollup = () => <RollupEmbed apiToken={apiToken} viewMode={viewMode} noToken={noTokenCard} />;
  // 车道 L4:数值跟进(被追踪视频实测曲线 + 7d/30d 增量)/ 镜头出镜(深析内容按镜头汇总),模块自取数。
  const renderMetricTracking = () => <MetricTrackingEmbed apiToken={apiToken} noToken={noTokenCard} isManager={isManager} />;
  const renderLensExposure = () => <LensExposureEmbed apiToken={apiToken} noToken={noTokenCard} isManager={isManager} />;
  // 波 C·C3:观察清单(按既有员工分组跟进重点 KOL 的追踪/深析进度;模块自取 watch-overview 两端点)。
  const renderWatchlist = () => (
    <ModuleCard {...cardProps("watchlist", "观察清单")}>
      <WatchlistModule apiToken={apiToken} noToken={noTokenCard} />
    </ModuleCard>
  );
  // 波 D·C:单品播放数据(被「数据关注」登记的视频按单品聚合播放/增量;模块自取 sku-play-overview)。
  const renderSkuPlay = () => (
    <ModuleCard {...cardProps("skuPlay", "单品播放数据")}>
      <SkuPlayModule apiToken={apiToken} noToken={noTokenCard} />
    </ModuleCard>
  );
  // 经典视图(palette 备选):旧两栏库整体内嵌保留不删,默认体验统一走新版行式库。
  const renderLibClassic = () => (
    <LibClassicEmbed apiToken={apiToken} data={data} viewMode={viewMode} noToken={noTokenCard} onRefreshData={onRefreshData} />
  );

  // 分析动态(span4 小模组):进行中的 账号分析/视频深析/评论采集,泳道同源只读口;
  // 点行直达 KOL Pool(有主体开该 KOL 详情,无主体切泳道全景)。
  const renderActivity = () => (
    <ModuleCard {...cardProps("activity", "分析动态")}>
      {apiToken ? (
        <AnalysisActivityModule apiToken={apiToken} onJumpPool={() => onSelectPage?.("kolPoolV2")} />
      ) : (
        noTokenCard
      )}
    </ModuleCard>
  );

  // 【M3/M4】KOL 库真身:aggregate 行 + board-ext V 名单精确过滤;弹窗族在模块内自持。
  const renderLibrary = () => {
    const extraRows: Array<[string, string]> =
      vKolCount != null
        ? [
            ["V KOL 计数", `${vKolCount.toLocaleString()}(board-ext v_content · 全库口径)`],
            ...(vKolIds
              ? ([["V 名单", `${vKolIds.size.toLocaleString()} 个 kol_pool_id 精确过滤${vIdsTruncated ? " · 已截断(封顶 2000)如实降级" : ""}`]] as Array<[string, string]>)
              : []),
          ]
        : extError
          ? [["board-ext", `读取失败:${extError} —— V 计数/名单缺席不编数`]]
          : [];
    return (
      <ModuleCard {...cardProps("library", "KOL 库", kolCount != null ? kolCount.toLocaleString() : undefined, extraRows)}>
        {kpiGate() ?? (
          <KolLibraryModule
            apiToken={apiToken}
            baseRows={libraryRows}
            vKolCount={vKolCount}
            vKolIds={vKolIds}
            vIdsTruncated={vIdsTruncated}
            vIdsNote={vIdsNote}
            filter={libFilter}
            onFilter={setLibFilter}
            isManager={isManager}
            staffOptions={staffOptions}
            projects={data?.projects || []}
            page={agg?.pool_favorites_page}
            loadingMore={aggLoadingMore}
            loadMoreError={aggLoadMoreError}
            onLoadMore={loadMoreFavorites}
            onActionDone={() => {
              setReloadTick((tick) => tick + 1);
              if (onRefreshData) onRefreshData();
            }}
          />
        )}
      </ModuleCard>
    );
  };

  /* ---------- 【M4】图形模块族(数据 = board-ext 七组;extGate 逐组诚实降级) ---------- */

  const renderFunnel = () => {
    const g = ext?.funnel;
    return (
      <ModuleCard {...cardProps("funnel", "合作漏斗", g && String(g.status) === "ready" ? Number(g.total || 0).toLocaleString() : undefined, basisRows(g))}>
        {extGate(g) ?? (
          <FunnelBody
            funnel={g!}
            selectedStage={libFilter.stage?.stage || ""}
            onSelectStage={(seg) =>
              setLibFilter((f) => (f.stage?.stage === seg.stage ? { ...f, stage: null } : { ...f, stage: seg }))
            }
          />
        )}
      </ModuleCard>
    );
  };

  const renderFitdist = () => {
    const g = ext?.fit_dist;
    return (
      <ModuleCard {...cardProps("fitdist", "Fit 分布", g && String(g.status) === "ready" ? Number(g.total || 0).toLocaleString() : undefined, basisRows(g))}>
        {extGate(g) ?? <FitDistBody fitDist={g!} />}
      </ModuleCard>
    );
  };

  const renderPlatdist = () => {
    const g = ext?.platform_dist;
    return (
      <ModuleCard {...cardProps("platdist", "平台分布", g && String(g.status) === "ready" ? Number(g.total || 0).toLocaleString() : undefined, basisRows(g))}>
        {extGate(g) ?? (
          <PlatDistBody
            dist={g!}
            selectedPlatform={libFilter.platform}
            onSelectPlatform={(platform) =>
              setLibFilter((f) => ({ ...f, platform: f.platform === platform ? "" : platform }))
            }
          />
        )}
      </ModuleCard>
    );
  };

  // 内容墙(span8):收藏集最近采集视频网格(board-ext recent_videos);
  // 选单 KOL 改走 /kol-pool/{id}/videos(库详情同源);空态按板面文案(不透传后端 reason)。
  const wallKolOptions = React.useMemo(
    () =>
      libraryRows
        .map((row) => ({ poolId: row.poolId, name: row.name }))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [libraryRows],
  );
  const renderContentWall = () => {
    const g = ext?.recent_videos;
    const n = g && String(g.status) === "ready" && Array.isArray(g.items) ? g.items.length : undefined;
    return (
      <ModuleCard {...cardProps("contentWall", "内容墙", n != null ? `近 ${n} 条` : undefined, basisRows(g))}>
        {String(g?.status || "") === "empty" ? (
          <EmptyLine text="暂无已采集内容——可在KOL详情发起补采。" />
        ) : (
          extGate(g) ?? <ContentWallModule apiToken={apiToken} group={g!} kolOptions={wallKolOptions} />
        )}
      </ModuleCard>
    );
  };

  const renderViewsTop = () => {
    const g = ext?.views_top;
    const n = g?.items?.length || 0;
    return (
      <ModuleCard {...cardProps("viewsTop", "播放 Top 视频", g && String(g.status) === "ready" ? `Top ${n}` : undefined, basisRows(g))}>
        {extGate(g) ?? <ViewsTopBody viewsTop={g!} />}
      </ModuleCard>
    );
  };

  const renderContacts = () => {
    const g = ext?.contact_coverage;
    const coverage = typeof g?.coverage === "number" && Number.isFinite(g.coverage) ? g.coverage : null;
    return (
      <ModuleCard {...cardProps("contacts", "联系方式覆盖", g && String(g.status) === "ready" && coverage != null ? `${pctText(coverage)}%` : undefined, basisRows(g))}>
        {extGate(g) ?? <ContactsBody cc={g!} />}
      </ModuleCard>
    );
  };

  const renderFollowerTrend = () => {
    const g = ext?.kpi_series;
    return (
      <ModuleCard {...cardProps("followerTrend", "粉丝趋势", g && String(g.status) === "ready" ? "双线 · 按日" : undefined)}>
        {extGate(g) ?? <FollowerTrendBody kpiSeries={g!} />}
      </ModuleCard>
    );
  };

  const renderClaims = () => {
    const claims = Array.isArray(agg?.claims) ? (agg.claims as Array<Record<string, unknown>>) : [];
    return (
      <ModuleCard {...cardProps("claims", "我的认领", agg ? `${claims.length}` : undefined)}>
        {kpiGate() ?? <ClaimsBody claims={claims} />}
      </ModuleCard>
    );
  };

  const renderShares = () => {
    const shared = libraryRows.filter((row) => row.isShared);
    return (
      <ModuleCard {...cardProps("shares", "共享池", agg ? `${shared.length}` : undefined)}>
        {kpiGate() ?? <SharesBody rows={shared} />}
      </ModuleCard>
    );
  };

  /* ---------- 模块注册表(palette 全量可选;risk/rollup 管理层专属 = 员工注册表直接不含,
     默认布局经 moduleMap 过滤自动少两块 —— 不渲染 403 卡,裁决②A) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiM", label: "KOL 指标带", description: "在库 / 合作推进 / 内容播放实测 / 官号粉丝 + 关联时序与环比", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "digest", label: "每日学习摘要", description: "收藏 KOL + 官号变化 · daily-digest 真聚合(内嵌)", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 11, minHeight: 5, maxHeight: 26, render: renderDigest },
    { key: "funnel", label: "合作漏斗", description: "8 段真阶段条形 · 点段过滤 KOL 库", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 4, maxHeight: 16, render: renderFunnel },
    { key: "team", label: "团队矩阵", description: "负责人卡 + 分管 KOL · TeamMatrix 内嵌", category: "业务板块", defaultSpan: 8, minSpan: 6, defaultHeight: 13, minHeight: 6, maxHeight: 32, render: renderTeam },
    { key: "activity", label: "分析动态", description: "进行中的账号分析/深析/评论采集 · 点行直达 KOL Pool", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 13, minHeight: 4, maxHeight: 20, render: renderActivity },
    { key: "watchlist", label: "观察清单", description: "按分组跟进重点 KOL:追踪视频 / 最近快照 / 7 天播放增量 / 深析完成比 / 待处理失败", category: "业务板块", defaultSpan: 12, minSpan: 6, defaultHeight: 12, minHeight: 5, maxHeight: 30, render: renderWatchlist },
    { key: "skuPlay", label: "单品播放数据", description: "被「数据关注」登记的视频按单品聚合:累计播放 / Δ7 天增量 / 逐视频实测明细", category: "业务板块", defaultSpan: 12, minSpan: 6, defaultHeight: 12, minHeight: 5, maxHeight: 30, render: renderSkuPlay },
    { key: "library", label: "KOL 库", description: "收藏/共享全量 + 行级采集数据 · V 名单精确筛选 + 分区详情连续翻", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 5, maxHeight: 36, render: renderLibrary },
    { key: "contentWall", label: "内容墙", description: "收藏集已采集内容 · KOL/Viltrox证据/排序筛选 · 点卡直跳原帖", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 5, maxHeight: 48, render: renderContentWall },
    { key: "fitdist", label: "Fit 分布", description: "全池十分位直方 + 未评分诚实桶(只读)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 4, maxHeight: 16, render: renderFitdist },
    { key: "official", label: "官方账号矩阵", description: "当前官号平台总览 · OfficialMatrix 内嵌", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 6, maxHeight: 32, render: renderOfficial },
    { key: "platdist", label: "平台分布", description: "收藏集按平台条形 · 点行过滤 KOL 库", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 13, minHeight: 4, maxHeight: 16, render: renderPlatdist },
    { key: "metricTracking", label: "数值跟进", description: "被追踪视频的实测曲线 + 7 天 / 30 天播放·点赞·评论增量与日均", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 14, minHeight: 6, maxHeight: 30, render: renderMetricTracking },
    { key: "lensExposure", label: "镜头出镜", description: "已深析内容按镜头汇总 · 出镜视频数 / KOL 数 / 播放 / 证据来源", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 14, minHeight: 5, maxHeight: 30, render: renderLensExposure },
    ...(isManager
      ? ([
          { key: "risk", label: "KOL 风险指数", description: "final_v1 深析信号聚合 · 管理层专属(内嵌)", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 11, minHeight: 5, maxHeight: 26, render: renderRisk },
          { key: "rollup", label: "贡献度聚合", description: "每负责人一行 · 管理层专属(内嵌)", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 5, maxHeight: 26, render: renderRollup },
        ] as DashboardModuleDefinition[])
      : []),
    // ↓ M4 六真身:viewsTop 2026-07-12 起进默认布局(内容墙旁柱 span4),其余仍为 palette 备选
    { key: "viewsTop", label: "播放 Top 视频", description: "实测播放 Top 12 KOL 条形榜(NULL 剔除)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 13, minHeight: 4, maxHeight: 24, render: renderViewsTop },
    { key: "contacts", label: "联系方式覆盖", description: "覆盖率大数 + 类型条形(永远零明文)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 16, render: renderContacts },
    { key: "followerTrend", label: "粉丝趋势", description: "收藏集 vs 官号双线 · 缺快照日断线", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 8, minHeight: 4, maxHeight: 20, render: renderFollowerTrend },
    { key: "claims", label: "我的认领", description: "本人认领行 · active/到期如实列出", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 16, render: renderClaims },
    { key: "shares", label: "共享池", description: "共享给我的真实库行 · 空集显示空态", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 16, render: renderShares },
    // 经典视图(libclassic)已退役出 palette(2026-07-12 用户裁决:旧两栏皮肤拉低质感,
    // 功能已被新版行式库+详情弹窗零丢失覆盖)。想临时找回:恢复下一行 + embeds 的 renderLibClassic。
    // { key: "libclassic", label: "经典视图 · KOL 库", description: "升级前两栏库整体内嵌", category: "业务板块", defaultSpan: 12, minSpan: 6, defaultHeight: 16, minHeight: 8, maxHeight: 36, render: renderLibClassic },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="MY KOL" />;
  }

  // 员工视角:默认布局里的 risk/rollup 会被 EditableDashboardBoard 的 moduleMap 过滤
  // (定义缺席 → 布局项丢弃),这里再显式滤一遍,语义自证 + 不依赖板组件实现细节。
  const availableKeys = new Set(modules.map((module) => module.key));
  const defaultLayout = DEFAULT_LAYOUT.filter((item) => availableKeys.has(item.moduleKey));

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 药丸徽(视角 / KOL 数)+ 实时辉光点 + 刷新 + 编辑布局 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">MY KOL</span>
          <span className={PH_BADGE}>{isManager ? "管理层视角" : `${userName || "成员"} · own-only`}</span>
          {kolCount != null && <span className={PH_BADGE}>{kolCount.toLocaleString()} KOL</span>}
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            实时
          </span>
          <button
            type="button"
            onClick={() => {
              setReloadTick((tick) => tick + 1);
              void matrix.refresh();
              if (onRefreshData) onRefreshData();
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

      {!apiToken && <div className="mb-3">{noTokenCard}</div>}
      {aggError && (
        <div className="mb-3">
          <ErrorCard title="my-kol/aggregate 读取失败" text={aggError} />
        </div>
      )}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={defaultLayout} editing={editing} storageKey={STORAGE_KEY} />
    </div>
  );
}
