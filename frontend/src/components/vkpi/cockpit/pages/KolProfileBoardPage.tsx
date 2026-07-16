import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { decodeDashboardLayoutPreference, encodeDashboardLayoutPreference } from "../dashboardPreferenceStore";
import {
  getKolPoolDetailBundle,
  getKolCooperation,
  getKolPoolLlmDeepAnalysis,
  getKolPoolIntelligenceCard,
  getKolPoolCompetitors,
  resolveKolPool,
  type VkpiKolPoolDetailBundleResponse,
} from "../../../../services/vkpi/kolPool-api";
import { getKolCompetingActivity, getKolProductionLeadtime } from "../../../../services/vkpi/kolProfile-api";
import { getBoardSeries, type VkpiBoardSeriesResponse } from "../../../../services/vkpi/boardSeries-api";
import { ErrorCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { IdentityBody, DeepBody, CommentsBody, MODULE_SOURCES } from "./KolProfileBoardPage.modules";
import {
  AuthenticityBody,
  CompetingActivityBody,
  CompetitorRelationsBody,
  GearBody,
  LeadtimeBody,
  ProfileKpiBand,
  asArray,
  asRow,
  num,
  pct,
  str,
  type Row,
} from "./KolProfileBoardPage.charts";
import { AudienceEmbed, CoopEmbed, SignatureEmbed, VideoAnalysisEmbed } from "./KolProfileBoardPage.embeds";
import { KolProfileHistoryModule } from "./KolProfileHistoryModule";

// KOL 档案 → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage 五件套 1:1 同构)。
//   旧 KolProfilePage.tsx 保留不删(回滚垫);功能块零丢失映射:
//   头部身份卡+三秒读懂+动作条 → identity 模块;①身份 KV → identity;②招牌面板 →
//   signature(SignaturePanel 内嵌,端点已上线);③受众画像 → audience(AudienceGeoPanel
//   内嵌 + 档案估算附注);④合作 → coop(CooperationPanel 内嵌,读+记动作超集)+
//   三秒读懂「值多少钱」行;④周期 → leadtime 真身;⑤竞业 → credit(competing-activity
//   兜底 competitors,兜底口径如实标注);⑤假粉 → credit 真实度半区(旧页读的
//   real_followers_pct 后端不产出恒空 → 换 real_er/suspect_inflation 真列,更真);
//   ⑥深析摘要 → deep(未深析=「未分析」);⑦评论声音 → comments;视频深析 →
//   videos(KOLVideoAnalysisPanel 内嵌,detail-bundle 直喂零重复请求);页脚口径 →
//   各模块 SrcChip rows。id 三通道(prop / sessionStorage+事件 / URL 参数)+ 手输引导态原样保留。
//   数据源(全真,零编造;八路独立加载,单路失败只暗掉自己的卡):
//     GET /kol-pool/{id}/detail-bundle       —— item + video_analysis + 深析汇总
//     GET /kol-pool/{id}/cooperation         —— 当前合作状态(三秒读懂行;时间线在 coop 内嵌)
//     GET /kol-pool/{id}/llm-deep-analysis   —— 深析摘要
//     GET /kol-pool/{id}/intelligence-card   —— 评论声音
//     GET /kol-pool/{id}/competing-activity  —— 竞业(失败兜底 /competitors)
//     GET /kol-pool/{id}/production-leadtime —— 制作周期
//     signature / audience-geo / coop 时间线 —— 内嵌组件自取(embeds 族)
// 红线:纯展示,fit 分只读绝不写回;颜色全 token 零写死色;零 opacity 修饰类;
//   卡面零技术术语(表名/端点只进 SrcChip);诚实空态;时间绝对戳;布局只走本机
//   storageKey,不传 apiToken 给 EditableDashboardBoard(其账户级持久化写死
//   dashboard_layout_v1 键,传了会覆写 Dashboard 布局 —— 金样板同注释)。

const STORAGE_KEY = "vkpi-kol-profile-layout-v1";
const HISTORY_LAYOUT_MIGRATION_KEY = "vkpi:kol-profile-history-layout-v1";
const PROFILE_ID_KEY = "vkpi:kol-profile-id";
const OPEN_PROFILE_EVENT = "vkpi:open-kol-profile";

function positiveIntegerId(value: unknown): number {
  const text = String(value ?? "").trim();
  if (!/^\d+$/.test(text)) return 0;
  const parsed = Number(text);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 0;
}

function profileReadError(error: string, attemptedId: number): string {
  const normalized = error.toLowerCase();
  if (normalized.includes("not found") || normalized.includes("404")) {
    return `KOL Pool 中不存在档案 #${attemptedId}。请检查 ID / 用户名，或返回 KOL Pool 重新选择。`;
  }
  return `档案 #${attemptedId} 暂时无法读取。请稍后重试，或返回 KOL Pool 重新选择。`;
}

function migrateProfileHistoryLayoutOnce() {
  if (typeof window === "undefined") return;
  try {
    if (window.localStorage.getItem(HISTORY_LAYOUT_MIGRATION_KEY) === "done") return;
    const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
    const items = decodeDashboardLayoutPreference<Record<string, unknown>>(raw);
    if (items && items.length > 0 && !items.some((item) => String(item.moduleKey || "") === "history")) {
      const bottom = items.reduce((max, item) => {
        const y = Number(item.y ?? item.row ?? 0);
        const height = Number(item.height ?? item.h ?? 0);
        return Math.max(max, (Number.isFinite(y) ? y : 0) + (Number.isFinite(height) ? height : 0));
      }, 0);
      const next = [
        ...items,
        { instanceId: "profile-history-migration-v1", moduleKey: "history", span: 12, height: 15, x: 0, y: bottom },
      ];
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(encodeDashboardLayoutPreference(next)));
    }
    // One-shot only: after this additive migration the user may remove the module again.
    window.localStorage.setItem(HISTORY_LAYOUT_MIGRATION_KEY, "done");
  } catch {
    // Invalid/disabled local storage falls back to the default layout; never block the page.
  }
}

// 默认布局(12 列 · 七行):kpiP(12) → identity(8)+credit(4) → signature(8)+deep(4)
// → history(12) → videos(8)+comments(4) → coop(4)+leadtime(4)+audience(4);gear=palette 备选
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiP", span: 12 },
  { moduleKey: "identity", span: 8 },
  { moduleKey: "credit", span: 4 },
  { moduleKey: "signature", span: 8 },
  { moduleKey: "deep", span: 4 },
  { moduleKey: "history", span: 12 },
  { moduleKey: "videos", span: 8 },
  { moduleKey: "comments", span: 4 },
  { moduleKey: "coop", span: 4 },
  { moduleKey: "leadtime", span: 4 },
  { moduleKey: "audience", span: 4 },
];

const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

/* ---------- 通用远端状态(旧页 useRemote 原样平移;八路互不阻塞) ---------- */
type RemoteState<T> = { status: "idle" | "loading" | "ready" | "error"; data: T | null; error: string };

function useRemote<T>(enabled: boolean, loader: () => Promise<T>, deps: React.DependencyList): RemoteState<T> {
  const [state, setState] = React.useState<RemoteState<T>>({ status: "idle", data: null, error: "" });
  React.useEffect(() => {
    if (!enabled) {
      setState({ status: "idle", data: null, error: "" });
      return;
    }
    let alive = true;
    setState({ status: "loading", data: null, error: "" });
    loader()
      .then((data) => {
        if (alive) setState({ status: "ready", data, error: "" });
      })
      .catch((err: unknown) => {
        if (alive) setState({ status: "error", data: null, error: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

// 从深析摘要/研判里捞一句结论(三秒读懂「什么最灵」;捞不到=如实占位)
const MEANING_KEYS = ["account_judgment", "summary", "headline", "conclusion", "signature", "insight", "reason", "note", "description", "text", "label", "title"];
function firstMeaningfulString(row: Row | null, maxLen = 90): string {
  if (!row) return "";
  for (const key of MEANING_KEYS) {
    const s = str(row[key]).trim();
    if (s.length >= 4) return s.length > maxLen ? `${s.slice(0, maxLen)}…` : s;
  }
  return "";
}

export function KolProfileBoardPage({
  apiToken = "",
  kolId: kolIdProp = null,
  onNavigate,
  embeddedModuleKey,
}: {
  apiToken?: string;
  kolId?: number | string | null;
  onNavigate?: (navKey: string) => void;
  embeddedModuleKey?: string;
}) {
  React.useState(() => {
    migrateProfileHistoryLayoutOnce();
    return true;
  });
  const readPendingId = React.useCallback((): number => {
    try {
      const url = new URLSearchParams(window.location.search).get("kolProfileId");
      const fromUrl = Number(url);
      if (url && Number.isFinite(fromUrl) && fromUrl > 0) return fromUrl;
      const stored = Number(window.sessionStorage.getItem(PROFILE_ID_KEY));
      return Number.isFinite(stored) && stored > 0 ? stored : 0;
    } catch {
      return 0;
    }
  }, []);

  const [kolId, setKolId] = React.useState<number>(() => {
    const fromProp = Number(kolIdProp);
    if (Number.isFinite(fromProp) && fromProp > 0) return fromProp;
    return typeof window !== "undefined" ? readPendingId() : 0;
  });
  const [draftId, setDraftId] = React.useState("");
  const [selectionError, setSelectionError] = React.useState("");
  const [resolvingInput, setResolvingInput] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);
  const [editing, setEditing] = React.useState(false);

  // 打开档案的两条入口:prop 变化 / vkpi:open-kol-profile 事件(sessionStorage 传 id)
  React.useEffect(() => {
    const fromProp = Number(kolIdProp);
    if (Number.isFinite(fromProp) && fromProp > 0) setKolId(fromProp);
  }, [kolIdProp]);
  React.useEffect(() => {
    const consume = () => {
      const next = readPendingId();
      if (next > 0) setKolId(next);
    };
    window.addEventListener(OPEN_PROFILE_EVENT, consume);
    return () => window.removeEventListener(OPEN_PROFILE_EVENT, consume);
  }, [readPendingId]);

  const clearPendingId = React.useCallback((targetId?: number) => {
    try {
      const stored = positiveIntegerId(window.sessionStorage.getItem(PROFILE_ID_KEY));
      if (!targetId || stored === targetId) window.sessionStorage.removeItem(PROFILE_ID_KEY);
    } catch {
      /* sessionStorage 不可用忽略 */
    }
    try {
      const current = new URL(window.location.href);
      const queryId = positiveIntegerId(current.searchParams.get("kolProfileId"));
      if (!targetId || queryId === targetId) {
        current.searchParams.delete("kolProfileId");
        window.history.replaceState(window.history.state, "", current.toString());
      }
    } catch {
      /* URL / history 不可用忽略 */
    }
  }, []);

  const resetSelection = React.useCallback(() => {
    clearPendingId();
    setKolId(0);
    setDraftId("");
    setSelectionError("");
    setResolvingInput(false);
  }, [clearPendingId]);

  const applyDraftId = async () => {
    const input = draftId.trim();
    if (!input) {
      setSelectionError("请输入 KOL 池 ID、用户名或 @handle。");
      return;
    }

    setSelectionError("");
    const directId = positiveIntegerId(input);
    if (directId > 0) {
      setKolId(directId);
      return;
    }

    if (!apiToken) {
      setSelectionError("登录后才能查询 KOL 档案。");
      return;
    }

    setResolvingInput(true);
    try {
      const resolved = await resolveKolPool(apiToken, input.replace(/^@/, ""));
      const resolvedId = positiveIntegerId(resolved?.kol_pool_id);
      if (!resolved?.matched || resolvedId <= 0) {
        setSelectionError(`KOL Pool 中未找到“${input}”。请核对用户名，或去 KOL Pool 选择已有档案。`);
        return;
      }
      setKolId(resolvedId);
    } catch {
      setSelectionError("用户名查询暂时不可用。请稍后重试，或直接输入 KOL 池数字 ID。");
    } finally {
      setResolvingInput(false);
    }
  };

  const enabled = Boolean(apiToken) && kolId > 0;
  const bundleDeps: React.DependencyList = [apiToken, kolId, reloadTick, enabled];

  /* ---------- 六路 page 层加载(signature/audience/coop 时间线在 embeds 自取) ---------- */
  const bundleRequestRef = React.useRef<{
    token: string;
    kolId: number;
    reloadTick: number;
    promise: Promise<VkpiKolPoolDetailBundleResponse>;
  } | null>(null);
  const loadBundle = React.useCallback(() => {
    const cached = bundleRequestRef.current;
    if (cached && cached.token === apiToken && cached.kolId === kolId && cached.reloadTick === reloadTick) return cached.promise;
    const promise = getKolPoolDetailBundle(apiToken, kolId);
    bundleRequestRef.current = { token: apiToken, kolId, reloadTick, promise };
    const clear = () => {
      if (bundleRequestRef.current?.promise === promise) bundleRequestRef.current = null;
    };
    void promise.then(clear, clear);
    return promise;
  }, [apiToken, kolId, reloadTick]);
  const bundle = useRemote<VkpiKolPoolDetailBundleResponse>(enabled, loadBundle, bundleDeps);
  const bundleItemId = positiveIntegerId((bundle.data?.item as unknown as Row | undefined)?.id);
  const bundleResponseId = positiveIntegerId(bundle.data?.kol_pool_id);
  const profileReady = enabled && bundle.status === "ready" && Boolean(bundle.data?.item) && (bundleResponseId === kolId || bundleItemId === kolId);
  const detailDeps: React.DependencyList = [apiToken, kolId, reloadTick, profileReady];

  // 档案主体是唯一身份闸门。只有它确认 ID 存在后，才允许其余模块扇出读取；
  // 失败时清掉持久化入口，避免一个坏 ID 在跨页返回后持续污染整页。
  React.useEffect(() => {
    if (!enabled) return;
    if (profileReady) {
      try {
        window.sessionStorage.setItem(PROFILE_ID_KEY, String(kolId));
      } catch {
        /* sessionStorage 不可用忽略 */
      }
      setSelectionError("");
      return;
    }
    if (bundle.status !== "error" && bundle.status !== "ready") return;
    const message = bundle.status === "error"
      ? profileReadError(bundle.error, kolId)
      : `档案 #${kolId} 返回了不完整的主体数据。请返回选择，或去 KOL Pool 核验该记录。`;
    clearPendingId(kolId);
    setDraftId(String(kolId));
    setSelectionError(message);
    setKolId(0);
  }, [bundle.error, bundle.status, clearPendingId, enabled, kolId, profileReady]);

  const cooperation = useRemote<Row>(profileReady, () => getKolCooperation(apiToken, kolId) as Promise<Row>, detailDeps);
  const deep = useRemote<Row>(profileReady, () => getKolPoolLlmDeepAnalysis(apiToken, kolId, 20) as unknown as Promise<Row>, detailDeps);
  const card = useRemote<Row>(profileReady, () => getKolPoolIntelligenceCard(apiToken, kolId) as unknown as Promise<Row>, detailDeps);
  const credit = useRemote<{ source: string; data: Row }>(
    profileReady,
    async () => {
      try {
        return { source: "competing-activity", data: (await getKolCompetingActivity(apiToken, kolId)) as Row };
      } catch {
        // 主源不可用 → 兜底已落库品牌关系(卡内如实标注兜底口径)
        return { source: "competitors", data: (await getKolPoolCompetitors(apiToken, kolId)) as Row };
      }
    },
    detailDeps,
  );
  const leadtime = useRemote<Row>(profileReady, () => getKolProductionLeadtime(apiToken, kolId) as Promise<Row>, detailDeps);
  // KPI 卡趋势线(挂账迸发①):board-series?board=kol-profile&kol_id= 该 KOL evidence 按日;
  // 失败 = KPI 卡照旧 spempty 诚实虚线,不拖累档案主体
  const kpiSeries = useRemote<VkpiBoardSeriesResponse>(
    profileReady,
    () => getBoardSeries(apiToken, { board: "kol-profile", kolId }),
    detailDeps,
  );

  const item: Row | null = bundle.data?.item ? (bundle.data.item as unknown as Row) : null;
  const displayName = str(item?.display_name) || str(item?.handle) || (kolId > 0 ? `#${kolId}` : "");
  const analysisSummary = asRow(asRow((bundle.data as unknown as Row)?.video_analysis)?.summary);
  const coopLabel = str(asRow(cooperation.data)?.status_label);
  const signatureLine =
    firstMeaningfulString(asRow(asRow(deep.data?.primary_result)?.llm_dimensions_11)) ||
    firstMeaningfulString(asRow(deep.data?.summary)) ||
    "未深析";

  // 动作:打开 KOL 池抽屉(既有事件桥;抽屉自带 收藏/联系/补全 真动作)
  const openDrawer = React.useCallback(() => {
    if (kolId <= 0) return;
    try {
      window.localStorage.setItem("vkpi:pending-kolpool-open-id", String(kolId));
    } catch {
      /* localStorage 不可用忽略 */
    }
    window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-item"));
    onNavigate?.("kol-pool");
  }, [kolId, onNavigate]);

  const goToKolPool = React.useCallback(() => {
    if (onNavigate) {
      onNavigate("kol-pool");
      return;
    }
    window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-search"));
  }, [onNavigate]);

  /* ---------- gate(金样板同构:未登录 → PendingCard;加载/失败 → 诚实卡) ---------- */
  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载档案。
    </PendingCard>
  );
  const remoteGate = <T,>(state: RemoteState<T>, name: string): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (state.status === "loading" || state.status === "idle") return <LoadingLine text="读取中…" />;
    if (state.status === "error") return <ErrorCard title={`${name}读取失败`} text={state.error} />;
    if (state.data === null) return <LoadingLine text="读取中…" />;
    return null;
  };

  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows };
  };
  // 内嵌件重挂载键:换 KOL / 手动刷新 → 自取型内嵌组件整卡重取
  const embedKey = `${kolId}-${reloadTick}`;

  /* ---------- 模块 body ---------- */

  const renderKpiBand = () => {
    const extraRows: Array<[string, string]> = item
      ? [
          ["采集时点", `${str(item.updated_at) || str(item.last_seen_at) || "—"}(UTC · 点时快照)`],
          [
            "趋势线",
            "board-series?board=kol-profile&kol_id= 该 KOL 内容收录/日真序列(30 天窗,已深析视频卡;关联指标,卡面大数是存量 → 不挂环比药丸);粉丝/均播放/互动率为点时快照无历史 → 虚线如实",
          ],
          ...(kpiSeries.status === "error"
            ? ([["趋势线源", `读取失败:${kpiSeries.error}(卡面虚线如实,不编时序)`]] as Array<[string, string]>)
            : []),
        ]
      : [];
    return (
      <ModuleCard {...cardProps("kpiP", "档案指标带", item ? displayName : undefined, extraRows)}>
        {remoteGate(bundle, "档案主体") ?? (
          <ProfileKpiBand
            followers={num(item?.followers)}
            avgViews={num(item?.avg_views)}
            engagementPct={pct(num(item?.engagement_rate))}
            deepReady={num(analysisSummary?.ready_count)}
            evidenceCount={num(analysisSummary?.evidence_count)}
            pending={!item}
            pendingNote="档案主体未就绪"
            boardSeries={kpiSeries.data}
          />
        )}
      </ModuleCard>
    );
  };

  const renderIdentity = () => (
    <ModuleCard {...cardProps("identity", "身份档案", item ? str(item.platform) || undefined : undefined)}>
      {remoteGate(bundle, "档案主体") ?? (
        <IdentityBody item={item as Row} signatureLine={signatureLine} coopLabel={coopLabel} onOpenDrawer={openDrawer} />
      )}
    </ModuleCard>
  );

  const renderCredit = () => {
    const source = credit.data?.source || "";
    const data = credit.data?.data || null;
    const extraRows: Array<[string, string]> =
      source === "competitors" ? [["本次口径", "主源不可用 → 已落库品牌关系兜底(非近窗计数)"]] : [];
    const cnt =
      source === "competing-activity" && data
        ? `${asArray(data.brands).length} 品牌`
        : source === "competitors" && data
          ? `${asArray(data.relations).length} 关系`
          : undefined;
    return (
      <ModuleCard {...cardProps("credit", "信用与真实度", cnt, extraRows)}>
        {remoteGate(credit, "竞业") ?? (
          <div>
            {source === "competitors" ? (
              <CompetitorRelationsBody relations={asArray(data?.relations).map((r) => asRow(r)).filter((r): r is Row => Boolean(r))} />
            ) : (
              <CompetingActivityBody data={data as Row} />
            )}
            {item ? <AuthenticityBody item={item} /> : null}
          </div>
        )}
      </ModuleCard>
    );
  };

  const renderSignature = () => (
    <ModuleCard {...cardProps("signature", "招牌内容")}>
      {!apiToken ? noTokenCard : <SignatureEmbed key={embedKey} apiToken={apiToken} kolId={kolId} />}
    </ModuleCard>
  );

  const renderDeep = () => {
    const n = num(deep.data?.count);
    const provider = str(asRow(deep.data?.primary_result)?.provider);
    const extraRows: Array<[string, string]> = provider ? [["本条来源", `provider=${provider}(行级字段原样)`]] : [];
    return (
      <ModuleCard {...cardProps("deep", "深析摘要", n !== null && n > 0 ? `${n}` : undefined, extraRows)}>
        {remoteGate(deep, "深析") ?? <DeepBody deep={deep.data as Row} />}
      </ModuleCard>
    );
  };

  const renderHistory = () => (
    <ModuleCard {...cardProps("history", "统一历史")}>
      <KolProfileHistoryModule
        key={embedKey}
        apiToken={apiToken}
        kolId={kolId}
        reloadTick={reloadTick}
        deep={deep}
        cooperation={cooperation}
      />
    </ModuleCard>
  );

  const renderVideos = () => {
    const ready = num(analysisSummary?.ready_count);
    const total = num(analysisSummary?.evidence_count);
    return (
      <ModuleCard {...cardProps("videos", "视频深析", ready !== null && total !== null ? `${ready}/${total}` : undefined)}>
        {remoteGate(bundle, "档案主体") ?? <VideoAnalysisEmbed key={embedKey} apiToken={apiToken} item={item} bundle={bundle.data as unknown as Row} />}
      </ModuleCard>
    );
  };

  const renderComments = () => {
    const intel = asRow(card.data?.comment_intelligence);
    const cached = num(intel?.cached_comment_count);
    return (
      <ModuleCard {...cardProps("comments", "评论声音", cached !== null && cached > 0 ? `${cached}` : undefined)}>
        {remoteGate(card, "评论情报") ?? <CommentsBody intel={intel} />}
      </ModuleCard>
    );
  };

  const renderCoop = () => (
    <ModuleCard {...cardProps("coop", "合作动作", coopLabel || undefined)}>
      {!apiToken ? noTokenCard : <CoopEmbed key={embedKey} apiToken={apiToken} kolId={kolId} />}
    </ModuleCard>
  );

  const renderLeadtime = () => {
    const n = num(leadtime.data?.sample_count);
    return (
      <ModuleCard {...cardProps("leadtime", "制作周期", n !== null && n > 0 ? `${n} 样本` : undefined)}>
        {remoteGate(leadtime, "制作周期") ?? <LeadtimeBody data={leadtime.data as Row} />}
      </ModuleCard>
    );
  };

  const renderAudience = () => (
    <ModuleCard {...cardProps("audience", "受众画像")}>
      {!apiToken ? noTokenCard : <AudienceEmbed key={embedKey} apiToken={apiToken} kolId={kolId} item={item} />}
    </ModuleCard>
  );

  const renderGear = () => (
    <ModuleCard {...cardProps("gear", "创作装备", item && str(item.device_primary) ? str(item.device_primary) : undefined)}>
      {remoteGate(bundle, "档案主体") ?? <GearBody item={item as Row} />}
    </ModuleCard>
  );

  /* ---------- 模块注册表(palette 全量可选;gear 为 palette 备选不进默认布局) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiP", label: "档案指标带", description: "粉丝 / 均播放 / 互动率 / 已深析视频(点时快照)", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "identity", label: "身份档案", description: "名号 · 三秒读懂 · 档案字段 · 收藏/联系动作", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 15, minHeight: 8, maxHeight: 28, render: renderIdentity },
    { key: "credit", label: "信用与真实度", description: "竞品露出 · 实测互动率 · 虚高嫌疑", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 15, minHeight: 6, maxHeight: 24, render: renderCredit },
    { key: "signature", label: "招牌内容", description: "擅长拍法 · 最爆代表作 · 最引反馈(内嵌)", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 14, minHeight: 6, maxHeight: 30, render: renderSignature },
    { key: "deep", label: "深析摘要", description: "研判 · 强项/短板 · 建议 · 置信(未深析=未分析)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 14, minHeight: 6, maxHeight: 26, render: renderDeep },
    { key: "history", label: "统一历史", description: "搜索/归档恢复 · 深析 · 合作动作 · 来源与操作者 · 分页", category: "业务板块", defaultSpan: 12, minSpan: 6, defaultHeight: 15, minHeight: 7, maxHeight: 34, render: renderHistory },
    { key: "videos", label: "视频深析", description: "逐视频深析卡 · 关键帧核验(内嵌)", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 6, maxHeight: 30, render: renderVideos },
    { key: "comments", label: "评论声音", description: "样本 · 提问/机会/吐槽 · 情绪与品牌态度", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 13, minHeight: 5, maxHeight: 20, render: renderComments },
    { key: "coop", label: "合作动作", description: "当前状态 · 续约/加大/评估/退出 · 时间线(内嵌)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 5, maxHeight: 20, render: renderCoop },
    { key: "leadtime", label: "制作周期", description: "签收 → 发布中位天数 + 完整样本行", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 5, maxHeight: 18, render: renderLeadtime },
    { key: "audience", label: "受众画像", description: "多信号地理融合 · 语言分布 · 估算附注(内嵌)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 5, maxHeight: 22, render: renderAudience },
    // ↓ palette 备选(不进默认布局)
    { key: "gear", label: "创作装备", description: "机身 · 镜头品牌 · 换镜窗口(档案抽取)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 16, render: renderGear },
  ];

  const selectionPage = (checking: boolean) => (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">KOL 档案</span>
        <span className={PH_BADGE}>可编辑看板</span>
      </div>
      {!apiToken ? (
        noTokenCard
      ) : (
        <div className="rounded-[18px] border border-dashed border-line-strong p-8 text-center">
          {checking ? (
            <>
              <div className="text-[13px] text-ink-2">正在核验 KOL Pool 档案 #{kolId}…</div>
              <div className="mx-auto mt-3 max-w-xs"><LoadingLine text="只读取档案主体，核验成功后再加载其他模块…" /></div>
              <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
                <button
                  type="button"
                  onClick={resetSelection}
                  className="rounded-lg border border-line bg-card px-3 py-1.5 text-[12px] text-muted transition-colors hover:border-line-strong hover:text-ink"
                >
                  返回选择
                </button>
                <button
                  type="button"
                  onClick={goToKolPool}
                  className="rounded-lg border border-line bg-card px-3 py-1.5 text-[12px] text-muted transition-colors hover:border-line-strong hover:text-ink"
                >
                  去 KOL Pool
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="text-[13px] text-ink-2">未选择 KOL —— 从 KOL Pool / MY KOL 点入，或输入数字 ID、用户名 / @handle：</div>
              <div className="mx-auto mt-3 flex max-w-md flex-wrap items-center justify-center gap-2">
                <input
                  value={draftId}
                  onChange={(ev) => {
                    setDraftId(ev.target.value);
                    if (selectionError) setSelectionError("");
                  }}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter") void applyDraftId();
                  }}
                  placeholder="KOL 池 ID / 用户名 / @handle"
                  aria-label="KOL 池 ID 或用户名"
                  title="支持 kol_pool_id、用户名、@handle 或精确显示名"
                  autoComplete="off"
                  className="min-w-[220px] flex-1 rounded-lg border border-line bg-card px-3 py-1.5 text-[12px] text-ink outline-none placeholder:text-muted focus:border-accent"
                />
                <button
                  type="button"
                  onClick={() => void applyDraftId()}
                  disabled={resolvingInput || !draftId.trim()}
                  className="rounded-lg border border-accent bg-accent-soft px-3 py-1.5 text-[12px] text-accent transition-colors hover:border-accent-hover disabled:cursor-default disabled:border-line disabled:bg-card disabled:text-muted"
                >
                  {resolvingInput ? "查询中…" : "打开档案"}
                </button>
                <button
                  type="button"
                  onClick={goToKolPool}
                  className="rounded-lg border border-line bg-card px-3 py-1.5 text-[12px] text-muted transition-colors hover:border-line-strong hover:text-ink"
                >
                  去 KOL Pool
                </button>
              </div>
              {selectionError ? (
                <div role="alert" className="mx-auto mt-3 max-w-md rounded-lg border border-bad bg-card px-3 py-2 text-left text-[11px] leading-relaxed text-bad">
                  {selectionError}
                </div>
              ) : null}
            </>
          )}
        </div>
      )}
    </div>
  );

  if (embeddedModuleKey) {
    if (!profileReady) {
      return (
        <section className="ds-mod flex h-full min-h-[120px] flex-col items-center justify-center gap-2 p-4 text-center text-[11px] text-muted">
          <b className="text-ink-2">{enabled ? "正在核验最近的 KOL 档案…" : "待选择 KOL 档案"}</b>
          <span>{selectionError || "先在 KOL 档案或 KOL Pool 打开一位真实达人，本模块才会读取数据。"}</span>
          <button type="button" className="text-accent hover:text-accent-hover" onClick={() => onNavigate?.("kolProfile")}>去 KOL 档案选择 →</button>
        </section>
      );
    }
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="KOL 档案" />;
  }

  /* ---------- 未选择 KOL:引导态(不杜撰任何档案) ---------- */
  if (!enabled) {
    return selectionPage(false);
  }

  if (!profileReady) {
    return selectionPage(true);
  }

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 名号/平台/Fit 药丸徽 + 实时辉光点 + 刷新 + 编辑布局 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">KOL 档案</span>
          {displayName ? <span className={PH_BADGE}>{displayName}</span> : null}
          {item && str(item.platform) ? <span className={PH_BADGE}>{str(item.platform)}</span> : null}
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            实时
          </span>
          <button
            type="button"
            onClick={resetSelection}
            className="rounded-xl border border-line bg-card px-3 py-2 text-[12px] text-muted transition-colors hover:text-ink"
          >
            重新选择
          </button>
          <button
            type="button"
            onClick={() => setReloadTick((tick) => tick + 1)}
            className="flex items-center gap-1.5 rounded-xl border border-line bg-card px-3 py-2 text-[12px] text-muted transition-colors hover:text-ink"
          >
            <RefreshCw size={13} />
            <span>刷新</span>
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

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />
    </div>
  );
}
