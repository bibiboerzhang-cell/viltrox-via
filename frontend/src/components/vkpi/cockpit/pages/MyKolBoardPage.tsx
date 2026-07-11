import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { getMyKolAggregate, type VkpiMyKolAggregateResponse } from "../../../../services/vkpi/kol-api";
import { useOfficialChannelMatrix } from "../../pages/channels/useOfficialChannelMatrix";
import { DailyDigestCard } from "../../pages/myKol/DailyDigestCard";
import { RiskIndexPanel } from "../../pages/myKol/RiskIndexPanel";
import { ContributionRollupPanel } from "../../pages/myKol/ContributionRollupPanel";
import type { VkpiDashboardData, VkpiPageKey } from "../../vkpiTypes";
import { ErrorCard, LoadingLine, ModuleCard, PendingCard, type Row } from "./MarketVoicePage.modules";
import {
  MODULE_SOURCES,
  MyKolKpiBand,
  OfficialMatrixModule,
  PendingBody,
  TeamMatrixModule,
} from "./MyKolBoardPage.modules";

// MY KOL → 板块页范式改版 · M1 骨架刀(金样板 = MarketVoicePage 可编辑板块页 1:1 同构)。
//   页壳:pagehead(标题 + 视角/KOL 数药丸徽 + 实时辉光点 + 编辑布局钮)+
//   EditableDashboardBoard(模块注册表 + 默认布局六行 + palette 备选)。
//   数据源(全真,零编造,本刀全走现成端点):
//     GET /api/admin/vkpi/my-kol/aggregate        —— K1 在库 KOL / K2 合作推进中(kpi_summary)
//     GET /api/marketing/channels/official-matrix —— K4 官号粉丝(vkpi_channel_metrics 最新快照)
//     metrics prop(CockpitApp dashboardRuntime)  —— K3 内容播放实测 = 主控 evidence_metrics
//       .total_exposure(SUM view_count 点时实测 · 非时序;复用主控已拉数据,零重复请求、
//       零触发 /dashboard 的 metric_lineage 隐藏写入);series/delta 等 M2 board-ext 端点。
//   模块落地:kpiM 真值;digest/team/official/risk/rollup 内嵌 pages/myKol 现有组件(零重写,
//   M2+ 再模块化);funnel/library/fitdist/platdist 与 palette 备选 = PendingCard 诚实待接。
//   用户裁决②=A:risk/rollup 管理层专属 —— 员工视角注册表直接不出现(默认布局自动少两块),
//   不是 403 卡。旧 MyKolPage.tsx 保留不删(回滚垫);跨页事件管道(vkpi:open-mykol-kol /
//   vkpi:open-kol-pool-item 等)签名不变,内嵌组件自带监听照常工作。
// 红线:纯展示,绝不写 viltrox fit 分 / 不触 rule_v0;颜色全 token 零写死色;发光只走
//   既有 ds-* 类(自带 reduced-motion 降级);端点失败 = 诚实错误卡。布局只走本机
//   storageKey,不传 apiToken 给 EditableDashboardBoard(其账户级持久化写死
//   dashboard_layout_v1 键,传了会覆写 Dashboard 布局 —— 金样板同注释)。

const STORAGE_KEY = "vkpi-my-kol-layout-v1";

// 默认布局(12 列 · 设计单定稿六行):
// kpiM(12) → digest(8)+funnel(4) → team(12) → library(8)+fitdist(4)
// → official(8)+platdist(4) → risk(8)+rollup(4)(manager-only,员工视角自动六行变五行)
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiM", span: 12 },
  { moduleKey: "digest", span: 8 },
  { moduleKey: "funnel", span: 4 },
  { moduleKey: "team", span: 12 },
  { moduleKey: "library", span: 8 },
  { moduleKey: "fitdist", span: 4 },
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
}

export function MyKolBoardPage({
  apiToken = "",
  viewMode,
  data,
  userName,
  onRefreshData,
  metrics,
}: MyKolBoardPageProps) {
  const isManager = viewMode === "manager";
  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);

  // K1/K2 + 徽章 KOL 数:统一只读聚合端点(员工 own-only 由后端 scope 强制)
  const [agg, setAgg] = React.useState<VkpiMyKolAggregateResponse | null>(null);
  const [aggLoading, setAggLoading] = React.useState(false);
  const [aggError, setAggError] = React.useState("");

  // K4 官号粉丝 + team/official 模块共用一份矩阵(hook 自带 30s 内存缓存 + localStorage SWR)
  const matrix = useOfficialChannelMatrix(apiToken || undefined);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setAggLoading(true);
    setAggError("");
    getMyKolAggregate(apiToken)
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
  }, [apiToken, reloadTick]);

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

  /* ---------- 卡头 props(金样板 cardProps 同构;本刀 SrcChip hover 口径卡起步,
     点击溯源弹窗随 M2/M3 dialogs 刀补——不借市场之声 GENERIC_CHAIN,链口径不同不冒充) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows };
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

  /* ---------- 模块 body ---------- */

  const renderKpiBand = () => {
    const extraRows: Array<[string, string]> = agg
      ? [
          ["窗口", `${Number(agg.window_days) || 30} 天(aggregate 口径)`],
          ["视角", isManager ? "管理层 · 全量" : `${userName || "成员"} · own-only(服务端裁剪)`],
          ...(exposure.trend ? ([["播放覆盖", exposure.trend]] as Array<[string, string]>) : []),
        ]
      : [];
    return (
      <ModuleCard {...cardProps("kpiM", "KOL 指标带", kolCount != null ? kolCount.toLocaleString() : undefined, extraRows)}>
        {kpiGate() ?? (
          <MyKolKpiBand
            kpi={kpi}
            exposureValue={exposure.value}
            exposureNote="主控 evidence_metrics 未注入 · M2 board-ext 自取"
            officialFollowers={officialFollowers}
            officialNote={
              matrix.error && matrix.platforms.length === 0
                ? "official-matrix 读取失败"
                : matrix.loading
                  ? "官号矩阵读取中…"
                  : "暂无官号账号(vkpi_employee_channels 空)"
            }
          />
        )}
      </ModuleCard>
    );
  };

  const renderDigest = () => (
    <ModuleCard {...cardProps("digest", "每日学习摘要")}>
      {apiToken ? <DailyDigestCard apiToken={apiToken} /> : noTokenCard}
    </ModuleCard>
  );

  const renderTeam = () => (
    <ModuleCard {...cardProps("team", "团队矩阵", matrix.accountCount ? `${matrix.accountCount} 账号` : undefined)}>
      {apiToken ? <TeamMatrixModule data={data} matrix={matrix} /> : noTokenCard}
    </ModuleCard>
  );

  const renderOfficial = () => (
    <ModuleCard {...cardProps("official", "官方账号矩阵", matrix.platforms.length ? `${matrix.platforms.length} 平台` : undefined)}>
      {apiToken ? <OfficialMatrixModule apiToken={apiToken} matrix={matrix} /> : noTokenCard}
    </ModuleCard>
  );

  const renderRisk = () => (
    <ModuleCard {...cardProps("risk", "KOL 风险指数")}>
      {apiToken ? <RiskIndexPanel apiToken={apiToken} /> : noTokenCard}
    </ModuleCard>
  );

  const renderRollup = () => (
    <ModuleCard {...cardProps("rollup", "贡献度聚合")}>
      {apiToken ? <ContributionRollupPanel apiToken={apiToken} viewMode={viewMode} /> : noTokenCard}
    </ModuleCard>
  );

  const pendingModule = (key: string, title: string, stage: string, note: string) => (
    <ModuleCard {...cardProps(key, title)}>
      <PendingBody stage={stage}>{note}</PendingBody>
    </ModuleCard>
  );

  /* ---------- 模块注册表(palette 全量可选;risk/rollup 管理层专属 = 员工注册表直接不含,
     默认布局经 moduleMap 过滤自动少两块 —— 不渲染 403 卡,裁决②A) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiM", label: "KOL 指标带", description: "在库 / 合作推进 / 内容播放实测 / 官号粉丝(现值 · 时序等 M2)", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "digest", label: "每日学习摘要", description: "收藏 KOL + 官号变化 · daily-digest 真聚合(内嵌)", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 11, minHeight: 5, maxHeight: 26, render: renderDigest },
    { key: "funnel", label: "四环漏斗", description: "收藏→认领→进项目→已发布 · M2 接线", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 4, maxHeight: 16, render: () => pendingModule("funnel", "四环漏斗", "M2", "board-ext 端点接通后点亮四环漏斗(收藏→认领→进项目→已发布),本刀诚实待接不摆假漏斗。") },
    { key: "team", label: "团队矩阵", description: "负责人卡 + 分管 KOL · TeamMatrix 内嵌", category: "业务板块", defaultSpan: 12, minSpan: 6, defaultHeight: 13, minHeight: 6, maxHeight: 32, render: renderTeam },
    { key: "library", label: "KOL 库", description: "员工 KOL 库模块化 · M2/M3 接线", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 8, minHeight: 4, maxHeight: 24, render: () => pendingModule("library", "KOL 库", "M2/M3", "EmployeeKolLibrary 模块化搬入(收藏/共享/漏斗过滤)在后续刀;底表 aggregate.pool_favorites 已在读。") },
    { key: "fitdist", label: "Fit 分布", description: "在库 KOL fit 只读分布 · M2 接线", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 16, render: () => pendingModule("fitdist", "Fit 分布", "M2", "board-ext 端点接通后点亮 fit 只读分布(评分公式永不进前端)。") },
    { key: "official", label: "官方账号矩阵", description: "18 官号平台总览 · OfficialMatrix 内嵌", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 6, maxHeight: 32, render: renderOfficial },
    { key: "platdist", label: "平台分布", description: "在库 KOL 按平台分桶 · M2 接线", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 13, minHeight: 4, maxHeight: 16, render: () => pendingModule("platdist", "平台分布", "M2", "board-ext 端点接通后点亮在库 KOL 平台分桶(vkpi_kol_pool.platform 纯读)。") },
    ...(isManager
      ? ([
          { key: "risk", label: "KOL 风险指数", description: "final_v1 深析信号聚合 · 管理层专属(内嵌)", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 11, minHeight: 5, maxHeight: 26, render: renderRisk },
          { key: "rollup", label: "贡献度聚合", description: "每负责人一行 · 管理层专属(内嵌)", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 5, maxHeight: 26, render: renderRollup },
        ] as DashboardModuleDefinition[])
      : []),
    // ↓ palette 备选(不进默认布局;设计单定稿六项,全部诚实待接)
    { key: "viewsTop", label: "播放 Top 视频", description: "view_count 实测降序榜 · M2 接线", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 8, minHeight: 4, maxHeight: 24, render: () => pendingModule("viewsTop", "播放 Top 视频", "M2", "vkpi_kol_video_evidence 播放榜接 board-ext 后点亮。") },
    { key: "contacts", label: "联系方式覆盖", description: "类型/来源计数(明文走门控)· M2 接线", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 16, render: () => pendingModule("contacts", "联系方式覆盖", "M2", "vkpi_kol_pool_contacts 覆盖计数接线后点亮;明文永远走 contact_reveal 门控。") },
    { key: "followerTrend", label: "粉丝趋势", description: "官号日快照序列;KOL 侧无历史如实标 · M2", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 8, minHeight: 4, maxHeight: 20, render: () => pendingModule("followerTrend", "粉丝趋势", "M2", "官号 vkpi_channel_metrics 有日快照可画;KOL 池无历史快照,接线后也只画官号侧,诚实分轨。") },
    { key: "claims", label: "认领状态", description: "vkpi_kol_claims active/到期 · M2 接线", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 16, render: () => pendingModule("claims", "认领状态", "M2", "认领(active/expired + 到期窗口)接线后点亮。") },
    { key: "shares", label: "共享池", description: "vkpi_kol_pool_members 只读授予 · M2 接线", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 16, render: () => pendingModule("shares", "共享池", "M2", "共享行(谁共享给谁)接线后点亮。") },
    { key: "cover", label: "数据覆盖", description: "逐源健康 + 盲区如实标注 · M3 接线", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 20, render: () => pendingModule("cover", "数据覆盖", "M3", "金样板 cover 同构的逐源健康盘点,M3 接线。") },
  ];

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
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 6px var(--ds-good)" }} />
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
