import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import {
  getSku360CampaignCard,
  getSku360Persona,
  getSku360Profile,
  listSku360Options,
  type Sku360ListItem,
} from "../../../../services/vkpi/sku360-api";
import { ErrorCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { AnglesBody, CatalogBody, ContentsBody, FitBody, KnowledgeBody, MODULE_SOURCES } from "./Sku360BoardPage.modules";
import { BhBody, CandidatesBody, CreatorsBody, SkuKpiBand, VoiceBody, asArray, asRow, num, pct, str, type Row } from "./Sku360BoardPage.charts";
import { ContentDetailModal, ContentListModal } from "./Sku360BoardPage.dialogs";

// 件③ · SKU 360° → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage
//   + KolProfileBoardPage 档案型范例 1:1 同构)。
//   旧 Sku360Page.tsx 保留不删(回滚垫);功能块零丢失映射:
//   SKU 选择器(300ms 防抖搜 /sku/list)→ pagehead 控件行原样;基础信息 → catalog
//   (超集:补 specs/fit_tags/目录更新,旧页读了不显的档案字段全数上卡);内容表现
//   聚合五数 → kpiS 四卡 + creators(TOP 创作者独立成卡,可跳 KOL 档案);内容清单 →
//   contents(默认 6 条 + 全量弹窗 + 详情 ‹#n/N› ↑↓ 连续翻,旧页平铺表格升级);
//   内容契合判断 → fit(AI 判断缓存只读,置信原样);相关评论抽样 → voice(声量);
//   B&H reviews → bh(表存在才读,0 行如实);新增:knowledge 知识库画像 + angles
//   推广方向(vkpi_product_persona,LLM 离线批产 → 卡面「AI 生成」徽 + 模型/生成时间
//   标注,该表无置信列如实不摆);candidates 推广候选(palette 备选,启发式圈选 +
//   市场信号,人工复核口径如实标)。
//   数据源(全真,零编造;三路独立加载,单路失败只暗掉自己的卡):
//     GET /sku/list                                   —— 选择器
//     GET /sku/{sku}/profile                          —— 档案聚合(catalog/kpiS/contents/creators/fit/voice/bh)
//     GET /sku/{sku}/persona                          —— 知识库画像(knowledge/angles)
//     GET /industry-data/product-campaign-card?sku=   —— 推广候选(candidates)
//   sku 三通道(prop / URL ?sku360Sku= / sessionStorage+vkpi:open-sku360 事件)——
//   与 KOL 档案页同手法,给市场之声「产品线声音榜」等跨板块跳转预留接收位。
// 红线:纯展示零写库;fit/评分只读;颜色全 token 零写死色;零 opacity 修饰类;
//   卡面零技术术语(表名/端点/模型名只进 SrcChip);诚实空态;时间绝对戳;布局只走
//   本机 storageKey,不传 apiToken 给 EditableDashboardBoard(其账户级持久化写死
//   dashboard_layout_v1 键,传了会覆写 Dashboard 布局 —— 金样板同注释)。

const STORAGE_KEY = "vkpi-sku360-layout-v1";
const SKU_KEY = "vkpi:sku360-sku";
const OPEN_SKU_EVENT = "vkpi:open-sku360";

// 默认布局(12 列 · 五行):kpiS(12) → catalog(8)+knowledge(4) → contents(8)+angles(4)
// → voice(8)+creators(4) → fit(4)+bh(4);candidates=palette 备选
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiS", span: 12 },
  { moduleKey: "catalog", span: 8 },
  { moduleKey: "knowledge", span: 4 },
  { moduleKey: "contents", span: 8 },
  { moduleKey: "angles", span: 4 },
  { moduleKey: "voice", span: 8 },
  { moduleKey: "creators", span: 4 },
  { moduleKey: "fit", span: 4 },
  { moduleKey: "bh", span: 4 },
];

const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

/* ---------- 通用远端状态(金样板 useRemote 原样平移;三路互不阻塞) ---------- */
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
        const detail = (err as { detail?: unknown; message?: unknown }) || {};
        if (alive) setState({ status: "error", data: null, error: String(detail.detail || detail.message || err) });
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export function Sku360BoardPage({
  apiToken = "",
  sku: skuProp = "",
  onNavigate,
}: {
  apiToken?: string;
  sku?: string;
  onNavigate?: (navKey: string) => void;
}) {
  const readPendingSku = React.useCallback((): string => {
    try {
      const fromUrl = new URLSearchParams(window.location.search).get("sku360Sku") || "";
      if (fromUrl.trim()) return fromUrl.trim();
      return (window.sessionStorage.getItem(SKU_KEY) || "").trim();
    } catch {
      return "";
    }
  }, []);

  const [selectedSku, setSelectedSku] = React.useState<string>(() => {
    if (skuProp && skuProp.trim()) return skuProp.trim();
    return typeof window !== "undefined" ? readPendingSku() : "";
  });
  const [reloadTick, setReloadTick] = React.useState(0);
  const [editing, setEditing] = React.useState(false);

  // 选择器(旧页 300ms 防抖原样;query 空也拉默认列表方便直接浏览)
  const [query, setQuery] = React.useState("");
  const [options, setOptions] = React.useState<Sku360ListItem[]>([]);
  const [dropdownOpen, setDropdownOpen] = React.useState(false);
  const [searching, setSearching] = React.useState(false);

  // 全量列表 / 详情弹窗(contents 模块)
  const [listOpen, setListOpen] = React.useState(false);
  const [detailIdx, setDetailIdx] = React.useState<number | null>(null);

  // 打开档案的两条外部入口:prop 变化 / vkpi:open-sku360 事件(sessionStorage 传 sku)
  React.useEffect(() => {
    if (skuProp && skuProp.trim()) setSelectedSku(skuProp.trim());
  }, [skuProp]);
  React.useEffect(() => {
    const consume = () => {
      const next = readPendingSku();
      if (next) setSelectedSku(next);
    };
    window.addEventListener(OPEN_SKU_EVENT, consume);
    return () => window.removeEventListener(OPEN_SKU_EVENT, consume);
  }, [readPendingSku]);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setSearching(true);
    const timer = window.setTimeout(() => {
      listSku360Options(apiToken, query)
        .then((items) => {
          if (alive) setOptions(items);
        })
        .catch(() => {
          if (alive) setOptions([]);
        })
        .finally(() => {
          if (alive) setSearching(false);
        });
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [apiToken, query]);

  const pickSku = React.useCallback((sku: string) => {
    const clean = sku.trim();
    if (!clean) return;
    try {
      window.sessionStorage.setItem(SKU_KEY, clean);
    } catch {
      /* sessionStorage 不可用忽略 */
    }
    setSelectedSku(clean);
    setDropdownOpen(false);
    setListOpen(false);
    setDetailIdx(null);
  }, []);

  const enabled = Boolean(apiToken) && Boolean(selectedSku);
  const deps: React.DependencyList = [apiToken, selectedSku, reloadTick, enabled];

  /* ---------- 三路 page 层加载 ---------- */
  const profile = useRemote<Row>(enabled, () => getSku360Profile(apiToken, selectedSku), deps);
  const persona = useRemote<Row>(enabled, () => getSku360Persona(apiToken, selectedSku), deps);
  const campaign = useRemote<Row>(enabled, () => getSku360CampaignCard(apiToken, selectedSku), deps);

  const product = asRow(profile.data?.product);
  const content = asRow(profile.data?.content);
  const agg = asRow(content?.aggregate);
  const items = asArray(content?.items)
    .map((it) => asRow(it))
    .filter((it): it is Row => Boolean(it));
  const fitMatches = asArray(content?.content_fit_matches)
    .map((m) => asRow(m))
    .filter((m): m is Row => Boolean(m));
  const topCreators = asArray(agg?.top_creators)
    .map((c) => asRow(c))
    .filter((c): c is Row => Boolean(c));
  const comments = asRow(profile.data?.comments) || {};
  const bh = asRow(profile.data?.bh_reviews) || {};
  const personaRow = asRow(persona.data?.persona);
  const displayName = str(product?.model_name) || str(product?.marketing_name) || selectedSku;

  // 跨板块下钻:打开 KOL 档案(既有事件管道,零重造)
  const openKolProfile = React.useCallback(
    (kolPoolId: number) => {
      if (!Number.isFinite(kolPoolId) || kolPoolId <= 0) return;
      try {
        window.sessionStorage.setItem("vkpi:kol-profile-id", String(kolPoolId));
      } catch {
        /* sessionStorage 不可用忽略 */
      }
      window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile"));
      onNavigate?.("kolProfile");
    },
    [onNavigate],
  );

  /* ---------- gate(金样板同构:未登录 → PendingCard;加载/失败 → 诚实卡) ---------- */
  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载档案。
    </PendingCard>
  );
  const remoteGate = <T,>(state: RemoteState<T>, name: string): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (state.status === "loading" || state.status === "idle") return <LoadingLine text="档案聚合中…" />;
    if (state.status === "error") return <ErrorCard title={`${name}读取失败`} text={state.error} />;
    if (state.data === null) return <LoadingLine text="档案聚合中…" />;
    return null;
  };

  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows };
  };

  /* ---------- 模块 body ---------- */

  const renderKpiBand = () => {
    const extraRows: Array<[string, string]> = profile.data
      ? [["聚合时点", `${str(profile.data.generated_at) || "—"}(UTC · 请求时实算)`]]
      : [];
    return (
      <ModuleCard {...cardProps("kpiS", "声量指标带", product ? str(product.sku) : undefined, extraRows)}>
        {remoteGate(profile, "档案聚合") ?? (
          <SkuKpiBand
            contentCount={num(agg?.content_count)}
            creatorCount={num(agg?.creator_count)}
            totalViews={num(agg?.total_views)}
            avgEngagementPct={pct(num(agg?.avg_engagement_rate))}
            pending={!agg}
            pendingNote="档案聚合未就绪"
          />
        )}
      </ModuleCard>
    );
  };

  const renderCatalog = () => (
    <ModuleCard {...cardProps("catalog", "产品档案", product ? str(product.category_main) || undefined : undefined)}>
      {remoteGate(profile, "档案聚合") ?? <CatalogBody product={product as Row} />}
    </ModuleCard>
  );

  const renderKnowledge = () => {
    const extraRows: Array<[string, string]> = personaRow
      ? [["本行来源", `model=${str(personaRow.model) || "—"} · source=${str(personaRow.source) || "—"}(行级字段原样)`]]
      : [];
    return (
      <ModuleCard {...cardProps("knowledge", "知识库画像", personaRow ? "AI" : undefined, extraRows)}>
        {remoteGate(persona, "知识库") ?? <KnowledgeBody persona={personaRow} />}
      </ModuleCard>
    );
  };

  const renderAngles = () => {
    const angleCount = personaRow ? asArray(personaRow.promotion_angles_json).length : 0;
    return (
      <ModuleCard {...cardProps("angles", "推广方向", angleCount > 0 ? `${angleCount}` : undefined)}>
        {remoteGate(persona, "知识库") ?? <AnglesBody persona={personaRow} />}
      </ModuleCard>
    );
  };

  const renderContents = () => (
    <ModuleCard {...cardProps("contents", "提及内容", items.length > 0 ? `${items.length}` : undefined)}>
      {remoteGate(profile, "档案聚合") ?? (
        <ContentsBody
          items={items}
          note={str(content?.note)}
          onOpenDetail={(i) => setDetailIdx(i)}
          onOpenList={() => setListOpen(true)}
        />
      )}
    </ModuleCard>
  );

  const renderCreators = () => (
    <ModuleCard {...cardProps("creators", "关联创作者", topCreators.length > 0 ? `${topCreators.length}` : undefined)}>
      {remoteGate(profile, "档案聚合") ?? <CreatorsBody creators={topCreators} onOpenKol={openKolProfile} />}
    </ModuleCard>
  );

  const renderFit = () => (
    <ModuleCard {...cardProps("fit", "内容契合", fitMatches.length > 0 ? `${fitMatches.length}` : undefined)}>
      {remoteGate(profile, "档案聚合") ?? <FitBody matches={fitMatches} />}
    </ModuleCard>
  );

  const renderVoice = () => {
    const matched = num(comments.matched_total);
    return (
      <ModuleCard {...cardProps("voice", "评论声量", matched !== null && matched > 0 ? `${matched}` : undefined)}>
        {remoteGate(profile, "档案聚合") ?? <VoiceBody comments={comments} />}
      </ModuleCard>
    );
  };

  const renderBh = () => {
    const matched = num(bh.matched_total);
    return (
      <ModuleCard {...cardProps("bh", "电商口碑", matched !== null && matched > 0 ? `${matched}` : undefined)}>
        {remoteGate(profile, "档案聚合") ?? <BhBody bh={bh} />}
      </ModuleCard>
    );
  };

  const renderCandidates = () => {
    const n = campaign.data ? asArray(campaign.data.kol_candidates).length : 0;
    return (
      <ModuleCard {...cardProps("candidates", "推广候选", n > 0 ? `${n}` : undefined)}>
        {remoteGate(campaign, "推广候选") ?? <CandidatesBody card={campaign.data as Row} onOpenKol={openKolProfile} />}
      </ModuleCard>
    );
  };

  /* ---------- 模块注册表(palette 全量可选;candidates 为 palette 备选不进默认布局) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiS", label: "声量指标带", description: "提及内容 / 覆盖创作者 / 总曝光 / 平均互动率(请求时实算)", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "catalog", label: "产品档案", description: "目录信息 · 规格 · 适配标签 · 官网直跳", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 15, minHeight: 8, maxHeight: 28, render: renderCatalog },
    { key: "knowledge", label: "知识库画像", description: "它是什么 · 适合谁 · 核心规格点(AI 生成,标注来源)", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 15, minHeight: 6, maxHeight: 26, render: renderKnowledge },
    { key: "contents", label: "提及内容", description: "谁的内容提到/评测了它 · 全量弹窗 + 详情连续翻", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 14, minHeight: 6, maxHeight: 30, render: renderContents },
    { key: "angles", label: "推广方向", description: "切入角 · 优先/规避创作者类型(AI 生成参考)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 14, minHeight: 6, maxHeight: 26, render: renderAngles },
    { key: "voice", label: "评论声量", description: "评论库别名命中抽样 · 命中/扫描口径如实", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 11, minHeight: 5, maxHeight: 24, render: renderVoice },
    { key: "creators", label: "关联创作者", description: "命中内容按创作者聚合 TOP 榜 · 可跳档案", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 5, maxHeight: 20, render: renderCreators },
    { key: "fit", label: "内容契合", description: "创作者×SKU 契合判断缓存(AI 判断只读 · 置信原样)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 5, maxHeight: 20, render: renderFit },
    { key: "bh", label: "电商口碑", description: "电商评论均分与样本(表存在才读 · 0 行如实)", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 5, maxHeight: 20, render: renderBh },
    // ↓ palette 备选(不进默认布局)
    { key: "candidates", label: "推广候选", description: "启发式圈选候选 + 市场信号(人工复核,不自动推荐)", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 6, maxHeight: 26, render: renderCandidates },
  ];

  /* ---------- 选择器(pagehead 控件行;引导态也用同一只) ---------- */
  const selector = (
    <div className="relative min-w-[240px] flex-1">
      <input
        value={query}
        onChange={(ev) => {
          setQuery(ev.target.value);
          setDropdownOpen(true);
        }}
        onFocus={() => setDropdownOpen(true)}
        onBlur={() => window.setTimeout(() => setDropdownOpen(false), 150)}
        placeholder="搜索 SKU / 型号,如 85mm、AF-85MM-F14-PRO-FE…"
        aria-label="搜索 SKU"
        className="w-full rounded-xl border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none placeholder:text-muted focus:border-accent"
      />
      {dropdownOpen && (
        <div className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-xl border border-line bg-card shadow-xl">
          {searching && <div className="px-3 py-2 text-[11px] text-muted">搜索中…</div>}
          {!searching && options.length === 0 && <div className="px-3 py-2 text-[11px] text-muted">无匹配 SKU</div>}
          {!searching &&
            options.map((opt) => (
              <button
                key={opt.sku}
                type="button"
                onMouseDown={(ev) => {
                  ev.preventDefault();
                  pickSku(opt.sku);
                }}
                className="flex w-full items-center justify-between gap-2 border-b border-line px-3 py-1.5 text-left last:border-0 hover:bg-panel"
              >
                <span className="min-w-0">
                  <span className="block truncate text-[11px] text-ink-2">{opt.model_name || opt.marketing_name || opt.sku}</span>
                  <span className="block truncate text-[9.5px] text-muted">
                    {opt.sku}
                    {opt.mount ? ` · ${opt.mount}` : ""}
                    {opt.category_main ? ` · ${opt.category_main}` : ""}
                  </span>
                </span>
                <span className="shrink-0 font-mono text-[10px] text-muted">{opt.price_usd != null ? `$${opt.price_usd}` : ""}</span>
              </button>
            ))}
        </div>
      )}
    </div>
  );

  /* ---------- 未选择 SKU:引导态(不杜撰任何档案) ---------- */
  if (!enabled) {
    return (
      <div className="p-4 md:px-[22px] md:py-[15px]">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">SKU 360°</span>
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        {!apiToken ? (
          noTokenCard
        ) : (
          <div className="rounded-[18px] border border-dashed border-line-strong p-8 text-center">
            <div className="text-[13px] text-ink-2">选一个 SKU,看谁的内容提到/评测了它 —— 纯聚合已有数据,零采集。</div>
            <div className="mx-auto mt-3 flex max-w-md items-center gap-2 text-left">{selector}</div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + SKU/类目药丸徽 + 实时辉光点 + 刷新 + 编辑布局 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">SKU 360°</span>
          {displayName ? <span className={PH_BADGE}>{displayName}</span> : null}
          {product && str(product.category_main) ? <span className={PH_BADGE}>{str(product.category_main)}</span> : null}
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            实时
          </span>
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

      {/* 控件行:SKU 选择器(换 SKU 不出页) */}
      <div className="mb-4 flex flex-wrap items-start gap-2">{selector}</div>

      {profile.status === "error" && (
        <div className="mb-3">
          <ErrorCard title="档案聚合读取失败" text={profile.error} />
        </div>
      )}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 全量列表 / 详情弹窗(contents 模块;详情在列表之上可叠开) */}
      {listOpen ? (
        <ContentListModal items={items} sku={selectedSku} onOpenDetail={(i) => setDetailIdx(i)} onClose={() => setListOpen(false)} />
      ) : null}
      {detailIdx !== null && items[detailIdx] ? (
        <ContentDetailModal
          item={items[detailIdx]}
          index={detailIdx}
          total={items.length}
          onNav={(i) => {
            if (i >= 0 && i < items.length) setDetailIdx(i);
          }}
          onClose={() => setDetailIdx(null)}
          onOpenKol={openKolProfile}
        />
      ) : null}
    </div>
  );
}
