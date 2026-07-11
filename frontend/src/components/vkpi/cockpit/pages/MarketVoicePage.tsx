import React from "react";
import { PencilLine } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import {
  enqueueReplyQueueComment,
  getVoiceFeed,
  getVoiceReport,
  type VoiceFeedItem,
} from "../../../../services/vkpi/marketVoice-api";
import {
  BucketsBody,
  ComplaintsBody,
  CoverRow,
  EmptyLine,
  ErrorCard,
  FeedDetailModal,
  FeedRowLine,
  GapsBody,
  KpiCard,
  LoadingLine,
  ModuleCard,
  ModuleProvModal,
  RecsBody,
  SOURCE_LABEL,
  SOURCE_ORDER,
  WishlistBody,
  type Row,
} from "./MarketVoicePage.modules";

// 件 C · 市场之声 → 板块页范式金样板 V0a(可编辑板块页,demo 1:1 对照)。
//   六要素落地:页级 KPI 带(ds-kpi)/ 图表优先 / 可编辑看板(EditableDashboardBoard,
//   布局本机记忆 storageKey=vkpi-market-voice-layout-v1)/ 溯源可点进(SrcChip+ProvChain+
//   RecordPreview)/ 全量+连续翻(反馈流分页 + ‹#n/N› + ↑↓)/ 诚实空态(逐源如实标)。
//   数据源(全真,零编造):
//     GET  /api/admin/vkpi/market/voice-report —— lexicon_v0 纯词表月报(抱怨/愿望/空白/建议)
//     GET  /api/admin/vkpi/market/voice-feed  —— vkpi_comments 分页原声(身份三分类+溯源 prov)
//     POST /api/admin/vkpi/reply-queue/enqueue-comment —— V0e 闭环「转回复队列」(幂等入队);
//       「转产品部」无落库通路(无 market_insights/PRD 表)→ 弹窗内诚实 disabled,不假装成功
//   红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;端点失败=诚实错误卡;
//         颜色全 token 类零写死色;动效只用既有 ds-viz 类(自带 reduced-motion 降级)。
//   注意:板块布局只走本机 storageKey,不给 EditableDashboardBoard 传 apiToken——
//         该组件账户级持久化写死 dashboard_layout_v1 键,传了会覆写 Dashboard 的账户布局。

const STORAGE_KEY = "vkpi-market-voice-layout-v1";
const FEED_PAGE = 20;

// 默认布局(12 列):kpiV(12) → complaints(8)+cover(4) → feed(8)+wishlist(4) → gaps(8)+recs(4)
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiV", span: 12 },
  { moduleKey: "complaints", span: 8 },
  { moduleKey: "cover", span: 4 },
  { moduleKey: "feed", span: 8 },
  { moduleKey: "wishlist", span: 4 },
  { moduleKey: "gaps", span: 8 },
  { moduleKey: "recs", span: 4 },
];

// 每模块 SrcChip 口径(label=真实表名;rows=board-paradigm 接入映射的真实来源,禁编造)
const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiV: {
    label: "voice-report · lexicon_v0",
    rows: [
      ["评论库", "vkpi_comments"],
      ["意向队列", "vkpi_reply_queue"],
      ["B&H 口碑", "vkpi_bh_reviews"],
      ["需求信号", "vkpi_brand_signal"],
      ["情感", "vkpi_sentiment_results(未点火)"],
    ],
  },
  complaints: {
    label: "vkpi_comments · lexicon_v0",
    rows: [
      ["口径", "话题词 + 负面线索双命中"],
      ["主表", "vkpi_comments 等三源"],
      ["方法", "纯词表聚合 · 零 LLM"],
    ],
  },
  wishlist: {
    label: "vkpi_comments · lexicon_v0",
    rows: [
      ["口径", "wish/hope/please make/需要 词表"],
      ["主表", "vkpi_comments 等三源"],
    ],
  },
  gaps: {
    label: "vkpi_products · 目录对照",
    rows: [
      ["声量", "vkpi_comments 等三源"],
      ["目录基准", "vkpi_products 焦段"],
    ],
  },
  recs: {
    label: "lexicon_v0 · 规则生成",
    rows: [
      ["输入", "抱怨 + 愿望 + 空白聚类"],
      ["方法", "规则阈值 · 人工复核"],
    ],
  },
  cover: {
    label: "voice-report · sources",
    rows: [
      ["健康", "逐源 status / count 如实标"],
      ["盲区", "空源 / 未建表如实标注"],
    ],
  },
  feed: {
    label: "vkpi_comments · 分页",
    rows: [
      ["正文", "vkpi_comments.comment_text"],
      ["身份", "post_table 三分类 kol/owned/user"],
      ["原帖", "evidence.content_url / 官号链接"],
    ],
  },
  buckets: {
    label: "focal_matrix · lexicon_v0",
    rows: [
      ["口径", "focal_matrix 产品线词表"],
      ["主表", "vkpi_comments 等三源"],
    ],
  },
};

const PROV_TITLES: Record<string, string> = {
  kpiV: "反馈总览",
  complaints: "抱怨聚类",
  wishlist: "愿望清单",
  gaps: "需求空白",
  recs: "给产品部的建议",
  cover: "监听覆盖",
  feed: "反馈流",
  buckets: "产品线声量分桶",
};

export function MarketVoicePage({ apiToken = "" }: { apiToken?: string; onNavigate?: (navKey: string) => void }) {
  const [month, setMonth] = React.useState<string>("");
  const [data, setData] = React.useState<Row | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [editing, setEditing] = React.useState(false);

  // 反馈流(voice-feed)分页状态
  const [feedItems, setFeedItems] = React.useState<VoiceFeedItem[] | null>(null);
  const [feedTotal, setFeedTotal] = React.useState(0);
  const [feedLoading, setFeedLoading] = React.useState(false);
  const [feedError, setFeedError] = React.useState("");
  const feedBusyRef = React.useRef(false);

  // 详情弹窗:participants = 当前已加载 items;wantIndex = 翻到尾部待加载的目标位
  const [detailIndex, setDetailIndex] = React.useState<number | null>(null);
  const [wantIndex, setWantIndex] = React.useState<number | null>(null);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  // V0e 闭环「转回复队列」:queuedIds = 本会话真实入队成功(含 already_queued)的评论 id;
  // 状态只在端点真实返回后落地,绝不点击即置绿(不编造成功状态)。
  const [queuedIds, setQueuedIds] = React.useState<Set<number>>(() => new Set());
  const [queueBusyId, setQueueBusyId] = React.useState<number | null>(null);
  const [queueError, setQueueError] = React.useState("");

  // 月报(现有真数据源,原样保留)
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    setData(null);
    getVoiceReport(apiToken, month)
      .then((res) => {
        if (alive) setData(res && typeof res === "object" ? res : null);
      })
      .catch((err: any) => {
        if (alive) setError(String(err?.detail || err?.message || "加载失败"));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, month]);

  const loadFeed = React.useCallback(
    (offset: number) => {
      if (!apiToken || feedBusyRef.current) return;
      feedBusyRef.current = true;
      setFeedLoading(true);
      if (offset === 0) setFeedError("");
      getVoiceFeed(apiToken, { offset, limit: FEED_PAGE })
        .then((res) => {
          if (res && String((res as Row).status) === "error") {
            // 后端诚实降级形状:契约不变 + status:error(绝不编数据)
            setFeedError(String((res as Row).reason || "反馈流读取失败"));
            if (offset === 0) {
              setFeedItems(null);
              setFeedTotal(0);
            }
            return;
          }
          const items = Array.isArray(res?.items) ? res.items : [];
          setFeedTotal(Number(res?.total) || 0);
          setFeedItems((prev) => (offset === 0 || !prev ? items : [...prev, ...items]));
        })
        .catch((err: any) => {
          setFeedError(String(err?.detail || err?.message || "加载失败"));
          if (offset === 0) setFeedItems(null);
        })
        .finally(() => {
          feedBusyRef.current = false;
          setFeedLoading(false);
        });
    },
    [apiToken],
  );

  React.useEffect(() => {
    if (apiToken) loadFeed(0);
  }, [apiToken, loadFeed]);

  // 连续翻:目标在已加载区 → 直接切;越过尾部且还有下一页 → 先拉 offset 再落位
  const gotoFeed = (i: number) => {
    const items = feedItems || [];
    if (i < 0 || (feedTotal > 0 && i >= feedTotal)) return;
    setQueueError("");
    if (i < items.length) {
      setDetailIndex(i);
      setWantIndex(null);
    } else if (items.length < feedTotal && !feedBusyRef.current) {
      setWantIndex(i);
      loadFeed(items.length);
    }
  };

  // V0e「转回复队列」:真调 POST /reply-queue/enqueue-comment;成功(含 already_queued=幂等
  // 命中已有行)才落 queuedIds;失败原因原样进 queueError(不吞、不假绿)。
  const enqueueToReplyQueue = React.useCallback(
    (item: VoiceFeedItem) => {
      if (!apiToken || !item || queueBusyId != null) return;
      setQueueBusyId(item.id);
      setQueueError("");
      enqueueReplyQueueComment(apiToken, item.id)
        .then((res) => {
          if (res && res.ok) {
            setQueuedIds((prev) => {
              const next = new Set(prev);
              next.add(item.id);
              return next;
            });
          } else {
            setQueueError(String((res as Row)?.reason || "端点返回非 ok"));
          }
        })
        .catch((err: any) => {
          setQueueError(String(err?.detail || err?.message || "入队失败"));
        })
        .finally(() => {
          setQueueBusyId((cur) => (cur === item.id ? null : cur));
        });
    },
    [apiToken, queueBusyId],
  );

  React.useEffect(() => {
    if (wantIndex != null && feedItems && wantIndex < feedItems.length) {
      setDetailIndex(wantIndex);
      setWantIndex(null);
    }
  }, [feedItems, wantIndex]);

  const sources: Row = data?.sources || {};
  const complaints: Row = data?.complaints || {};
  const wishlist: Row = data?.wishlist || {};
  const gaps: Row = data?.gaps || {};
  const suggestions: Row = data?.suggestions || {};
  const windowLabel = String(data?.window?.label || (month ? month : "近30天"));
  const sampleSize = Number(data?.sample_size) || 0;
  const dedupRemoved = Number(data?.dedup_removed) || 0;
  const reportEmpty = String(data?.status) === "empty";
  const reportErrored = String(data?.status) === "error";

  const srcCount = (key: string) => Number((sources[key] || {}).count) || 0;

  // 报表驱动模块的统一闸:未登录 / 加载中 / 端点失败 / 聚合失败 → 诚实卡,绝不假数据
  const reportGate = (): React.ReactNode | null => {
    if (!apiToken) return <EmptyLine text="未登录 / 无 token,无法加载数据。" />;
    if (loading) return <LoadingLine />;
    if (error) return <ErrorCard title="voice-report 读取失败" text={error} />;
    if (!data) return <EmptyLine text="暂无数据。" />;
    if (reportErrored) return <ErrorCard title="聚合失败" text={String(data.reason || "未知原因")} />;
    return null;
  };
  const sectionGate = (section: Row): React.ReactNode | null => {
    const gate = reportGate();
    if (gate) return gate;
    if (reportEmpty) return <EmptyLine text={String(data?.reason || "该窗口无声音数据。")} />;
    if (!section || Object.keys(section).length === 0) return <EmptyLine text="该窗口无本段数据。" />;
    return null;
  };

  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const cardProps = (key: string, title: string, cnt?: React.ReactNode) => ({
    title,
    cnt,
    srcLabel: srcOf(key).label,
    srcRows: srcOf(key).rows,
    onOpenSrc: () => setProvKey(key),
  });

  /* ---------- 模块 body ---------- */

  const renderKpiBand = () => {
    const gate = reportGate();
    const sentimentCount = srcCount("sentiment");
    return (
      <ModuleCard {...cardProps("kpiV", "反馈总览", `${windowLabel} · lexicon_v0`)}>
        {gate ?? (
          <>
            <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
              <KpiCard label="窗口样本" value={sampleSize.toLocaleString()} unit="条" seriesNote="三源合并去重 · 单窗口计数" />
              <KpiCard label="评论库" value={srcCount("comments").toLocaleString()} unit="条" seriesNote="vkpi_comments · 窗口计数" />
              <KpiCard label="意向队列" value={srcCount("intent_queue").toLocaleString()} unit="条" seriesNote="vkpi_reply_queue · 入队时间窗" />
              {sentimentCount > 0 ? (
                <KpiCard label="情感结果" value={sentimentCount.toLocaleString()} unit="条" seriesNote="vkpi_sentiment_results · 全表计数" />
              ) : (
                <KpiCard label="情感结果" value="—" pending pendingNote="情绪管线未点火" seriesNote="vkpi_sentiment_results · 0 行" />
              )}
            </div>
            <div className="mt-2 text-[10px] text-muted">
              窗口 {windowLabel} · 样本 {sampleSize} 条
              {dedupRemoved > 0 ? `(跨源去重 -${dedupRemoved})` : ""} · 纯词表聚合零 LLM · 不参与 V6 Fit 评分
            </div>
          </>
        )}
      </ModuleCard>
    );
  };

  const renderComplaints = () => (
    <ModuleCard {...cardProps("complaints", "抱怨聚类", `命中 ${Number(complaints.total_matched) || 0} 条 · 话题词+负面线索双命中`)}>
      {sectionGate(complaints) ?? <ComplaintsBody complaints={complaints} />}
    </ModuleCard>
  );

  const renderWishlist = () => (
    <ModuleCard {...cardProps("wishlist", "愿望清单", `命中 ${Number(wishlist.total) || 0} 条`)}>
      {sectionGate(wishlist) ?? <WishlistBody wishlist={wishlist} />}
    </ModuleCard>
  );

  const renderGaps = () => (
    <ModuleCard
      {...cardProps(
        "gaps",
        "需求空白",
        `目录焦段 ${Number(gaps.catalog_focal_count) || 0} 个${gaps.catalog_basis ? ` · ${gaps.catalog_basis}` : ""}`,
      )}
    >
      {sectionGate(gaps) ?? <GapsBody gaps={gaps} />}
    </ModuleCard>
  );

  const renderRecs = () => (
    <ModuleCard {...cardProps("recs", "给产品部的建议", `${Array.isArray(suggestions.items) ? suggestions.items.length : 0} 条 · 人工复核`)}>
      {sectionGate(suggestions) ?? <RecsBody suggestions={suggestions} />}
    </ModuleCard>
  );

  const renderCover = () => {
    const gate = reportGate();
    const readyCount = SOURCE_ORDER.filter((k) => String((sources[k] || {}).status) === "ready").length;
    return (
      <ModuleCard {...cardProps("cover", "监听覆盖", data ? `${readyCount}/${SOURCE_ORDER.length}` : undefined)}>
        {gate ?? (
          <div>
            {SOURCE_ORDER.map((key) => {
              const s: Row = sources[key] || {};
              const status = String(s.status || "unknown");
              const on = status === "ready";
              const value =
                status === "ready"
                  ? `${Number(s.count) || 0} 条 · ready`
                  : status === "absent"
                    ? "表未建 · 盲区"
                    : status === "empty"
                      ? "0 条 · 已建未跑"
                      : "状态未知";
              return (
                <CoverRow
                  key={key}
                  on={on}
                  name={SOURCE_LABEL[key] || key}
                  table={String(s.table || "")}
                  value={value}
                  note={String(s.note || s.table || "")}
                />
              );
            })}
            <div className="vkpi-prov-note">逐源如实标注 · 空源 / 未建表 = 盲区,不装 live</div>
          </div>
        )}
      </ModuleCard>
    );
  };

  const renderFeed = () => {
    const loaded = feedItems || [];
    let body: React.ReactNode;
    if (!apiToken) body = <EmptyLine text="未登录 / 无 token,无法加载数据。" />;
    else if (feedError) body = <ErrorCard title="端点待接 · voice-feed" text={`GET /api/admin/vkpi/market/voice-feed → ${feedError}`} />;
    else if (feedLoading && !feedItems) body = <LoadingLine text="反馈流读取中…" />;
    else if (!feedItems) body = <EmptyLine text="暂无数据。" />;
    else if (loaded.length === 0) body = <EmptyLine text="反馈流 0 条(vkpi_comments 窗口内无非空正文)。" />;
    else
      body = (
        <div>
          {loaded.map((item, i) => (
            <FeedRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={(idx) => gotoFeed(idx)} queued={queuedIds.has(item.id)} />
          ))}
          {loaded.length < feedTotal ? (
            <button
              type="button"
              onClick={() => loadFeed(loaded.length)}
              disabled={feedLoading}
              className="mt-2 w-full rounded-lg border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft disabled:opacity-50"
            >
              {feedLoading ? "加载中…" : `≡ 载入更多(已加载 ${loaded.length}/${feedTotal} · 点单条可 ↑↓ 连续翻)`}
            </button>
          ) : (
            <div className="mt-2 text-center text-[10px] text-muted">已全量加载 {feedTotal} 条</div>
          )}
        </div>
      );
    return (
      <ModuleCard {...cardProps("feed", "反馈流", feedItems ? `${feedTotal} 条` : undefined)}>
        {body}
      </ModuleCard>
    );
  };

  const renderBuckets = () => (
    <ModuleCard {...cardProps("buckets", "产品线声量分桶", String(data?.buckets?.product_line_basis || "focal_matrix"))}>
      {sectionGate(data?.buckets || {}) ?? <BucketsBody data={data} />}
    </ModuleCard>
  );

  /* ---------- 模块注册表(palette 全量可选) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiV", label: "反馈总览带", description: "样本 / 评论库 / 意向队列 / 情感 4 核心数", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 8, minHeight: 6, maxHeight: 14, render: renderKpiBand },
    { key: "complaints", label: "抱怨聚类", description: "话题词 + 负面线索双命中 · 原声引文", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 7, maxHeight: 24, render: renderComplaints },
    { key: "wishlist", label: "愿望清单", description: "焦段 / 变焦 / 卡口 chips + 原声", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 13, minHeight: 7, maxHeight: 24, render: renderWishlist },
    { key: "gaps", label: "需求空白", description: "有声量但目录零 SKU 的焦段", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 11, minHeight: 6, maxHeight: 24, render: renderGaps },
    { key: "recs", label: "给产品部的建议", description: "规则生成 · lexicon_v0 · 人工复核", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 6, maxHeight: 24, render: renderRecs },
    { key: "feed", label: "反馈流", description: "vkpi_comments 一行一条 · 身份徽 · 点开连续翻", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 7, maxHeight: 26, render: renderFeed },
    { key: "cover", label: "监听覆盖", description: "五源健康 + 盲区如实标注", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 12, minHeight: 6, maxHeight: 20, render: renderCover },
    { key: "buckets", label: "产品线声量分桶", description: "focal_matrix 词表口径", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 7, minHeight: 5, maxHeight: 16, render: renderBuckets },
  ];

  const detailItem = detailIndex != null && feedItems ? feedItems[detailIndex] : null;

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead:标题 + 月份选择 / 近30天(现有控件保留)+ 编辑布局 */}
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-[18px] font-semibold text-ink">市场之声 · 用户反馈雷达</div>
          <div className="mt-0.5 text-[12px] text-muted">
            用户反馈聚类反哺产品部 —— 评论库 + 意向队列 + B&H 口碑纯词表聚合(lexicon_v0,零 LLM 零采集),供人工复核。
          </div>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="month"
            value={month}
            onChange={(ev) => setMonth(ev.target.value)}
            className="rounded-xl border border-line bg-card px-3 py-2 text-[12px] text-ink outline-none focus:border-accent"
          />
          <button
            type="button"
            onClick={() => setMonth("")}
            className={`rounded-xl border px-3 py-2 text-[12px] transition-colors ${month ? "border-line text-muted hover:text-ink" : "border-accent text-accent"}`}
          >
            近30天
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

      {!apiToken && <div className="mb-3"><EmptyLine text="未登录 / 无 token,无法加载数据。" /></div>}
      {error && <div className="mb-3"><ErrorCard title="voice-report 读取失败" text={error} /></div>}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {data && !reportEmpty && !reportErrored && (
        <div className="mt-3 text-right text-[10px] text-muted">
          生成于 {String(data.generated_at || "—")}(UTC)· {String(data.note || "纯词表聚合已有数据,零 LLM;独立展示信号,不参与 V6 Fit 评分。")}
        </div>
      )}

      {/* 反馈单条详情:‹ #n/N › + ↑↓ 连续翻;participants=已加载 items,翻到尾部自动加载下一页 */}
      {detailItem && (
        <FeedDetailModal
          item={detailItem}
          index={detailIndex as number}
          total={feedTotal}
          loadingNext={feedLoading}
          onNav={gotoFeed}
          onClose={() => {
            setDetailIndex(null);
            setWantIndex(null);
            setQueueError("");
          }}
          queued={queuedIds.has(detailItem.id)}
          queueBusy={queueBusyId === detailItem.id}
          queueError={queueError}
          onEnqueueReply={
            // 入队按 vkpi_comments.id;反馈流当前唯一源即 vkpi_comments,其他源出现时如实禁用
            detailItem.source_table === "vkpi_comments" ? () => enqueueToReplyQueue(detailItem) : undefined
          }
        />
      )}

      {/* 模块溯源说明弹窗(SrcChip 点击) */}
      {provKey && (
        <ModuleProvModal title={PROV_TITLES[provKey] || provKey} caliber={srcOf(provKey).rows} onClose={() => setProvKey(null)} />
      )}
    </div>
  );
}
