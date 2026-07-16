import React from "react";
import { PencilLine, RefreshCw, X } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { apiFetch } from "../../../../services/http";
import { ErrorCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { ModuleProvModal } from "./MarketVoicePage.dialogs";
import {
  MODULE_SOURCES,
  PROV_TITLES,
  StrategyKpiBand,
  type BenchResp,
  type BrandItem,
  type PerfResp,
  type TracksResp,
} from "./StrategyDeskPage.modules";
import { FocalBody, H2HBody, MatrixBody, NoGoBody, OppTopBody, RankBody } from "./StrategyDeskPage.charts";
import { BetsBody, FulBody, LessonsBody, PredsBody, SimEmbed } from "./StrategyDeskPage.perf";

// 战略台 → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage embeds
//   手法 + GtmCommandBoardPage 总脑纯读范例 1:1 同构)。旧 StrategyBoardPage +
//   四面板族保留不删(回滚垫);切换点只在 CockpitApp.tsx 的 lazy import 一行
//   (报告注明,本刀不改 CockpitApp)。
//   页壳:pagehead(标题 + 联动品牌徽 + 实时辉光点 + 刷新 + 编辑布局)+
//   EditableDashboardBoard(12 模块注册表,旧页四块全功能零丢失全进默认布局)。
//   数据源(全真,零编造;行数 2026-07-12 只读 PG 实测):
//     GET /api/admin/vkpi/strategy/industry-benchmark?window_days=90 —— 行业对照
//     GET /api/admin/vkpi/strategy/category-tracks                   —— 新赛道机会
//     GET /api/admin/vkpi/strategy/performance                       —— 战略表现三账
//     GET /api/admin/vkpi/strategy/simulate + /sku/list(模拟器 embed 内自取)
//   四端点后端 router+domain 已核实零写库(纯 SQL/规则聚合)。
//   【总脑纯读红线】绝不调用 marketing-brain/daily 与 market/trends 的 GET(两端点
//   有隐藏写入,GTM-1 规格明令禁碰;全族已核实绕开,冒烟负向断言)。
//   【联动位】市场之声 comp 模块跳转来的品牌参数,照 SKU360 三通道先例接收:
//   prop brand / URL ?strategyBrand= / sessionStorage(vkpi:strategy-brand)+
//   vkpi:open-strategy-desk 事件 —— 命中后排名条标记 + 对照行自动展开,可一键清除。
//   【验收纪律】卡面零介绍性文案:旧页副题/口径脚注/方法论句全部收进 MODULE_SOURCES
//   (SrcChip + 溯源弹窗);机会分公式/权重原文不上前端(显示层宪法)。
// 红线:纯读零写路径;不触 viltrox_fit_score / rule_v0;颜色全 token 零写死色;
//   布局只走本机 storageKey,不传 apiToken 给 EditableDashboardBoard(其账户级
//   持久化写死 dashboard_layout_v1 键,金样板同注释);时间戳一律绝对时间。

const STORAGE_KEY = "vkpi-strategy-desk-layout-v1";

// 联动品牌三通道键(SKU360 三通道先例:prop / URL / sessionStorage+事件)
export const STRATEGY_BRAND_KEY = "vkpi:strategy-brand";
export const OPEN_STRATEGY_EVENT = "vkpi:open-strategy-desk";
const BRAND_URL_PARAM = "strategyBrand";

// 默认布局(12 列 · 七行):旧页四块(对照/赛道/模拟/表现)全功能零丢失全进默认
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiS", span: 12 },
  { moduleKey: "rank", span: 8 },
  { moduleKey: "focal", span: 4 },
  { moduleKey: "h2h", span: 12 },
  { moduleKey: "matrix", span: 8 },
  { moduleKey: "oppTop", span: 4 },
  { moduleKey: "sim", span: 8 },
  { moduleKey: "noGo", span: 4 },
  { moduleKey: "preds", span: 8 },
  { moduleKey: "bets", span: 4 },
  { moduleKey: "ful", span: 8 },
  { moduleKey: "lessons", span: 4 },
];

const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

// 页面级三路只读加载(旧四面板各自拉取 → 收敛到 page 层三请求;失败互不拖垮)
function useStrategyRemote<T extends { status?: string }>(apiToken: string, path: string, reloadTick: number) {
  const [data, setData] = React.useState<T | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    apiFetch<T>(path, { timeoutMs: 20000 }, apiToken)
      .then((payload) => {
        if (alive) setData(payload && typeof payload === "object" ? payload : null);
      })
      .catch((err: unknown) => {
        const detail = (err as { detail?: unknown; message?: unknown }) || {};
        if (alive) setError(String(detail.detail || detail.message || "读取失败"));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, path, reloadTick]);
  return { data, loading, error };
}

export function StrategyDeskPage({ apiToken = "", brand = "", embeddedModuleKey }: { apiToken?: string; brand?: string; embeddedModuleKey?: string }) {
  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  // 赛道模块联动态(oppTop 点行 → matrix 切维展开;state 提升到 page 层)
  const [dim, setDim] = React.useState<"category" | "focal">("category");
  const [selectedTrackId, setSelectedTrackId] = React.useState("");

  // 对照行展开态(联动品牌可从外部点亮某行)
  const [h2hOpenKey, setH2hOpenKey] = React.useState<string | null>(null);

  /* ---------- 联动品牌三通道接收(SKU360 先例:prop / URL / sessionStorage+事件) ---------- */
  const readPendingBrand = React.useCallback((): string => {
    try {
      const fromUrl = new URLSearchParams(window.location.search).get(BRAND_URL_PARAM) || "";
      if (fromUrl.trim()) return fromUrl.trim();
      return (window.sessionStorage.getItem(STRATEGY_BRAND_KEY) || "").trim();
    } catch {
      return "";
    }
  }, []);

  const [focusBrand, setFocusBrand] = React.useState<string>(() => {
    if (brand && brand.trim()) return brand.trim();
    return typeof window !== "undefined" ? readPendingBrand() : "";
  });

  React.useEffect(() => {
    if (brand && brand.trim()) setFocusBrand(brand.trim());
  }, [brand]);
  React.useEffect(() => {
    const consume = () => {
      const next = readPendingBrand();
      if (next) setFocusBrand(next);
    };
    window.addEventListener(OPEN_STRATEGY_EVENT, consume);
    return () => window.removeEventListener(OPEN_STRATEGY_EVENT, consume);
  }, [readPendingBrand]);

  const clearFocusBrand = React.useCallback(() => {
    setFocusBrand("");
    try {
      window.sessionStorage.removeItem(STRATEGY_BRAND_KEY);
    } catch {
      /* sessionStorage 不可用忽略 */
    }
  }, []);

  /* ---------- 三路只读加载(互不拖垮;窗口 90 天与旧页同口径) ---------- */
  const bench = useStrategyRemote<BenchResp>(apiToken, "/api/admin/vkpi/strategy/industry-benchmark?window_days=90", reloadTick);
  const tracks = useStrategyRemote<TracksResp>(apiToken, "/api/admin/vkpi/strategy/category-tracks", reloadTick);
  const perf = useStrategyRemote<PerfResp>(apiToken, "/api/admin/vkpi/strategy/performance", reloadTick);

  const benchOk = bench.data != null && String(bench.data.status || "") === "ok";
  const tracksOk = tracks.data != null && String(tracks.data.status || "") === "ready";
  const perfOk = perf.data != null && String(perf.data.status || "") === "ok";

  // 联动品牌命中 → 对照行自动展开(排名条同帧标记)
  React.useEffect(() => {
    if (!focusBrand || !benchOk) return;
    const needle = focusBrand.toLowerCase();
    const hit = (bench.data!.head_to_head || []).find(
      (h) => String(h.brand || "").toLowerCase() === needle || String(h.key || "").toLowerCase() === needle,
    );
    if (hit?.key) setH2hOpenKey(String(hit.key));
  }, [focusBrand, benchOk, bench.data]);

  /* ---------- 卡头 props(金样板 cardProps 同构:SrcChip hover + 点击溯源弹窗) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const mergedRowsRef = React.useRef<Record<string, Array<[string, string]>>>({});
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    mergedRowsRef.current[key] = rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows, onOpenSrc: () => setProvKey(key) };
  };

  /* ---------- 闸(金样板双轨:未登录/加载/失败 → 诚实卡;绝不假数据) ---------- */
  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载战略数据。
    </PendingCard>
  );

  const benchGate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (bench.loading && !bench.data) return <LoadingLine text="行业对照聚合中…" />;
    if (bench.error) return <ErrorCard title="strategy/industry-benchmark 读取失败" text={bench.error} />;
    if (!bench.data) return <LoadingLine text="行业对照聚合中…" />;
    if (String(bench.data.status || "") === "error") {
      return <ErrorCard title="行业对照聚合失败" text={String(bench.data.reason || "后端聚合异常")} />;
    }
    if (!benchOk) {
      // no_data_in_window / no_brand_signal:诚实空态短句(不甩报错)
      const text = bench.data.status === "no_data_in_window" ? "窗口内暂无入库视频证据。" : "窗口内视频未命中品牌词表。";
      return <div className="px-3 py-4 text-center text-[12px] text-muted">{text}</div>;
    }
    return null;
  };

  const tracksGate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (tracks.loading && !tracks.data) return <LoadingLine text="赛道信号聚合中…" />;
    if (tracks.error) return <ErrorCard title="strategy/category-tracks 读取失败" text={tracks.error} />;
    if (!tracks.data) return <LoadingLine text="赛道信号聚合中…" />;
    if (String(tracks.data.status || "") === "error") {
      return <ErrorCard title="赛道聚合失败" text={String(tracks.data.reason || "后端聚合异常")} />;
    }
    if (!tracksOk) {
      return <div className="px-3 py-4 text-center text-[12px] text-muted">{String(tracks.data.reason || "窗口内暂无声量数据。")}</div>;
    }
    return null;
  };

  const perfGate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (perf.loading && !perf.data) return <LoadingLine text="战略表现聚合中…" />;
    if (perf.error) return <ErrorCard title="strategy/performance 读取失败" text={perf.error} />;
    if (!perf.data) return <LoadingLine text="战略表现聚合中…" />;
    if (!perfOk) return <ErrorCard title="战略表现聚合失败" text={String((perf.data as { reason?: string }).reason || "后端聚合异常")} />;
    return null;
  };

  /* ---------- 模块 render ---------- */

  const renderKpiBand = () => {
    const extraRows: Array<[string, string]> = [];
    if (benchOk && bench.data!.basis) {
      const b = bench.data!.basis!;
      extraRows.push(["对照样本", `扫描 ${Number(b.videos_scanned) || 0} 条证据 · 品牌命中 ${Number(b.brand_hit_videos) || 0} · 深析 ${Number(b.deep_analyzed_in_window) || 0}`]);
    }
    if (perfOk && perf.data!.generated_at) {
      extraRows.push(["生成于", `${perf.data!.generated_at}(UTC 存 · 按浏览器时区显示)`]);
    }
    return (
      <ModuleCard {...cardProps("kpiS", "战略总览", "4 指标", extraRows)}>
        {!apiToken ? noTokenCard : <StrategyKpiBand bench={bench.data} tracks={tracks.data} perf={perf.data} />}
      </ModuleCard>
    );
  };

  const renderRank = () => {
    const viltrox = bench.data?.viltrox || {};
    const sov = typeof viltrox.share_of_voice === "number" ? Math.round(viltrox.share_of_voice * 1000) / 10 : null;
    const cnt = benchOk && sov != null ? `SoV ${sov}%` : undefined;
    const extraRows: Array<[string, string]> = [];
    if (benchOk) {
      extraRows.push(["窗口", `${Number(bench.data!.window_days) || 90} 天`]);
      if (viltrox.rank != null) extraRows.push(["我方位次", `#${viltrox.rank} / ${Number(bench.data!.brand_count_ranked) || "—"}(声量份额口径)`]);
      const conf = bench.data!.confidence;
      if (conf?.level) extraRows.push(["置信", `${conf.level}${conf.reason ? ` · ${conf.reason}` : ""}`]);
    }
    return (
      <ModuleCard {...cardProps("rank", "声量份额排名", cnt, extraRows)}>
        {benchGate() ?? (
          <RankBody
            viltrox={bench.data!.viltrox || {}}
            competitors={Array.isArray(bench.data!.competitors) ? bench.data!.competitors! : []}
            focusBrand={focusBrand}
          />
        )}
      </ModuleCard>
    );
  };

  const renderH2H = () => {
    const h2h = benchOk && Array.isArray(bench.data!.head_to_head) ? bench.data!.head_to_head! : [];
    const compByKey: Record<string, BrandItem> = {};
    if (benchOk) {
      for (const c of bench.data!.competitors || []) if (c.key) compByKey[c.key] = c;
    }
    return (
      <ModuleCard {...cardProps("h2h", "Viltrox vs 竞品", h2h.length > 0 ? `${h2h.length} 家` : undefined)}>
        {benchGate() ?? (
          <H2HBody h2h={h2h} compByKey={compByKey} openKey={h2hOpenKey} onToggle={(key) => setH2hOpenKey(h2hOpenKey === key ? null : key)} />
        )}
      </ModuleCard>
    );
  };

  const renderFocal = () => {
    const grid = bench.data?.focal_grid || {};
    const oppCount = Array.isArray(grid.opportunities) ? grid.opportunities.length : 0;
    return (
      <ModuleCard {...cardProps("focal", "焦段格局", benchOk && oppCount > 0 ? `空档 ×${oppCount}` : undefined)}>
        {benchGate() ?? <FocalBody grid={grid} />}
      </ModuleCard>
    );
  };

  const renderMatrix = () => {
    const extraRows: Array<[string, string]> = [];
    if (tracksOk && tracks.data!.sources) {
      const s = tracks.data!.sources!;
      extraRows.push(["本窗实测", `声音 ${Number(s.voice_docs) || 0} 条(60天)+ 证据 ${Number(s.evidence_rows) || 0} 条(180天)+ 目录 ${Number(s.catalog_skus) || 0} SKU`]);
    }
    const count = tracksOk ? ((dim === "category" ? tracks.data!.category_tracks : tracks.data!.focal_tracks) || []).length : 0;
    return (
      <ModuleCard {...cardProps("matrix", "机会矩阵", tracksOk ? `${count} 赛道` : undefined, extraRows)}>
        {tracksGate() ?? (
          <MatrixBody
            tracksResp={tracks.data!}
            dim={dim}
            onDimChange={(next) => {
              setDim(next);
              setSelectedTrackId("");
            }}
            selectedId={selectedTrackId}
            onSelect={setSelectedTrackId}
          />
        )}
      </ModuleCard>
    );
  };

  const renderOppTop = () => {
    const opps = tracksOk && Array.isArray(tracks.data!.opportunities) ? tracks.data!.opportunities! : [];
    return (
      <ModuleCard {...cardProps("oppTop", "Top 机会赛道", opps.length > 0 ? `${opps.length}` : undefined)}>
        {tracksGate() ?? (
          <OppTopBody
            opportunities={opps}
            onPick={(nextDim, trackId) => {
              setDim(nextDim);
              setSelectedTrackId(trackId);
            }}
          />
        )}
      </ModuleCard>
    );
  };

  const renderNoGo = () => {
    const noGo = tracksOk && Array.isArray(tracks.data!.no_go) ? tracks.data!.no_go! : [];
    const mounts = tracksOk && Array.isArray(tracks.data!.mount_signals) ? tracks.data!.mount_signals! : [];
    return (
      <ModuleCard {...cardProps("noGo", "不进清单", noGo.length > 0 ? `${noGo.length}` : undefined)}>
        {tracksGate() ?? <NoGoBody noGo={noGo} mounts={mounts} />}
      </ModuleCard>
    );
  };

  const renderSim = () => <SimEmbed apiToken={apiToken} noToken={noTokenCard} />;

  const renderBets = () => {
    const bets = perf.data?.scoreboard?.bets || {};
    const settled = Number(bets.settled) || 0;
    return (
      <ModuleCard {...cardProps("bets", "押注台账", perfOk ? `${settled} 结算` : undefined)}>
        {perfGate() ?? <BetsBody bets={bets} />}
      </ModuleCard>
    );
  };

  const renderPreds = () => {
    const preds = perf.data?.scoreboard?.predictions || {};
    const n = Array.isArray(preds.groups) ? preds.groups.length : 0;
    return (
      <ModuleCard {...cardProps("preds", "预测命中率", perfOk && n > 0 ? `${n} 组` : undefined)}>
        {perfGate() ?? <PredsBody preds={preds} />}
      </ModuleCard>
    );
  };

  const renderFul = () => {
    const ful = perf.data?.scoreboard?.fulfillment || {};
    return (
      <ModuleCard {...cardProps("ful", "履约对账", perfOk ? `${Number(ful.loops_completed) || 0} 闭环` : undefined)}>
        {perfGate() ?? <FulBody ful={ful} />}
      </ModuleCard>
    );
  };

  const renderLessons = () => {
    const lessons = perf.data?.lessons || {};
    const honesty = Array.isArray(perf.data?.honesty_note?.items) ? perf.data!.honesty_note!.items! : [];
    const n = Array.isArray(lessons.items) ? lessons.items.length : 0;
    return (
      <ModuleCard {...cardProps("lessons", "教训与数据荒", perfOk && n > 0 ? `${n}` : undefined)}>
        {perfGate() ?? <LessonsBody lessons={lessons} honesty={honesty} generatedAt={perf.data?.generated_at} />}
      </ModuleCard>
    );
  };

  /* ---------- 模块注册表(palette 全量可选;默认 = 旧页四块全功能) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiS", label: "战略总览", description: "声量份额 / 机会赛道 / 押注命中 / 待对答案 四真数", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "rank", label: "声量份额排名", description: "90 天品牌声量条 · Viltrox 高亮 · 联动品牌标记", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 4, maxHeight: 18, render: renderRank },
    { key: "focal", label: "焦段格局", description: "竞品声量 vs 我方声量 vs 官方 SKU · 空档红格", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 4, maxHeight: 18, render: renderFocal },
    { key: "h2h", label: "Viltrox vs 竞品", description: "逐竞品可展开:三行对比 + 质量侧写 + 例证视频", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 11, minHeight: 5, maxHeight: 24, render: renderH2H },
    { key: "matrix", label: "机会矩阵", description: "需求 × 覆盖 3×3 格 · 品类/焦段双维 · 点赛道看证据", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 6, maxHeight: 28, render: renderMatrix },
    { key: "oppTop", label: "Top 机会赛道", description: "需求高 × 我方弱 × 竞品未垄断 前五 · 点行进矩阵", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 4, maxHeight: 16, render: renderOppTop },
    { key: "sim", label: "策略模拟器", description: "选 SKU + 预算 → 三策略并排对比(决定性模拟)", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 6, maxHeight: 28, render: renderSim },
    { key: "noGo", label: "不进清单", description: "需求低 / 竞品垄断 · 理由随行 + 卡口愿望信号", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 4, maxHeight: 16, render: renderNoGo },
    { key: "preds", label: "预测命中率", description: "各动作组命中率条 + 待对答案积压总账", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 4, maxHeight: 20, render: renderPreds },
    { key: "bets", label: "押注台账", description: "押对/押错/未结算 + 最老未结算注账龄", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 4, maxHeight: 18, render: renderBets },
    { key: "ful", label: "履约对账", description: "闭环步骤留痕 + 计划 vs 实际逐条样例", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 5, maxHeight: 24, render: renderFul },
    { key: "lessons", label: "教训与数据荒", description: "已沉淀教训 top5 + 哪本账还空着直说", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 12, minHeight: 5, maxHeight: 24, render: renderLessons },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="战略台" />;
  }

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 联动品牌徽 + 实时辉光点 + 刷新 + 编辑布局 */}
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">战略台</span>
          {focusBrand ? (
            <span className={`${PH_BADGE} inline-flex items-center gap-1`} title="来自市场之声的联动品牌(排名条已标记 · 对照行已展开)">
              联动 · {focusBrand}
              <button type="button" onClick={clearFocusBrand} aria-label="清除联动品牌" className="text-accent transition-colors hover:text-accent-hover">
                <X size={10} />
              </button>
            </span>
          ) : null}
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

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 模块溯源弹窗(SrcChip 点击):口径 = MODULE_SOURCES + 调用点动态行,零第二份 */}
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
