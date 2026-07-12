import React from "react";
import {
  EmptyLine,
  ErrorCard,
  LoadingLine,
  type Row,
} from "./MarketVoicePage.modules";
import {
  KolDetailModal,
  KolLibraryListModal,
  KolRowLine,
  LibraryChips,
} from "./MyKolBoardPage.dialogs";
import {
  filterLibraryRows,
  libraryPlatformOptions,
  mapLibraryRows,
  type KolLibraryRow,
  type LibraryFilter,
} from "../../../../services/vkpi/myKolBoard-api";
import { OfficialMatrix, TeamMatrix } from "../../pages/myKol/MyKolPage.Sections";
import {
  isGenericStaffShell,
  knownStaffDisplay,
  matchesKnownStaff,
  staffDisplayRole,
  type StaffCard,
} from "../../pages/myKol/MyKolPage.helpers";
import type { useOfficialChannelMatrix } from "../../pages/channels/useOfficialChannelMatrix";
import type { OfficialChannelAccount } from "../../pages/channels/channelTypes";
import type { VkpiDashboardData, VkpiProjectRow } from "../../vkpiTypes";
import "../../pages/myKol/myKolPage.css";
import "../../pages/myKol/myKolTeamMatrix.css";

// MY KOL 板块页范式 · 辅助件(M1 骨架 → M4 图形真身 → M6 内嵌收编,金样板 =
//   MarketVoicePage.modules 同构)。通用骨架件(ModuleCard/PendingCard/EmptyLine/
//   ErrorCard/LoadingLine)直接从 MarketVoicePage.modules 复用(施工单放行「引组件」,
//   零平移重写);本文件只放 MY KOL 专属件:MODULE_SOURCES 溯源注册表 /
//   TeamMatrix·OfficialMatrix 内嵌包装(【M6】ModuleCard 卡头收编住
//   MyKolBoardPage.embeds.tsx,本文件的 useTeamStaffCards 供 embeds 取真负责人数)/
//   KOL 库模块(M3 弹窗族 + M4 精确 V 名单过滤 + 漏斗阶段联动)。
//   图表件(KPI 带四卡/漏斗/直方/条形/双线/认领/共享/覆盖)住 MyKolBoardPage.charts.tsx
//   (M4 建成);SrcChip hover 口径卡,点击溯源弹窗随后续刀补(不复用市场之声的
//   GENERIC_CHAIN——那条链是评论域口径,挂这里会说谎)。
// 红线:本文件零直连网络(取数住 page 层/内嵌组件自带);纯展示绝不写 fit 分/rule_v0;
//   颜色全 token 类零写死色;诚实空态(无历史快照 → spempty,降级/截断如实标注)。

/* ============ 溯源注册表(金样板 MODULE_SOURCES 同构;label=真实端点/表名,rows=真实
   行数与口径,2026-07-11 实测,禁编造) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiM: {
    label: "my-kol/aggregate · board-ext kpi_series",
    rows: [
      ["在库 KOL", "vkpi_kol_pool_favorites(779 行 · staff×kol 收藏对)+ 共享行 vkpi_kol_pool_members"],
      ["合作推进中", "vkpi_project_kol_assignments(2,189 行)在役 stage 去重 KOL"],
      ["内容播放", "vkpi_kol_video_evidence.view_count(board-ext kol_views 自取 · 主控注入兜底)"],
      ["播放口径", "点时实测 · 非时序 —— SUM(view_count) 当前快照,无历史序列 → K3 诚实虚线零药丸"],
      ["官号粉丝", "vkpi_channel_metrics(961 行 · 100% 填充)每账号最新快照 Σ followers"],
      ["K1 趋势线", "vkpi_kol_fit_snapshot 收藏集粉丝按日 SUM · 快照缺日 null 断点如实(关联时序,非在库数)"],
      ["K2 趋势线", "vkpi_kol_video_evidence 新视频/日(计数型 0 填齐;关联时序,非推进中数)"],
      ["K4 趋势线", "vkpi_channel_metrics 官号粉丝按日 SUM(日快照全量)"],
      ["环比药丸", "board-ext kpi_series.metrics 与趋势线同源同指标 · 上窗无数 → 药丸诚实不渲染"],
    ],
  },
  digest: {
    label: "my-kol/daily-digest",
    rows: [
      ["聚合", "vkpi_kol_video_evidence + vkpi_channel_metrics + vkpi_kol_pool_contacts 纯读聚合"],
      ["窗口", "1/7/30 天切换钮在卡内(内嵌组件内部状态,无外部口)——卡头不摆假窗口徽"],
      ["范围", "员工只看自己集合 · 管理层默认全员并集(后端 scope 裁剪)"],
      ["降级", "接口失败整卡安静缺席 · 各块 empty 带后端 reason 原样透出"],
    ],
  },
  funnel: {
    label: "board-ext funnel · assignments",
    rows: [
      ["口径", "vkpi_project_kol_assignments × 收藏集(收藏 ∪ 共享,去重)按 stage 行计数"],
      ["归并", "13 真阶段经 stage_canonical 归并 8 段展示(存量值 arrived 读侧补映射已签收)"],
      ["联动", "点段=按该段真库 raw 阶段过滤 KOL 库行;段计数=指派行、库行=KOL,两口径如实不强对齐"],
      ["未知桶", "未识别新阶段值落 other 诚实桶,绝不吞行"],
    ],
  },
  team: {
    label: "staff · official-matrix staff_managed",
    rows: [
      ["负责人", "staff 表(20 行 · 2026-07-11 实测)+ users 目录(员工视角 staff-directory 为管理层端点 → 卡列自然收敛)"],
      ["卡头计数", "负责人卡数 = 已知展示元数据 ∪ 真 staff 目录合并去重(与旧头「负责人」chip 同源)"],
      ["归属账号", "vkpi_employee_channels 按 staff_id 归属"],
      ["分管 KOL", "official-matrix.staff_managed(数量/粉丝合计/名单 cap 20)"],
    ],
  },
  library: {
    label: "my-kol/aggregate · board-ext",
    rows: [
      ["在库行", "vkpi_kol_pool_favorites + 共享 vkpi_kol_pool_members(aggregate.pool_favorites 全量下发)"],
      ["范围", "员工=own-only(服务端硬闸);管理层缺省 scope=team 全团队收藏集(收藏 ∪ 共享去重,与 board-ext 同两表同判据)"],
      ["状态徽", "收藏/共享=行本体;进行中=挂 assignments;已认领=vkpi_kol_claims 平台+名称桥(真值在详情 viewer-context)"],
      ["V 视频 KOL", "board-ext v_content.v_kol_count —— 至少 1 条合作/标题提及视频的去重 KOL(全库口径)"],
      ["V 三档判据", "合作=挂项目(project_id 非空)/ 标题提及=标题含 viltrox(不分大小写)/ 其余=未判定 —— 派生规则非采集字段(classify_v_content 同口径)"],
      ["列表 V 筛选", "board-ext v_content.v_kol_ids 名单精确过滤(去重升序,封顶 2000;超封顶 truncated 如实降级提示;名单缺席时降级为已挂项目近似并标注)"],
      ["KOL 级三档", "tiers_by_kol=cooperation_kols/title_mention_kols(KOL 级去重,同一 KOL 两档可重复计,与条数级 tiers 区分)"],
      ["单 KOL 视频", "GET /kol-pool/{id}/videos(view_count 点时实测 · NULL 剔除注明)"],
      ["负责人筛选", "管理层按 ?staff_id= 服务端 scope 重取,零本地猜"],
    ],
  },
  fitdist: {
    label: "board-ext fit_dist · vkpi_kol_pool",
    rows: [
      ["口径", "全池(duplicate_of_id IS NULL)fit 分十分位分桶直方 · 纯读展示"],
      ["评分", "规则分口径(rule_v0 系写点,与本卡无关)· 评分公式永不进前端"],
      ["未评分", "分值为空的诚实桶 · 绝不当 0 分"],
      ["红线", "接口只回分桶计数,不含任何单 KOL 分数;本卡零写零触发打分"],
    ],
  },
  official: {
    label: "vkpi_employee_channels · vkpi_channel_metrics",
    rows: [
      ["账号", "18 官号 · /api/marketing/channels/official-matrix(卡头计数=account_count 真值)"],
      ["指标", "vkpi_channel_metrics(961 行 · 100% 填充 · 2026-07-11 实测)每账号最新快照(followers/posts/views + delta)"],
      ["内容层", "channels/{id}/posts 按需分页(内嵌组件自取)"],
      ["个人矩阵", "official-matrix.personal 增量分组=官号名单之外的成员个人账号(持有人 staff→users)· 现状 0 行=诚实空态;帖子/播放复用同一条 channels/{id}/posts 链"],
    ],
  },
  platdist: {
    label: "board-ext platform_dist · vkpi_kol_pool",
    rows: [
      ["口径", "收藏集(收藏 ∪ 共享,去重)按 platform 分桶(纯读 GROUP BY)"],
      ["联动", "点行=KOL 库按平台过滤(与库筛选 chips 同一份状态)"],
      ["平台名", "门面映射 unknown→未知 / media→媒体站,其余首字母大写;过滤键仍用平台原值"],
    ],
  },
  risk: {
    label: "my-kol/risk-index",
    rows: [
      ["信号", "Gemini final_v1 深析结构化信号聚合(内容无深度/素材复用/竞品露出)"],
      ["深析表", "llm_deep_analysis_results(481 行 · 2026-07-11 实测)· 已析/总覆盖读数见卡内右上"],
      ["诚实", "只覆盖已深析 KOL · 未深析显「未分析」灰态,绝不当 0 风险"],
      ["可见", "管理层专属(裁决②A)· 员工注册表直接不出现"],
    ],
  },
  rollup: {
    label: "my-kol/contribution-rollup",
    rows: [
      ["口径", "每负责人一行:在管 KOL / 已发布 / 归因销售(has_attribution 诚实降级)"],
      ["数据表", "vkpi_kol_claims 在管 / vkpi_content_posts 已发布(0 行 · 盲区)/ vkpi_goaffpro_sales 归因(0 行 · 盲区,2026-07-11 实测)"],
      ["门禁", "后端 scope.can_view_all 二次 gate · 管理层专属(裁决②A)"],
    ],
  },
  viewsTop: {
    label: "board-ext views_top · vkpi_kol_video_evidence",
    rows: [
      ["口径", "view_count 点时实测按 KOL SUM · Top 12(全 evidence 口径,不随视角收窄)"],
      ["剔除", "view_count IS NULL(未实测 ≠ 0 播放)与 is_active=FALSE(归属纠错下线)行"],
    ],
  },
  contacts: {
    label: "board-ext contact_coverage · vkpi_kol_pool_contacts",
    rows: [
      ["类型计数", "全池 GROUP BY contact_type(明文值一列不进 SELECT)"],
      ["覆盖率", "收藏集内至少一条联系方式的 KOL 占比 · 分母 0 → 诚实 null"],
      ["明文", "永远走 contact_reveal 门控端点,本卡零明文联系方式"],
    ],
  },
  followerTrend: {
    label: "board-ext kpi_series · 双快照源",
    rows: [
      ["收藏侧", "vkpi_kol_fit_snapshot 按 snapshot_date SUM(followers)· 缺快照日断线不插值"],
      ["官号侧", "vkpi_channel_metrics × vkpi_employee_channels 按 snapshot_date SUM(followers)"],
      ["口径", "两线同轴同刻度 · 存量型快照,右沿=今天,零未来日"],
    ],
  },
  claims: {
    label: "my-kol/aggregate · vkpi_kol_claims",
    rows: [
      ["行", "本人认领(FK=kols.id,LEFT JOIN kols 回填名称/平台)"],
      ["状态", "active/expired + 到期时间原样展示 · 无行=诚实空"],
    ],
  },
  shares: {
    label: "my-kol/aggregate · vkpi_kol_pool_members",
    rows: [
      ["口径", "共享给我的库行(is_shared)+ 共享人展示名 · 只读可见性授予"],
      ["诚实", "0 行=已建未用如实空,不装 live"],
    ],
  },
  cover: {
    label: "静态盘点 2026-07-11 · 六源",
    rows: [
      ["性质", "board-ext 无此组 → 盘点日硬编码读数,非实时、会过期(卡面同款标注)"],
      ["外联记录", "kol_outreach(0 行)"],
      ["合作事件", "vkpi_kol_cooperation_events(0 行)"],
      ["生命周期", "vkpi_kol_lifecycle_events(0 行)"],
      ["联盟销售", "vkpi_goaffpro_sales / _kol_links(0 行)"],
      ["内容手录", "vkpi_content_posts(0 行)"],
      ["触点", "vkpi_kol_pool_touches(2 行)"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiM: "KOL 指标带",
  digest: "每日学习摘要",
  funnel: "合作漏斗",
  team: "团队矩阵",
  library: "KOL 库",
  fitdist: "Fit 分布",
  official: "官方账号矩阵",
  platdist: "平台分布",
  risk: "KOL 风险指数",
  rollup: "贡献度聚合",
  viewsTop: "播放 Top 视频",
  contacts: "联系方式覆盖",
  followerTrend: "粉丝趋势",
  claims: "我的认领",
  shares: "共享池",
  cover: "数据覆盖",
};

// KPI 带四卡与中文紧凑数(fmtZhCompact)M4 起搬家到 MyKolBoardPage.charts.tsx
// (series 接线后属图表族);待接线占位体 PendingBody 随 M4 全模块点亮退役。

export type MatrixState = ReturnType<typeof useOfficialChannelMatrix>;

/* ============ 团队矩阵模块(TeamMatrix 内嵌;staffCards 组装逻辑自 MyKolPage.tsx
   原样平移——已知负责人展示元数据 + 真 staff 目录合并、staff_managed 按 id 桥接;
   选中态本刀仅高亮自身,联动过滤 KOL 库随 library 模块 M2/M3 刀接回。
   【M6】staffCards 组装抽成 useTeamStaffCards 钩子:embeds 包装层要用 cards.length
   做卡头真短计数(负责人数),模块本体只吃算好的 cards/pendingCount,零重复计算) ============ */
export function useTeamStaffCards(data: VkpiDashboardData | undefined, matrix: MatrixState) {
  const projects = React.useMemo<VkpiProjectRow[]>(() => data?.projects || [], [data?.projects]);
  const staffMembers = React.useMemo(() => data?.staffMembers || [], [data?.staffMembers]);

  const cards = React.useMemo<StaffCard[]>(() => {
    const accounts = matrix.platforms.flatMap((platform) => platform.accounts);
    const projectsByOwner = new Map<string, VkpiProjectRow[]>();
    projects.forEach((project) => {
      if (!project.ownerId) return;
      projectsByOwner.set(project.ownerId, [...(projectsByOwner.get(project.ownerId) || []), project]);
    });
    // staff_managed 按 staff.id 桥接(后端 int → 前端 string,全仓惯例)
    const managedByStaff = new Map(matrix.staffManaged.map((entry) => [String(entry.staffId), entry]));
    const staff = staffMembers.length
      ? staffMembers
      : accounts.map((account) => ({
          id: String(account.staffId || account.staffEmail || account.staffName),
          name: account.staffName || "未分配",
          email: account.staffEmail || "",
          role: account.staffRole || "",
          active: account.staffActive,
          avatarUrl: account.staffAvatarUrl,
          vkpiPermission: "read" as const,
        }));
    const seen = new Set<string>();
    const baseCards = staff
      .filter((member) => {
        if (seen.has(member.id)) return false;
        seen.add(member.id);
        return true;
      })
      .map((member) => ({
        id: member.id,
        name: member.name,
        role: member.role || "KOL Manager",
        avatar: member.avatarUrl,
        accounts: accounts.filter((account) => String(account.staffId) === member.id || account.staffEmail === member.email),
        projects: projectsByOwner.get(member.id) || [],
        managed: managedByStaff.get(member.id),
      }));
    const consumedBaseIds = new Set<string>();
    const orderedKnownCards = knownStaffDisplay.map((known) => {
      const matched = baseCards.find((card) => !consumedBaseIds.has(card.id) && matchesKnownStaff(card, known));
      if (matched) {
        consumedBaseIds.add(matched.id);
        return {
          ...matched,
          name: matched.name === "Jianbo" ? "Jianbo Z" : matched.name,
          role: staffDisplayRole(matched.role, known.role),
          focus: known.focus,
          accent: known.accent,
        };
      }
      return {
        ...known,
        accounts: [] as OfficialChannelAccount[],
        projects: [] as VkpiProjectRow[],
      };
    });
    const remainingRealCards = baseCards.filter((card) => !consumedBaseIds.has(card.id) && !isGenericStaffShell(card));
    return [...orderedKnownCards, ...remainingRealCards];
  }, [matrix.platforms, matrix.staffManaged, projects, staffMembers]);

  const pendingCount = matrix.platforms
    .flatMap((platform) => platform.accounts)
    .filter((account) => account.syncStatus !== "synced" && account.syncStatus !== "official_readonly").length;

  return { cards, pendingCount };
}

export function TeamMatrixModule({ cards, pendingCount }: { cards: StaffCard[]; pendingCount: number }) {
  const [selectedStaff, setSelectedStaff] = React.useState<{ id: string; name: string } | null>(null);
  return (
    <TeamMatrix
      cards={cards}
      pendingCount={pendingCount}
      selectedStaffId={selectedStaff?.id ?? null}
      onSelectStaff={(card) =>
        setSelectedStaff((current) => (current?.id === card.id ? null : { id: card.id, name: card.name }))
      }
    />
  );
}

/* ============ 官方账号矩阵模块(OfficialMatrix 内嵌;平台/账号选中态自 MyKolPage.tsx
   原样平移,组件本体零改动;跨页事件管道由内嵌组件自带监听照常工作) ============ */
export function OfficialMatrixModule({ apiToken, matrix }: { apiToken?: string; matrix: MatrixState }) {
  const [selectedPlatformKey, setSelectedPlatformKey] = React.useState("");
  const [selectedAccountId, setSelectedAccountId] = React.useState<number | null>(null);
  const selectedPlatform =
    matrix.platforms.find((platform) => platform.platform === selectedPlatformKey) || matrix.platforms[0];

  React.useEffect(() => {
    if (!selectedPlatformKey && matrix.platforms[0]) {
      setSelectedPlatformKey(matrix.platforms[0].platform);
    }
  }, [matrix.platforms, selectedPlatformKey]);

  React.useEffect(() => {
    if (!selectedPlatform) {
      setSelectedAccountId(null);
      return;
    }
    if (!selectedPlatform.accounts.some((account) => account.id === selectedAccountId)) {
      setSelectedAccountId(selectedPlatform.accounts[0]?.id ?? null);
    }
  }, [selectedAccountId, selectedPlatform]);

  if (matrix.error && matrix.platforms.length === 0) {
    return (
      <div className="rounded-lg border border-crit bg-crit-soft px-3 py-2 text-[12px] text-crit">
        <div className="font-semibold">official-matrix 读取失败</div>
        <div className="mt-0.5 text-[11px]">{matrix.error}</div>
      </div>
    );
  }
  if (!matrix.loading && matrix.platforms.length === 0) {
    return <EmptyLine text="暂无官号账号(账号目录 0 行)。" />;
  }

  return (
    <OfficialMatrix
      apiToken={apiToken}
      matrix={matrix}
      selectedPlatform={selectedPlatform}
      selectedAccountId={selectedAccountId}
      onSelectPlatform={setSelectedPlatformKey}
      onSelectAccount={(account) => setSelectedAccountId(account.id)}
    />
  );
}

/* ============ KOL 库模块真身(M3 弹窗族 + M4 精确名单/漏斗联动)============
   卡面 = 筛选 chips(有V视频/全部 + 平台 strip + 负责人)+ 6 条 KOL 行 + 「查看全量」;
   弹窗族(全量列表/详情连续翻)住 MyKolBoardPage.dialogs(金样板 FeedList/FeedDetail 同构)。
   数据:baseRows 由 page 层从 aggregate.pool_favorites 映射注入(零重复请求);
   负责人筛选 = 管理层按 ?staff_id= 服务端 scope 重取(零本地猜);
   筛选状态 M4 起提升到 page 层(合作漏斗点段 / 平台分布点行与本模块共用同一份);
   「有 V 视频」= board-ext v_content.v_kol_ids 名单精确过滤(名单缺席降级为已挂项目
   近似 + 如实标注;truncated 如实降级提示)。 */
export function KolLibraryModule({
  apiToken,
  baseRows,
  vKolCount,
  vKolIds,
  vIdsTruncated,
  vIdsNote,
  filter,
  onFilter,
  isManager,
  staffOptions,
  projects,
  onActionDone,
}: {
  apiToken: string;
  baseRows: KolLibraryRow[];
  /** board-ext v_content.v_kol_count;null = 聚合未就绪(chip 不带数,不编) */
  vKolCount: number | null;
  /** board-ext v_content.v_kol_ids 的 Set(精确名单);null = 未就绪 → vOnly 降级近似 */
  vKolIds: ReadonlySet<number> | null;
  /** 名单超封顶被截断(后端如实标注)→ vOnly 激活时降级提示 */
  vIdsTruncated: boolean;
  /** 截断时后端原话说明(原样透出;空串则用兜底文案) */
  vIdsNote: string;
  /** 库筛选状态(page 层持有;漏斗/平台分布模块联动同一份) */
  filter: LibraryFilter;
  onFilter: (next: LibraryFilter) => void;
  isManager: boolean;
  staffOptions: Array<{ id: string; name: string }>;
  projects: VkpiProjectRow[];
  /** 动作落地后(入项目/释放认领)触发父级 aggregate 重拉 */
  onActionDone?: () => void;
}) {
  const [staffId, setStaffId] = React.useState("");
  const [staffRows, setStaffRows] = React.useState<KolLibraryRow[] | null>(null);
  const [staffBusy, setStaffBusy] = React.useState(false);
  const [staffError, setStaffError] = React.useState("");
  const [listOpen, setListOpen] = React.useState(false);
  const [detailIndex, setDetailIndex] = React.useState<number | null>(null);

  React.useEffect(() => {
    if (!apiToken || !staffId) {
      setStaffRows(null);
      setStaffError("");
      return;
    }
    let alive = true;
    setStaffBusy(true);
    setStaffError("");
    import("../../../../services/vkpi/kol-api")
      .then(({ getMyKolAggregate }) => getMyKolAggregate(apiToken, { staffId: Number(staffId) }))
      .then((resp) => {
        if (alive) setStaffRows(mapLibraryRows(resp.pool_favorites as Row[], resp.claims as Row[]));
      })
      .catch((err: unknown) => {
        if (alive) setStaffError(String((err as { detail?: unknown; message?: unknown })?.detail || (err as Error)?.message || "按负责人读取失败").slice(0, 100));
      })
      .finally(() => {
        if (alive) setStaffBusy(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, staffId]);

  const rows = staffId && staffRows ? staffRows : baseRows;
  const filtered = React.useMemo(() => filterLibraryRows(rows, filter, vKolIds), [rows, filter, vKolIds]);
  const platformOptions = React.useMemo(() => libraryPlatformOptions(rows), [rows]);
  const staffProp = isManager && staffOptions.length
    ? { options: staffOptions, value: staffId, onChange: setStaffId, busy: staffBusy }
    : undefined;
  const openDetail = (i: number) => {
    if (i >= 0 && i < filtered.length) setDetailIndex(i);
  };

  return (
    <div>
      <div className="mb-2">
        <LibraryChips filter={filter} onFilter={onFilter} platformOptions={platformOptions} vKolCount={vKolCount} staff={staffProp} />
        {/* 漏斗阶段联动 chip(状态在 page 层,与合作漏斗模块同一份;再点移除) */}
        {filter.stage ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[9.5px] text-muted">
            <button
              type="button"
              onClick={() => onFilter({ ...filter, stage: null })}
              className="inline-flex items-center gap-1 rounded-[7px] border border-accent bg-accent-soft px-2 py-0.5 font-semibold text-accent"
              title="来自合作漏斗点段 · 点此移除该过滤"
            >
              漏斗阶段:{filter.stage.label} ✕
            </button>
            <span>按行内项目阶段匹配(段计数=指派行,与库行数口径不同,如实不强对齐)</span>
          </div>
        ) : null}
        {/* V 名单诚实降级(仅 vOnly 激活时出现;绝不悄悄装精确) */}
        {filter.vOnly && vKolIds == null ? (
          <div className="mt-1.5 text-[9.5px] text-warn">
            V 名单未就绪(board-ext 未返回)——暂按「已挂项目」近似过滤,如实降级。
          </div>
        ) : null}
        {filter.vOnly && vKolIds != null && vIdsTruncated ? (
          <div className="mt-1.5 text-[9.5px] text-warn">
            {vIdsNote || "V 名单超封顶被截断——过滤只覆盖名单内 KOL,全量以计数为准(如实降级)。"}
          </div>
        ) : null}
      </div>
      {staffError ? <ErrorCard title="负责人筛选读取失败" text={staffError} /> : null}
      {staffBusy && !staffRows ? (
        <LoadingLine text="按负责人读取中…" />
      ) : rows.length === 0 ? (
        <EmptyLine text="暂无在库 KOL(收藏/共享为空)。" />
      ) : filtered.length === 0 ? (
        <EmptyLine text="该筛选组合下 0 条——诚实空,不编行。" />
      ) : (
        <div>
          {filtered.slice(0, 6).map((row, i) => (
            <KolRowLine key={row.poolId} row={row} index={i} onOpen={openDetail} />
          ))}
          <button
            type="button"
            onClick={() => setListOpen(true)}
            className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
          >
            ≡ 查看全量 {filtered.length} 条 · 点单条连续翻
          </button>
        </div>
      )}
      {listOpen ? (
        <KolLibraryListModal
          apiToken={apiToken}
          rows={filtered}
          totalAll={rows.length}
          filter={filter}
          onFilter={onFilter}
          platformOptions={platformOptions}
          vKolCount={vKolCount}
          staff={staffProp}
          projects={projects}
          onOpenDetail={openDetail}
          onClose={() => setListOpen(false)}
          onActionDone={onActionDone}
        />
      ) : null}
      {detailIndex != null && filtered[detailIndex] ? (
        <KolDetailModal
          apiToken={apiToken}
          rows={filtered}
          index={detailIndex}
          onNav={openDetail}
          onClose={() => setDetailIndex(null)}
          projects={projects}
          onActionDone={onActionDone}
        />
      ) : null}
    </div>
  );
}

export type { Row };
