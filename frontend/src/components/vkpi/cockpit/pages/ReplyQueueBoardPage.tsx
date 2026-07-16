import React from "react";
import { PencilLine } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import type { ReplyQueueItem } from "../../../../services/vkpi/replyQueue-api";
import { EmptyLine, ErrorCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { CatDonutBody } from "./MarketVoicePage.charts";
import { ModuleProvModal, platformBadge } from "./MarketVoicePage.dialogs";
import {
  useCopyDraft,
  useDraftAction,
  useKpiSeriesData,
  useMarkAction,
  useQueueData,
  useScreenAction,
} from "./ReplyQueueBoardPage.actions";
import {
  FunnelBody,
  LangBody,
  MODULE_SOURCES,
  PROV_TITLES,
  PlatBody,
  QueueKpiBand,
  QueueRowLine,
  STATUS_META,
  StatusChips,
  langLabel,
  queueCounts,
} from "./ReplyQueueBoardPage.modules";
import { QueueDetailModal, QueueListModal, type QueueDetailActions } from "./ReplyQueueBoardPage.dialogs";

// 回复队列 · 板块页范式改版(金样板 MarketVoicePage 四件套同构;可编辑看板)。
//   旧页(pages/ReplyQueuePage.tsx,保留为回滚垫)功能零丢失:
//     状态过滤(待起草/待回复/已回复/已忽略/全部)→ queue 模块 chips 本地过滤;
//     意向徽(price/compat/question,补 manual=市场之声单条转入)/ 语言徽 / 平台徽;
//     扫描新意向(POST /reply-queue/screen)→ pagehead 按钮 + 真回执(已扫/命中/新入队);
//     生成草稿/重新起草(POST /{id}/draft)/ 一键复制 / 标记已回 / 忽略(POST /{id}/mark
//     + expected_status 乐观锁)→ 单条详情闭环动作行;刷新 → pagehead。
//   数据源(全真,零编造):GET /api/admin/vkpi/reply-queue 服务端分页(首页 500 +
//   total 真分母;>500 由全量弹窗「载入更多(已显 X/Y)」逐页追加,失败不吞已载入列表;
//   四态/意向/平台/语言全部由同一份口径函数 queueCounts 在已载入行上计算)。
//   KPI 时序:GET /reply-queue/kpi-series 按日真序列 + 真环比接四卡(0 填齐钳 now,
//   上窗 0 → 诚实无药丸;端点失败四卡回落虚线,原因进 SrcChip)。
//   溯源:每模块 SrcChip(真表名)→ ModuleProvModal;单条详情溯源链链回 vkpi_comments
//   源评论(platform+external_comment_id 幂等键,2026-07-12 核实 132/132 全命中);
//   kol_pool_id 命中 KOL 池 → 身份节点跳 KOL 档案。
// 红线:纯待办队列展示,零触 viltrox_fit_score / rule_v0;端点失败=诚实错误卡;
//   颜色全 token;零 opacity 修饰类;绝对时间戳;动作端点真实返回才落状态(gone 不写 ✓);
//   布局只走本机 storageKey,不给 EditableDashboardBoard 传 apiToken。

const STORAGE_KEY = "vkpi-reply-queue-layout-v1";
const FACE_ROWS = 6; // 卡面收敛条数(demo FULL.slice(0,6);全量走弹窗)

// 默认布局(12 列,默认简:总览带 + 队列 + 意向环图 + 处理进度;plat/lang 进 palette 备选)
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiQ", span: 12 },
  { moduleKey: "queue", span: 8 },
  { moduleKey: "intent", span: 4 },
  { moduleKey: "funnel", span: 4 },
];

/** 列表过滤规格(已载入页内本地过滤;「载入更多」追加后同口径自动扩大)。 */
interface ListFilter {
  status?: string;
  intent?: string;
  platform?: string;
  lang?: string;
  label: string;
}

function applyFilter(items: ReplyQueueItem[], f: ListFilter): ReplyQueueItem[] {
  return items.filter((it) => {
    if (f.status && String(it.status || "").toLowerCase() !== f.status) return false;
    if (f.intent && String(it.intent_tag || "").toLowerCase() !== f.intent) return false;
    if (f.platform && String(it.platform || "").toLowerCase() !== f.platform) return false;
    if (f.lang && String(it.lang || "").toLowerCase() !== f.lang) return false;
    return true;
  });
}

export function ReplyQueueBoardPage({
  apiToken = "",
  onNavigate,
  embeddedModuleKey,
}: {
  apiToken?: string;
  onNavigate?: (navKey: string) => void;
  embeddedModuleKey?: string;
}) {
  const [editing, setEditing] = React.useState(false);
  const [statusFilter, setStatusFilter] = React.useState<string>("pending"); // 旧页默认待起草
  const [listFilter, setListFilter] = React.useState<ListFilter | null>(null); // 全量列表弹窗
  // 详情上下文:打开时按当时过滤快照冻结 id 序(动作改状态不churn连续翻;数据仍按 id 读活行)
  const [detailCtx, setDetailCtx] = React.useState<{ ids: number[]; index: number } | null>(null);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  const queue = useQueueData(apiToken);
  const kpiSeries = useKpiSeriesData(apiToken);
  // 刷新/扫描后列表与时序一起重拉(入队会动按日序列;两端点独立加载互不拖累)
  const reloadAll = React.useCallback(() => {
    queue.reload();
    kpiSeries.reload();
  }, [queue.reload, kpiSeries.reload]);
  const screen = useScreenAction(apiToken, reloadAll);
  const draft = useDraftAction(apiToken, queue.patchItem);
  const mark = useMarkAction(apiToken, queue.patchItem);
  const clip = useCopyDraft();

  const items = queue.items;
  const hasMore = !!items && queue.total > items.length;
  const counts = React.useMemo(() => (items ? queueCounts(items) : null), [items]);
  const itemsById = React.useMemo(() => {
    const map = new Map<number, ReplyQueueItem>();
    (items || []).forEach((it) => map.set(it.id, it));
    return map;
  }, [items]);

  /* ---------- 详情连续翻(冻结 id 序;↑↓/‹› 由弹窗回调) ---------- */
  const openDetail = React.useCallback(
    (list: ReplyQueueItem[], index: number) => {
      draft.clearError();
      mark.clearError();
      setDetailCtx({ ids: list.map((it) => it.id), index });
    },
    [draft, mark],
  );
  const gotoDetail = (i: number) => {
    if (!detailCtx || i < 0 || i >= detailCtx.ids.length) return;
    draft.clearError();
    mark.clearError();
    setDetailCtx({ ...detailCtx, index: i });
  };
  const closeDetail = () => {
    draft.clearError();
    mark.clearError();
    setDetailCtx(null);
  };

  // 溯源身份跳:kol_pool_id 命中 KOL 池 → KOL 档案页(sessionStorage + 既有事件管道)
  const jumpKol = React.useCallback(
    (item: ReplyQueueItem) => {
      if (item.kol_pool_id == null) return;
      try {
        window.sessionStorage.setItem("vkpi:kol-profile-id", String(item.kol_pool_id));
      } catch {
        /* sessionStorage 不可用忽略,事件管道仍会切页 */
      }
      if (onNavigate) onNavigate("kolProfile");
      window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile"));
    },
    [onNavigate],
  );

  // 详情闭环动作适配器:端点真实返回才落状态;终态(已回复/已忽略)不给回炉按钮
  const detailActions = (item: ReplyQueueItem): QueueDetailActions => {
    const status = String(item.status || "").toLowerCase();
    const terminal = status === "replied" || status === "dismissed";
    return {
      draftBusy: draft.busyId === item.id,
      draftError: draft.error,
      onDraft: !terminal && apiToken ? () => draft.run(item.id) : undefined,
      copied: clip.copiedId === item.id,
      onCopy: () => {
        if (item.draft_reply) clip.copy(item.id, item.draft_reply);
      },
      markBusy: mark.busyId === item.id,
      markError: mark.error,
      onMarkReplied: status !== "replied" && apiToken ? () => mark.run(item.id, "replied", status) : undefined,
      onMarkDismissed: status !== "dismissed" && apiToken ? () => mark.run(item.id, "dismissed", status) : undefined,
      draftReceipt: draft.receipts[item.id],
      onIdentityJump: item.kol_pool_id != null ? () => jumpKol(item) : undefined,
    };
  };

  /* ---------- 模块闸(未登录/加载/端点失败 → 诚实卡,绝不假数据) ---------- */
  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载回复队列。
    </PendingCard>
  );
  const gate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (queue.error && !items) return <ErrorCard title="reply-queue 读取失败" text={queue.error} />;
    if (queue.loading && !items) return <LoadingLine text="回复队列读取中…" />;
    if (!items) return <EmptyLine text="暂无数据。" />;
    return null;
  };

  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  // extraRows:调用点动态口径行,合并结果记 ref → ModuleProvModal 与 SrcChip hover 同一份全量
  const mergedRowsRef = React.useRef<Record<string, Array<[string, string]>>>({});
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    mergedRowsRef.current[key] = rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows, onOpenSrc: () => setProvKey(key) };
  };

  /* ---------- 模块 body ---------- */

  const renderKpi = () => {
    // 动态口径行(诚实信息进 SrcChip):分页真分母 / 时序真窗口 / 时序端点失败原因
    const kpiExtraRows: Array<[string, string]> = [];
    if (items && hasMore) {
      kpiExtraRows.push(["已载入", `${items.length}/${queue.total} 条 · 计数为已载入部分(弹窗「载入更多」拉齐)`]);
    }
    const win = kpiSeries.kpi?.window;
    if (win?.since && win?.until) {
      kpiExtraRows.push(["时序窗口", `${String(win.since).slice(0, 10)} → ${String(win.until).slice(0, 10)}(UTC 日轴,右沿=今天)`]);
    }
    if (kpiSeries.error) {
      kpiExtraRows.push(["时序端点", `kpi-series → ${kpiSeries.error}(四卡诚实回落虚线)`]);
    }
    return (
      <ModuleCard {...cardProps("kpiQ", "队列总览", counts ? `${counts.total}` : undefined, kpiExtraRows)}>
        {gate() ?? <QueueKpiBand counts={counts!} kpi={kpiSeries.kpi} />}
      </ModuleCard>
    );
  };

  const faceFilter: ListFilter = {
    status: statusFilter || undefined,
    label: statusFilter ? STATUS_META[statusFilter]?.label || statusFilter : "全部",
  };

  const renderQueue = () => {
    const g = gate();
    let body: React.ReactNode;
    if (g) body = g;
    else {
      const filtered = applyFilter(items!, faceFilter);
      body = (
        <div>
          <StatusChips active={statusFilter} counts={counts} onChange={setStatusFilter} />
          {items!.length === 0 ? (
            <EmptyLine text="队列 0 条。点右上「扫描新意向」从评论里筛购买意向。" />
          ) : (
            <>
              {filtered.length === 0 ? (
                <EmptyLine
                  text={
                    hasMore
                      ? `「${faceFilter.label}」下已载入页内暂无队列项(服务端还有未载入行,下方入口可载入更多)。`
                      : `「${faceFilter.label}」下暂无队列项。`
                  }
                />
              ) : (
                filtered.slice(0, FACE_ROWS).map((item, i) => (
                  <QueueRowLine key={item.id} item={item} index={i} onOpen={(idx) => openDetail(filtered, idx)} />
                ))
              )}
              {(filtered.length > FACE_ROWS || hasMore) && (
                <button
                  type="button"
                  onClick={() => setListFilter(faceFilter)}
                  title={hasMore ? `服务端还有未载入队列行(已载入 ${items!.length}/${queue.total}),弹窗内「载入更多」拉齐` : undefined}
                  className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
                >
                  ≡ 查看全量 {filtered.length} 条{hasMore ? `(队列已载入 ${items!.length}/${queue.total})` : ""} · 点单条连续翻
                </button>
              )}
            </>
          )}
        </div>
      );
    }
    const filteredCount = items ? applyFilter(items, faceFilter).length : null;
    return <ModuleCard {...cardProps("queue", "回复队列", filteredCount != null ? `${filteredCount}` : undefined)}>{body}</ModuleCard>;
  };

  const renderIntent = () => (
    <ModuleCard {...cardProps("intent", "意向构成", counts ? `${counts.byIntent.length} 类` : undefined)}>
      {gate() ??
        (counts!.total === 0 ? (
          <EmptyLine text="队列 0 条,环图诚实不画。" />
        ) : (
          <CatDonutBody
            categories={counts!.byIntent}
            totalMatched={counts!.total}
            onSelect={(key, label) => setListFilter({ intent: key, label: `意向 ${label}` })}
          />
        ))}
    </ModuleCard>
  );

  const renderFunnel = () => (
    <ModuleCard {...cardProps("funnel", "处理进度", counts ? `${counts.byStatus.replied || 0} 已回` : undefined)}>
      {gate() ?? <FunnelBody counts={counts!} />}
    </ModuleCard>
  );

  const renderPlat = () => (
    <ModuleCard {...cardProps("plat", "平台分布", counts ? `${counts.byPlatform.length}` : undefined)}>
      {gate() ?? <PlatBody counts={counts!} onRow={(key) => setListFilter({ platform: key, label: `平台 ${platformBadge(key)}` })} />}
    </ModuleCard>
  );

  const renderLang = () => (
    <ModuleCard {...cardProps("lang", "语言分布", counts ? `${counts.byLang.length}` : undefined)}>
      {gate() ?? <LangBody counts={counts!} onRow={(key) => setListFilter({ lang: key, label: `语言 ${langLabel(key)}` })} />}
    </ModuleCard>
  );

  /* ---------- 模块注册表(palette 全量可选;默认简 = 前四) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiQ", label: "队列总览带", description: "待起草 / 待回复 / 已回复 / 价格购买意向 真值四卡 · 按日时序 + 环比", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpi },
    { key: "queue", label: "回复队列", description: "状态过滤 + 一行一条 · 点开连续翻 · 起草/复制/标记闭环", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 6, maxHeight: 26, render: renderQueue },
    { key: "intent", label: "意向构成", description: "价格/兼容/问询/手动入队 环图 · 分段点开该类队列", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 6, minHeight: 5, maxHeight: 16, render: renderIntent },
    { key: "funnel", label: "处理进度", description: "待起草 → 待回复 → 已回复/已忽略 状态漏斗", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16, render: renderFunnel },
    // ↓ palette 备选(不进默认布局,注册表保留全量可选)
    { key: "plat", label: "平台分布", description: "队列行按平台条形分布 · 点行看该平台队列", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 6, minHeight: 4, maxHeight: 16, render: renderPlat },
    { key: "lang", label: "语言分布", description: "队列行按语种条形分布 · 点行看该语种队列", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 6, minHeight: 4, maxHeight: 16, render: renderLang },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="回复队列" />;
  }

  /* ---------- 弹窗数据 ---------- */
  const listItems = listFilter && items ? applyFilter(items, listFilter) : null;
  const detailIds = detailCtx ? detailCtx.ids.filter((id) => itemsById.has(id)) : [];
  const detailIndex = detailCtx ? Math.min(detailCtx.index, Math.max(0, detailIds.length - 1)) : 0;
  const detailItem = detailCtx && detailIds.length > 0 ? itemsById.get(detailIds[detailIndex]) || null : null;

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 实时辉光点 + 扫描/刷新/编辑布局;零介绍文案(口径住 SrcChip) */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">回复队列 · 评论区销售员</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            实时
          </span>
          <button
            type="button"
            onClick={screen.run}
            disabled={screen.busy || !apiToken}
            title="从评论库扫购买意向入队(POST /reply-queue/screen,幂等,重复扫不重复入队)"
            className="rounded-xl border border-accent bg-accent-soft px-3 py-2 text-[12px] text-accent transition-colors hover:border-accent-hover disabled:cursor-default disabled:border-line disabled:bg-card disabled:text-muted"
          >
            {screen.busy ? "扫描中…" : "⌁ 扫描新意向"}
          </button>
          <button
            type="button"
            onClick={reloadAll}
            disabled={queue.loading || !apiToken}
            title="重拉队列首页 + KPI 时序(服务器真数)"
            className="rounded-xl border border-line px-3 py-2 text-[12px] text-muted transition-colors hover:text-ink disabled:cursor-default disabled:text-muted"
          >
            {queue.loading ? "刷新中…" : "刷新"}
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
      {queue.error && items && (
        <div className="mb-3"><ErrorCard title="刷新失败(列表为上次成功数据)" text={queue.error} /></div>
      )}
      {screen.receipt && (
        <div className="mb-3 rounded-lg border border-good bg-good-soft px-3 py-1.5 text-[11.5px] text-good">
          扫描完成:已扫 {screen.receipt.scanned} 条 · 命中 {screen.receipt.matched} · 新入队 {screen.receipt.enqueued}(幂等,已在队不重复)
        </div>
      )}
      {screen.error && (
        <div className="mb-3"><ErrorCard title="扫描新意向失败" text={screen.error} /></div>
      )}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken(账户级持久化键为 Dashboard 专属) */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 全量列表弹窗(服务端分页:>500 由「载入更多(已显 X/Y)」逐页追加,失败不吞已载入列表) */}
      {listFilter && listItems && (
        <QueueListModal
          total={listItems.length}
          filterLabel={listFilter.label}
          loadedCount={items ? items.length : 0}
          streamTotal={queue.total}
          hasMore={hasMore}
          loading={queue.loadingMore}
          loadMoreError={queue.moreError}
          onLoadMore={queue.loadMore}
          onClose={() => setListFilter(null)}
        >
          {listItems.length === 0 ? (
            <EmptyLine
              text={
                hasMore
                  ? "已载入页内该过滤组合下 0 条 —— 服务端还有未载入队列行,点下方「载入更多」继续找。"
                  : "该过滤组合下 0 条 —— 诚实空,不编样本。"
              }
            />
          ) : (
            listItems.map((item, i) => (
              <QueueRowLine key={item.id} item={item} index={i} onOpen={(idx) => openDetail(listItems, idx)} />
            ))
          )}
        </QueueListModal>
      )}

      {/* 单条详情:‹#n/N› + ↑↓ 连续翻 + 溯源链 + 闭环动作(id 快照冻结,数据读活行) */}
      {detailItem && (
        <QueueDetailModal
          item={detailItem}
          index={detailIndex}
          total={detailIds.length}
          onNav={gotoDetail}
          onClose={closeDetail}
          actions={detailActions(detailItem)}
        />
      )}

      {/* 模块溯源说明弹窗(SrcChip 点击) */}
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
