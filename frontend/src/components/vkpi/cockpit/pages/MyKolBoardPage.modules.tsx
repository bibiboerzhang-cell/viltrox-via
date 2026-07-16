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
} from "./MyKolBoardPage.dialogs";
import { KolLibraryFilterControls, KolLibraryPageActions } from "./MyKolBoardPage.library-controls";
import { CHIP, CHIP_OFF, CHIP_ON, MINI_BADGE, useKolEvidenceStats, V_TIER_META } from "./MyKolBoardPage.libdetail";
import { useWorkflowRunsStream } from "../useWorkflowRunsStream";
import { formatLocal } from "../../lib/timeLocal";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import type { TaskQueueItem } from "../../../../services/vkpi/tasks-api";
import {
  classifyVideoRow,
  filterLibraryRows,
  getMyKolPoolVideos,
  libraryPlatformOptions,
  mapLibraryRows,
  sortClassifiedVideos,
  type KolLibraryRow,
  type LibraryFilter,
  type VContentTier,
  type VkpiRecentVideoItem,
  type VkpiRecentVideosGroup,
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
      ["采集数据列", "行尾 视频 N · 播放合计 · 深析 n/N = 同一 /kol-pool/{id}/videos 逐行实算(与详情弹窗同源同口径);只取当前可见行 + 会话级缓存,读取中=… / 零采集=—"],
      ["我的收藏", "kol-pool/favorites 本人收藏名单(现有端点一次拉取);chip 计数=名单∩当前库行,管理层全团队视图一键切回自己的"],
      ["详情分区", "档案 → 追踪链(GOAFFPRO 共享件原样)→ 合作项目结果(Projects 映射)→ V 相关内容(全量视频五 tabs)→ 闭环动作排"],
      ["负责人筛选", "管理层按 ?staff_id= 服务端 scope 重取,零本地猜"],
    ],
  },
  activity: {
    label: "task-queue/compact · workflow_runs",
    rows: [
      ["数据源", "后台任务事实源投影(与 KOL Pool 泳道同一只读端点)· 20s 轮询,标签页转后台自动暂停"],
      ["口径", "只显进行中/排队:账号分析=在分析 · 视频深析=在思考 · 评论采集=采集中;其余任务种类看泳道全景"],
      ["状态点", "绿点呼吸=执行中(系统减弱动效时自动静止)/ 琥珀=排队;时间=任务最近更新,按浏览器时区"],
      ["跳转", "点行=有 KOL 主体的任务直达 KOL Pool 该 KOL 详情(既有事件管道);卡底钮=泳道全景"],
    ],
  },
  libclassic: {
    label: "my-kol/aggregate · 旧两栏库整体内嵌",
    rows: [
      ["性质", "升级前的两栏 KOL 库(EmployeeKolLibrary)整体内嵌保留——默认体验已由行式 KOL 库(库模块)取代,本卡是经典视图备选"],
      ["数据", "组件自取 my-kol/aggregate(pool_favorites 真收藏源)+ 主控 projects/kolOptions;与新库同源"],
      ["范围", "员工 own-only / 管理层过渡口径(组件内部原样,零改动)"],
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
  contentWall: {
    label: "board-ext recent_videos · vkpi_kol_video_evidence",
    rows: [
      ["口径", "收藏集(收藏 ∪ 共享,去重)最近采集 evidence(封顶 60 条)按发布时间降序;is_active=FALSE(归属纠错下线)剔除"],
      ["缩略图", "best_thumbnail 三链:本地缓存(毒缓存自愈,失败占位不作真图)→ 原始 URL → youtube 派生;三路皆无/加载失败 = 诚实 ▶ 占位"],
      ["播放/点赞", "view_count/like_count 点时实测(抓取时刻读数)· 未实测显「未实测」≠ 0 播放"],
      ["V 三档徽", "合作=挂项目(project_id 非空)/ 标题提及=标题含 viltrox(不分大小写)/ 其余=未判定 —— classify_v_content 同口径派生"],
      ["已深析标", "vkpi_analysis_cache final_v1 ready 才点亮(绝不猜)"],
      ["按 KOL 筛选", "选中单个收藏 KOL 时改走 GET /kol-pool/{id}/videos(与库详情弹窗同源同端点),不受最近 60 条窗限制"],
      ["排序", "最新=发布时间降序 / 播放=实测播放降序(未实测排最后,不当 0 混序)"],
      ["点卡", "直跳原帖(平台原链接)· 无原链的行如实不加链"],
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
  contentWall: "内容墙",
  contacts: "联系方式覆盖",
  followerTrend: "粉丝趋势",
  claims: "我的认领",
  shares: "共享池",
  cover: "数据覆盖",
  activity: "分析动态",
  libclassic: "经典视图 · KOL 库",
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

/* ============ 分析动态模块(span4 小模组):进行中的分析任务 ============
   数据 = useWorkflowRunsStream(泳道/顶栏同一只读端点 task-queue/compact 的共享轮询 hook,
   20s 间隔 + 后台标签页自动暂停,零新端点)。只挑三类与 MY KOL 采集闭环直接相关的任务:
   账号分析(在分析)/ video深析(在思考)/ 评论采集(采集中)——kind 判据与后端
   queue_view._infer_kind 产出字面一致。点行 = 有 KOL 主体的任务直达 KOL Pool 该 KOL
   详情(vkpi:pending-kolpool-open-id + vkpi:open-kol-pool-item 既有事件管道,
   TaskProgressBoard 同口径);无主体/卡底钮 = onJumpPool 切泳道全景。
   红线:纯读展示;呼吸点只用 motion-safe(系统减弱动效自动静止);绝对时间戳
   (formatLocal 按浏览器时区),禁「刚刚/实时」假新鲜度。 ============ */

export const ACTIVITY_TASK_META: Record<string, { label: string; cls: string }> = {
  账号分析: { label: "在分析", cls: "border-accent bg-accent-soft text-accent" },
  video深析: { label: "在思考", cls: "border-accent-2 text-accent-2" },
  评论采集: { label: "采集中", cls: "border-info bg-info-soft text-info" },
};

const ACTIVITY_BADGE = "flex-none rounded-[5px] border px-1.5 py-px text-[8.5px] font-bold";
const ACTIVITY_MAX_ROWS = 8;

function activityTargetText(task: TaskQueueItem): string {
  const target = (task.target && typeof task.target === "object" ? task.target : {}) as Row;
  return (
    String(target.label || target.handle || target.display_name || target.target_id || "").trim() || "未命名任务"
  );
}

// KOL 主体定位(TaskProgressBoard 同口径):video/评论任务在 target.kol_pool_id;
// 账号分析(kol_profile)的 target_id 即 kol_pool_id。
function activityPoolId(task: TaskQueueItem): number | undefined {
  const target = (task.target && typeof task.target === "object" ? task.target : {}) as Row;
  const direct = Number(target.kol_pool_id);
  if (Number.isFinite(direct) && direct > 0) return direct;
  const profileId = Number(target.target_id);
  return String(target.target_type || "") === "kol_profile" && Number.isFinite(profileId) && profileId > 0
    ? profileId
    : undefined;
}

export function AnalysisActivityModule({ apiToken, onJumpPool }: { apiToken: string; onJumpPool: () => void }) {
  const stream = useWorkflowRunsStream(apiToken, { intervalMs: 20000, limit: 30, recentMinutes: 5 });
  const items = React.useMemo(() => {
    const active = Array.isArray(stream.payload?.active) ? stream.payload!.active! : [];
    return active.filter((task) => ACTIVITY_TASK_META[String(task.kind || "")]).slice(0, ACTIVITY_MAX_ROWS);
  }, [stream.payload]);
  const openTask = (task: TaskQueueItem) => {
    const poolId = activityPoolId(task);
    if (poolId && typeof window !== "undefined") {
      try {
        window.localStorage.setItem("vkpi:pending-kolpool-open-id", String(poolId));
      } catch {
        /* localStorage 不可用不阻断,事件管道仍切页 */
      }
      window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-item", { detail: { kolPoolId: poolId, task } }));
      return;
    }
    onJumpPool();
  };
  if (stream.error && !stream.payload) return <ErrorCard title="任务队列读取失败" text={stream.error} />;
  if (!stream.payload) return <LoadingLine text="任务队列读取中…" />;
  return (
    <div>
      {items.length === 0 ? (
        <EmptyLine text="暂无进行中任务。" />
      ) : (
        items.map((task) => {
          const meta = ACTIVITY_TASK_META[String(task.kind || "")];
          const running = String(task.status || "") !== "queued";
          return (
            <div
              key={task.id}
              role="button"
              tabIndex={0}
              onClick={() => openTask(task)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  openTask(task);
                }
              }}
              className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0"
              title="点击直达 KOL Pool(有 KOL 主体的任务直接开该 KOL 详情)"
            >
              <span className={`${ACTIVITY_BADGE} ${meta.cls}`}>{meta.label}</span>
              <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent">
                {activityTargetText(task)}
              </span>
              <span
                className={`h-[6px] w-[6px] flex-none rounded-full ${running ? "bg-good motion-safe:animate-pulse" : "bg-warn"}`}
                title={running ? "执行中" : "排队中"}
              />
              <span className="flex-none font-mono text-[9px] text-muted" title="任务最近更新时间(按浏览器时区)">
                {formatLocal(task.updated_at || task.created_at)}
              </span>
            </div>
          );
        })
      )}
      <button
        type="button"
        onClick={onJumpPool}
        className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
      >
        ≡ 去 KOL Pool 泳道看全部任务
      </button>
    </div>
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
  page,
  loadingMore = false,
  loadMoreError = "",
  onLoadMore,
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
  page?: {
    total?: number;
    has_more?: boolean;
    next_cursor?: string | null;
  };
  loadingMore?: boolean;
  loadMoreError?: string;
  onLoadMore?: () => void | Promise<void>;
  /** 动作落地后(入项目/释放认领)触发父级 aggregate 重拉 */
  onActionDone?: () => void;
}) {
  const [staffId, setStaffId] = React.useState("");
  const [staffRows, setStaffRows] = React.useState<KolLibraryRow[] | null>(null);
  const [staffBusy, setStaffBusy] = React.useState(false);
  const [staffError, setStaffError] = React.useState("");
  const [staffPage, setStaffPage] = React.useState<typeof page>();
  const [staffLoadingMore, setStaffLoadingMore] = React.useState(false);
  const [staffLoadMoreError, setStaffLoadMoreError] = React.useState("");
  const [listOpen, setListOpen] = React.useState(false);
  const [detailIndex, setDetailIndex] = React.useState<number | null>(null);

  // 「我的收藏」快捷筛选:viewer 本人收藏名单(kol-pool/favorites 现有端点,一次拉取)。
  // 名单读到才渲染 chip(读取失败/未就绪 = chip 缺席,绝不摆个会筛空的假开关)。
  const [mineOnly, setMineOnly] = React.useState(false);
  const [mineIds, setMineIds] = React.useState<ReadonlySet<number> | null>(null);
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    import("../../../../services/vkpi/kolPool-api")
      .then(({ listKolPoolFavorites }) => listKolPoolFavorites(apiToken))
      .then((resp) => {
        if (!alive) return;
        const ids = (Array.isArray(resp.items) ? resp.items : [])
          .map((row) => Number((row as Row).kol_pool_id))
          .filter((id) => Number.isFinite(id) && id > 0);
        setMineIds(new Set(ids));
      })
      .catch(() => {
        if (alive) setMineIds(null);
      });
    return () => {
      alive = false;
    };
  }, [apiToken]);

  React.useEffect(() => {
    if (!apiToken || !staffId) {
      setStaffRows(null);
      setStaffError("");
      setStaffPage(undefined);
      setStaffLoadMoreError("");
      return;
    }
    let alive = true;
    setStaffBusy(true);
    setStaffError("");
    import("../../../../services/vkpi/kol-api")
      .then(({ getMyKolAggregate }) => getMyKolAggregate(apiToken, {
        staffId: Number(staffId),
        mode: "summary",
        favoritesLimit: 50,
      }))
      .then((resp) => {
        if (alive) {
          setStaffRows(mapLibraryRows(resp.pool_favorites as Row[], resp.claims as Row[]));
          setStaffPage(resp.pool_favorites_page);
        }
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

  const loadMoreStaffRows = React.useCallback(async () => {
    const cursor = staffPage?.next_cursor;
    if (!apiToken || !staffId || !cursor || staffLoadingMore) return;
    setStaffLoadingMore(true);
    setStaffLoadMoreError("");
    try {
      const { getMyKolAggregate } = await import("../../../../services/vkpi/kol-api");
      const resp = await getMyKolAggregate(apiToken, {
        staffId: Number(staffId),
        mode: "summary",
        favoritesLimit: 50,
        favoritesCursor: cursor,
      });
      const nextRows = mapLibraryRows(resp.pool_favorites as Row[], resp.claims as Row[]);
      setStaffRows((current) => {
        const existing = current || [];
        const seen = new Set(existing.map((row) => row.poolId));
        return [...existing, ...nextRows.filter((row) => !seen.has(row.poolId))];
      });
      setStaffPage(resp.pool_favorites_page);
    } catch (err) {
      setStaffLoadMoreError(String((err as { detail?: unknown; message?: unknown })?.detail || (err as Error)?.message || "更多 KOL 加载失败").slice(0, 100));
    } finally {
      setStaffLoadingMore(false);
    }
  }, [apiToken, staffId, staffLoadingMore, staffPage?.next_cursor]);

  const rows = staffId && staffRows ? staffRows : baseRows;
  const activePage = staffId ? staffPage : page;
  const activeLoadingMore = staffId ? staffLoadingMore : loadingMore;
  const activeLoadMoreError = staffId ? staffLoadMoreError : loadMoreError;
  const activeLoadMore = staffId ? loadMoreStaffRows : onLoadMore;
  // 「我的收藏」先于其余筛选收窄(名单∩库行;名单未就绪时开关不渲染,rows 原样)。
  const scopedRows = React.useMemo(
    () => (mineOnly && mineIds ? rows.filter((row) => mineIds.has(row.poolId)) : rows),
    [rows, mineOnly, mineIds],
  );
  const filtered = React.useMemo(() => filterLibraryRows(scopedRows, filter, vKolIds), [scopedRows, filter, vKolIds]);
  const platformOptions = React.useMemo(() => libraryPlatformOptions(scopedRows), [scopedRows]);
  const staffProp = isManager && staffOptions.length
    ? { options: staffOptions, value: staffId, onChange: setStaffId, busy: staffBusy }
    : undefined;
  const mineCount = React.useMemo(
    () => (mineIds ? rows.filter((row) => mineIds.has(row.poolId)).length : 0),
    [rows, mineIds],
  );
  const mineProp = mineIds
    ? { on: mineOnly, count: mineCount, onToggle: () => setMineOnly((value) => !value) }
    : undefined;
  // 采集数据列:卡面只拉当前可见 6 行(与全量弹窗/详情同源,会话级缓存)。
  const visibleIds = React.useMemo(() => filtered.slice(0, 6).map((row) => row.poolId), [filtered]);
  const statsMap = useKolEvidenceStats(apiToken, visibleIds);
  const openDetail = (i: number) => {
    if (i >= 0 && i < filtered.length) setDetailIndex(i);
  };

  return (
    <div>
      <KolLibraryFilterControls
        filter={filter}
        onFilter={onFilter}
        platformOptions={platformOptions}
        vKolCount={vKolCount}
        staff={staffProp}
        mine={mineProp}
        vKolIds={vKolIds}
        vIdsTruncated={vIdsTruncated}
        vIdsNote={vIdsNote}
      />
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
            <KolRowLine key={row.poolId} row={row} index={i} onOpen={openDetail} stats={statsMap.get(row.poolId)} />
          ))}
          <KolLibraryPageActions
            page={activePage}
            rowCount={rows.length}
            filteredCount={filtered.length}
            loadingMore={activeLoadingMore}
            loadMoreError={activeLoadMoreError}
            onLoadMore={activeLoadMore}
            onOpenList={() => setListOpen(true)}
          />
        </div>
      )}
      {listOpen ? (
        <KolLibraryListModal
          apiToken={apiToken}
          rows={filtered}
          totalAll={Number(activePage?.total || rows.length)}
          filter={filter}
          onFilter={onFilter}
          platformOptions={platformOptions}
          vKolCount={vKolCount}
          staff={staffProp}
          mine={mineProp}
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

/* ============ 内容墙模块(span8,2026-07-12 补刀):板面直刷收藏 KOL 采集视频 ============
   数据两路同源真端点:全部 KOL = board-ext recent_videos(收藏集最近采集,封顶 60);
   选中单 KOL = GET /kol-pool/{id}/videos(与库详情弹窗同端点同算法,模块级缓存)。
   缩略图 = 创意库同款毒缓存自愈链(best_thumbnail:cached → raw → youtube 派生)→
   proxiedImageUrl 兜受墙 CDN;onError / 三路皆无 → 诚实 ▶ 占位,绝不编图。
   卡 = 缩略图 + 标题截断 + KOL 名 + 播放/点赞 mono(未实测如实标)+ V 三档徽 +
   已深析标 + 发布日期(绝对日期);点卡直跳原帖(无原链行如实不加链)。
   工具行 = KOL 下拉(收藏名单)+ 仅 V 相关 + 排序(最新/播放);默认 12 张,
   「≡ 查看更多」逐页 +12。V 判定/排序复用 classifyVideoRow / sortClassifiedVideos
   (与详情弹窗同口径)。红线:纯读展示;颜色全 token;零 opacity 修饰类。 ============ */

const WALL_PAGE = 12;
const WALL_KOL_VIDEOS_LIMIT = 200;
// 逐 KOL 视频模块级缓存(会话级;与 useKolEvidenceStats 同思路,失败不缓存下次重试)
const WALL_KOL_CACHE = new Map<number, VkpiRecentVideoItem[]>();

/** 墙缩略图:自愈链读端(SegmentThumb 同构);加载失败/三路皆无 → 诚实 ▶ 占位。 */
function WallThumb({ video }: { video: VkpiRecentVideoItem }) {
  const src = proxiedImageUrl(
    String(video.best_thumbnail || video.cached_thumbnail_url || video.thumbnail_url || video.youtube_thumbnail_url || ""),
  );
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [src]);
  if (!src || failed) {
    return (
      <span
        className="grid h-full w-full place-items-center bg-card text-[16px] text-muted"
        title={failed ? "缩略图加载失败(不摆假图)" : "该视频无可用缩略图(未存 · 缓存无 · 非 youtube)"}
        aria-hidden="true"
      >
        ▶
      </span>
    );
  }
  return <img src={src} alt="" loading="lazy" className="h-full w-full object-cover" onError={() => setFailed(true)} />;
}

function WallVideoCard({ video, tier, fallbackKolName }: { video: VkpiRecentVideoItem; tier: VContentTier; fallbackKolName?: string }) {
  const eid = Number(video.evidence_id ?? video.id) || 0;
  const title = String(video.title || video.video_title || "未命名视频");
  const kolName = String(video.kol_name || fallbackKolName || video.kol_handle || "—");
  const day = String(video.publish_date || video.posted_at || "").slice(0, 10);
  const meta = V_TIER_META[tier];
  const href = String(video.content_url || "");
  const body = (
    <>
      <div className="h-[92px] w-full overflow-hidden bg-card">
        <WallThumb video={video} />
      </div>
      <div className="px-2.5 py-2">
        <div className="truncate text-[11px] text-ink" title={title}>{title}</div>
        <div className="mt-0.5 truncate text-[9.5px] text-muted" title="所属收藏 KOL">{kolName}</div>
        <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[9.5px] text-muted">
          <span title={video.view_count == null ? "未实测(≠ 0 播放)" : "播放(点时实测)"}>
            ▶ {video.view_count != null ? Number(video.view_count).toLocaleString() : "未实测"}
          </span>
          <span title="点赞(点时实测)">♥ {Number(video.like_count ?? 0).toLocaleString()}</span>
          {day ? <span title="发布日期(平台原发布日,非采集日)">{day}</span> : null}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <span className={`rounded-[5px] border px-1 py-px text-[8px] font-bold ${meta.cls}`}>{meta.label}</span>
          {video.has_final_v1_cache ? <span className={`${MINI_BADGE} border-good bg-good-soft text-good`}>已深析</span> : null}
          {href ? (
            <span className="ml-auto flex-none font-mono text-[9px] text-muted transition-colors group-hover:text-accent" aria-hidden="true">
              原帖 ↗
            </span>
          ) : null}
        </div>
      </div>
    </>
  );
  const cardCls = "block overflow-hidden rounded-[11px] border border-line bg-panel";
  return href ? (
    <a
      key={eid}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title="点卡直跳原帖"
      className={`group ${cardCls} transition-colors hover:border-accent`}
    >
      {body}
    </a>
  ) : (
    <div key={eid} className={cardCls} title="该条无原帖链接(采集未存 URL)">
      {body}
    </div>
  );
}

export function ContentWallModule({
  apiToken,
  group,
  kolOptions,
}: {
  apiToken: string;
  /** board-ext recent_videos(页层已 gate:到这里必然 ready) */
  group: VkpiRecentVideosGroup;
  /** 收藏名单(library 行同源;下拉筛选用) */
  kolOptions: Array<{ poolId: number; name: string }>;
}) {
  const [kolId, setKolId] = React.useState(0);
  const [vOnly, setVOnly] = React.useState(false);
  const [sortBy, setSortBy] = React.useState<"time" | "views">("time");
  const [visible, setVisible] = React.useState(WALL_PAGE);
  const [kolRows, setKolRows] = React.useState<VkpiRecentVideoItem[] | null>(null);
  const [kolBusy, setKolBusy] = React.useState(false);
  const [kolError, setKolError] = React.useState("");

  React.useEffect(() => {
    setVisible(WALL_PAGE);
  }, [kolId, vOnly, sortBy]);

  // 单 KOL 视图:与库详情弹窗同端点(会话级缓存;失败不缓存,重选自动重试)
  React.useEffect(() => {
    if (!apiToken || !kolId) {
      setKolRows(null);
      setKolError("");
      return;
    }
    const cached = WALL_KOL_CACHE.get(kolId);
    if (cached) {
      setKolRows(cached);
      setKolError("");
      return;
    }
    let alive = true;
    setKolBusy(true);
    setKolError("");
    setKolRows(null);
    getMyKolPoolVideos(apiToken, kolId, WALL_KOL_VIDEOS_LIMIT)
      .then((resp) => {
        if (!alive) return;
        const items = (Array.isArray(resp.items) ? resp.items : []) as VkpiRecentVideoItem[];
        WALL_KOL_CACHE.set(kolId, items);
        setKolRows(items);
      })
      .catch((err: unknown) => {
        if (alive) setKolError(String((err as { detail?: unknown; message?: unknown })?.detail || (err as Error)?.message || "读取失败").slice(0, 120));
      })
      .finally(() => {
        if (alive) setKolBusy(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, kolId]);

  const kolName = React.useMemo(() => kolOptions.find((o) => o.poolId === kolId)?.name || "", [kolOptions, kolId]);
  const baseItems: VkpiRecentVideoItem[] = kolId ? kolRows || [] : Array.isArray(group.items) ? group.items : [];
  const shown = React.useMemo(
    () => sortClassifiedVideos(baseItems.map((video) => ({ video, tier: classifyVideoRow(video) })), vOnly, sortBy),
    [baseItems, vOnly, sortBy],
  );

  return (
    <div>
      {/* 工具行:KOL 下拉(收藏名单)+ 仅 V 相关 + 排序 chips */}
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[10.5px] text-muted">
        <select
          aria-label="按 KOL 筛选"
          value={String(kolId)}
          onChange={(ev) => setKolId(Number(ev.target.value) || 0)}
          className="max-w-[190px] rounded-xl border border-line bg-card px-2.5 py-1.5 text-[11px] text-ink outline-none focus:border-accent [&>option]:bg-[var(--ds-card)]"
          title="选单个收藏 KOL = 改走该 KOL 全量采集(与库详情同源)"
        >
          <option value="0">全部收藏 KOL</option>
          {kolOptions.map((option) => (
            <option key={option.poolId} value={String(option.poolId)}>
              {option.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className={`${CHIP} ${vOnly ? CHIP_ON : CHIP_OFF}`}
          onClick={() => setVOnly((value) => !value)}
          title="只看合作产出与标题提及V(未判定隐藏)"
        >
          仅 V 相关
        </button>
        <span className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            className={`${CHIP} ${sortBy === "time" ? CHIP_ON : CHIP_OFF}`}
            onClick={() => setSortBy("time")}
            title="按发布时间排序"
          >
            最新
          </button>
          <button
            type="button"
            className={`${CHIP} ${sortBy === "views" ? CHIP_ON : CHIP_OFF}`}
            onClick={() => setSortBy("views")}
            title="按实测播放排序(未实测排最后)"
          >
            播放
          </button>
        </span>
      </div>
      {kolError ? (
        <ErrorCard title="按 KOL 读取失败" text={kolError} />
      ) : kolId && kolBusy && !kolRows ? (
        <LoadingLine text={`${kolName || "该 KOL"} 采集视频读取中…`} />
      ) : baseItems.length === 0 ? (
        <EmptyLine text={kolId ? `${kolName || "该 KOL"} 暂无采集视频——在库行发起采集。` : "暂无采集视频——在库行发起采集。"} />
      ) : shown.length === 0 ? (
        <EmptyLine text="该筛选下 0 条(仅 V 相关开启)——诚实空。" />
      ) : (
        <div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-4">
            {shown.slice(0, visible).map(({ video, tier }) => (
              <WallVideoCard key={Number(video.evidence_id ?? video.id) || 0} video={video} tier={tier} fallbackKolName={kolId ? kolName : undefined} />
            ))}
          </div>
          {shown.length > visible ? (
            <button
              type="button"
              onClick={() => setVisible((value) => value + WALL_PAGE)}
              className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
            >
              ≡ 查看更多(已显 {Math.min(visible, shown.length)} / {shown.length})
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

export type { Row };
