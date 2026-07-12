import React from "react";
import { Loader2, RefreshCw, Search } from "lucide-react";
import { EmptyLine, KpiCard, PendingCard, type Row } from "./MarketVoicePage.modules";
import { platformBadge } from "./MarketVoicePage.dialogs";
import { fmtUsd2 } from "../../../../services/vkpi/projectsBoard-api";
import { GOAFF_SOURCE_LABEL, GOAFF_STATUS_META, asUsd } from "../../../../services/vkpi/shopifyBoard-api";
import type { GoaffproStatus, GoaffproSummaryRow, GoaffproSummaryTotals } from "../../../../services/vkpi/goaffpro-api";

// Shopify 板块页 · 图形件(金样板 MarketVoicePage.modules / ProjectsBoardPage.charts 同构)。
//   ShopifyKpiBand   四卡:归因收入(对账净额)/ 归因明细(行数)/ 联盟推广(GOAFFPRO
//                    缓存 GMV)/ 直连订单(订单台账)。有真数才真值;未配置的源 →
//                    pending 诚实卡注明缺配置。无按日序列端点 → 趋势位 spempty
//                    诚实虚线零药丸(不编时序)。
//   TrackBody        联盟归因汇总行(每个已建链 KOL 一行):头像/名/handle + ref 码 +
//                    优惠码 + 点击/订单/GMV,点行详情连续翻;totals 汇总条 + 搜索。
//   GoaffStatusBody  联盟连接只读状态(masked-only,配置入口在设置,绝不回显明文)。
// 红线:本文件零直连网络(取数在 page 层);不触 viltrox_fit_score / rule_v0;颜色全
//   token 零写死色;禁 opacity 修饰类;卡面零内部术语(口径全进 SrcChip/溯源弹窗);
//   时间一律绝对时间戳(存 UTC 按浏览器时区,formatLocal);金额:*_cents 走 centsToUsd
//   唯一换算点、*_usd 走 asUsd 直读,双向守卫单测。

export const FACE_ROWS = 6; // demo FULL.slice(0,6):卡面收敛条数,全量走弹窗

const keyActivate = (fn: () => void) => (ev: React.KeyboardEvent) => {
  if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    fn();
  }
};

/* ============ 模块口径唯一注册表(label=真实表名;卡面零术语,口径全住这里) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiS: {
    label: "shopify/gmv · vkpi_sales_attributions",
    rows: [
      ["归因收入", "GET /shopify/gmv 对账口径:订单台账(vkpi_shopify_orders)有行用台账,否则归因台账净额(confirmed+refund,退款负行冲抵)"],
      ["归因明细", "GET /attribution 行数(vkpi_sales_attributions 全置信档,own-only 服务端裁剪)"],
      ["联盟推广", "GOAFFPRO 缓存合计(vkpi_goaffpro_kol_metrics;gmv_usd 服务端已换算美元)"],
      ["直连订单", "vkpi_shopify_orders Σ total_price_cents;未配置店铺凭据 → 如实 pending"],
      ["金额红线", "*_cents 展示前 centsToUsd 唯一换算点(100 倍缺陷守卫单测);*_usd 直读零二次换算"],
      ["趋势位", "无按日序列端点 → 诚实虚线零药丸(不编时序)"],
    ],
  },
  trackS: {
    label: "goaffpro/summary · vkpi_goaffpro_kol_links",
    rows: [
      ["行来源", "vkpi_goaffpro_kol_links ⋈ vkpi_kol_pool ⋈ vkpi_goaffpro_kol_metrics(缓存读库秒出)"],
      ["刷新", "POST goaffpro/sync-metrics 拉最新点击/订单/GMV/佣金落库后重读"],
      ["金额", "gmv_usd / commission_usd 服务端已美元(缓存 gmv_cents ÷ 100),前端零二次换算(守卫单测)"],
      ["排序", "按 GMV 降序;可搜 KOL 名 / handle / ref 码 / 优惠码"],
      ["时间", "synced_at 存 UTC · 按浏览器时区显示"],
    ],
  },
  goaffS: {
    label: "goaffpro/creds(masked)",
    rows: [
      ["状态", "not_configured / pending / connected / error / revoked 真值"],
      ["来源", "db(加密落库)/ env(环境变量)/ none(未配置)"],
      ["配置入口", "设置 → GOAFFPRO 联盟营销(管理员);本卡只读,绝不回显明文 token"],
    ],
  },
  attrS: {
    label: "vkpi_sales_attributions",
    rows: [
      ["主表", "vkpi_sales_attributions(own-only 服务端裁剪)"],
      ["金额", "revenue_cents 美分存储 ÷ 100 展示(唯一换算点,带单测)"],
      ["置信", "confirmed / estimated / manual / unmatched / refund 真值徽"],
      ["退款", "refund 负额行冲抵,净额与归因收入卡同口径"],
    ],
  },
  connS: {
    label: "marketing/shopify/status · vkpi_shopify_sync_runs",
    rows: [
      ["状态", "SHOPIFY_SHOP_DOMAIN / ADMIN_ACCESS_TOKEN / WEBHOOK_SECRET 配置布尔(绝不回显值)"],
      ["Webhook", "orders/create + orders/refund/create → /api/vkpi/webhooks/shopify/*"],
      ["测试同步", "POST marketing/shopify/sync 写 vkpi_shopify_sync_runs(未配置=not_configured,不编订单)"],
      ["历史", "vkpi_shopify_sync_runs 最近 10 条"],
    ],
  },
  credsS: {
    label: "shopify/creds(加密落库)",
    rows: [
      ["保存", "POST /shopify/creds 后端加密存储,保存后清空输入,绝不回显 / 入日志"],
      ["定位", "旧·自建直连(联盟推广已取代);保留供回滚 / 排障,展开仍可用"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiS: "指标带",
  trackS: "联盟归因汇总",
  goaffS: "联盟连接",
  attrS: "归因明细",
  connS: "店铺连接向导",
  credsS: "店铺凭据(旧)",
};

/* ============ KPI 带四卡(有真数才真值;连不上 → pending 注明缺配置) ============ */
export function ShopifyKpiBand({
  revenueUsd,
  revenueNote,
  attrCount,
  attrNote,
  goaffGmvUsd,
  goaffNote,
  directUsd,
  directNote,
}: {
  revenueUsd: number | null;
  revenueNote: string;
  attrCount: number | null;
  attrNote: string;
  goaffGmvUsd: number | null;
  goaffNote: string;
  directUsd: number | null;
  directNote: string;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {revenueUsd != null ? (
        <KpiCard label="归因收入" value={fmtUsd2(revenueUsd)} />
      ) : (
        <KpiCard label="归因收入" value="—" pending pendingNote={revenueNote} />
      )}
      {attrCount != null ? (
        <KpiCard label="归因明细" value={attrCount.toLocaleString()} unit="行" />
      ) : (
        <KpiCard label="归因明细" value="—" pending pendingNote={attrNote} />
      )}
      {goaffGmvUsd != null ? (
        <KpiCard label="联盟推广" value={fmtUsd2(goaffGmvUsd)} />
      ) : (
        <KpiCard label="联盟推广" value="—" pending pendingNote={goaffNote} />
      )}
      {directUsd != null ? (
        <KpiCard label="直连订单" value={fmtUsd2(directUsd)} />
      ) : (
        <KpiCard label="直连订单" value="—" pending pendingNote={directNote} />
      )}
    </div>
  );
}

/* ============ 联盟归因汇总(totals 条 + 搜索 + 行列表;点行详情连续翻) ============ */

export function TrackTotalsStrip({ totals }: { totals: GoaffproSummaryTotals | null | undefined }) {
  if (!totals) return null;
  const cells: Array<[string, string]> = [
    ["总点击", (Number(totals.clicks) || 0).toLocaleString()],
    ["总订单", (Number(totals.orders) || 0).toLocaleString()],
    ["总 GMV", fmtUsd2(asUsd(totals.gmv_usd))],
    ["应付佣金", fmtUsd2(asUsd(totals.commission_usd))],
  ];
  return (
    <div className="mb-3 grid grid-cols-4 gap-2">
      {cells.map(([label, value]) => (
        <div key={label} className="rounded-lg border border-line bg-card px-3 py-2">
          <div className="text-[9px] text-muted">{label}</div>
          <div className="mt-0.5 text-[14px] font-semibold tabular-nums text-ink">{value}</div>
        </div>
      ))}
    </div>
  );
}

export function TrackSearchBar({
  draft,
  onDraft,
  onCommit,
  onClear,
  committed,
  syncing,
  onSync,
}: {
  draft: string;
  onDraft: (v: string) => void;
  onCommit: () => void;
  onClear: () => void;
  committed: string;
  syncing: boolean;
  onSync: () => void;
}) {
  return (
    <div className="mb-3 flex items-center gap-2">
      <input
        type="text"
        value={draft}
        onChange={(ev) => onDraft(ev.target.value)}
        onKeyDown={(ev) => {
          if (ev.key === "Enter") onCommit();
        }}
        placeholder="搜 KOL 名 / handle / ref 码 / 优惠码"
        className="min-w-0 flex-1 rounded-lg border border-line bg-card px-3 py-1.5 text-[12px] text-ink placeholder:text-muted focus:border-accent focus:outline-none"
      />
      <button
        type="button"
        onClick={onCommit}
        className="flex flex-none items-center gap-1 rounded-lg border border-accent bg-accent-soft px-3 py-1.5 text-[11.5px] text-accent transition-colors hover:border-accent-hover"
      >
        <Search size={12} />
        查询
      </button>
      {committed ? (
        <button type="button" onClick={onClear} className="flex-none text-[11px] text-muted transition-colors hover:text-ink">
          清除
        </button>
      ) : null}
      <button
        type="button"
        onClick={onSync}
        disabled={syncing}
        title="从联盟后台拉取最新点击 / 订单 / GMV / 佣金落库后重读"
        className="flex flex-none items-center gap-1 rounded-lg border border-line bg-card px-3 py-1.5 text-[11.5px] text-muted transition-colors hover:text-ink disabled:cursor-default"
      >
        {syncing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
        {syncing ? "同步中…" : "同步"}
      </button>
    </div>
  );
}

export function TrackRowLine({ item, index, onOpen }: { item: GoaffproSummaryRow; index: number; onOpen: (i: number) => void }) {
  const name = String(item.kol_name || "—");
  const gmv = asUsd(item.gmv_usd);
  return (
    <div
      className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(index)}
      onKeyDown={keyActivate(() => onOpen(index))}
    >
      <span className="min-w-[46px] flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-center text-[8.5px] font-semibold text-ink-2">
        {platformBadge(String(item.kol_platform || ""))}
      </span>
      {item.kol_avatar ? (
        <img src={String(item.kol_avatar)} alt="" referrerPolicy="no-referrer" className="h-6 w-6 flex-none rounded-full object-cover" />
      ) : (
        <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-accent-soft text-[10px] font-bold text-accent">
          {(name || "K").charAt(0)}
        </span>
      )}
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent">
        {name}
        {item.partial ? (
          <span className="ml-1 text-warn" title="该 KOL 数据查询失败,数值可能偏低">
            ⚠
          </span>
        ) : null}
        {item.kol_handle ? <span className="ml-1.5 text-[9.5px] text-muted">{String(item.kol_handle)}</span> : null}
      </span>
      {item.coupon ? <span className="flex-none font-mono text-[9.5px] text-good">{String(item.coupon)}</span> : null}
      <span className="flex-none font-mono text-[9.5px] text-muted" title="ref 码">
        {String(item.ref_code || "—")}
      </span>
      <span className="flex-none font-mono text-[9.5px] text-muted" title="点击 / 订单">
        {Number(item.clicks) || 0} · {Number(item.orders) || 0}
      </span>
      <span className="flex-none font-mono text-[10.5px] text-ink" title="GMV(服务端已美元)">
        {fmtUsd2(gmv)}
      </span>
    </div>
  );
}

export function TrackBody({
  configured,
  items,
  total,
  totals,
  onOpen,
  onOpenAll,
  search,
}: {
  configured: boolean;
  items: GoaffproSummaryRow[];
  total: number;
  totals: GoaffproSummaryTotals | null | undefined;
  onOpen: (i: number) => void;
  onOpenAll: () => void;
  search: React.ReactNode;
}) {
  if (!configured && items.length === 0) {
    return (
      <PendingCard>
        <b>未配置</b> —— 联盟推广 token 未填。在 设置 → GOAFFPRO 联盟营销(管理员)完成配置后,
        每个 KOL 的追踪链 / 优惠码 / 点击 / 订单将自动出现在此。不装数据。
      </PendingCard>
    );
  }
  return (
    <div>
      {search}
      <TrackTotalsStrip totals={totals} />
      {items.length === 0 ? (
        <EmptyLine text="0 行 · 链路已通但暂无已建链 KOL(如实)。" />
      ) : (
        <div>
          {items.slice(0, FACE_ROWS).map((item, i) => (
            <TrackRowLine key={`${item.kol_pool_id}-${i}`} item={item} index={i} onOpen={onOpen} />
          ))}
          {total > FACE_ROWS && (
            <button
              type="button"
              onClick={onOpenAll}
              className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
            >
              ≡ 查看全量 {total} 行 · 点单条连续翻
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ============ 联盟连接只读状态(masked-only;配置入口在设置) ============ */

export function goaffStatusPill(status: string) {
  const meta = GOAFF_STATUS_META[String(status)] || { label: String(status || "—"), cls: "border-line text-muted" };
  return <span className={`flex-none rounded-[5px] border px-1.5 py-px text-[9px] font-bold tracking-[0.05em] ${meta.cls}`}>{meta.label}</span>;
}

function StatusRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 border-b border-line py-1.5 text-[11.5px] last:border-0">
      <span className="w-[76px] flex-none text-muted">{k}</span>
      <span className="min-w-0 flex-1 text-right text-ink-2">{v}</span>
    </div>
  );
}

export function GoaffStatusBody({ status, error }: { status: GoaffproStatus | null; error: string }) {
  if (error) {
    return (
      <PendingCard>
        <b>读取失败</b> —— {error}
      </PendingCard>
    );
  }
  if (!status) return <EmptyLine text="读取中…" />;
  const okBadge = (on: boolean | undefined) => (
    <span className={`rounded-[5px] border px-1.5 py-px text-[9px] font-bold ${on ? "border-good bg-good-soft text-good" : "border-warn bg-warn-soft text-warn"}`}>
      {on ? "已配置" : "未配置"}
    </span>
  );
  return (
    <div>
      <StatusRow k="连接状态" v={goaffStatusPill(String(status.status || "not_configured"))} />
      <StatusRow k="管理密钥" v={okBadge(status.access_token_configured)} />
      <StatusRow k="公钥" v={okBadge(status.public_token_configured)} />
      <StatusRow k="凭据来源" v={GOAFF_SOURCE_LABEL[String(status.source || "none")] || String(status.source || "—")} />
      {status.token ? <StatusRow k="密钥指纹" v={<span className="font-mono text-[10px]">{String(status.token)}</span>} /> : null}
      <p className="mt-2 text-[10.5px] leading-[1.7] text-muted">
        配置入口在 设置 → GOAFFPRO 联盟营销(管理员)。本卡只读展示,绝不回显明文密钥。
      </p>
    </div>
  );
}

/* ============ 联盟归因详情行组(MiniDetailModal rows;时间绝对时间戳) ============ */
export function trackDetailRows(item: GoaffproSummaryRow): Array<[string, React.ReactNode]> {
  const rate = String(item.commission_rate || "—");
  return [
    ["KOL", `${String(item.kol_name || "—")}${item.kol_handle ? ` · ${String(item.kol_handle)}` : ""}`],
    ["平台", String(item.kol_platform || "—")],
    ["档案", item.kol_pool_id != null ? `kol_pool #${item.kol_pool_id}` : "—"],
    ["ref 码", <span className="font-mono">{String(item.ref_code || "—")}</span>],
    ["优惠码", <span className="font-mono text-good">{String(item.coupon || "—")}</span>],
    ["佣金比例", rate],
    ["点击", (Number(item.clicks) || 0).toLocaleString()],
    ["订单", (Number(item.orders) || 0).toLocaleString()],
    ["GMV", `${fmtUsd2(asUsd(item.gmv_usd))}(服务端已换算美元)`],
    ["佣金", fmtUsd2(asUsd(item.commission_usd))],
    [
      "追踪链",
      item.tracking_url ? (
        <a href={String(item.tracking_url)} target="_blank" rel="noopener noreferrer" className="break-all text-accent transition-colors hover:text-accent-hover">
          {String(item.tracking_url)} ↗
        </a>
      ) : (
        "—"
      ),
    ],
    ["数据可信度", item.partial ? "部分失败 —— 该 KOL 拉数失败,数值可能偏低(如实)" : "完整"],
  ];
}

export type { Row };
