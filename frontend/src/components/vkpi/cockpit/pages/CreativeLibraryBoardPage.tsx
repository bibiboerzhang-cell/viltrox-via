import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { formatLocal } from "../../lib/timeLocal";
import {
  searchCreativeSegments,
  type CreativeSegmentItem,
  type CreativeSegmentsResponse,
} from "../../../../services/vkpi/creativeLibrary-api";
import { CoverRow, EmptyLine, ErrorCard, KpiCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { BarRow, CatDonutBody } from "./MarketVoicePage.charts";
import {
  FilterBar,
  MODULE_SOURCES,
  PROV_TITLES,
  STYLE_FALLBACK,
  SegmentRowLine,
  segmentTypeMeta,
  type CreativeFilterState,
} from "./CreativeLibraryBoardPage.modules";
import { CreativeProvModal, SegmentDetailModal, SegmentListModal } from "./CreativeLibraryBoardPage.dialogs";

// 创意资产库 → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage 同构)。
//   「哪个开头/哪种画面最灵」:已深析(final_v1)视频拆段(开头/分镜/产品露出),
//   三路词表过滤检索,按所属视频播放数降序 —— 旧 CreativeLibraryPage 全功能块零丢失:
//   三筛选(自由词 300ms 防抖 / 拍法 facets 下拉 / 焦段带库内最多提示)、四轨诚实态
//   (无 token / 端点失败 / status:error / 深析库空 / 过滤零命中)、条目行全字段
//   (段型徽/时间戳/开头·拍法·全片·焦段标签/播放/原帖外链/KOL/发布时间/evidence 溯源)、
//   扫描·索引·命中 meta 行、note 与 generated_at(收进 SrcChip 口径行 + 索引健康卡)。
//   新增(范式六要素):页级 KPI 带 4 卡真值(段级条目/已深析视频/覆盖 KOL/当前命中,
//   端点无时序 → 诚实虚线零假 sparkline)、图表优先(段型环图/拍法·开头·焦段条形,
//   点数据行 = 真过滤联动)、可编辑看板(注册表 + palette 备选)、溯源可点进
//   (SrcChip → 模块溯源弹窗;条目详情 ProvChain 每跳可点 + KOL 档案真跳)、
//   全量 + 连续翻(卡面 6 条 → 全量弹窗 → 详情 ‹ #n/N › + ↑↓;端点单次封顶 200 如实
//   标注,零假分页)、诚实空态。缩略图 = 后端毒缓存自愈链(cached_image_url 语义:
//   失败占位 SVG 不算真图;best_thumbnail 链序 cached → raw → youtube 派生),
//   前端 onError / 三路皆无 → 诚实 ▶ 占位。
//   数据源(全真,零编造):GET /api/admin/vkpi/creative-segments/search
//   (vkpi_analysis_cache final_v1 × vkpi_kol_video_evidence × vkpi_kol_pool,
//   请求时实时拆解,无独立聚合表)。白名单使用权请求进外联模板 = 后续刀,本页只读。
// 红线:纯读展示,绝不写 viltrox fit 分 / 不触 rule_v0;颜色全 token 零写死色;
//   零 opacity 修饰类;时间 = 绝对时间戳(存 UTC 显示按浏览器时区);发光只走既有
//   ds-* 类(自带 reduced-motion 降级);端点失败 = 诚实错误卡。布局只走本机
//   storageKey,不传 apiToken 给 EditableDashboardBoard(其账户级持久化写死
//   dashboard_layout_v1 键,传了会覆写 Dashboard 布局 —— 金样板同注释)。

const STORAGE_KEY = "vkpi-creative-library-layout-v1";
const FACE_ROWS = 6; // 卡面收敛条数(demo FULL.slice(0,6);全量走弹窗)
const FETCH_LIMIT = 200; // 端点单次封顶(无 offset 分页 —— 超出封顶如实标注)

// 默认布局(12 列 · 默认简三行):kpiC(12) → segments(8)+types(4) →
// styles(4)+openings(4)+focals(4);health 为 palette 备选
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiC", span: 12 },
  { moduleKey: "segments", span: 8 },
  { moduleKey: "types", span: 4 },
  { moduleKey: "styles", span: 4 },
  { moduleKey: "openings", span: 4 },
  { moduleKey: "focals", span: 4 },
];

// demo .ph-b:pagehead 药丸徽(金样板同款)
const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

type Row = Record<string, any>;

export function CreativeLibraryBoardPage({
  apiToken = "",
  onNavigate,
}: {
  apiToken?: string;
  onNavigate?: (navKey: string) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);

  // 三路服务端过滤(旧页同款)+ 段型客户端过滤(段型构成环图点入;端点无此参数,如实)
  const [filter, setFilter] = React.useState<CreativeFilterState>({ query: "", style: "", focal: "" });
  const [typeFilter, setTypeFilter] = React.useState("");

  const [data, setData] = React.useState<CreativeSegmentsResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  // 弹窗:全量列表 / 单条详情 / 模块溯源
  const [listOpen, setListOpen] = React.useState(false);
  const [detailIndex, setDetailIndex] = React.useState<number | null>(null);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  // 三筛选任一变化 → 300ms 防抖重查(旧页同款);首载空筛选拉全库 TOP(封顶 200)
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    const timer = window.setTimeout(() => {
      searchCreativeSegments(apiToken, { ...filter, limit: FETCH_LIMIT })
        .then((res) => {
          if (alive) setData(res && typeof res === "object" ? res : null);
        })
        .catch((err: any) => {
          if (alive) setError(String(err?.detail || err?.message || "加载失败"));
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [apiToken, filter, reloadTick]);

  const items: CreativeSegmentItem[] = React.useMemo(() => (Array.isArray(data?.items) ? data!.items! : []), [data]);
  // 段型过滤只在已加载条目内进行(端点无此参数 —— FilterBar chip / SrcChip 口径均如实标注)
  const displayItems = React.useMemo(
    () => (typeFilter ? items.filter((it) => it.segment_type === typeFilter) : items),
    [items, typeFilter],
  );
  const facets: Row = data?.facets || {};
  const matched = Number(data?.matched) || 0;
  const generatedLocal = data?.generated_at ? formatLocal(data.generated_at, { year: "numeric" }) : "—";

  // 拍法下拉:facets 真计数;facets 未到货(首载/降级)→ 静态词表兜底(旧页零丢失,
  // 与后端 SHOOTING_MODES 同口径,零计数不装数)
  const styleOptions = React.useMemo(() => {
    const real = (Array.isArray(facets.styles) ? facets.styles : []).map((f: Row) => ({
      key: String(f.key),
      label: String(f.label || f.key),
      count: Number(f.segment_count) || 0,
    }));
    return real.length > 0 ? real : STYLE_FALLBACK;
  }, [facets.styles]);
  const focalHint = React.useMemo(
    () => (Array.isArray(facets.focals) ? facets.focals : []).slice(0, 3).map((f: Row) => String(f.focal)),
    [facets.focals],
  );

  // 溯源身份跳:kol_pool_id → KOL 档案页(sessionStorage + 既有事件管道,
  // MarketVoice jumpIdentity 同口径;CockpitApp 既有监听切页)
  const jumpKol = React.useCallback(
    (item: CreativeSegmentItem) => {
      if (item.kol.kol_pool_id == null) return;
      try {
        window.sessionStorage.setItem("vkpi:kol-profile-id", String(item.kol.kol_pool_id));
      } catch {
        /* sessionStorage 不可用忽略,事件管道仍会切页 */
      }
      if (onNavigate) onNavigate("kolProfile");
      window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile"));
    },
    [onNavigate],
  );

  // 连续翻(全量已一次加载,封顶内直接切)
  const gotoSegment = (i: number) => {
    if (i < 0 || i >= displayItems.length) return;
    setDetailIndex(i);
  };

  /* ---------- 卡头 props(金样板 cardProps 同构:静态口径 + 调用点动态 extraRows,
     合并结果记 ref 供点击溯源弹窗拿全量) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const mergedRowsRef = React.useRef<Record<string, Array<[string, string]>>>({});
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    mergedRowsRef.current[key] = rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows, onOpenSrc: () => setProvKey(key) };
  };

  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载段级创意资产。
    </PendingCard>
  );

  // 统一闸(旧页四轨诚实态收编):无 token / 端点失败 / 加载中 / 聚合失败 → 诚实卡
  const reportGate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (error) return <ErrorCard title="creative-segments 读取失败" text={error} />;
    if (loading && !data) return <LoadingLine text="段级索引检索中…" />;
    if (!data) return <EmptyLine text="暂无数据。" />;
    if (String(data.status) === "error") return <ErrorCard title="检索暂不可用" text={String(data.reason || "未知原因")} />;
    return null;
  };
  // 深析库空(status:empty)= 管线级空态,带后端原因原样透出
  const readyGate = (): React.ReactNode | null => {
    const gate = reportGate();
    if (gate) return gate;
    if (String(data!.status) === "empty") return <EmptyLine text={String(data!.reason || "深析库为空,段级资产无从索引。")} />;
    return null;
  };
  // facets 驱动的图形模块闸:ready 但缺该组字段 → 诚实 pending(不编数据)
  const facetGate = (group: unknown): React.ReactNode | null => {
    const gate = readyGate();
    if (gate) return gate;
    if (!Array.isArray(group) || group.length === 0) {
      return (
        <PendingCard>
          <b>该组字段缺席</b> —— 检索端点未返回本组计数,接通后自动点亮(不摆假图)。
        </PendingCard>
      );
    }
    return null;
  };

  /* ---------- 模块 body ---------- */

  // KPI 带四卡(全真值;端点无按日时序 → KpiCard 诚实虚线,零假 sparkline 零假环比;
  // 深析库空 = readyGate 直出 reason 行,不摆四个 0 装有库)
  const renderKpiBand = () => {
    const gate = readyGate();
    const extraRows: Array<[string, string]> =
      data && String(data.status) !== "error"
        ? [
            ["扫描", `${Number(data.scanned_videos) || 0} 条深析视频`],
            ["索引", `${Number(data.segment_count) || 0} 个段级条目`],
            ["命中", `${matched} 段(当前三路过滤口径)`],
            ["时序", "端点无按日序列 —— KPI 卡虚线如实,不编趋势"],
            ["生成于", `${String(data.generated_at || "—")}(UTC)· 本地 ${generatedLocal}`],
            ...(data.note ? ([["口径备注", String(data.note)]] as Array<[string, string]>) : []),
          ]
        : [];
    const kolCount = data?.kol_count;
    return (
      <ModuleCard {...cardProps("kpiC", "资产总览", data ? `${Number(data.segment_count) || 0}` : undefined, extraRows)}>
        {gate ?? (
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <KpiCard label="段级条目" value={(Number(data!.segment_count) || 0).toLocaleString()} unit="段" seriesColor="var(--ds-accent)" />
            <KpiCard label="已深析视频" value={(Number(data!.scanned_videos) || 0).toLocaleString()} unit="条" seriesColor="var(--ds-accent-2)" />
            {typeof kolCount === "number" ? (
              <KpiCard label="覆盖 KOL" value={kolCount.toLocaleString()} unit="位" seriesColor="var(--ds-good)" />
            ) : (
              <KpiCard label="覆盖 KOL" value="—" pending pendingNote="覆盖计数待接通(端点未返回)" />
            )}
            <KpiCard label="当前命中" value={matched.toLocaleString()} unit="段" seriesColor="var(--ds-info)" />
          </div>
        )}
      </ModuleCard>
    );
  };

  // 段级条目流:三筛选(旧页零丢失)+ meta 行 + 卡面 6 条 + 全量弹窗入口
  const renderSegments = () => {
    const gate = readyGate();
    let body: React.ReactNode;
    if (gate) body = gate;
    else if (displayItems.length === 0)
      body = (
        <EmptyLine
          text={
            typeFilter
              ? `已加载条目内无「${segmentTypeMeta(typeFilter).label}」—— 段型过滤只在已加载 ${items.length} 条内进行,清除段型或收窄三路筛选。`
              : "三路过滤零命中 —— 词表规则不硬凑(诚实空态);换个词,或用下拉里真实存在的拍法/焦段。"
          }
        />
      );
    else
      body = (
        <div>
          <div className="mb-1.5 font-mono text-[9.5px] text-muted">
            扫描 {Number(data!.scanned_videos) || 0} 条深析视频 · 索引 {Number(data!.segment_count) || 0} 段 · 命中 {matched} 段
            {typeFilter ? ` · 段型过滤后 ${displayItems.length} 段(已加载内)` : ""} · 按所属视频播放数降序
          </div>
          {displayItems.slice(0, FACE_ROWS).map((item, i) => (
            <SegmentRowLine key={`${item.segment_id}-${i}`} item={item} index={i} onOpen={gotoSegment} />
          ))}
          {displayItems.length > FACE_ROWS && (
            <button
              type="button"
              onClick={() => setListOpen(true)}
              className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
            >
              ≡ 查看全量 {displayItems.length} 段 · 点单条连续翻
            </button>
          )}
        </div>
      );
    const extraRows: Array<[string, string]> =
      data && String(data.status) === "ready"
        ? [
            ["命中", `${matched} 段 · 已加载 ${items.length} 段(端点单次封顶 ${FETCH_LIMIT})`],
            ...(typeFilter ? ([["段型过滤", `${segmentTypeMeta(typeFilter).label} · 已加载条目内过滤(端点无此参数,如实)`]] as Array<[string, string]>) : []),
          ]
        : [];
    return (
      <ModuleCard {...cardProps("segments", "段级条目流", data && String(data.status) === "ready" ? `${displayItems.length}` : undefined, extraRows)}>
        <FilterBar
          filter={filter}
          onChange={setFilter}
          styleOptions={styleOptions}
          focalHint={focalHint}
          typeFilter={typeFilter}
          onClearType={() => setTypeFilter("")}
        />
        {body}
      </ModuleCard>
    );
  };

  // 段型构成环图:facets.segment_types 全索引真计数;点分段 = 段型过滤(已加载内,如实)
  const renderTypes = () => {
    const group: Row[] = Array.isArray(facets.segment_types) ? facets.segment_types : [];
    const cats = group.map((t) => ({ key: String(t.key), label: String(t.label || t.key), count: Number(t.segment_count) || 0 }));
    const total = cats.reduce((acc, c) => acc + c.count, 0);
    return (
      <ModuleCard {...cardProps("types", "段型构成", group.length > 0 ? `${group.length} 型` : undefined)}>
        {facetGate(group) ?? (
          <div>
            <CatDonutBody
              categories={cats}
              totalMatched={total}
              onSelect={(key) => setTypeFilter((cur) => (cur === key ? "" : key))}
            />
            <div className="vkpi-prov-note">点分段 = 段型过滤(已加载条目内 · 端点无此参数,如实)</div>
          </div>
        )}
      </ModuleCard>
    );
  };

  // 拍法构成:词表七类条形(段级+全片合并去重计数);点行 = style 精确过滤(端点参数)
  const renderStyles = () => {
    const group: Row[] = Array.isArray(facets.styles) ? facets.styles : [];
    const max = group.reduce((acc, s) => Math.max(acc, Number(s.segment_count) || 0), 0);
    return (
      <ModuleCard {...cardProps("styles", "拍法构成", group.length > 0 ? `${group.length} 类` : undefined)}>
        {facetGate(group) ?? (
          <div>
            {group.map((s) => {
              const label = String(s.label || s.key);
              const count = Number(s.segment_count) || 0;
              const active = filter.style === label;
              return (
                <BarRow
                  key={String(s.key)}
                  name={label}
                  widthPct={max > 0 ? (count / max) * 100 : 0}
                  value={`×${count}`}
                  highlight={active}
                  title={active ? "再点清除拍法过滤" : "点击 = 拍法精确过滤段级条目流"}
                  onClick={() => setFilter((f) => ({ ...f, style: f.style === label ? "" : label }))}
                />
              );
            })}
            <div className="vkpi-prov-note">段级 + 全片标签合并去重计数 · 点行过滤条目流</div>
          </div>
        )}
      </ModuleCard>
    );
  };

  // 开头类型:五类条形;点行 = 标签词填入自由词检索(端点无 opening 参数,如实近似)
  const renderOpenings = () => {
    const group: Row[] = Array.isArray(facets.openings) ? facets.openings : [];
    const max = group.reduce((acc, s) => Math.max(acc, Number(s.segment_count) || 0), 0);
    return (
      <ModuleCard {...cardProps("openings", "开头类型", group.length > 0 ? `${group.length} 类` : undefined)}>
        {facetGate(group) ?? (
          <div>
            {group.map((s) => {
              const label = String(s.label || s.key);
              const count = Number(s.segment_count) || 0;
              const active = filter.query === label;
              return (
                <BarRow
                  key={String(s.key)}
                  name={label}
                  widthPct={max > 0 ? (count / max) * 100 : 0}
                  color="linear-gradient(90deg, var(--ds-accent-2), var(--ds-accent))"
                  value={`×${count}`}
                  highlight={active}
                  title={active ? "再点清除自由词" : "点击 = 以标签词填入自由词检索(端点无开头参数,如实近似)"}
                  onClick={() => setFilter((f) => ({ ...f, query: f.query === label ? "" : label }))}
                />
              );
            })}
            <div className="vkpi-prov-note">开头段词表命中 · 识别不了不硬贴(未分类不计)</div>
          </div>
        )}
      </ModuleCard>
    );
  };

  // 焦段热度:Top 10 条形;点行 = focal 精确过滤(端点参数)
  const renderFocals = () => {
    const group: Row[] = Array.isArray(facets.focals) ? facets.focals : [];
    const top = group.slice(0, 10);
    const max = top.reduce((acc, s) => Math.max(acc, Number(s.segment_count) || 0), 0);
    return (
      <ModuleCard {...cardProps("focals", "焦段热度", group.length > 0 ? `Top ${top.length}` : undefined)}>
        {facetGate(group) ?? (
          <div>
            {top.map((s) => {
              const focal = String(s.focal);
              const count = Number(s.segment_count) || 0;
              const active = filter.focal === focal;
              return (
                <BarRow
                  key={focal}
                  name={focal}
                  widthPct={max > 0 ? (count / max) * 100 : 0}
                  color="linear-gradient(90deg, var(--ds-info), var(--ds-accent))"
                  value={`×${count}`}
                  highlight={active}
                  title={active ? "再点清除焦段过滤" : "点击 = 焦段精确过滤段级条目流"}
                  onClick={() => setFilter((f) => ({ ...f, focal: f.focal === focal ? "" : focal }))}
                />
              );
            })}
            <div className="vkpi-prov-note">段描述 + 标题正则提取(6-800mm)· facets 封顶 Top 24</div>
          </div>
        )}
      </ModuleCard>
    );
  };

  // 索引健康(palette 备选):库量 / 缩略图可用率(已加载内,如实标口径)/ 生成时间
  const renderHealth = () => {
    const gate = readyGate();
    const thumbOk = items.filter((it) => it.video.best_thumbnail || it.video.cached_thumbnail_url || it.video.thumbnail_url || it.video.youtube_thumbnail_url).length;
    return (
      <ModuleCard {...cardProps("health", "索引健康", data && String(data.status) === "ready" ? "实时" : undefined)}>
        {gate ?? (
          <div>
            <CoverRow on={(Number(data!.scanned_videos) || 0) > 0} name="深析库" table="vkpi_analysis_cache" value={`${Number(data!.scanned_videos) || 0} 条 ready`} note="derive_method=video_analysis_final_v1" />
            <CoverRow on={(Number(data!.segment_count) || 0) > 0} name="段级索引" table="请求时实时拆解 · 无独立聚合表" value={`${Number(data!.segment_count) || 0} 段`} />
            <CoverRow on={typeof data!.kol_count === "number" && data!.kol_count > 0} name="覆盖 KOL" table="vkpi_kol_pool" value={typeof data!.kol_count === "number" ? `${data!.kol_count} 位` : "计数待接通"} />
            <CoverRow on={thumbOk > 0} name="缩略图可用" table="毒缓存自愈链:本地缓存 → 原始 URL → youtube 派生" value={`${thumbOk}/${items.length} 条(已加载内)`} note="失败占位不作真图 · 三路皆无 = 诚实占位" />
            <div className="vkpi-prov-note">生成于 {generatedLocal}(存 UTC · 按浏览器时区显示)· 缩略图口径 = 已加载条目内,非全库</div>
          </div>
        )}
      </ModuleCard>
    );
  };

  /* ---------- 模块注册表(palette 全量可选;默认简六卡,health 备选) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiC", label: "资产总览带", description: "段级条目 / 已深析视频 / 覆盖 KOL / 当前命中(无时序 · 诚实虚线)", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "segments", label: "段级条目流", description: "三路筛选 + 一行一条 + 全量弹窗 + 详情连续翻", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 6, maxHeight: 26, render: renderSegments },
    { key: "types", label: "段型构成", description: "开头/分镜/产品露出环图 · 点分段过滤条目流", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 5, maxHeight: 16, render: renderTypes },
    { key: "styles", label: "拍法构成", description: "词表七类条形 · 点行 = 拍法精确过滤", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 16, render: renderStyles },
    { key: "openings", label: "开头类型", description: "开头五类条形 · 点行 = 标签词检索", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16, render: renderOpenings },
    { key: "focals", label: "焦段热度", description: "焦段 Top 条形 · 点行 = 焦段精确过滤", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 4, maxHeight: 16, render: renderFocals },
    // ↓ palette 备选(不进默认布局)
    { key: "health", label: "索引健康", description: "库量 / 缩略图可用率 / 生成时间 · 口径如实", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16, render: renderHealth },
  ];

  const detailItem = detailIndex != null ? displayItems[detailIndex] : null;

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 药丸徽 + 实时辉光点 + 刷新 + 编辑布局;
          旧页头长句(方法论/白名单)按「卡面去术语」收进 kpiC SrcChip 口径行 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">创意资产库 · 哪个开头最灵</span>
          {data && String(data.status) === "ready" ? (
            <span className={PH_BADGE}>{(Number(data.segment_count) || 0).toLocaleString()} 段</span>
          ) : null}
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 6px var(--ds-good)" }} />
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
      {error && (
        <div className="mb-3">
          <ErrorCard title="creative-segments 读取失败" text={error} />
        </div>
      )}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 全量列表弹窗:卡面 6 条之外在这里滚(单次全量,封顶如实,零假分页) */}
      {listOpen && (
        <SegmentListModal
          matched={matched}
          loadedCount={displayItems.length}
          capNote={
            matched > items.length
              ? `端点单次封顶 ${FETCH_LIMIT} 段(按播放数降序取前排)—— 长尾未加载,收窄筛选可见;不摆假「载入更多」。`
              : undefined
          }
          onClose={() => setListOpen(false)}
        >
          {displayItems.map((item, i) => (
            <SegmentRowLine key={`${item.segment_id}-${i}`} item={item} index={i} onOpen={gotoSegment} />
          ))}
        </SegmentListModal>
      )}

      {/* 单条详情:‹ #n/N › + ↑↓ 连续翻 + 溯源链 + KOL 档案真跳 */}
      {detailItem && (
        <SegmentDetailModal
          item={detailItem}
          index={detailIndex as number}
          total={displayItems.length}
          onNav={gotoSegment}
          onClose={() => setDetailIndex(null)}
          onOpenKol={detailItem.kol.kol_pool_id != null ? () => jumpKol(detailItem) : undefined}
        />
      )}

      {/* 模块溯源说明弹窗(SrcChip 点开):合并口径行 + 通用链 + 底层样本入口 */}
      {provKey && (
        <CreativeProvModal
          title={PROV_TITLES[provKey] || provKey}
          caliber={mergedRowsRef.current[provKey] || srcOf(provKey).rows}
          onOpenSamples={() => {
            setProvKey(null);
            setListOpen(true);
          }}
          onClose={() => setProvKey(null)}
        />
      )}
    </div>
  );
}
