import React from "react";
import {
  EmptyLine,
  ErrorCard,
  KpiCard,
  LoadingLine,
  PendingCard,
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

// MY KOL 板块页范式 · 辅助件(M1 骨架刀,金样板 = MarketVoicePage.modules 同构)。
//   通用骨架件(ModuleCard/KpiCard/PendingCard/EmptyLine/ErrorCard/LoadingLine)直接
//   从 MarketVoicePage.modules 复用(施工单放行「引组件」,零平移重写);本文件只放
//   MY KOL 专属件:MODULE_SOURCES 溯源注册表 / KPI 带四卡 / TeamMatrix·OfficialMatrix
//   内嵌包装(把 pages/myKol 现有组件的取数与选中态搬进模块作用域,组件本体零改动)。
//   charts/dialogs 文件 M2/M3 刀再建;SrcChip 本刀 hover 口径卡起步(点击溯源弹窗随
//   dialogs 刀补,不复用市场之声的 GENERIC_CHAIN——那条链是评论域口径,挂这里会说谎)。
// 红线:本文件零直连网络(取数住 page 层/内嵌组件自带);纯展示绝不写 fit 分/rule_v0;
//   颜色全 token 类零写死色;诚实空态(无历史快照 → spempty,待接线 → PendingCard 直说)。

/* ============ 溯源注册表(金样板 MODULE_SOURCES 同构;label=真实端点/表名,rows=真实
   行数与口径,2026-07-11 实测,禁编造) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiM: {
    label: "my-kol/aggregate · official-matrix",
    rows: [
      ["在库 KOL", "vkpi_kol_pool_favorites(779 行 · staff×kol 收藏对)+ 共享行 vkpi_kol_pool_members"],
      ["合作推进中", "vkpi_project_kol_assignments(2,189 行)在役 stage 去重 KOL"],
      ["内容播放", "vkpi_kol_video_evidence.view_count(2,134 条实测 · 填充 77.1%)"],
      ["播放口径", "点时实测 · 非时序 —— SUM(view_count) 当前快照,无历史序列"],
      ["官号粉丝", "vkpi_channel_metrics(961 行 · 100% 填充)每账号最新快照 Σ followers"],
      ["series/delta", "无历史快照 → sparkline 诚实 spempty · 环比药丸不渲染;M2 board-ext 端点补时序"],
    ],
  },
  digest: {
    label: "my-kol/daily-digest",
    rows: [
      ["聚合", "vkpi_kol_video_evidence + vkpi_channel_metrics + vkpi_kol_pool_contacts 纯读聚合"],
      ["范围", "员工只看自己集合 · 管理层默认全员并集(后端 scope 裁剪)"],
      ["降级", "接口失败整卡安静缺席 · 各块 empty 带后端 reason 原样透出"],
    ],
  },
  funnel: {
    label: "四环漏斗 · M2 待接线",
    rows: [
      ["口径", "收藏(favorites)→ 认领(vkpi_kol_claims)→ 进项目(assignments)→ 已发布"],
      ["状态", "M2 board-ext 端点接线后点亮 · 本刀诚实待接不摆假漏斗"],
    ],
  },
  team: {
    label: "staff · official-matrix staff_managed",
    rows: [
      ["负责人", "staff + users 目录(员工视角 staff-directory 为管理层端点 → 卡列自然收敛)"],
      ["归属账号", "vkpi_employee_channels 按 staff_id 归属"],
      ["分管 KOL", "official-matrix.staff_managed(数量/粉丝合计/名单 cap 20)"],
    ],
  },
  library: {
    label: "my-kol/aggregate · board-ext",
    rows: [
      ["在库行", "vkpi_kol_pool_favorites + 共享 vkpi_kol_pool_members(aggregate.pool_favorites 全量下发)"],
      ["状态徽", "收藏/共享=行本体;进行中=挂 assignments;已认领=vkpi_kol_claims 平台+名称桥(真值在详情 viewer-context)"],
      ["V 视频 KOL", "board-ext v_content.v_kol_count —— 至少 1 条合作/标题提及视频的去重 KOL(全库口径)"],
      ["V 三档判据", "合作=挂项目(project_id 非空)/ 标题提及=标题含 viltrox(不分大小写)/ 其余=未判定 —— 派生规则非采集字段(classify_v_content 同口径)"],
      ["列表 V 筛选", "行级可判据=已进项目(合作);标题提及需逐 KOL 视频判定 → 详情弹窗逐条标注"],
      ["单 KOL 视频", "GET /kol-pool/{id}/videos(view_count 点时实测 · NULL 剔除注明)"],
      ["负责人筛选", "管理层按 ?staff_id= 服务端 scope 重取,零本地猜"],
    ],
  },
  fitdist: {
    label: "fit 分布 · M2 待接线",
    rows: [
      ["口径", "vkpi_kol_pool.viltrox_fit_score 只读分布(评分公式永不进前端)"],
      ["状态", "M2 board-ext 端点接线后点亮"],
    ],
  },
  official: {
    label: "vkpi_employee_channels · vkpi_channel_metrics",
    rows: [
      ["账号", "18 官号 · /api/marketing/channels/official-matrix"],
      ["指标", "vkpi_channel_metrics 最新快照(followers/posts/views + delta)"],
      ["内容层", "channels/{id}/posts 按需分页(内嵌组件自取)"],
    ],
  },
  platdist: {
    label: "vkpi_kol_pool.platform · M2 待接线",
    rows: [
      ["口径", "在库 KOL 按 platform 分桶(纯读 GROUP BY)"],
      ["状态", "M2 board-ext 端点接线后点亮"],
    ],
  },
  risk: {
    label: "my-kol/risk-index",
    rows: [
      ["信号", "Gemini final_v1 深析结构化信号聚合(内容无深度/素材复用/竞品露出)"],
      ["诚实", "只覆盖已深析 KOL · 未深析显「未分析」灰态,绝不当 0 风险"],
      ["可见", "管理层专属(裁决②A)· 员工注册表直接不出现"],
    ],
  },
  rollup: {
    label: "my-kol/contribution-rollup",
    rows: [
      ["口径", "每负责人一行:在管 KOL / 已发布 / 归因销售(has_attribution 诚实降级)"],
      ["门禁", "后端 scope.can_view_all 二次 gate · 管理层专属(裁决②A)"],
    ],
  },
  viewsTop: {
    label: "vkpi_kol_video_evidence · M2 待接线",
    rows: [["口径", "播放 Top 视频榜(view_count 实测降序)· M2 接线"]],
  },
  contacts: {
    label: "vkpi_kol_pool_contacts · M2 待接线",
    rows: [["口径", "联系方式覆盖(类型/来源计数,明文走 contact_reveal 门控)· M2 接线"]],
  },
  followerTrend: {
    label: "粉丝时序 · M2 待接线",
    rows: [["口径", "官号 vkpi_channel_metrics 日快照序列;KOL 侧无历史快照如实标"]],
  },
  claims: {
    label: "vkpi_kol_claims · M2 待接线",
    rows: [["口径", "认领状态(active/expired + 到期窗口)· M2 接线"]],
  },
  shares: {
    label: "vkpi_kol_pool_members · M2 待接线",
    rows: [["口径", "共享池(谁共享给谁 · 只读可见性授予)· M2 接线"]],
  },
  cover: {
    label: "数据覆盖盘点 · M3 待接线",
    rows: [["口径", "逐源健康 + 盲区如实标注(金样板 cover 同构)· M3 接线"]],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiM: "KOL 指标带",
  digest: "每日学习摘要",
  funnel: "四环漏斗",
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
  claims: "认领状态",
  shares: "共享池",
  cover: "数据覆盖",
};

/* ============ 中文紧凑数(20.5亿 / 25.1万;KPI 卡大数用,mono 由 ds-kpi 基类管) ============ */
export function fmtZhCompact(value: number | null | undefined): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  return n.toLocaleString();
}

/* ============ KPI 带四卡(设计单 K1-K4;KpiCard 金样板复用):
   K1 在库 KOL / K2 合作推进中 = aggregate.kpi_summary 现值;
   K3 内容播放实测 = 主控 evidence_metrics 注入(点时实测 SUM view_count,非时序);
   K4 官号粉丝 = official-matrix 最新快照 Σ followers。
   四卡 series 一律不传 → KpiCard 自落 demo .spempty 纯虚线(无历史快照,诚实);
   delta 一律不传 → 环比药丸不渲染;时序/环比等 M2 board-ext 端点(props 位已留)。 ============ */
export function MyKolKpiBand({
  kpi,
  exposureValue,
  exposureNote,
  officialFollowers,
  officialNote,
}: {
  /** aggregate.kpi_summary(favorites_count / in_project_count …),null=上游 gate 已拦 */
  kpi: Record<string, number> | null;
  /** 主控 evidence_metrics.total_exposure(SUM view_count 点时实测);null=未注入 → 诚实 pending */
  exposureValue: number | null;
  exposureNote: string;
  /** official-matrix Σ totalFollowers;null=矩阵未就绪 → 诚实 pending */
  officialFollowers: number | null;
  officialNote: string;
}) {
  const favorites = Number(kpi?.favorites_count);
  const inProject = Number(kpi?.in_project_count);
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard
        label="在库 KOL"
        value={Number.isFinite(favorites) ? favorites.toLocaleString() : "—"}
        unit="个"
      />
      <KpiCard
        label="合作推进中"
        value={Number.isFinite(inProject) ? inProject.toLocaleString() : "—"}
        unit="个"
        seriesColor="var(--ds-accent-2)"
      />
      <KpiCard
        label="内容播放实测"
        value={fmtZhCompact(exposureValue)}
        pending={exposureValue == null}
        pendingNote={exposureNote}
        seriesColor="var(--ds-good)"
      />
      <KpiCard
        label="官号粉丝"
        value={fmtZhCompact(officialFollowers)}
        pending={officialFollowers == null}
        pendingNote={officialNote}
        seriesColor="var(--ds-info)"
      />
    </div>
  );
}

/* ============ 待接线占位体(诚实空态:说明「什么在 M 几接线」,绝不摆假数据) ============ */
export function PendingBody({ stage, children }: { stage: string; children: React.ReactNode }) {
  return (
    <PendingCard>
      <b>{stage} 接线中</b> —— {children}
    </PendingCard>
  );
}

type MatrixState = ReturnType<typeof useOfficialChannelMatrix>;

/* ============ 团队矩阵模块(TeamMatrix 内嵌;staffCards 组装逻辑自 MyKolPage.tsx
   原样平移——已知负责人展示元数据 + 真 staff 目录合并、staff_managed 按 id 桥接;
   选中态本刀仅高亮自身,联动过滤 KOL 库随 library 模块 M2/M3 刀接回) ============ */
export function TeamMatrixModule({ data, matrix }: { data?: VkpiDashboardData; matrix: MatrixState }) {
  const [selectedStaff, setSelectedStaff] = React.useState<{ id: string; name: string } | null>(null);
  const projects = React.useMemo<VkpiProjectRow[]>(() => data?.projects || [], [data?.projects]);
  const staffMembers = React.useMemo(() => data?.staffMembers || [], [data?.staffMembers]);

  const staffCards = React.useMemo<StaffCard[]>(() => {
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

  return (
    <TeamMatrix
      cards={staffCards}
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
    return <EmptyLine text="暂无官号账号(vkpi_employee_channels 空)。" />;
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

/* ============ KOL 库模块真身(M3:替换 M1 PendingCard)============
   卡面 = 筛选 chips(有V视频/全部 + 平台 strip + 负责人)+ 6 条 KOL 行 + 「查看全量」;
   弹窗族(全量列表/详情连续翻)住 MyKolBoardPage.dialogs(金样板 FeedList/FeedDetail 同构)。
   数据:baseRows 由 page 层从 aggregate.pool_favorites 映射注入(零重复请求);
   负责人筛选 = 管理层按 ?staff_id= 服务端 scope 重取(零本地猜);
   「有 V 视频」count = board-ext v_content.v_kol_count(全库口径,行级过滤判据如实注明)。 */
export function KolLibraryModule({
  apiToken,
  baseRows,
  vKolCount,
  isManager,
  staffOptions,
  projects,
  onActionDone,
}: {
  apiToken: string;
  baseRows: KolLibraryRow[];
  /** board-ext v_content.v_kol_count;null = 聚合未就绪(chip 不带数,不编) */
  vKolCount: number | null;
  isManager: boolean;
  staffOptions: Array<{ id: string; name: string }>;
  projects: VkpiProjectRow[];
  /** 动作落地后(入项目/释放认领)触发父级 aggregate 重拉 */
  onActionDone?: () => void;
}) {
  const [filter, setFilter] = React.useState<LibraryFilter>({ vOnly: false, platform: "", query: "" });
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
  const filtered = React.useMemo(() => filterLibraryRows(rows, filter), [rows, filter]);
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
        <LibraryChips filter={filter} onFilter={setFilter} platformOptions={platformOptions} vKolCount={vKolCount} staff={staffProp} />
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
          onFilter={setFilter}
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
