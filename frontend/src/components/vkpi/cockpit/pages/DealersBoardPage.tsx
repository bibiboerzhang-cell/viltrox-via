import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { ErrorCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { ModuleProvModal } from "./MarketVoicePage.dialogs";
import { MiniDetailModal, MiniListModal } from "./ProjectsBoardPage.dialogs";
import { useEndpoint } from "./ProjectsBoardPage.actions";
import {
  createDealer,
  getDealerLocations,
  listDealers,
  scrapeDealersEnqueue,
  type VkpiDealer,
  type VkpiDealerPin,
} from "../../../../services/vkpi/dealers-api";
import {
  AddDealerForm,
  DealerListBody,
  DealerRowLine,
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
//   vkpi_dealers 本地 0 行 → 全带 pending 诚实空态注明数据在线上库;地区分布条形
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

const STORAGE_KEY = "vkpi-dealers-layout-v1";

// 默认布局(12 列 · 默认简六模块):kpiD(12) → regionD(8)+pendD(4) → mapD(12)
// → rosterD(8)+opsD(4);palette 全量可选(注册表即全集)。
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiD", span: 12 },
  { moduleKey: "regionD", span: 8 },
  { moduleKey: "pendD", span: 4 },
  { moduleKey: "mapD", span: 12 },
  { moduleKey: "rosterD", span: 8 },
  { moduleKey: "opsD", span: 4 },
];

const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

export function DealersBoardPage({ apiToken = "" }: { apiToken?: string } = {}) {
  const token = apiToken || "";

  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  // 采集动作(旧页 runScrape 同构:预检 record_only=true / 真跑成功后重拉)
  const [scrapeBusy, setScrapeBusy] = React.useState(false);
  const [scrapeMsg, setScrapeMsg] = React.useState("");
  const [scrapeErr, setScrapeErr] = React.useState("");

  // 手动添加表单(旧页 handleCreate 同构:必填校验 / 成功清空 / 重拉)
  const [addName, setAddName] = React.useState("");
  const [addAddress, setAddAddress] = React.useState("");
  const [addCity, setAddCity] = React.useState("");
  const [addState, setAddState] = React.useState("");
  const [adding, setAdding] = React.useState(false);
  const [addMsg, setAddMsg] = React.useState("");
  const [addErr, setAddErr] = React.useState("");

  // 行模块弹窗状态(名录 / 待补定位:全量列表 + 单条详情连续翻)
  const [rosterIdx, setRosterIdx] = React.useState<number | null>(null);
  const [rosterListOpen, setRosterListOpen] = React.useState(false);
  const [pendIdx, setPendIdx] = React.useState<number | null>(null);
  const [pendListOpen, setPendListOpen] = React.useState(false);

  /* ---------- 只读端点(逐源独立装载,单源失败不拖累) ---------- */
  const dealersResp = useEndpoint(
    token,
    reloadTick,
    React.useCallback((t: string) => listDealers(t, { limit: 500 }), []),
  );
  const locsResp = useEndpoint(
    token,
    reloadTick,
    React.useCallback((t: string) => getDealerLocations(t), []),
  );

  const dealers = React.useMemo(
    () => (Array.isArray(dealersResp.data?.dealers) ? (dealersResp.data!.dealers as VkpiDealer[]) : null),
    [dealersResp.data],
  );
  const pins = React.useMemo(
    () => (Array.isArray(locsResp.data?.pins) ? (locsResp.data!.pins as VkpiDealerPin[]) : null),
    [locsResp.data],
  );

  /* ---------- KPI 四数(全真;0 行 → 全带诚实空态注明数据在线上库) ---------- */
  const zeroRows = dealers != null && dealers.length === 0;
  const totalNote = dealersResp.error ? `读取失败:${dealersResp.error}` : zeroRows ? ZERO_NOTE : "读取中…";
  const locatedNote = locsResp.error ? `读取失败:${locsResp.error}` : zeroRows ? ZERO_NOTE : "读取中…";
  const total = dealers && !zeroRows ? dealers.length : null;
  const located = pins && !zeroRows ? pins.length : null;
  const stateCount =
    dealers && !zeroRows
      ? new Set(dealers.map((d) => String(d.state || "").trim().toUpperCase()).filter(Boolean)).size
      : null;
  // 表无 country 列;采集管线=美国相机零售商 → 国家按定位点归属计(口径住 SrcChip)
  const countryCount = pins && !zeroRows ? (pins.length > 0 ? 1 : 0) : null;

  // 待补定位:lat/lng 缺失(旧页 pendingGeocode 同口径)
  const pendingGeo = React.useMemo(
    () => (dealers ? dealers.filter((d) => d.lat == null || d.lng == null) : []),
    [dealers],
  );

  // 地区分布:按州 GROUP BY,count 降序 top10;state 空 → 「未标注」桶
  const regionRows = React.useMemo(() => {
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
  }, [dealers]);

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
      setScrapeBusy(true);
      setScrapeMsg("");
      setScrapeErr("");
      try {
        const res = await scrapeDealersEnqueue(token, { limit: 20, record_only: recordOnly });
        setScrapeMsg(scrapeReceiptText(res));
        if (!recordOnly) setReloadTick((tick) => tick + 1);
      } catch (err) {
        setScrapeErr(err instanceof Error ? err.message : "抓取触发失败");
      } finally {
        setScrapeBusy(false);
      }
    },
    [token, scrapeBusy],
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
    setAdding(true);
    setAddMsg("");
    setAddErr("");
    try {
      await createDealer(token, { name, address, city: addCity.trim(), state: addState.trim() });
      setAddMsg(`已添加:${name}`);
      setAddName("");
      setAddAddress("");
      setAddCity("");
      setAddState("");
      setReloadTick((tick) => tick + 1);
    } catch (err) {
      setAddErr(err instanceof Error ? err.message : "添加失败");
    } finally {
      setAdding(false);
    }
  }, [token, adding, addName, addAddress, addCity, addState]);

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
    ];
    return (
      <ModuleCard {...cardProps("kpiD", "指标带", total != null ? `${total} 家` : undefined, extraRows)}>
        {!token ? (
          noTokenCard
        ) : (
          <DealersKpiBand
            total={total}
            totalNote={totalNote}
            located={located}
            locatedNote={locatedNote}
            stateCount={stateCount}
            countryCount={countryCount}
          />
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
        pins={pins || []}
        loading={!pins}
        emptyNote={zeroRows || !dealers ? ZERO_NOTE : "补齐经纬度后自动上图"}
        onOpenSrc={() => setProvKey("mapD")}
      />
    );
  };

  const renderPending = () => (
    <ModuleCard {...cardProps("pendD", "待补定位", pendingGeo.length > 0 ? `${pendingGeo.length}` : undefined)}>
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
    <ModuleCard {...cardProps("rosterD", "经销商名录", dealers && dealers.length > 0 ? `${dealers.length}` : undefined)}>
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
          />
        ))}
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
              onName={setAddName}
              onAddress={setAddAddress}
              onCity={setAddCity}
              onState={setAddState}
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

  /* ---------- 模块注册表(palette 全量可选;默认简;高度贴内容 1 格=22px+14px gap) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiD", label: "指标带", description: "经销商数 / 已定位 / 覆盖州 / 国家数 · 库内 0 行如实空态", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "regionD", label: "地区分布", description: "按州分布条形 · 有数据才画,0 行如实空", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 5, maxHeight: 22, render: renderRegion },
    { key: "pendD", label: "待补定位", description: "缺经纬度的经销商 · 补齐后自动上图", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 12, minHeight: 5, maxHeight: 22, render: renderPending },
    { key: "mapD", label: "经销商地图", description: "定位点上图 · 地图渲染原件整体收编", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 16, minHeight: 10, maxHeight: 30, render: renderMap },
    { key: "rosterD", label: "经销商名录", description: "全部经销商 · 全量列表 + 单条连续翻", category: "实时模块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 5, maxHeight: 26, render: renderRoster },
    { key: "opsD", label: "录入与采集", description: "预检 / 有界抓取(≤20)/ 手动添加", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 12, minHeight: 6, maxHeight: 24, render: renderOps },
  ];

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

      {!token && <div className="mb-3">{noTokenCard}</div>}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 名录全量 + 详情连续翻 */}
      {rosterListOpen && dealers && (
        <MiniListModal title="经销商名录" total={dealers.length} unit="家" onClose={() => setRosterListOpen(false)}>
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
        />
      )}

      {/* 待补定位全量 + 详情连续翻 */}
      {pendListOpen && (
        <MiniListModal title="待补定位" total={pendingGeo.length} unit="家" onClose={() => setPendListOpen(false)}>
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
