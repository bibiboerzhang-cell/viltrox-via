import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { ErrorCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { ModuleProvModal } from "./MarketVoicePage.dialogs";
import { MiniDetailModal, MiniListModal } from "./ProjectsBoardPage.dialogs";
import { AttributionBody, AttributionRowLine, confidencePill } from "./ProjectsBoardPage.charts";
import { useEndpoint } from "./ProjectsBoardPage.actions";
import {
  centsToUsd,
  fmtUsd2,
  listSalesAttributions,
  type Row,
} from "../../../../services/vkpi/projectsBoard-api";
import { asUsd, getShopifyGmv } from "../../../../services/vkpi/shopifyBoard-api";
import {
  getGoaffproStatus,
  getGoaffproSummary,
  syncGoaffproMetrics,
  type GoaffproSummaryRow,
} from "../../../../services/vkpi/goaffpro-api";
import { getShopifyStatus } from "../../../../domains/attribution";
import {
  GoaffStatusBody,
  MODULE_SOURCES,
  PROV_TITLES,
  ShopifyKpiBand,
  TrackBody,
  TrackRowLine,
  TrackSearchBar,
  trackDetailRows,
} from "./ShopifyBoardPage.modules";
import { ConnectEmbed, LegacyCredsEmbed } from "./ShopifyBoardPage.embeds";
import { formatLocal } from "../../lib/timeLocal";

// Shopify → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage embeds
//   收编 + ProjectsBoardPage 金额守卫 1:1 同构)。
//   旧两页全功能零丢失搬家:
//   · 连接向导(状态/环境变量/Webhook 注册/测试同步/同步历史)= ConnectEmbed 整页
//     零改动内嵌(cockpit/ShopifyConnectPage,回滚垫=旧页原件);
//   · 自建凭据表单(加密保存 + 状态明细,旧·待退役折叠块)= LegacyCredsEmbed
//     (ShopifyHubPage.Parts.LegacyShopifyConnect 零改动,容器状态原样搬家);
//   · 联盟归因汇总(搜索/同步刷新/totals/行表 + partial ⚠)= trackS 模块 token 化重画
//     (全量 + 单条连续翻;回滚垫=旧 ShopifyHubPage TrackRegion);
//   · 联盟连接只读状态 + 「配置已迁设置」口径 = goaffS 模块(masked-only);
//   · 短链生成器保持退役(RETIRE_SELF_LINK 口径不变,不复活)。
//   新增看板模块(全真数据):KPI 带四卡(归因收入对账净额 / 归因明细行数 / 联盟推广
//   GMV / 直连订单)—— 有真数才真值,未配置的源 pending 诚实卡注明缺配置;归因明细
//   模块(ProjectsBoardPage 行件复用,金额美分→美元)。
//   数据源(全真,零编造):
//     GET /api/admin/vkpi/shopify/gmv        —— 对账 GMV(订单台账否则归因净额)
//     GET /api/admin/vkpi/attribution        —— vkpi_sales_attributions(美分→美元带单测)
//     GET /api/admin/vkpi/goaffpro/summary   —— 联盟归因缓存(gmv_usd 已美元零二次换算)
//     GET /api/admin/vkpi/goaffpro/creds     —— 联盟连接状态(masked-only)
//     GET /api/marketing/shopify/status      —— 店铺直连状态 + vkpi_shopify_sync_runs
//   动作(白名单,端点真实返回才更新):POST goaffpro/sync-metrics 同步指标 /
//   POST marketing/shopify/sync 测试同步(嵌在 ConnectEmbed 内,旧逻辑) /
//   POST shopify/creds 加密保存(嵌在 LegacyCredsEmbed 内,旧逻辑)。
// 红线:纯展示 + 白名单动作端点,绝不写 viltrox fit 分 / 不触 rule_v0;颜色全 token
//   零写死色;禁 opacity 修饰类;卡面零内部术语(口径全进 SrcChip/溯源弹窗);时间绝对
//   时间戳(存 UTC 按浏览器时区);金额:*_cents 走 centsToUsd 唯一换算点(历史 100 倍
//   缺陷带单测)、*_usd 走 asUsd 直读零二次换算(双向守卫);布局只走本机 storageKey,
//   不传 apiToken 给 EditableDashboardBoard(其账户级持久化写死 dashboard_layout_v1
//   键 —— 金样板同注释)。

const STORAGE_KEY = "vkpi-shopify-layout-v1";

// 默认布局(12 列 · 默认简):kpiS(12) → trackS(8)+goaffS(4) → attrS(12) → connS(12);
// palette 备选:credsS(旧·自建凭据)
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiS", span: 12 },
  { moduleKey: "trackS", span: 8 },
  { moduleKey: "goaffS", span: 4 },
  { moduleKey: "attrS", span: 12 },
  { moduleKey: "connS", span: 12 },
];

const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

export function ShopifyBoardPage({ apiToken = "", embeddedModuleKey }: { apiToken?: string; embeddedModuleKey?: string } = {}) {
  const token = apiToken || "";

  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  // 联盟归因搜索(draft=输入框;committed=已提交查询词,变更即重拉)
  const [searchDraft, setSearchDraft] = React.useState("");
  const [committedSearch, setCommittedSearch] = React.useState("");
  const [syncing, setSyncing] = React.useState(false);
  const [syncErr, setSyncErr] = React.useState("");

  // 行模块弹窗状态(全量列表 / 单条详情连续翻)
  const [trackIdx, setTrackIdx] = React.useState<number | null>(null);
  const [trackListOpen, setTrackListOpen] = React.useState(false);
  const [attrIdx, setAttrIdx] = React.useState<number | null>(null);
  const [attrListOpen, setAttrListOpen] = React.useState(false);

  /* ---------- 只读端点(逐源独立装载,单源失败不拖累) ---------- */
  const gmvResp = useEndpoint(
    token,
    reloadTick,
    React.useCallback((t: string) => getShopifyGmv(t), []),
  );
  const attrResp = useEndpoint(
    token,
    reloadTick,
    React.useCallback((t: string) => listSalesAttributions(t), []),
  );
  const statusResp = useEndpoint(
    token,
    reloadTick,
    React.useCallback((t: string) => getShopifyStatus(t, 10), []),
  );
  const goaffStatusResp = useEndpoint(
    token,
    reloadTick,
    React.useCallback((t: string) => getGoaffproStatus(t), []),
  );
  const trackResp = useEndpoint(
    token,
    reloadTick,
    React.useCallback((t: string) => getGoaffproSummary(t, { limit: 200, search: committedSearch }), [committedSearch]),
  );

  const attributions = React.useMemo(
    () => (Array.isArray(attrResp.data?.attributions) ? (attrResp.data!.attributions as Row[]) : null),
    [attrResp.data],
  );
  const trackItems = React.useMemo(
    () => (Array.isArray(trackResp.data?.items) ? (trackResp.data!.items as GoaffproSummaryRow[]) : null),
    [trackResp.data],
  );

  /* ---------- KPI 四数(全真;源失败/未配置 = pending 带原因,绝不编数) ---------- */
  const srcNote = (error: string) => (error ? `读取失败:${error}` : "读取中…");

  // ① 归因收入:对账净额(gmv_cents 美分 → centsToUsd 唯一换算点)
  const revenueUsd = gmvResp.data ? centsToUsd(gmvResp.data.gmv_cents ?? 0) : null;
  // ② 归因明细:vkpi_sales_attributions 行数
  const attrCount = attributions ? attributions.length : null;
  // ③ 联盟推广:token 未配置 → pending 注明缺配置;已配置 → totals.gmv_usd(已美元)
  const goaffConnected = Boolean(
    goaffStatusResp.data && (goaffStatusResp.data.status === "connected" || goaffStatusResp.data.access_token_configured),
  );
  const goaffGmvUsd = goaffConnected && trackResp.data ? (asUsd(trackResp.data.totals?.gmv_usd) ?? 0) : null;
  const goaffNote = goaffStatusResp.error
    ? `读取失败:${goaffStatusResp.error}`
    : goaffStatusResp.data && !goaffConnected
      ? "未配置 · 设置 → 联盟营销"
      : srcNote(trackResp.error);
  // ④ 直连订单:店铺凭据未配置 → pending 注明缺配置;已配置 → 订单台账 GMV(美分→美元)
  const directConfigured = statusResp.data?.provider_status === "configured";
  const directUsd = directConfigured && gmvResp.data ? (centsToUsd(gmvResp.data.order_ledger_gmv_cents ?? 0) ?? 0) : null;
  const directNote = statusResp.error
    ? `读取失败:${statusResp.error}`
    : statusResp.data && !directConfigured
      ? "未配置店铺凭据 · 见连接向导"
      : srcNote(gmvResp.error);

  /* ---------- 联盟归因同步刷新(端点真实返回后重拉;失败原因不吞) ---------- */
  const onSyncTrack = React.useCallback(async () => {
    if (!token || syncing) return;
    setSyncing(true);
    setSyncErr("");
    try {
      await syncGoaffproMetrics(token);
    } catch (err) {
      setSyncErr(err instanceof Error ? err.message : "同步联盟指标失败");
    } finally {
      setSyncing(false);
      setReloadTick((tick) => tick + 1);
    }
  }, [token, syncing]);

  /* ---------- 闸(金样板同构) ---------- */
  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载店铺与联盟归因数据。
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
      ...(gmvResp.data?.gmv_source ? ([["收入来源", String(gmvResp.data.gmv_source)]] as Array<[string, string]>) : []),
      ...(gmvResp.data?.mixed_currency ? ([["多币种", "存在非主币种行,合计只算主币种(不跨币种相加)"]] as Array<[string, string]>) : []),
      ...(gmvResp.error ? ([["收入源", `读取失败:${gmvResp.error}`]] as Array<[string, string]>) : []),
      ...(attrResp.error ? ([["明细源", `读取失败:${attrResp.error}`]] as Array<[string, string]>) : []),
    ];
    return (
      <ModuleCard {...cardProps("kpiS", "指标带", attrCount != null ? `${attrCount} 归因行` : undefined, extraRows)}>
        {!token ? (
          noTokenCard
        ) : (
          <ShopifyKpiBand
            revenueUsd={revenueUsd}
            revenueNote={srcNote(gmvResp.error)}
            attrCount={attrCount}
            attrNote={srcNote(attrResp.error)}
            goaffGmvUsd={goaffGmvUsd}
            goaffNote={goaffNote}
            directUsd={directUsd}
            directNote={directNote}
          />
        )}
      </ModuleCard>
    );
  };

  const renderTrack = () => {
    const lastSynced = trackResp.data?.last_synced_at ? formatLocal(trackResp.data.last_synced_at) : "";
    const extraRows: Array<[string, string]> = [
      ...(lastSynced ? ([["最近同步", `${lastSynced}(UTC 存 · 按浏览器时区显示)`]] as Array<[string, string]>) : []),
      ...(trackResp.data?.note ? ([["服务端备注", String(trackResp.data.note)]] as Array<[string, string]>) : []),
    ];
    return (
      <ModuleCard
        {...cardProps("trackS", "联盟归因汇总", trackItems && trackItems.length > 0 ? `${trackItems.length}` : undefined, extraRows)}
      >
        {opsGate(trackItems, trackResp.error, "goaffpro/summary") ?? (
          <div>
            <TrackBody
              configured={goaffConnected}
              items={trackItems || []}
              total={trackItems?.length || 0}
              totals={trackResp.data?.totals}
              onOpen={(i) => setTrackIdx(i)}
              onOpenAll={() => setTrackListOpen(true)}
              search={
                <TrackSearchBar
                  draft={searchDraft}
                  onDraft={setSearchDraft}
                  onCommit={() => setCommittedSearch(searchDraft.trim())}
                  onClear={() => {
                    setSearchDraft("");
                    setCommittedSearch("");
                  }}
                  committed={committedSearch}
                  syncing={syncing}
                  onSync={() => void onSyncTrack()}
                />
              }
            />
            {syncErr ? <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">同步失败:{syncErr}</div> : null}
          </div>
        )}
      </ModuleCard>
    );
  };

  const renderGoaff = () => (
    <ModuleCard {...cardProps("goaffS", "联盟连接")}>
      {!token ? noTokenCard : <GoaffStatusBody status={goaffStatusResp.data} error={goaffStatusResp.error} />}
    </ModuleCard>
  );

  const renderAttr = () => (
    <ModuleCard
      {...cardProps(
        "attrS",
        "归因明细",
        attributions ? `${attributions.length}` : undefined,
        revenueUsd != null ? [["净额", `${fmtUsd2(revenueUsd)}(confirmed+refund,与归因收入卡同口径)`]] : [],
      )}
    >
      {opsGate(attributions, attrResp.error, "attribution") ?? (
        <AttributionBody items={attributions || []} total={attributions?.length || 0} onOpen={(i) => setAttrIdx(i)} onOpenAll={() => setAttrListOpen(true)} />
      )}
    </ModuleCard>
  );

  const renderConn = () => (token ? <ConnectEmbed apiToken={token} onOpenSrc={() => setProvKey("connS")} /> : <ModuleCard {...cardProps("connS", "店铺连接向导")}>{noTokenCard}</ModuleCard>);

  const renderCreds = () => (token ? <LegacyCredsEmbed apiToken={token} onOpenSrc={() => setProvKey("credsS")} /> : <ModuleCard {...cardProps("credsS", "店铺凭据(旧)")}>{noTokenCard}</ModuleCard>);

  /* ---------- 模块注册表(palette 全量可选;默认简;高度贴内容 1 格=22px+14px gap) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiS", label: "指标带", description: "归因收入 / 归因明细 / 联盟推广 / 直连订单 · 有真数才真值,缺配置如实 pending", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "trackS", label: "联盟归因汇总", description: "每个已建链 KOL 一行:点击 / 订单 / GMV / 佣金 · 搜索 + 同步刷新", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 6, maxHeight: 26, render: renderTrack },
    { key: "goaffS", label: "联盟连接", description: "连接状态只读(masked)· 配置入口在设置(管理员)", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 12, minHeight: 5, maxHeight: 20, render: renderGoaff },
    { key: "attrS", label: "归因明细", description: "订单归因行 · 金额美分→美元 · 置信徽 + 退款负行如实", category: "实时模块", defaultSpan: 12, minSpan: 4, defaultHeight: 9, minHeight: 4, maxHeight: 20, render: renderAttr },
    { key: "connS", label: "店铺连接向导", description: "环境变量 / Webhook 注册 / 测试同步 / 同步历史 · 整页内嵌零改动", category: "业务板块", defaultSpan: 12, minSpan: 6, defaultHeight: 24, minHeight: 8, maxHeight: 44, render: renderConn },
    // ↓ palette 备选(不进默认布局)
    { key: "credsS", label: "店铺凭据(旧)", description: "自建直连加密凭据表单(旧·待退役)· 保留供回滚排障", category: "业务板块", defaultSpan: 6, minSpan: 4, defaultHeight: 10, minHeight: 5, maxHeight: 30, render: renderCreds },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="Shopify" />;
  }

  const trackItem = trackIdx != null && trackItems ? trackItems[trackIdx] : null;
  const attrItem = attrIdx != null && attributions ? attributions[attrIdx] : null;

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 归因行徽 + 实时辉光点 + 刷新 + 编辑布局 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">Shopify</span>
          {attrCount != null && attrCount > 0 && <span className={PH_BADGE}>{attrCount} 归因行</span>}
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

      {!token && <div className="mb-3">{noTokenCard}</div>}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 联盟归因全量 + 详情连续翻 */}
      {trackListOpen && trackItems && (
        <MiniListModal title="联盟归因汇总" total={trackItems.length} unit="行" onClose={() => setTrackListOpen(false)}>
          {trackItems.map((item, i) => (
            <TrackRowLine key={`${item.kol_pool_id}-${i}`} item={item} index={i} onOpen={(idx) => setTrackIdx(idx)} />
          ))}
        </MiniListModal>
      )}
      {trackItem && trackItems && (
        <MiniDetailModal
          title={String(trackItem.kol_name || `KOL #${trackItem.kol_pool_id ?? "—"}`)}
          pill={trackItem.partial ? <span className="flex-none rounded-[5px] border border-warn bg-warn-soft px-1 py-px text-[8px] font-bold text-warn">部分</span> : undefined}
          rows={trackDetailRows(trackItem)}
          index={trackIdx as number}
          total={trackItems.length}
          onNav={(i) => setTrackIdx(Math.max(0, Math.min(trackItems.length - 1, i)))}
          onClose={() => setTrackIdx(null)}
        />
      )}

      {/* 归因明细全量 + 详情连续翻(金额美分→美元;ProjectsBoardPage 同构) */}
      {attrListOpen && attributions && (
        <MiniListModal title="归因明细" total={attributions.length} unit="行" onClose={() => setAttrListOpen(false)}>
          {attributions.map((item, i) => (
            <AttributionRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={(idx) => setAttrIdx(idx)} />
          ))}
        </MiniListModal>
      )}
      {attrItem && attributions && (
        <MiniDetailModal
          title={String(attrItem.order_id || attrItem.source_ref || `归因 #${attrItem.id}`)}
          pill={confidencePill(String(attrItem.confidence || ""))}
          rows={[
            ["来源", `${String(attrItem.source_platform || "—")} · ${String(attrItem.source_ref || "—")}`],
            ["订单", String(attrItem.order_id || "—")],
            ["项目", attrItem.project_id != null ? `#${attrItem.project_id}` : "—(未挂项目)"],
            ["KOL / 负责人", `${attrItem.kol_id != null ? `kol #${attrItem.kol_id}` : "—"} · ${attrItem.staff_id != null ? `staff #${attrItem.staff_id}` : "—"}`],
            ["SKU", String(attrItem.product_sku || "—")],
            ["收入", `${fmtUsd2(centsToUsd(attrItem.revenue_cents))}(revenue_cents=${attrItem.revenue_cents ?? "—"})`],
            ["佣金", fmtUsd2(centsToUsd(attrItem.commission_cents))],
            ["归因模型", `${String(attrItem.attribution_model || "—")} · ${String(attrItem.confidence || "—")}`],
            ["发生于", `${formatLocal(attrItem.occurred_at as string)}(UTC 存 · 按浏览器时区显示)`],
            ["导入于", formatLocal(attrItem.imported_at as string)],
          ]}
          index={attrIdx as number}
          total={attributions.length}
          onNav={(i) => setAttrIdx(Math.max(0, Math.min(attributions.length - 1, i)))}
          onClose={() => setAttrIdx(null)}
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

export default ShopifyBoardPage;
