import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { ErrorCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { ModuleProvModal } from "./MarketVoicePage.dialogs";
import { MiniDetailModal, MiniListModal } from "./ProjectsBoardPage.dialogs";
import { useEndpoint } from "./ProjectsBoardPage.actions";
import {
  createDealer,
  getDealerCandidateStagingSummary,
  getDealerCoverage,
  getDealerLocations,
  getDealerUsSourceRegistry,
  listDealers,
  publishDealer,
  scrapeDealersEnqueue,
  unpublishDealer,
  updateDealer,
  type VkpiDealer,
  type VkpiDealerPin,
  type VkpiDealerWritePayload,
} from "../../../../services/vkpi/dealers-api";
import { getBoardSeries } from "../../../../services/vkpi/boardSeries-api";
import {
  AddDealerForm,
  DealerActivityExtension,
  DealerCoverageStrip,
  DealerListBody,
  DealerRowLine,
  DealerRecordEditor,
  DealerTrustPipeline,
  DealersKpiBand,
  MODULE_SOURCES,
  PROV_TITLES,
  RegionBars,
  ScrapeControls,
  ZERO_NOTE,
  dealerDetailRows,
  scrapeReceiptText,
} from "./DealersBoardPage.modules";
import { DealerMapEmbed } from "./DealersBoardPage.embeds";
import { DealerRankingsBody, DealerRankingRowLine, dealerRankingDetailRows } from "./DealerRankingsModule";
import { getDealerRankings, type VkpiDealerRankingRow } from "../../../../services/vkpi/dealers-api";
import { formatLocal } from "../../lib/timeLocal";

// Dealers → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage embeds
//   收编手法 + ShopifyBoardPage 同构)。
//   旧页(pages/DealerMapPage)全功能零丢失搬家:
//   · RealMap 地图(pins 上图 / zoom 4 / 加载覆盖层)= DealerMapEmbed 整体零改动
//     收编(地图本体是隔离皮肤对象,本刀零改动;回滚垫=旧页原件保留);
//   · 预检(record-only)+ 有界抓取(≤20)两按钮 + 回执行 = opsD 采集区 token 化
//     重画(payload / 回执字段全同,真跑成功后重拉);
//   · 手动添加表单(名称*/地址*/城市/州 + 幂等 upsert + 成功清空)= opsD 添加区;
//   · 待补 geocode 清单 = pendD「待补定位」模块(升级:face 6 + 全量 + 单条连续翻);
//   · 页头计数(已定位 / 待补)= KPI 带 + 卡头徽接管;介绍文案按门面纪律收进溯源。
//   新增看板模块(全真数据):KPI 带四卡(经销商数 / 已定位 / 覆盖州 / 国家数)——
//   vkpi_dealers 本地 0 行 → 全带 pending 诚实空态注明公开候选待核验;地区分布条形
//   (按州,有数据才画);经销商名录(全量 + 连续翻)。
//   数据源(全真,零编造):
//     GET  /api/admin/vkpi/dealers            —— vkpi_dealers(上限 500 行)
//     GET  /api/admin/vkpi/dealers/locations  —— lat/lng 齐全行的扁平 pin
//   动作(白名单,端点真实返回才更新):
//     POST /api/admin/vkpi/dealers/scrape-enqueue —— 有界抓取(单批 ≤20 服务端硬上限,
//          record_only=true 纯预检零外发)
//     POST /api/admin/vkpi/dealers            —— 手动添加(name+address 幂等 upsert)
// 红线:纯展示 + 白名单动作端点,绝不写 viltrox fit 分 / 不触 rule_v0;颜色全 token
//   零写死色;禁 token 色 opacity 修饰类;卡面零内部术语零介绍文案(口径全进 SrcChip/
//   溯源弹窗);时间绝对时间戳(存 UTC 按浏览器时区);布局只走本机 storageKey,
//   不传 apiToken 给 EditableDashboardBoard(其账户级持久化写死 dashboard_layout_v1
//   键 —— 金样板同注释)。

const STORAGE_KEY = "vkpi-dealers-layout-v2-truth";
const LEGACY_STORAGE_KEYS = ["vkpi-dealers-layout-v1"];
const DEALER_PAGE_SIZE = 100;

// 默认布局(12 列 · 默认简六模块):kpiD(12) → regionD(8)+pendD(4) → mapD(12)
// → rosterD(8)+opsD(4);palette 全量可选(注册表即全集)。
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiD", span: 12 },
  { moduleKey: "trustD", span: 12 },
  { moduleKey: "regionD", span: 8 },
  { moduleKey: "pendD", span: 4 },
  { moduleKey: "mapD", span: 12 },
  // 2026-07-18 体检修:排名榜(全站唯一连锁聚合视图)此前只在 palette、默认
  // 布局不渲染;lite 核验回填 vkpi_dealer_web_verification 后有真数据,入默认布局。
  { moduleKey: "rankD", span: 12 },
  { moduleKey: "rosterD", span: 8 },
  { moduleKey: "opsD", span: 4 },
];

const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

type DealerReadSource = "dealers" | "locations" | "coverage" | "candidateStaging" | "sourceRegistry" | "series" | "rankings";

// Dashboard 跨板块只挂一个模块时，只读该模块真正消费的数据。完整 Dealers
// 页仍拉全部六源；未知嵌入 key 零请求，由 EmbeddedDashboardModule 显示不可见空态。
const EMBEDDED_MODULE_SOURCES: Record<string, readonly DealerReadSource[]> = {
  kpiD: ["dealers", "locations", "coverage", "series"],
  trustD: ["dealers", "coverage", "candidateStaging", "sourceRegistry"],
  regionD: ["dealers", "coverage"],
  pendD: ["dealers"],
  mapD: ["dealers", "locations", "coverage", "candidateStaging", "sourceRegistry"],
  rosterD: ["dealers"],
  rankD: ["rankings"],
  opsD: [],
};

export function DealersBoardPage({ apiToken = "", embeddedModuleKey }: { apiToken?: string; embeddedModuleKey?: string } = {}) {
  const token = apiToken || "";
  const needsSource = React.useCallback(
    (source: DealerReadSource) => !embeddedModuleKey || Boolean(EMBEDDED_MODULE_SOURCES[embeddedModuleKey]?.includes(source)),
    [embeddedModuleKey],
  );

  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);
  const [rosterOffset, setRosterOffset] = React.useState(0);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  // 采集动作(旧页 runScrape 同构:预检 record_only=true / 真跑成功后重拉)
  const [scrapeBusy, setScrapeBusy] = React.useState(false);
  const [scrapeMsg, setScrapeMsg] = React.useState("");
  const [scrapeErr, setScrapeErr] = React.useState("");
  const [scrapePreviewGate, setScrapePreviewGate] = React.useState<{
    importAllowed: boolean;
    qualityStatus: string;
    blockReason: string;
  } | null>(null);

  // 手动添加表单(旧页 handleCreate 同构:必填校验 / 成功清空 / 重拉)
  const [addName, setAddName] = React.useState("");
  const [addAddress, setAddAddress] = React.useState("");
  const [addCity, setAddCity] = React.useState("");
  const [addState, setAddState] = React.useState("");
  const [addPostalCode, setAddPostalCode] = React.useState("");
  const [addPhone, setAddPhone] = React.useState("");
  const [addWebsiteUrl, setAddWebsiteUrl] = React.useState("");
  const [addLat, setAddLat] = React.useState("");
  const [addLng, setAddLng] = React.useState("");
  const [addBrands, setAddBrands] = React.useState<string[]>([]);
  const [addCustomBrand, setAddCustomBrand] = React.useState("");
  const [addPublishAfterCreate, setAddPublishAfterCreate] = React.useState(false);
  const [adding, setAdding] = React.useState(false);
  const [addMsg, setAddMsg] = React.useState("");
  const [addErr, setAddErr] = React.useState("");
  const [recordBusy, setRecordBusy] = React.useState(false);
  const [recordMsg, setRecordMsg] = React.useState("");
  const [recordErr, setRecordErr] = React.useState("");

  // 行模块弹窗状态(名录 / 待补定位:全量列表 + 单条详情连续翻)
  const [rosterIdx, setRosterIdx] = React.useState<number | null>(null);
  const [rosterListOpen, setRosterListOpen] = React.useState(false);
  const [pendIdx, setPendIdx] = React.useState<number | null>(null);
  const [pendListOpen, setPendListOpen] = React.useState(false);
  const [rankRow, setRankRow] = React.useState<VkpiDealerRankingRow | null>(null);
  const [rankListOpen, setRankListOpen] = React.useState(false);

  React.useEffect(() => {
    setRecordMsg("");
    setRecordErr("");
  }, [rosterIdx, pendIdx]);

  /* ---------- 只读端点(逐源独立装载,单源失败不拖累) ---------- */
  const dealersResp = useEndpoint(
    needsSource("dealers") ? token : "",
    reloadTick,
    React.useCallback((t: string) => listDealers(t, { limit: DEALER_PAGE_SIZE, offset: rosterOffset }), [rosterOffset]),
  );
  const locsResp = useEndpoint(
    needsSource("locations") ? token : "",
    reloadTick,
    React.useCallback((t: string) => getDealerLocations(t, { publishedOnly: true }), []),
  );
  const coverageResp = useEndpoint(
    needsSource("coverage") ? token : "",
    reloadTick,
    React.useCallback((t: string) => getDealerCoverage(t), []),
  );
  const candidateStagingResp = useEndpoint(
    needsSource("candidateStaging") ? token : "",
    reloadTick,
    React.useCallback((t: string) => getDealerCandidateStagingSummary(t), []),
  );
  const usSourceRegistryResp = useEndpoint(
    needsSource("sourceRegistry") ? token : "",
    reloadTick,
    React.useCallback((t: string) => getDealerUsSourceRegistry(t), []),
  );
  // KPI 卡趋势线(挂账迸发①):board-series 按日真序列;vkpi_dealers 0 行 → 端点
  // 诚实 empty(绝不 0 填平线)→ 卡面照旧 spempty 虚线;有数据流入后自动点亮
  const kpiSeriesResp = useEndpoint(
    needsSource("series") ? token : "",
    reloadTick,
    React.useCallback((t: string) => getBoardSeries(t, { board: "dealers" }), []),
  );

  // 官网核验排名(迁移 271 最新回执/店;未迁移诚实 migration_pending)
  const rankingsResp = useEndpoint(
    needsSource("rankings") ? token : "",
    reloadTick,
    React.useCallback((t: string) => getDealerRankings(t), []),
  );

  const dealers = React.useMemo(
    () => (Array.isArray(dealersResp.data?.dealers) ? (dealersResp.data!.dealers as VkpiDealer[]) : null),
    [dealersResp.data],
  );
  const pins = React.useMemo(
    () => (Array.isArray(locsResp.data?.pins) ? (locsResp.data!.pins as VkpiDealerPin[]) : null),
    [locsResp.data],
  );
  const rosterTotalCount = Number.isFinite(Number(dealersResp.data?.total_count))
    ? Number(dealersResp.data?.total_count)
    : dealers?.length ?? null;
  const rosterHasMore = Boolean(
    dealersResp.data?.page?.has_more
      ?? (rosterTotalCount != null && rosterOffset + (dealers?.length || 0) < rosterTotalCount),
  );
  const rosterPageNumber = Math.floor(rosterOffset / DEALER_PAGE_SIZE) + 1;

  // KPI 必须来自全表 coverage 端点。名录端点单次最多 500 行，不能在未来扩容后
  // 被误当成全表分母；country 也必须读真实列，不能因“当前有 pin”硬推断为 1。
  const coverage = coverageResp.data ?? null;
  const coverageReady = Boolean(coverage && coverage.status !== "migration_pending");
  const coverageHasRows = Boolean(coverageReady && Number(coverage!.total) > 0);
  /* ---------- KPI 四数(全真;0 行 → 全带诚实空态注明候选待核验) ---------- */
  const zeroRows = coverageReady ? Number(coverage?.total || 0) === 0 : dealers != null && dealers.length === 0;
  const totalNote = dealersResp.error ? `读取失败:${dealersResp.error}` : zeroRows ? ZERO_NOTE : "读取中…";
  const locatedNote = locsResp.error ? `读取失败:${locsResp.error}` : zeroRows ? ZERO_NOTE : "读取中…";
  const total = coverageHasRows ? Number(coverage!.total) : dealers && !zeroRows ? dealers.length : null;
  const publishedMapPins = Number(coverage?.published_map_pins);
  const located = zeroRows
    ? null
    : Number.isFinite(publishedMapPins)
      ? publishedMapPins
      : pins ? pins.length : null;
  const stateCount = coverageHasRows
    ? Number(coverage!.states)
    : dealers && !zeroRows
      ? new Set(dealers.map((d) => String(d.state || "").trim().toUpperCase()).filter(Boolean)).size
      : null;
  const countryCount = coverageHasRows
    ? Number(coverage!.countries)
    : dealers && !zeroRows
      ? new Set(dealers.map((d) => String(d.country || "").trim().toUpperCase()).filter(Boolean)).size
      : null;

  // 待补定位:lat/lng 缺失(旧页 pendingGeocode 同口径)
  const pendingGeo = React.useMemo(
    () => (dealers ? dealers.filter((d) => d.lat == null || d.lng == null) : []),
    [dealers],
  );

  // 地区分布:按州 GROUP BY,count 降序 top10;state 空 → 「未标注」桶
  const regionRows = React.useMemo(() => {
    const aggregateCounts = coverage?.us_jurisdiction_matrix?.dealer_counts_by_state_dc;
    if (aggregateCounts) {
      return Object.entries(aggregateCounts)
        .map(([label, count]) => ({ label, count: Number(count || 0) }))
        .filter((row) => row.count > 0)
        .sort((a, b) => b.count - a.count)
        .slice(0, 10);
    }
    if (!dealers || dealers.length === 0) return [];
    const counts = new Map<string, number>();
    dealers.forEach((d) => {
      const key = String(d.state || "").trim().toUpperCase() || "未标注";
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return [...counts.entries()]
      .map(([label, count]) => ({ label, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 10);
  }, [dealers, coverage]);

  // 数据新鲜度:最新入库时间(绝对时间戳,进 SrcChip extraRows)
  const latestCreated = React.useMemo(() => {
    if (!dealers || dealers.length === 0) return "";
    const latest = dealers.reduce<string>((m, d) => (d.created_at && d.created_at > m ? d.created_at : m), "");
    return latest ? formatLocal(latest) : "";
  }, [dealers]);

  /* ---------- 采集(端点真实返回才出回执;真跑成功后重拉) ---------- */
  const runScrape = React.useCallback(
    async (recordOnly: boolean) => {
      if (!token || scrapeBusy) return;
      if (!recordOnly && scrapePreviewGate?.importAllowed !== true) {
        setScrapeErr("导入已阻断：须先完成预检，且后端明确返回 import_allowed=true。");
        return;
      }
      setScrapeBusy(true);
      setScrapeMsg("");
      setScrapeErr("");
      try {
        const res = await scrapeDealersEnqueue(token, { limit: 20, record_only: recordOnly });
        if (recordOnly) {
          setScrapePreviewGate({
            importAllowed: res.import_allowed === true,
            qualityStatus: String(res.quality_status || "未返回"),
            blockReason: String(res.import_block_reason || ""),
          });
        }
        setScrapeMsg(scrapeReceiptText(res));
        if (!recordOnly) {
          setScrapePreviewGate(null);
          setReloadTick((tick) => tick + 1);
        }
      } catch (err) {
        if (recordOnly) setScrapePreviewGate(null);
        setScrapeErr(err instanceof Error ? err.message : "抓取触发失败");
      } finally {
        setScrapeBusy(false);
      }
    },
    [token, scrapeBusy, scrapePreviewGate],
  );

  /* ---------- 手动添加(幂等 upsert;成功清空 + 重拉,失败原因不吞) ---------- */
  const handleCreate = React.useCallback(async () => {
    if (!token || adding) return;
    const name = addName.trim();
    const address = addAddress.trim();
    if (!name || !address) {
      setAddErr("名称 + 地址必填");
      return;
    }
    const hasLat = Boolean(addLat.trim());
    const hasLng = Boolean(addLng.trim());
    if (hasLat !== hasLng || (hasLat && (!Number.isFinite(Number(addLat)) || !Number.isFinite(Number(addLng))))) {
      setAddErr("经纬度必须成对填写且为数字");
      return;
    }
    setAdding(true);
    setAddMsg("");
    setAddErr("");
    const customBrands = addCustomBrand
      .split(/[,\n]/)
      .map((value) => value.trim().toUpperCase().replace(/[^A-Z0-9]+/g, "_"))
      .filter(Boolean);
    let createdId: string | number | null = null;
    try {
      const created = await createDealer(token, {
        name,
        address,
        city: addCity.trim() || undefined,
        state: addState.trim() || undefined,
        postal_code: addPostalCode.trim() || undefined,
        phone: addPhone.trim() || undefined,
        website_url: addWebsiteUrl.trim() || undefined,
        ...(hasLat ? { lat: Number(addLat), lng: Number(addLng) } : {}),
        brands: [...new Set([...addBrands, ...customBrands])],
      });
      createdId = created.id;
      if (addPublishAfterCreate) {
        if (createdId == null) throw new Error("草稿已创建，但后端未返回 id，无法发布到地图");
        await publishDealer(token, createdId);
      }
      setAddMsg(addPublishAfterCreate ? `已添加并发布到地图:${name}` : `已添加草稿:${name}`);
      setAddName("");
      setAddAddress("");
      setAddCity("");
      setAddState("");
      setAddPostalCode("");
      setAddPhone("");
      setAddWebsiteUrl("");
      setAddLat("");
      setAddLng("");
      setAddBrands([]);
      setAddCustomBrand("");
      setAddPublishAfterCreate(false);
      setRosterOffset(0);
      setReloadTick((tick) => tick + 1);
    } catch (err) {
      setAddErr(`${createdId != null ? "草稿已创建；" : ""}${err instanceof Error ? err.message : "添加失败"}`);
      if (createdId != null) setReloadTick((tick) => tick + 1);
    } finally {
      setAdding(false);
    }
  }, [token, adding, addName, addAddress, addCity, addState, addPostalCode, addPhone, addWebsiteUrl, addLat, addLng, addBrands, addCustomBrand, addPublishAfterCreate]);

  const saveDealer = React.useCallback(async (dealerId: string | number, payload: VkpiDealerWritePayload) => {
    if (!token || recordBusy) return;
    setRecordBusy(true);
    setRecordMsg("");
    setRecordErr("");
    try {
      await updateDealer(token, dealerId, payload);
      setRecordMsg("修改已保存");
      setReloadTick((tick) => tick + 1);
    } catch (error) {
      setRecordErr(error instanceof Error ? error.message : "保存失败");
    } finally {
      setRecordBusy(false);
    }
  }, [token, recordBusy]);

  const setDealerPublication = React.useCallback(async (dealerId: string | number, publish: boolean) => {
    if (!token || recordBusy) return;
    setRecordBusy(true);
    setRecordMsg("");
    setRecordErr("");
    try {
      if (publish) await publishDealer(token, dealerId);
      else await unpublishDealer(token, dealerId);
      setRecordMsg(publish ? "已发布到本地地图" : "已从本地地图撤下，记录仍保留");
      setReloadTick((tick) => tick + 1);
    } catch (error) {
      setRecordErr(error instanceof Error ? error.message : publish ? "发布失败" : "撤下失败");
    } finally {
      setRecordBusy(false);
    }
  }, [token, recordBusy]);

  /* ---------- 闸(金样板同构) ---------- */
  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载经销商数据。
    </PendingCard>
  );
  const opsGate = (value: unknown, error: string, name: string): React.ReactNode | null => {
    if (!token) return noTokenCard;
    if (error) return <ErrorCard title={`${name} 读取失败`} text={error} />;
    if (!value) return <LoadingLine text="读取中…" />;
    return null;
  };

  /* ---------- 卡头 props(SrcChip hover 卡 + 点击溯源弹窗;动态口径进 extraRows) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const mergedRowsRef = React.useRef<Record<string, Array<[string, string]>>>({});
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    mergedRowsRef.current[key] = rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows, onOpenSrc: () => setProvKey(key) };
  };

  /* ---------- 模块 body ---------- */

  const renderKpiBand = () => {
    const extraRows: Array<[string, string]> = [
      ...(latestCreated ? ([["最新入库", `${latestCreated}(UTC 存 · 按浏览器时区显示)`]] as Array<[string, string]>) : []),
      ...(dealersResp.error ? ([["名录源", `读取失败:${dealersResp.error}`]] as Array<[string, string]>) : []),
      ...(locsResp.error ? ([["定位源", `读取失败:${locsResp.error}`]] as Array<[string, string]>) : []),
      ...(coverageResp.error ? ([["覆盖质量", `读取失败:${coverageResp.error}`]] as Array<[string, string]>) : []),
      [
        "趋势线",
        "board-series?board=dealers 新入库经销商/日真序列(30 天窗;经销商数卡,关联指标不挂环比药丸);全表 0 行 → 端点诚实空 → 卡面虚线如实,绝不摆 0 填平线",
      ],
      ...(kpiSeriesResp.error
        ? ([["趋势线源", `读取失败:${kpiSeriesResp.error}(卡面虚线如实,不编时序)`]] as Array<[string, string]>)
        : []),
    ];
    return (
      <ModuleCard {...cardProps("kpiD", "指标带", total != null ? `${total} 家` : undefined, extraRows)}>
        {!token ? (
          noTokenCard
        ) : (
          <>
            <DealersKpiBand
              total={total}
              totalNote={totalNote}
              located={located}
              locatedNote={locatedNote}
              stateCount={stateCount}
              countryCount={countryCount}
              boardSeries={kpiSeriesResp.data ?? null}
            />
            {coverageResp.error ? (
              <div className="mt-3"><ErrorCard title="覆盖质量读取失败" text={coverageResp.error} /></div>
            ) : (
              <DealerCoverageStrip coverage={coverageResp.data ?? null} />
            )}
          </>
        )}
      </ModuleCard>
    );
  };

  const renderRegion = () => (
    <ModuleCard {...cardProps("regionD", "地区分布", regionRows.length > 0 ? `${regionRows.length} 区` : undefined)}>
      {opsGate(dealers, dealersResp.error, "dealers") ??
        (zeroRows ? (
          <PendingCard>
            <b>0 行</b> —— {ZERO_NOTE};有数据才画分布,不编条形。
          </PendingCard>
        ) : (
          <RegionBars rows={regionRows} />
        ))}
    </ModuleCard>
  );

  const renderMap = () => {
    if (!token) return <ModuleCard {...cardProps("mapD", "经销商地图")}>{noTokenCard}</ModuleCard>;
    if (locsResp.error) {
      return (
        <ModuleCard {...cardProps("mapD", "经销商地图")}>
          <ErrorCard title="定位读取失败" text={locsResp.error} />
        </ModuleCard>
      );
    }
    return (
      <DealerMapEmbed
        token={token}
        pins={pins || []}
        loading={!pins}
        emptyNote={
          zeroRows || !dealers
            ? ZERO_NOTE
            : "地图只显示已显式发布且坐标齐全的记录；公开来源是独立证据字段；当前筛选下无点"
        }
        candidateCount={candidateStagingResp.data ? Number(candidateStagingResp.data.total || 0) : null}
        registry={usSourceRegistryResp.data ?? null}
        entityMatrix={coverage?.us_jurisdiction_matrix ?? null}
        onOpenSrc={() => setProvKey("mapD")}
      />
    );
  };

  const renderTrust = () => (
    <ModuleCard
      {...cardProps(
        "trustD",
        "美国经销商来源与实体覆盖",
        candidateStagingResp.data ? `${Number(candidateStagingResp.data.total || 0)} 候选` : undefined,
        [
          ["业务行", total == null ? "读取中 / 不可用" : `${total} 行`],
          ["候选队列", candidateStagingResp.data?.status || (candidateStagingResp.error ? "读取失败" : "读取中")],
          ["发现来源", usSourceRegistryResp.data?.coverage_claim || (usSourceRegistryResp.error ? "读取失败" : "读取中")],
        ],
      )}
    >
      {!token ? noTokenCard : (
        <DealerTrustPipeline
          businessTotal={total}
          verifiedBusinessTotal={coverageReady ? Number(coverage?.public_listing_verified || 0) : null}
          businessJurisdictionMatrix={coverage?.us_jurisdiction_matrix ?? null}
          staging={candidateStagingResp.data ?? null}
          registry={usSourceRegistryResp.data ?? null}
          stagingError={candidateStagingResp.error}
          registryError={usSourceRegistryResp.error}
        />
      )}
    </ModuleCard>
  );

  const renderPending = () => (
    <ModuleCard {...cardProps("pendD", "当前页待补定位", pendingGeo.length > 0 ? `${pendingGeo.length}` : undefined)}>
      {opsGate(dealers, dealersResp.error, "dealers") ??
        (zeroRows ? (
          <PendingCard>
            <b>0 行</b> —— {ZERO_NOTE}。
          </PendingCard>
        ) : (
          <DealerListBody
            items={pendingGeo}
            emptyText="全部已定位。"
            onOpen={(i) => setPendIdx(i)}
            onOpenAll={() => setPendListOpen(true)}
          />
        ))}
    </ModuleCard>
  );

  const renderRoster = () => (
    <ModuleCard {...cardProps("rosterD", "经销商名录", rosterTotalCount != null ? `${rosterTotalCount}` : undefined)}>
      {opsGate(dealers, dealersResp.error, "dealers") ??
        (zeroRows ? (
          <PendingCard>
            <b>0 行</b> —— {ZERO_NOTE}。
          </PendingCard>
        ) : (
          <DealerListBody
            items={dealers || []}
            emptyText="0 家 · 暂无经销商(如实)。"
            onOpen={(i) => setRosterIdx(i)}
            onOpenAll={() => setRosterListOpen(true)}
            totalCount={rosterTotalCount}
          />
        ))}
      {dealers && !zeroRows ? (
        <div className="mt-2 flex items-center justify-between gap-2 border-t border-line pt-2 text-[9.5px] text-muted" data-testid="dealer-server-pagination">
          <span>服务端分页 · 第 {rosterPageNumber} 页 · 当前 {dealers.length} 家{rosterTotalCount != null ? ` / 总计 ${rosterTotalCount} 家` : ""}</span>
          <span className="flex gap-1.5">
            <button type="button" disabled={rosterOffset <= 0} onClick={() => { setRosterIdx(null); setRosterListOpen(false); setRosterOffset((value) => Math.max(0, value - DEALER_PAGE_SIZE)); }} className="rounded-[6px] border border-line px-2 py-1 disabled:cursor-default">上一页</button>
            <button type="button" disabled={!rosterHasMore} onClick={() => { setRosterIdx(null); setRosterListOpen(false); setRosterOffset((value) => value + DEALER_PAGE_SIZE); }} className="rounded-[6px] border border-line px-2 py-1 disabled:cursor-default">下一页</button>
          </span>
        </div>
      ) : null}
      <div className="mt-3 border-t border-line pt-3">
        <div className="mb-2 text-[10px] font-semibold text-ink">Dealer 活动扩展 · 当前页</div>
        <DealerActivityExtension dealers={dealers} />
      </div>
    </ModuleCard>
  );

  const renderOps = () => (
    <ModuleCard {...cardProps("opsD", "录入与采集")}>
      {!token ? (
        noTokenCard
      ) : (
        <div className="flex flex-col gap-3">
          <ScrapeControls
            busy={scrapeBusy}
            disabled={!token}
            importAllowed={scrapePreviewGate?.importAllowed ?? null}
            qualityStatus={scrapePreviewGate?.qualityStatus ?? ""}
            blockReason={scrapePreviewGate?.blockReason ?? ""}
            msg={scrapeMsg}
            err={scrapeErr}
            onPreview={() => void runScrape(true)}
            onRun={() => void runScrape(false)}
          />
          <div className="border-t border-line pt-3">
            <AddDealerForm
              name={addName}
              address={addAddress}
              city={addCity}
              state={addState}
              postalCode={addPostalCode}
              phone={addPhone}
              websiteUrl={addWebsiteUrl}
              lat={addLat}
              lng={addLng}
              brands={addBrands}
              customBrand={addCustomBrand}
              publishAfterCreate={addPublishAfterCreate}
              onName={setAddName}
              onAddress={setAddAddress}
              onCity={setAddCity}
              onState={setAddState}
              onPostalCode={setAddPostalCode}
              onPhone={setAddPhone}
              onWebsiteUrl={setAddWebsiteUrl}
              onLat={setAddLat}
              onLng={setAddLng}
              onBrands={setAddBrands}
              onCustomBrand={setAddCustomBrand}
              onPublishAfterCreate={setAddPublishAfterCreate}
              adding={adding}
              disabled={!token}
              msg={addMsg}
              err={addErr}
              onSubmit={() => void handleCreate()}
            />
          </div>
        </div>
      )}
    </ModuleCard>
  );

  const renderRankings = () => (
    <ModuleCard
      {...cardProps(
        "rankD",
        "排名与知名度",
        rankingsResp.data?.status === "ready" ? `${rankingsResp.data.verified_count} 家` : undefined,
        rankingsResp.data?.generated_at
          ? [["生成时间", `${formatLocal(rankingsResp.data.generated_at)}(UTC 存 · 按浏览器时区显示)`]]
          : undefined,
      )}
    >
      {!token ? (
        noTokenCard
      ) : (
        <DealerRankingsBody
          data={rankingsResp.data ?? null}
          error={rankingsResp.error}
          onOpen={(row) => setRankRow(row)}
          onOpenAll={() => setRankListOpen(true)}
        />
      )}
    </ModuleCard>
  );

  /* ---------- 模块注册表(palette 全量可选;默认简;高度贴内容 1 格=22px+14px gap) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiD", label: "指标带", description: "经销商数 / 证据合格上图 / 覆盖州 / 国家数 · 库内 0 行如实空态", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "trustD", label: "美国来源与实体覆盖", description: "来源辖区 / 候选待审 / 已入库门店实体严格分口径", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 10, minHeight: 7, maxHeight: 22, render: renderTrust },
    { key: "regionD", label: "地区分布", description: "按州分布条形 · 有数据才画,0 行如实空", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 5, maxHeight: 22, render: renderRegion },
    { key: "pendD", label: "待补定位", description: "当前服务端分页中缺经纬度的记录 · 补齐后仍须显式发布", category: "数据库模块", defaultSpan: 4, minSpan: 3, defaultHeight: 12, minHeight: 5, maxHeight: 22, render: renderPending },
    { key: "mapD", label: "经销商地图", description: "定位点上图 · 地图渲染原件整体收编", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 16, minHeight: 10, maxHeight: 30, render: renderMap },
    { key: "rosterD", label: "经销商名录", description: "服务端分页（每页 100） · 编辑 / 发布 + 当前页活动三态", category: "数据库模块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 5, maxHeight: 26, render: renderRoster },
    { key: "opsD", label: "录入与采集", description: "预检 / 有界抓取(≤20)/ 手动添加草稿 / 可选发布", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 12, minHeight: 6, maxHeight: 24, render: renderOps },
    { key: "rankD", label: "排名与知名度", description: "官网核验回执排名 · 体量档 / 知名度分 / Viltrox 在售 · 未核验如实空态", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 14, minHeight: 7, maxHeight: 26, render: renderRankings },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="Dealers" />;
  }

  const rosterItem = rosterIdx != null && dealers ? dealers[rosterIdx] ?? null : null;
  const pendItem = pendIdx != null ? pendingGeo[pendIdx] ?? null : null;

  const locatedPill = (d: VkpiDealer) =>
    d.lat != null && d.lng != null ? (
      <span className="flex-none rounded-[5px] border border-good bg-good-soft px-1 py-px text-[8px] font-bold text-good">已定位</span>
    ) : (
      <span className="flex-none rounded-[5px] border border-warn bg-warn-soft px-1 py-px text-[8px] font-bold text-warn">待定位</span>
    );

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 家数徽 + 实时辉光点 + 刷新 + 编辑布局 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">经销商</span>
          {total != null && total > 0 && <span className={PH_BADGE}>{total} 家</span>}
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            数据库快照
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

      {!token && <div className="mb-3">{noTokenCard}</div>}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard
        modules={modules}
        defaultLayout={DEFAULT_LAYOUT}
        editing={editing}
        storageKey={STORAGE_KEY}
        legacyStorageKeys={LEGACY_STORAGE_KEYS}
        requiredDefaultModuleKeys={["trustD"]}
      />

      {/* 名录全量 + 详情连续翻 */}
      {rosterListOpen && dealers && (
        <MiniListModal title={`经销商名录 · 第 ${rosterPageNumber} 页`} total={dealers.length} unit="家" onClose={() => setRosterListOpen(false)}>
          {dealers.map((item, i) => (
            <DealerRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={(idx) => setRosterIdx(idx)} />
          ))}
        </MiniListModal>
      )}
      {rosterItem && dealers && (
        <MiniDetailModal
          title={String(rosterItem.name || `经销商 #${rosterItem.id}`)}
          pill={locatedPill(rosterItem)}
          rows={dealerDetailRows(rosterItem)}
          index={rosterIdx as number}
          total={dealers.length}
          onNav={(i) => setRosterIdx(Math.max(0, Math.min(dealers.length - 1, i)))}
          onClose={() => setRosterIdx(null)}
          footer={
            <DealerRecordEditor
              dealer={rosterItem}
              busy={recordBusy}
              message={recordMsg}
              error={recordErr}
              onSave={(payload) => void saveDealer(rosterItem.id, payload)}
              onPublish={() => void setDealerPublication(rosterItem.id, true)}
              onUnpublish={() => void setDealerPublication(rosterItem.id, false)}
            />
          }
        />
      )}

      {/* 待补定位全量 + 详情连续翻 */}
      {pendListOpen && (
        <MiniListModal title={`当前页待补定位 · 第 ${rosterPageNumber} 页`} total={pendingGeo.length} unit="家" onClose={() => setPendListOpen(false)}>
          {pendingGeo.map((item, i) => (
            <DealerRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={(idx) => setPendIdx(idx)} />
          ))}
        </MiniListModal>
      )}
      {pendItem && (
        <MiniDetailModal
          title={String(pendItem.name || `经销商 #${pendItem.id}`)}
          pill={locatedPill(pendItem)}
          rows={dealerDetailRows(pendItem)}
          index={pendIdx as number}
          total={pendingGeo.length}
          onNav={(i) => setPendIdx(Math.max(0, Math.min(pendingGeo.length - 1, i)))}
          onClose={() => setPendIdx(null)}
          footer={
            <DealerRecordEditor
              dealer={pendItem}
              busy={recordBusy}
              message={recordMsg}
              error={recordErr}
              onSave={(payload) => void saveDealer(pendItem.id, payload)}
              onPublish={() => void setDealerPublication(pendItem.id, true)}
              onUnpublish={() => void setDealerPublication(pendItem.id, false)}
            />
          }
        />
      )}

      {rankListOpen && rankingsResp.data && (
        <MiniListModal
          title="排名与知名度 · 全量"
          total={rankingsResp.data.rankings.length}
          unit="家"
          onClose={() => setRankListOpen(false)}
        >
          {rankingsResp.data.rankings.map((row) => (
            <DealerRankingRowLine key={String(row.dealer_id)} row={row} onOpen={(r) => setRankRow(r)} />
          ))}
        </MiniListModal>
      )}
      {rankRow && (
        <MiniDetailModal
          title={String(rankRow.name || `经销商 #${rankRow.dealer_id}`)}
          rows={dealerRankingDetailRows(rankRow)}
          index={0}
          total={1}
          onNav={() => undefined}
          onClose={() => setRankRow(null)}
        />
      )}

      {/* 模块溯源弹窗(SrcChip 点击) */}
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

export default DealersBoardPage;
