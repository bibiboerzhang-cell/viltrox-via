import React from "react";
import { Loader2 } from "lucide-react";
import { EmptyLine, KpiCard, PendingCard } from "./MarketVoicePage.modules";
import { formatLocal } from "../../lib/timeLocal";
import type { VkpiDealer, VkpiDealerScrapeResult } from "../../../../services/vkpi/dealers-api";
import { boardSeriesVals, type VkpiBoardSeriesResponse } from "../../../../services/vkpi/boardSeries-api";

// Dealers 板块页 · 图形件(金样板 MarketVoicePage.modules / ShopifyBoardPage.modules 同构)。
//   DealersKpiBand   四卡:经销商数 / 已定位 / 覆盖州 / 国家数。有真数才真值;
//                    vkpi_dealers 0 行 → 全带 pending 诚实空态注明数据在线上库;
//                    经销商数卡趋势线 = board-series?board=dealers 新入库/日真序列
//                    (全表 0 行 → 端点诚实 empty → boardSeriesVals=null → spempty
//                    虚线如实,绝不摆 0 填平线;关联指标不挂环比药丸)。
//   RegionBars       地区分布条形(按州 GROUP BY,count 降序 top10;有数据才画,
//                    空态由 page 层闸住,本件只画真行)。
//   DealerListBody   经销商行列表(名录 / 待补定位共用):定位徽 + 名称 + 城市州 +
//                    地址,face 6 行 + 「≡ 查看全量」,点行单条详情连续翻。
//   ScrapeControls   预检(record_only,只出计划零外发)/ 有界抓取(单批 ≤20 服务端
//                    硬上限)+ 回执行 —— 旧页 runScrape 全功能零丢失搬家。
//   AddDealerForm    手动添加经销商(名称*/地址*/城市/州,name+address 幂等)——
//                    旧页 handleCreate 表单零丢失搬家,成功清空绝不静默。
// 红线:本文件零直连网络(取数/动作全在 page 层);不触 viltrox_fit_score / rule_v0;
//   颜色全 token 零写死色;禁 token 色 opacity 修饰类;卡面零内部术语(record_only /
//   geocode 等口径全进 SrcChip/溯源弹窗);时间一律绝对时间戳(存 UTC 按浏览器时区)。

export const FACE_ROWS = 6; // demo FULL.slice(0,6):卡面收敛条数,全量走弹窗

// vkpi_dealers 0 行的唯一诚实空态口径(KPI 带 / 地区分布 / 地图角标 / 清单共用)
export const ZERO_NOTE = "库内 0 行 · 经销商数据在线上库,本地未导入";

const keyActivate = (fn: () => void) => (ev: React.KeyboardEvent) => {
  if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    fn();
  }
};

/* ============ 模块口径唯一注册表(label=真实表名;卡面零术语,口径全住这里) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiD: {
    label: "vkpi_dealers · dealers/locations",
    rows: [
      ["经销商数", "GET /dealers 行数(vkpi_dealers,单次读取上限 500 行)"],
      ["已定位", "GET /dealers/locations 行数(lat/lng 齐全的经销商才成为定位点)"],
      ["覆盖州", "vkpi_dealers.state 非空去重计数"],
      ["国家数", "表无 country 列 · 采集管线=美国相机零售商(有界合规),按定位点归属计"],
      ["趋势位", "无按日序列端点 → 诚实虚线零环比药丸(不编时序)"],
      ["空态", "本地库 0 行如实空 —— 经销商数据在线上环境,本地未导入"],
    ],
  },
  regionD: {
    label: "vkpi_dealers.state",
    rows: [
      ["口径", "按州 GROUP BY 计数,count 降序取前 10"],
      ["未标注", "state 为空的行归「未标注」桶,如实不丢"],
      ["纪律", "有数据才画;0 行 → 诚实空态,绝不编分布"],
    ],
  },
  mapD: {
    label: "dealers/locations · 地图渲染原件",
    rows: [
      ["定位点", "lat/lng 齐全的经销商(服务端只吐已定位行,上限 5000)"],
      ["待补", "缺经纬度的经销商不上图,住「待补定位」清单,补齐后自动上图"],
      ["来源", "美国相机零售商 · 有界合规采集(单批 ≤20 服务端硬上限)"],
      ["渲染", "地图本体为旧渲染件原样收编(零改动);深浅底图随主题切换"],
    ],
  },
  pendD: {
    label: "vkpi_dealers(lat / lng 为空)",
    rows: [
      ["口径", "lat 或 lng 为空的经销商行(与旧页同口径)"],
      ["补齐", "新增 / 采集时按地址自动尝试补经纬度;补齐后自动上图并离开本清单"],
    ],
  },
  rosterD: {
    label: "vkpi_dealers",
    rows: [
      ["读取", "GET /dealers 单次上限 500 行,服务端按入库时间降序"],
      ["详情", "点行单条详情 ‹#n/N› + ↑↓ 连续翻;含库记录 id 与绝对入库时间"],
    ],
  },
  opsD: {
    label: "dealers/scrape-enqueue · POST dealers",
    rows: [
      ["预检", "record_only=true 只返回抓取计划 —— 不写库、不对外请求"],
      ["抓取", "单批硬上限 20(服务端 clamp),幂等入库(name+address 去重)"],
      ["手动添加", "POST /dealers 幂等 upsert;有地址即自动尝试补经纬度"],
      ["回执", "requested / inserted / skipped / geocoded / pending 全部来自端点真实返回"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiD: "指标带",
  regionD: "地区分布",
  mapD: "经销商地图",
  pendD: "待补定位",
  rosterD: "经销商名录",
  opsD: "录入与采集",
};

/* ============ KPI 带四卡(有真数才真值;0 行 / 读取失败 → pending 带原因) ============ */
export function DealersKpiBand({
  total,
  totalNote,
  located,
  locatedNote,
  stateCount,
  countryCount,
  boardSeries,
}: {
  total: number | null;
  totalNote: string;
  located: number | null;
  locatedNote: string;
  stateCount: number | null;
  countryCount: number | null;
  /** board-series?board=dealers 响应(0 行 empty / 失败 → 趋势位 spempty 诚实虚线) */
  boardSeries?: VkpiBoardSeriesResponse | null;
}) {
  const cards: Array<{ label: string; value: number | null; unit: string; note: string; series?: Array<number | null> | null }> = [
    { label: "经销商数", value: total, unit: "家", note: totalNote, series: boardSeriesVals(boardSeries ?? null, "dealers_new") },
    { label: "已定位", value: located, unit: "家", note: locatedNote },
    { label: "覆盖州", value: stateCount, unit: "州", note: totalNote },
    { label: "国家数", value: countryCount, unit: "国", note: locatedNote },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {cards.map((card) =>
        card.value != null ? (
          <KpiCard key={card.label} label={card.label} value={card.value.toLocaleString()} unit={card.unit} series={card.series ?? null} />
        ) : (
          <KpiCard key={card.label} label={card.label} value="—" pending pendingNote={card.note} />
        ),
      )}
    </div>
  );
}

/* ============ 地区分布条形(有数据才画;空态由 page 层闸住) ============ */
export function RegionBars({ rows }: { rows: Array<{ label: string; count: number }> }) {
  const max = rows.reduce((m, r) => Math.max(m, r.count), 0);
  if (rows.length === 0 || max <= 0) {
    return <EmptyLine text="0 行 · 暂无地区数据(如实)。" />;
  }
  return (
    <div className="flex flex-col gap-2">
      {rows.map((row) => (
        <div key={row.label} className="flex items-center gap-2">
          <span className="w-[64px] flex-none truncate text-right text-[10.5px] text-muted" title={row.label}>
            {row.label}
          </span>
          <span className="relative h-[14px] min-w-0 flex-1 overflow-hidden rounded-[4px] border border-line bg-card">
            <span
              className="absolute inset-y-0 left-0 rounded-[3px] bg-accent"
              style={{ width: `${Math.max(4, Math.round((row.count / max) * 100))}%` }}
            />
          </span>
          <span className="w-[40px] flex-none text-right font-mono text-[10.5px] tabular-nums text-ink">{row.count}</span>
        </div>
      ))}
    </div>
  );
}

/* ============ 经销商行(名录 / 待补定位共用):定位徽 + 名称 + 城市州 + 地址 ============ */
export function DealerRowLine({ item, index, onOpen }: { item: VkpiDealer; index: number; onOpen: (i: number) => void }) {
  const located = item.lat != null && item.lng != null;
  const cityState = [item.city, item.state].filter(Boolean).join(", ");
  return (
    <div
      className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(index)}
      onKeyDown={keyActivate(() => onOpen(index))}
    >
      <span
        className={`flex-none rounded-[5px] border px-1.5 py-px text-[8.5px] font-bold tracking-[0.05em] ${
          located ? "border-good bg-good-soft text-good" : "border-warn bg-warn-soft text-warn"
        }`}
      >
        {located ? "已定位" : "待定位"}
      </span>
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent">
        {String(item.name || "—")}
        {cityState ? <span className="ml-1.5 text-[9.5px] text-muted">{cityState}</span> : null}
      </span>
      <span className="max-w-[180px] flex-none truncate text-[9.5px] text-muted" title={String(item.address || "")}>
        {String(item.address || "—")}
      </span>
    </div>
  );
}

/* ============ 行列表 body:face 6 行 + 「≡ 查看全量」(名录 / 待补定位共用) ============ */
export function DealerListBody({
  items,
  emptyText,
  onOpen,
  onOpenAll,
}: {
  items: VkpiDealer[];
  emptyText: string;
  onOpen: (i: number) => void;
  onOpenAll: () => void;
}) {
  if (items.length === 0) return <EmptyLine text={emptyText} />;
  return (
    <div>
      {items.slice(0, FACE_ROWS).map((item, i) => (
        <DealerRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={onOpen} />
      ))}
      {items.length > FACE_ROWS && (
        <button
          type="button"
          onClick={onOpenAll}
          className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
        >
          ≡ 查看全量 {items.length} 家 · 点单条连续翻
        </button>
      )}
    </div>
  );
}

/* ============ 单条详情行组(MiniDetailModal rows;时间绝对时间戳) ============ */
export function dealerDetailRows(d: VkpiDealer): Array<[string, React.ReactNode]> {
  const located = d.lat != null && d.lng != null;
  return [
    ["名称", String(d.name || "—")],
    ["地址", String(d.address || "—")],
    ["城市 / 州", `${d.city || "—"} · ${d.state || "—"}`],
    [
      "定位",
      located ? (
        <span className="font-mono">
          {Number(d.lat).toFixed(4)}, {Number(d.lng).toFixed(4)}
        </span>
      ) : (
        "待补经纬度 —— 补齐后自动上图"
      ),
    ],
    ["来源", String(d.source || "—")],
    ["入库时间", d.created_at ? `${formatLocal(d.created_at)}(UTC 存 · 按浏览器时区显示)` : "—"],
    ["库记录", `vkpi_dealers #${d.id}`],
  ];
}

/* ============ 采集回执(端点真实返回才有字;字段全真,零编造) ============ */
export function scrapeReceiptText(res: VkpiDealerScrapeResult): string {
  const head = res.record_only ? "预检" : "抓取";
  const parts = [
    `请求 ${Number(res.requested) || 0}`,
    `新增 ${Number(res.inserted) || 0}`,
    `跳过 ${Number(res.skipped) || 0}`,
    `已定位 ${Number(res.geocoded) || 0}`,
    `待补 ${Number(res.pending_geocode) || 0}`,
  ];
  if (Array.isArray(res.errors) && res.errors.length > 0) parts.push(`失败 ${res.errors.length}`);
  return `${head}:${parts.join(" · ")}`;
}

/* ============ 采集动作(预检 / 有界抓取 ≤20 —— 旧页两按钮零丢失) ============ */
export function ScrapeControls({
  busy,
  disabled,
  msg,
  err,
  onPreview,
  onRun,
}: {
  busy: boolean;
  disabled: boolean;
  msg: string;
  err: string;
  onPreview: () => void;
  onRun: () => void;
}) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy || disabled}
          onClick={onPreview}
          title="只返回抓取计划,不写库、不对外请求"
          className="flex items-center gap-1.5 rounded-lg border border-line bg-card px-3 py-1.5 text-[11.5px] text-muted transition-colors hover:text-ink disabled:cursor-default"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : null}
          预检
        </button>
        <button
          type="button"
          disabled={busy || disabled}
          onClick={onRun}
          className="flex items-center gap-1.5 rounded-lg border border-accent bg-accent-soft px-3 py-1.5 text-[11.5px] text-accent transition-colors hover:border-accent-hover disabled:cursor-default"
        >
          有界抓取(≤20)
        </button>
      </div>
      {msg ? <div className="mt-2 rounded-lg border border-line bg-card px-3 py-1.5 font-mono text-[10.5px] text-ink-2">{msg}</div> : null}
      {err ? <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">采集失败:{err}</div> : null}
    </div>
  );
}

/* ============ 手动添加经销商(名称 + 地址必填,城市 / 州选填 —— 旧页表单零丢失) ============ */
const INPUT_CLS =
  "min-w-0 rounded-lg border border-line bg-card px-2.5 py-1.5 text-[11.5px] text-ink placeholder:text-muted focus:border-accent focus:outline-none";

export function AddDealerForm({
  name,
  address,
  city,
  state,
  onName,
  onAddress,
  onCity,
  onState,
  adding,
  disabled,
  msg,
  err,
  onSubmit,
}: {
  name: string;
  address: string;
  city: string;
  state: string;
  onName: (v: string) => void;
  onAddress: (v: string) => void;
  onCity: (v: string) => void;
  onState: (v: string) => void;
  adding: boolean;
  disabled: boolean;
  msg: string;
  err: string;
  onSubmit: () => void;
}) {
  const invalid = !name.trim() || !address.trim();
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        <input value={name} onChange={(ev) => onName(ev.target.value)} placeholder="名称*" className={INPUT_CLS} />
        <input value={address} onChange={(ev) => onAddress(ev.target.value)} placeholder="地址*" className={INPUT_CLS} />
        <input value={city} onChange={(ev) => onCity(ev.target.value)} placeholder="城市" className={INPUT_CLS} />
        <input value={state} onChange={(ev) => onState(ev.target.value)} placeholder="州" className={INPUT_CLS} />
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={adding || disabled || invalid}
          onClick={onSubmit}
          className="flex items-center gap-1.5 rounded-lg border border-accent bg-accent-soft px-3 py-1.5 text-[11.5px] text-accent transition-colors hover:border-accent-hover disabled:cursor-default"
        >
          {adding ? <Loader2 size={12} className="animate-spin" /> : null}
          {adding ? "添加中…" : "添加"}
        </button>
        {msg ? <span className="min-w-0 truncate text-[11px] text-good">{msg}</span> : null}
        {err ? <span className="min-w-0 truncate text-[11px] text-crit">{err}</span> : null}
      </div>
      <p className="text-[10px] leading-[1.6] text-muted">名称 + 地址必填;无经纬度时进「待补定位」,补全后自动上图。</p>
    </div>
  );
}
