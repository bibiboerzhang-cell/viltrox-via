import React from "react";
import { PencilLine } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import {
  fetchVoiceReportExt,
  getVoiceFeed,
  getVoiceReport,
  type VoiceFeedItem,
  type VoiceReportExt,
} from "../../../../services/vkpi/marketVoice-api";
import { prdKey, recoStableKey, usePrdReferral, usePrdStatusBoard, useReplyQueueAction } from "./MarketVoicePage.actions";
import {
  BucketsBody,
  ComplaintsBody,
  CoverRow,
  EmptyLine,
  ErrorCard,
  FeedRowLine,
  GapsBody,
  LISTEN_KEYS,
  LoadingLine,
  MODULE_SOURCES,
  ModuleCard,
  PROV_TITLES,
  PendingCard,
  PrdStatusBody,
  RecsBody,
  SOURCE_LABEL,
  SOURCE_ORDER,
  WishlistBody,
  type Row,
} from "./MarketVoicePage.modules";
import {
  AlertsBody,
  CatDonutBody,
  CompetitorBody,
  KpiBandCards,
  LanguageBody,
  LineVoiceBody,
  PlatformBody,
  SentiTrendBody,
  TopicsBody,
} from "./MarketVoicePage.charts";
import { DrillFeedModal, FeedDetailModal, FeedListModal, ModuleProvModal, drillAlertSpec, drillCategorySpec, drillSentimentSpec, drillTopicSpec, type VoiceDrillSpec } from "./MarketVoicePage.dialogs";

// 件 C · 市场之声 → 板块页范式金样板(可编辑板块页,demo 1:1 对照)。
//   V0h-ab 波2 终棒:demo 全部图形模块补齐 + KPI 带图形化——
//   KPI 四卡对齐 demo 语义(本月反馈/待处理/正面情绪占比/已转产品部)+ 真 sparkline
//   (kpi_series 按日序列)+ 真环比药丸(kpi_prev,null=诚实省略);新增图表模块族
//   alerts/cat/senti/line_voice/plat/topics/geo/comp(MarketVoicePage.charts.tsx),
//   默认布局对齐 demo 六行;complaints/wishlist/gaps/buckets/plat 降为 palette 备选。
//   数据源(全真,零编造):
//     GET  /api/admin/vkpi/market/voice-report     —— lexicon_v0 纯词表月报(抱怨/愿望/空白/建议)
//     GET  /api/admin/vkpi/market/voice-report-ext —— 九组图形化字段(每组独立 status,逐组诚实降级)
//     GET  /api/admin/vkpi/market/voice-feed       —— vkpi_comments 分页原声(身份三分类+溯源 prov)
//     POST /api/admin/vkpi/reply-queue/enqueue-comment —— V0e 闭环「转回复队列」(幂等入队)
//     POST /api/admin/vkpi/market/prd-referrals    —— V1.1 真闭环「转产品部」:落
//       vkpi_market_prd_referrals(迁移 234,幂等);KPI 第四卡读 voice-report-ext
//       prd_referrals 组真计数(0 也如实 0,成功转交后重拉服务器数,绝不本地编数)
//     GET   /api/admin/vkpi/market/prd-referrals   —— prdStatus 模块转交账本(最近 50 条)
//     PATCH /api/admin/vkpi/market/prd-referrals/{id}/status —— PRD 状态流转:
//       referred → accepted / rejected(幂等);药丸只在端点真实返回后按服务器行变色
//   跳转桥(诚实版):line_voice 产品线行 → SKU360,产品线级无逐 SKU 关联,不装 SKU
//   直跳 —— 只把产品线词写 vkpi:sku360-search 预填 SKU 搜索(接收位在 Sku360BoardPage)。
//   红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;端点失败=诚实错误卡;颜色全
//   token(SVG 直接 var(--ds-*));发光只走 --ds-glow-radius(浅色 0);动效只用既有
//   ds-viz 类 + vkpi-lane-pulse(自带 reduced-motion 降级)。布局只走本机 storageKey,
//   不给 EditableDashboardBoard 传 apiToken(其账户级持久化写死 dashboard_layout_v1 键)。

// v4:prdStatus(反哺产品部 · PRD 状态流转)进默认布局,bump 版本让新默认盖过本机旧布局
const STORAGE_KEY = "vkpi-market-voice-layout-v4";
const FEED_PAGE = 20; // 每次拉取页大小(端点分页)
const FEED_FACE = 6; // 卡面收敛条数(demo FULL.slice(0,6);全量走弹窗)

// 默认布局(12 列,demo defaultLayout 六行同构 + 第七行 prdStatus):
// kpiV(12) → alerts(8)+cover(4) → cat(4)+senti(8) → feed(8)+line_voice(4)
// → comp(8)+recs(4) → topics(8)+geo(4) → prdStatus(8)
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiV", span: 12 },
  { moduleKey: "alerts", span: 8 },
  { moduleKey: "cover", span: 4 },
  { moduleKey: "cat", span: 4 },
  { moduleKey: "senti", span: 8 },
  { moduleKey: "feed", span: 8 },
  { moduleKey: "line_voice", span: 4 },
  { moduleKey: "comp", span: 8 },
  { moduleKey: "recs", span: 4 },
  { moduleKey: "topics", span: 8 },
  { moduleKey: "geo", span: 4 },
  { moduleKey: "prdStatus", span: 8 },
];

// 榜单行下钻 v1(产品线/平台/语言维度过滤待接,诚实不装):行/SrcChip → 模块溯源弹窗 + 底部「底层样本」跳反馈流全量
// (line_voice 行点击已升级为 SKU360 跳转桥;其 SrcChip 溯源弹窗仍如实标维度过滤待接)
const DIM_PENDING = (dim: string) => `${dim}维度过滤待接 —— 底层样本打开的是反馈流全量,如实未按本行过滤。`;
const SAMPLE_PENDING: Record<string, string> = { line_voice: DIM_PENDING("产品线"), plat: DIM_PENDING("平台"), geo: DIM_PENDING("语言") };

// line_voice → SKU360 跳转桥的发送端三通道键(接收位在 Sku360BoardPage,同名常量刻意
// 不 import —— 两页各自 lazy chunk,避免打破分包;手法同 comp → 战略台桥)
const SKU360_SEARCH_KEY = "vkpi:sku360-search";
const SKU360_SEARCH_EVENT = "vkpi:open-sku360-search";

export function MarketVoicePage({ apiToken = "", onNavigate, embeddedModuleKey }: { apiToken?: string; onNavigate?: (navKey: string) => void; embeddedModuleKey?: string }) {
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

  // 弹窗:全量列表(卡面只留 6 条)/ 单条详情 / 模块溯源 / 数据点下钻(规格住 dialogs)
  const [feedListOpen, setFeedListOpen] = React.useState(false);
  const [detailIndex, setDetailIndex] = React.useState<number | null>(null);
  const [wantIndex, setWantIndex] = React.useState<number | null>(null);
  const [provKey, setProvKey] = React.useState<string | null>(null);
  const [drill, setDrill] = React.useState<VoiceDrillSpec | null>(null);

  // 闭环动作(状态只在端点真实返回后落地,绝不点击即置绿;逻辑住 MarketVoicePage.actions):
  // V0e「转回复队列」+ V1.1「转产品部」(vkpi_market_prd_referrals 真通路)
  const replyQueue = useReplyQueueAction(apiToken);
  const prd = usePrdReferral(apiToken);
  // prdStatus 模块:转交账本列表 + 行内 ✓采纳/✕拒绝(PATCH /{id}/status,幂等);
  // prd.version 变化(真实新增转交)→ 重拉列表,新转交行即刻进账本
  const prdBoard = usePrdStatusBoard(apiToken, prd.version);

  // voice-report-ext(V0h-ab 九组图形化字段;单组失败后端已诚实降级,整体失败走 extError)
  const [extData, setExtData] = React.useState<VoiceReportExt | null>(null);
  const [extLoading, setExtLoading] = React.useState(false);
  const [extError, setExtError] = React.useState("");

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

  // 图形化增量端点(与月报同窗口口径,独立加载互不拖累)。
  // prd.version:真实新增转交后自增 → 重拉本端点,「已转产品部」KPI 读服务器真数(绝不本地 +1 编数)
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setExtLoading(true);
    setExtError("");
    setExtData(null);
    fetchVoiceReportExt(apiToken, month)
      .then((res) => {
        if (alive) setExtData(res && typeof res === "object" ? res : null);
      })
      .catch((err: any) => {
        if (alive) setExtError(String(err?.detail || err?.message || "加载失败"));
      })
      .finally(() => {
        if (alive) setExtLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, month, prd.version]);

  const loadFeed = React.useCallback(
    (offset: number) => {
      if (!apiToken || feedBusyRef.current) return;
      feedBusyRef.current = true;
      setFeedLoading(true);
      setFeedError(""); // 每次请求发起都清(含「载入更多」),瞬时分页失败不再黏死吞列表
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
    replyQueue.clearError();
    prd.clearError();
    if (i < items.length) {
      setDetailIndex(i);
      setWantIndex(null);
    } else if (items.length < feedTotal && !feedBusyRef.current) {
      setWantIndex(i);
      loadFeed(items.length);
    }
  };

  // 溯源身份跳(波D 验收遗留):kol → KOL 档案页(sessionStorage 传 kol_pool_id,
  // CockpitApp 既有事件/onNavigate 管道);owned → 官号矩阵(MY KOL 团队矩阵/账号管理)。
  // 跳不了(user / kol 缺 identity_id)由调用点不给回调 → 身份节点纯文本,如实不可点。
  const jumpIdentity = React.useCallback(
    (item: VoiceFeedItem) => {
      if (item.identity === "kol" && item.identity_id != null) {
        try {
          window.sessionStorage.setItem("vkpi:kol-profile-id", String(item.identity_id));
        } catch {
          /* sessionStorage 不可用忽略,事件管道仍会切页 */
        }
        if (onNavigate) onNavigate("kolProfile");
        window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile"));
      } else if (item.identity === "owned") {
        if (onNavigate) onNavigate("my-kol");
        else window.dispatchEvent(new CustomEvent("vkpi:open-mykol-kol"));
      }
    },
    [onNavigate],
  );

  // comp 品牌行 → 战略台联动(接收端 StrategyDeskPage 三通道已挂:sessionStorage
  // vkpi:strategy-brand + vkpi:open-strategy-desk 事件 + onNavigate 切板块)。键名与
  // StrategyDeskPage 导出常量一致,刻意不 import(两页各自 lazy chunk,避免打破分包)。
  const openStrategyBrand = React.useCallback(
    (brandName: string) => {
      const brand = String(brandName || "").trim();
      if (!brand) return;
      try {
        window.sessionStorage.setItem("vkpi:strategy-brand", brand);
      } catch {
        /* sessionStorage 不可用忽略,事件通道仍在 */
      }
      window.dispatchEvent(new CustomEvent("vkpi:open-strategy-desk"));
      if (onNavigate) onNavigate("strategyBoard");
    },
    [onNavigate],
  );

  // line_voice 产品线行 → SKU360 跳转桥(诚实版):声音榜是产品线级、无逐 SKU 关联,
  // 不装 SKU 直跳 —— 只把产品线词写 vkpi:sku360-search 预填 SKU 搜索(接收位在
  // Sku360BoardPage,300ms 防抖搜索自动跑,搜不到=诚实「无匹配 SKU」)。三通道同 comp。
  const openSku360Search = React.useCallback(
    (lineLabel: string) => {
      const word = String(lineLabel || "").trim();
      if (!word) return;
      try {
        window.sessionStorage.setItem(SKU360_SEARCH_KEY, word);
      } catch {
        /* sessionStorage 不可用忽略,事件通道仍在 */
      }
      window.dispatchEvent(new CustomEvent(SKU360_SEARCH_EVENT));
      if (onNavigate) onNavigate("sku360");
    },
    [onNavigate],
  );

  React.useEffect(() => {
    if (wantIndex != null && feedItems && wantIndex < feedItems.length) {
      setDetailIndex(wantIndex);
      setWantIndex(null);
    }
  }, [feedItems, wantIndex]);

  // 详情弹窗适配器(主反馈流与数据点下钻弹窗共用一套闭环动作/身份跳):端点真实返回才置绿;
  // 转产品部按 (vkpi_comments, id) 幂等键、title=正文截断、category 留空如实;身份跳 user 不给。
  const detailAdapter = (item: VoiceFeedItem) => ({
    queued: replyQueue.queuedIds.has(item.id),
    queueBusy: replyQueue.busyId === item.id,
    queueError: replyQueue.error,
    onEnqueueReply: item.source_table === "vkpi_comments" ? () => replyQueue.enqueue(item.id) : undefined,
    referred: prd.referredKeys.has(prdKey("vkpi_comments", item.id)),
    referBusy: prd.busyKey === prdKey("vkpi_comments", item.id),
    referError: prd.error,
    onReferPrd: item.source_table === "vkpi_comments" ? () => prd.refer({ source_table: "vkpi_comments", source_id: String(item.id), title: (item.text || "").slice(0, 120), detail: "", category: "" }) : undefined,
    onIdentityJump: (item.identity === "kol" && item.identity_id != null) || item.identity === "owned" ? () => jumpIdentity(item) : undefined,
  });

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

  // 报表驱动模块的统一闸:未登录(管线级待接 → PendingCard)/ 加载中 / 端点失败 /
  // 聚合失败 → 诚实卡,绝不假数据
  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载市场之声数据。
    </PendingCard>
  );
  const reportGate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
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

  // voice-report-ext 逐组闸:整体失败 → 错误卡;缺组字段 → 诚实 pending(不编数据);
  // 组内 status error/empty → 逐组诚实降级(empty 带后端 reason 原样透出)。
  const extGate = (group: Row | undefined | null): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (extError) return <ErrorCard title="voice-report-ext 读取失败" text={extError} />;
    if (extLoading && !extData) return <LoadingLine text="图形化字段聚合中…" />;
    if (!extData || !group) {
      return (
        <PendingCard>
          <b>数据字段施工中</b> —— 扩展报表未返回该组字段,接通后自动点亮。
        </PendingCard>
      );
    }
    if (String(group.status) === "error") return <ErrorCard title="该组聚合失败" text={String(group.reason || "未知原因")} />;
    if (String(group.status) === "empty") return <EmptyLine text={String(group.reason || "窗口内无本组数据。")} />;
    return null;
  };

  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  // extraRows:调用点的动态口径行(原卡头长句的新家),拼在静态 rows 之后进 SrcChip hover 卡;
  // 合并结果记入 ref,点击打开的 ModuleProvModal 同样拿全量口径(hover 卡在矮模块里可能被裁)
  const mergedRowsRef = React.useRef<Record<string, Array<[string, string]>>>({});
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    mergedRowsRef.current[key] = rows;
    return {
      title,
      cnt,
      srcLabel: srcOf(key).label,
      srcRows: rows,
      onOpenSrc: () => setProvKey(key),
    };
  };

  /* ---------- 模块 body ---------- */

  // KPI 带四卡(V0h-ab 语义 + V1.1 第四卡真数;卡体在 charts.KpiBandCards):
  // sparkline=按日真序列,delta=真环比(null=诚实省略);「已转产品部」=
  // prd_referrals 组真计数(0 也如实 0,表未建 → 诚实 pending 带 reason)。
  const renderKpiBand = () => {
    const gate = reportGate();
    const senti: Row = extData?.sentiment_summary || {};
    // 原页脚「生成于…」/ 带底备注 / 旧口径四数 → 全部收进 SrcChip rows(诚实口径不丢)
    const kpiExtraRows: Array<[string, string]> =
      data && !reportErrored
        ? [
            ["窗口", `${windowLabel} · 样本 ${sampleSize} 条${dedupRemoved > 0 ? `(跨源去重 -${dedupRemoved})` : ""}`],
            ["旧口径·窗口样本", `${sampleSize.toLocaleString()} 条(三源合并去重)`],
            ["旧口径·评论库", `${srcCount("comments").toLocaleString()} 条 · vkpi_comments`],
            ["旧口径·意向队列", `${srcCount("intent_queue").toLocaleString()} 条 · vkpi_reply_queue`],
            ["情绪批注", `${Number(senti.done_total ?? srcCount("sentiment")) || 0} 条 · vkpi_sentiment_results`],
            ["生成于", `${String(data.generated_at || "—")}(UTC)`],
            ["口径", String(data.note || "纯词表聚合零 LLM · 不参与 V6 Fit 评分")],
            ...(extError ? ([["扩展端点", `voice-report-ext → ${extError}`]] as Array<[string, string]>) : []),
          ]
        : [];
    return (
      <ModuleCard {...cardProps("kpiV", "反馈总览", windowLabel, kpiExtraRows)}>
        {gate ?? (
          <KpiBandCards
            ext={extData as Row | null}
            fallbackComments={srcCount("comments")}
            fallbackQueue={srcCount("intent_queue")}
          />
        )}
      </ModuleCard>
    );
  };

  /* ---------- V0h-ab 图表模块族(数据=voice-report-ext,extGate 逐组诚实降级;
     cnt 只在组真实到货后显示,body 惰性求值) ---------- */

  const arr = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);
  const extModule = (
    key: string,
    title: string,
    group: Row | undefined,
    cnt: React.ReactNode,
    body: () => React.ReactNode,
    extraRows?: Array<[string, string]>,
  ) => <ModuleCard {...cardProps(key, title, group ? cnt : undefined, extraRows)}>{extGate(group) ?? body()}</ModuleCard>;

  // 数据点即溯源:图表数据点回调 → setDrill(下钻规格住 dialogs,含诚实口径差说明);
  // 产品线/平台/语言榜单行 v1 → 模块溯源弹窗(维度过滤待接,底部「底层样本」)
  const renderAlerts = () => {
    const g = extData?.alerts_state as Row | undefined;
    const triggeredN = arr(g?.categories).filter((c) => c.triggered).length;
    return extModule("alerts", "声量告警", g, `${triggeredN} 触发`, () => <AlertsBody alerts={g || {}} onSelect={(k, l) => setDrill(drillAlertSpec(k, l, Number(g?.window_hours) || 8))} />);
  };
  const renderSenti = () => {
    const g = extData?.sentiment_summary as Row | undefined;
    const cnt = `${String(g?.granularity) === "week" ? "周" : "日"} × ${arr(g?.trend).length}`;
    return extModule("senti", "情绪趋势", g, cnt, () => <SentiTrendBody senti={g || {}} onPointClick={(kind) => setDrill(drillSentimentSpec(kind))} />);
  };
  // 行点击 = SKU360 跳转桥(onNavigate 缺失跳不了 → 如实不可点,手法同 comp);
  // 溯源(维度过滤待接的诚实说明)仍走卡头 SrcChip → ModuleProvModal
  const renderLineVoice = () => {
    const g = extData?.line_voice as Row | undefined;
    const hitN = arr(g?.items).filter((it) => (Number(it.count) || 0) > 0).length;
    return extModule("line_voice", "产品线声音榜", g, `${hitN} 线`, () => <LineVoiceBody items={arr(g?.items)} onRowClick={onNavigate ? openSku360Search : undefined} />);
  };
  const renderPlat = () => {
    const g = extData?.platform_dist as Row | undefined;
    return extModule("plat", "平台分布", g, `${arr(g?.items).length}`, () => <PlatformBody items={arr(g?.items)} onRowClick={() => setProvKey("plat")} />);
  };
  const renderTopics = () => {
    const g = extData?.topics as Row | undefined;
    return extModule("topics", "热点话题", g, `${arr(g?.items).length}`, () => <TopicsBody topics={g || {}} onSelect={(k, l, c) => setDrill(drillTopicSpec(k, l, c))} />);
  };
  // geo:language_dist 语言分布(诚实:非地理归属,und=待检)
  const renderGeo = () => {
    const g = extData?.language_dist as Row | undefined;
    return extModule("geo", "按语言 / 市场", g, `${arr(g?.items).length}`, () => <LanguageBody dist={g || {}} onRowClick={() => setProvKey("geo")} />);
  };
  const renderComp = () => {
    const g = extData?.competitor_voice as Row | undefined;
    const extraRows = g?.basis ? ([["后端口径", String(g.basis)]] as Array<[string, string]>) : undefined;
    return extModule("comp", "同话题竞品声量", g, `${arr(g?.items).length} 家`, () => <CompetitorBody comp={g || {}} onBrandClick={onNavigate ? openStrategyBrand : undefined} />, extraRows);
  };
  // cat 环图:数据 = 现有 voice-report complaints 类别计数(复用既有 fetch,零新请求);
  // 分段/图例行可点 → 类别原声下钻(词族过滤,口径差在弹窗 sub 如实注明)
  const renderCat = () => {
    const cats = arr(complaints.categories);
    const hitN = cats.filter((c) => (Number(c.count) || 0) > 0).length;
    return (
      <ModuleCard {...cardProps("cat", "类别构成", data && !reportErrored ? `${hitN} 类` : undefined)}>
        {sectionGate(complaints) ?? <CatDonutBody categories={cats} totalMatched={Number(complaints.total_matched) || 0} onSelect={(k, l, c) => setDrill(drillCategorySpec(k, l, c))} />}
      </ModuleCard>
    );
  };

  const renderComplaints = () => {
    const n = Number(complaints.total_matched) || 0;
    return (
      <ModuleCard {...cardProps("complaints", "抱怨聚类", `${n}`, [["命中", `${n} 条 · 话题词+负面线索双命中`]])}>
        {sectionGate(complaints) ?? <ComplaintsBody complaints={complaints} />}
      </ModuleCard>
    );
  };

  const renderWishlist = () => {
    const n = Number(wishlist.total) || 0;
    return (
      <ModuleCard {...cardProps("wishlist", "愿望清单", `${n}`, [["命中", `${n} 条`]])}>
        {sectionGate(wishlist) ?? <WishlistBody wishlist={wishlist} />}
      </ModuleCard>
    );
  };

  const renderGaps = () => {
    const n = Number(gaps.catalog_focal_count) || 0;
    return (
      <ModuleCard
        {...cardProps("gaps", "需求空白", `${n} 焦段`, [
          ["目录焦段", `${n} 个${gaps.catalog_basis ? ` · ${gaps.catalog_basis}` : ""}`],
        ])}
      >
        {sectionGate(gaps) ?? <GapsBody gaps={gaps} />}
      </ModuleCard>
    );
  };

  const renderRecs = () => {
    const n = Array.isArray(suggestions.items) ? suggestions.items.length : 0;
    // 每条建议的「转产品部」真动作:source_table=lexicon_reco,source_id=稳定 key(kind+标题主干)
    const recsPrd = {
      keyOf: (s: Row) => prdKey("lexicon_reco", recoStableKey(String(s.kind || ""), String(s.title || ""))),
      isReferred: (key: string) => prd.referredKeys.has(key),
      isBusy: (key: string) => prd.busyKey === key,
      error: prd.error,
      refer: (s: Row, _key: string) =>
        prd.refer({
          source_table: "lexicon_reco",
          source_id: recoStableKey(String(s.kind || ""), String(s.title || "")),
          title: String(s.title || "").slice(0, 300),
          detail: String(s.detail || "").slice(0, 2000),
          category: String(s.kind || "").slice(0, 80),
        }),
    };
    return (
      <ModuleCard {...cardProps("recs", "给产品部的建议", `${n}`, [["条数", `${n} 条 · 人工复核`]])}>
        {sectionGate(suggestions) ?? <RecsBody suggestions={suggestions} prd={recsPrd} />}
      </ModuleCard>
    );
  };

  // prdStatus · 反哺产品部(PRD 状态流转):数据 = vkpi_market_prd_referrals 转交账本
  // (GET 列表,最近 50 条);行内 ✓采纳/✕拒绝 = PATCH /{id}/status(referred → 终态,
  // 幂等),药丸只在端点真实返回后按服务器回传行变色;表未建 → 诚实 pending 带后端 reason。
  const renderPrdStatus = () => {
    const items = prdBoard.items || [];
    const ready = prdBoard.items != null && prdBoard.listStatus === "ready";
    const nBy = (s: string) => items.filter((it) => String(it.status) === s).length;
    const extraRows: Array<[string, string]> = ready
      ? [["状态分布", `已转交 ${nBy("referred")} · 已采纳 ${nBy("accepted")} · 已拒绝 ${nBy("rejected")}(最近 ${items.length} 条口径)`]]
      : [];
    let body: React.ReactNode;
    if (!apiToken) body = noTokenCard;
    else if (prdBoard.error) body = <ErrorCard title="prd-referrals 读取失败" text={prdBoard.error} />;
    else if (prdBoard.loading && !prdBoard.items) body = <LoadingLine text="转交账本读取中…" />;
    else if (prdBoard.listStatus === "absent")
      body = (
        <PendingCard>
          <b>转交账本未建</b> —— {prdBoard.reason || "数据表未建,接通后自动点亮。"}
        </PendingCard>
      );
    else if (!prdBoard.items) body = <EmptyLine text="暂无数据。" />;
    else body = <PrdStatusBody items={items} busyId={prdBoard.busyId} error={prdBoard.actionError} onSetStatus={prdBoard.setStatus} />;
    return <ModuleCard {...cardProps("prdStatus", "反哺产品部", ready ? `${items.length}` : undefined, extraRows)}>{body}</ModuleCard>;
  };

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
              // 迸发⑤ 扩源行三态(读采集闸真状态,禁装):not_connected=闸键未登记;
              // scaffold=键在但 0(骨架就绪);旧后端缺键 → 如实回落「未接入·盲区」
              const value =
                status === "ready"
                  ? `${Number(s.count) || 0} 条 · 在线`
                  : status === "scaffold"
                    ? "骨架就绪 · 待开闸"
                    : status === "not_connected"
                      ? "未接入 · 盲区"
                      : status === "absent"
                        ? "表未建 · 盲区"
                        : status === "empty"
                          ? "0 条 · 已建未跑"
                          : LISTEN_KEYS.has(key)
                            ? "未接入 · 盲区"
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
            <div className="vkpi-prov-note">逐源如实标注 · 空源 / 未建表 / 闸未登记 = 盲区,骨架就绪 ≠ 在线,不装 live</div>
          </div>
        )}
      </ModuleCard>
    );
  };

  const renderFeed = () => {
    const loaded = feedItems || [];
    let body: React.ReactNode;
    if (!apiToken) body = noTokenCard;
    // 整卡错误只留零数据;已有列表时分页失败降级为列表底部行内提示(不吞真实数据)
    else if (feedError && loaded.length === 0)
      body = <ErrorCard title="端点待接 · voice-feed" text={`GET /api/admin/vkpi/market/voice-feed → ${feedError}`} />;
    else if (feedLoading && !feedItems) body = <LoadingLine text="反馈流读取中…" />;
    else if (!feedItems) body = <EmptyLine text="暂无数据。" />;
    else if (loaded.length === 0) body = <EmptyLine text="反馈流 0 条(评论库暂无非空正文)。" />;
    else
      body = (
        <div>
          {/* demo:卡面只渲染前 6 条,全量走弹窗(FULL.slice(0,6) + .feedmore) */}
          {loaded.slice(0, FEED_FACE).map((item, i) => (
            <FeedRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={(idx) => gotoFeed(idx)} queued={replyQueue.queuedIds.has(item.id)} />
          ))}
          {feedTotal > FEED_FACE && (
            <button
              type="button"
              onClick={() => setFeedListOpen(true)}
              className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
            >
              ≡ 查看全量 {feedTotal} 条 · 点单条连续翻
            </button>
          )}
          {feedError && (
            <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">
              分页加载失败:{feedError}
            </div>
          )}
        </div>
      );
    return <ModuleCard {...cardProps("feed", "反馈流", feedItems ? `${feedTotal}` : undefined)}>{body}</ModuleCard>;
  };

  const renderBuckets = () => {
    const lineBuckets: Row[] = Array.isArray(data?.buckets?.product_lines) ? data!.buckets.product_lines : [];
    return (
      <ModuleCard
        {...cardProps("buckets", "产品线声量分桶", data ? `${lineBuckets.length}` : undefined, [
          ["产品线基准", String(data?.buckets?.product_line_basis || "focal_matrix")],
        ])}
      >
        {sectionGate(data?.buckets || {}) ?? <BucketsBody data={data} />}
      </ModuleCard>
    );
  };

  /* ---------- 模块注册表(palette 全量可选;默认高度贴内容 · 1 格 = 22px + 14px gap;
     V0h-ab:图表模块族进默认布局,complaints/wishlist/gaps/buckets/plat 降为 palette 备选) ---------- */
  const modules: DashboardModuleDefinition[] = [
    // kpiV:卡头 + 单行 4 KPI 卡(sparkline 30px)≈ 200px → 6 格(202px)贴合
    { key: "kpiV", label: "反馈总览带", description: "本月反馈 / 待处理 / 正面占比 / 已转产品部 + 迷你趋势", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    // alerts:6 类评估 → 正常态 1-3 行 + 口径注 ≈ 230px → 7 格
    { key: "alerts", label: "声量告警", description: "类别 × 8h 负面阈值触发 · 已推送徽 · 正常态全绿", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 7, minHeight: 4, maxHeight: 20, render: renderAlerts },
    // cat:132px 环图 + 图例 ≈ 190px → 6 格
    { key: "cat", label: "类别构成", description: "抱怨类别环图 · 中心命中总数 + 占比图例", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 6, minHeight: 5, maxHeight: 16, render: renderCat },
    // senti:tstats 三大数 + 176px 双线 + legend ≈ 300px → 9 格
    { key: "senti", label: "情绪趋势", description: "正/负占比双线 · 空期断线 · 日/周自适应", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 6, maxHeight: 20, render: renderSenti },
    // feed:卡面 6 条 + 「查看全量」按钮 ≈ 295px → 9 格(310px)
    { key: "feed", label: "反馈流", description: "评论原声一行一条 · 身份徽 · 点开连续翻", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 6, maxHeight: 26, render: renderFeed },
    // line_voice:5-7 条形行 + 口径注 ≈ 230px → 7 格
    { key: "line_voice", label: "产品线声音榜", description: "产品线级正面% 彩条(诚实:非逐 SKU)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16, render: renderLineVoice },
    // comp:Viltrox+竞品条形 + 口径注 ≈ 200px → 6 格
    { key: "comp", label: "同话题竞品声量", description: "百家饭同口径品牌份额条 · Viltrox 高亮", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 6, minHeight: 4, maxHeight: 16, render: renderComp },
    { key: "recs", label: "给产品部的建议", description: "规则生成 · 人工复核", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 6, minHeight: 5, maxHeight: 24, render: renderRecs },
    // prdStatus:demo prd 卡形态 · 行=来源徽+标题+状态药丸+绝对时间+行内动作 ≈ 260px → 8 格
    { key: "prdStatus", label: "反哺产品部", description: "转产品部账本 · 已转交 → 采纳/拒绝状态流转", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 8, minHeight: 5, maxHeight: 24, render: renderPrdStatus },
    // topics:chips 一两行 + 口径注 ≈ 130px → 4 格
    { key: "topics", label: "热点话题", description: "词族热度 chips + ▲▼ 环比", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 4, minHeight: 3, maxHeight: 12, render: renderTopics },
    // geo:语言条形 + 待检桶 + 口径注 ≈ 230px → 7 格
    { key: "geo", label: "按语言 / 市场", description: "语言分桶 · 待检=未检出(非地理)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16, render: renderGeo },
    // cover:固定 7 源行(五源 + 迸发⑤ X/论坛扩源两行)+ 口径注 ≈ 280px → 8 格
    { key: "cover", label: "监听覆盖", description: "七源健康 + 盲区/骨架如实标注", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 5, maxHeight: 20, render: renderCover },
    // ↓ palette 备选(不进默认布局,注册表保留全量可选)
    { key: "plat", label: "平台分布", description: "评论按平台条形分布", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16, render: renderPlat },
    { key: "complaints", label: "抱怨聚类", description: "话题词 + 负面线索双命中 · 原声引文", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 6, maxHeight: 24, render: renderComplaints },
    { key: "wishlist", label: "愿望清单", description: "焦段 / 变焦 / 卡口 chips + 原声", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 6, maxHeight: 24, render: renderWishlist },
    { key: "gaps", label: "需求空白", description: "有声量但目录零 SKU 的焦段", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 8, minHeight: 5, maxHeight: 24, render: renderGaps },
    { key: "buckets", label: "产品线声量分桶", description: "产品线词表口径", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 4, minHeight: 3, maxHeight: 16, render: renderBuckets },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="市场之声" />;
  }

  const detailItem = detailIndex != null && feedItems ? feedItems[detailIndex] : null;

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 实时辉光点 + 月份控件 + 编辑布局;原三枚方法论徽按「卡面去术语」收进 kpiV SrcChip 口径行 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">市场之声 · 用户反馈雷达</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            实时
          </span>
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

      {!apiToken && <div className="mb-3">{noTokenCard}</div>}
      {error && <div className="mb-3"><ErrorCard title="voice-report 读取失败" text={error} /></div>}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 反馈流全量弹窗:卡面 6 条之外的全量在这里滚(分页加载逻辑随行为进弹窗) */}
      {feedListOpen && feedItems && (
        <FeedListModal
          total={feedTotal}
          loadedCount={feedItems.length}
          hasMore={feedItems.length < feedTotal}
          loading={feedLoading}
          error={feedError}
          onLoadMore={() => loadFeed(feedItems.length)}
          onClose={() => setFeedListOpen(false)}
        >
          {feedItems.map((item, i) => (
            <FeedRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={(idx) => gotoFeed(idx)} queued={replyQueue.queuedIds.has(item.id)} />
          ))}
        </FeedListModal>
      )}

      {/* 反馈单条详情:‹ #n/N › + ↑↓ 连续翻;闭环动作/身份跳走 detailAdapter(下钻弹窗共用) */}
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
            replyQueue.clearError();
            prd.clearError();
          }}
          {...detailAdapter(detailItem)}
        />
      )}

      {/* 数据点下钻:环图分段/告警行/话题 chip/趋势点 → 底层原声弹窗(category/sentiment 过滤) */}
      {drill && (
        <DrillFeedModal
          spec={drill}
          fetchPage={(offset) => getVoiceFeed(apiToken, { offset, limit: FEED_PAGE, ...drill.filters })}
          renderRow={(item, i, onOpen) => <FeedRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={onOpen} queued={replyQueue.queuedIds.has(item.id)} />}
          detailProps={detailAdapter}
          onClose={() => setDrill(null)}
        />
      )}

      {/* 模块溯源说明弹窗(SrcChip / 榜单行点击);产品线/平台/语言 v1:底部「底层样本」+ 维度过滤待接诚实说明 */}
      {provKey && (
        <ModuleProvModal
          title={PROV_TITLES[provKey] || provKey}
          caliber={mergedRowsRef.current[provKey] || srcOf(provKey).rows}
          sampleNote={SAMPLE_PENDING[provKey]}
          onOpenSamples={SAMPLE_PENDING[provKey] ? () => { setProvKey(null); setFeedListOpen(true); } : undefined}
          onClose={() => setProvKey(null)}
        />
      )}
    </div>
  );
}
