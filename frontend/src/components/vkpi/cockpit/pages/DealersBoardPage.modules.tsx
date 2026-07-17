import React from "react";
import { Loader2 } from "lucide-react";
import { EmptyLine, KpiCard, LoadingLine, PendingCard } from "./MarketVoicePage.modules";
import { formatLocal } from "../../lib/timeLocal";
import type {
  VkpiDealer,
  VkpiDealerCoverage,
  VkpiDealerScrapeResult,
  VkpiDealerWritePayload,
} from "../../../../services/vkpi/dealers-api";
import { boardSeriesVals, type VkpiBoardSeriesResponse } from "../../../../services/vkpi/boardSeries-api";

export { DealerTrustPipeline } from "./DealersBoardPage.trust";

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
export const ZERO_NOTE = "本地库 0 行 · 公开零售商候选待核验导入";

export const DEALER_BRAND_OPTIONS = [
  "VILTROX",
  "NIKON",
  "SONY",
  "CANON",
  "FUJIFILM",
  "PANASONIC",
  "LEICA",
  "SIGMA",
  "TAMRON",
  "OM_SYSTEM",
] as const;

const DEALER_SOCIAL_PLATFORMS = ["Instagram", "YouTube", "Facebook", "TikTok", "X"] as const;

function dealerSocialUrls(dealer: Pick<VkpiDealer, "social_links">): Record<string, string> {
  const values: Record<string, string> = {};
  for (const item of dealer.social_links || []) {
    const key = String(item.platform || "").trim().toLowerCase();
    if (key && item.url) values[key] = item.url;
  }
  return values;
}

export function dealerBrandCodes(dealer: Pick<VkpiDealer, "brand_codes" | "brand_relationships">): string[] {
  const values = [
    ...(dealer.brand_codes || []),
    ...(dealer.brand_relationships || []).map((item) => item.brand_key),
  ];
  return [...new Set(values.map((value) => String(value || "").trim().toUpperCase()).filter(Boolean))];
}

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
      ["经销商数", "GET /dealers/coverage 全表登记行数；名录只读当前服务端分页，不一次性拉全国数据"],
      ["已发布地图点", "优先读 coverage.published_map_pins（显式发布 + 坐标齐全）；旧后端回退当前 /locations 返回数；located 证据合格数仍单独保留"],
      ["覆盖州", "GET /dealers/coverage 中 state 非空去重计数"],
      ["国家数", "GET /dealers/coverage 中 country 非空去重计数；不是按定位点推断"],
      ["趋势位", "无按日序列端点 → 诚实虚线零环比药丸(不编时序)"],
      ["空态", "本地库 0 行如实空；来源候选必须经地址、身份和证据审核后才能入库"],
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
      ["定位点", "GET /locations?published_only=true 仅返回已显式发布且 lat/lng 齐全的私有地图记录；publication 只代表可见性"],
      ["证据", "source_status / truth_status 单独显示；人工发布不会升级为公开来源已核验、官方授权或实时库存"],
      ["待补", "缺经纬度的经销商不上图；补齐后仍须显式发布"],
      ["来源", "美国公开零售商候选 · 已登记品牌/产品页与门店地址页；发布者身份护照另计"],
      ["加载", "当前量级先一次读全美已发布 pin；尚未宣称已按视窗 bbox 分页，RealMap viewport + cluster 列为 P1"],
    ],
  },
  trustD: {
    label: "vkpi_dealers · candidate-staging · us-source-registry",
    rows: [
      ["业务行", "vkpi_dealers 已入库门店；公开来源核验、Viltrox 授权、在售与库存分别记证"],
      ["候选行", "candidate-staging 只返回当前工作区审核队列汇总；原始地址不下发、不上地图"],
      ["来源入口", "US source registry 仅是制造商经销商目录入口；其他品牌授权不等于 Viltrox 授权"],
      ["晋级", "自动晋级永远关闭；只有逐字段证据 + 人工审核 + 促销门禁才能进入业务表"],
    ],
  },
  pendD: {
    label: "vkpi_dealers(lat / lng 为空)",
    rows: [
      ["口径", "lat 或 lng 为空的经销商行(与旧页同口径)"],
      ["补齐", "新增 / 采集时可按地址补经纬度；补齐后离开本清单，但仍须显式发布才上图"],
    ],
  },
  rosterD: {
    label: "vkpi_dealers",
    rows: [
      ["读取", "GET /dealers 服务端分页，当前页最多 100 行；总量只信 total_count / coverage"],
      ["详情", "点行查当前页单条详情 ‹#n/N› + ↑↓ 连续翻；可编辑、发布或撤下地图"],
    ],
  },
  opsD: {
    label: "dealers/scrape-enqueue · POST dealers",
    rows: [
      ["预检", "record_only=true 只返回抓取计划 —— 不写库、不对外请求"],
      ["导入", "单批硬上限 20；只导入代码内已逐页复核的候选，幂等(name+address 去重)"],
      ["手动添加", "POST /dealers 先存草稿；地址、品牌关系和坐标可补全后再显式发布到地图"],
      ["回执", "requested / inserted / skipped / geocoded / pending 全部来自端点真实返回"],
    ],
  },
  rankD: {
    label: "dealers/rankings · vkpi_dealer_web_verification",
    rows: [
      ["读取", "GET /dealers/rankings 每店最新一条核验回执(历史全保留,只取 MAX(verified_at))"],
      ["核验通道", "联网搜索逐店判定(官网在营 / 仍售相机器材 / Viltrox 在售 / 体量档 / 知名度);证据 URL 逐条落库可溯"],
      ["Viltrox 在售", "confirmed 必须带零售商自有域名页面 URL;只代表核验时点页面出现过 Viltrox,不代表实时库存、成交或官方授权"],
      ["体量档", "全国连锁 / 区域连锁 / 本地单店 / 以线上为主 / 待定 —— 依零售商自有门店页 + 公开来源判定"],
      ["知名度分", "0-100 公开知名度信号(连锁店数 / 媒体口碑 / 本地评价规模),附判定依据;描述性排序用,绝不进任何评分管线"],
      ["空态", "未跑核验 → 「尚未核验」如实;数据表未迁移 → migration_pending;绝不编名次"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiD: "指标带",
  regionD: "地区分布",
  mapD: "经销商地图",
  trustD: "美国经销商来源与实体覆盖",
  pendD: "待补定位",
  rosterD: "经销商名录",
  opsD: "录入与采集",
  rankD: "排名与知名度",
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
    { label: "已发布地图点", value: located, unit: "家", note: locatedNote },
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

export function DealerCoverageStrip({ coverage }: { coverage: VkpiDealerCoverage | null }) {
  if (!coverage) return <LoadingLine text="覆盖质量读取中…" />;
  if (coverage.status === "migration_pending") {
    return <PendingCard>Dealer 数据表待迁移；当前没有可引用的覆盖证据。</PendingCard>;
  }
  const items = [
    ["门店地址来源已核验", coverage.public_listing_verified, coverage.total],
    ["坐标已完整", coverage.coordinate_present ?? coverage.located, coverage.total],
    ["地图已发布", coverage.published_map_pins ?? coverage.located, coverage.total],
    ["公开电话已登记", coverage.contacts.phone, coverage.total],
  ] as const;
  const usMatrix = coverage.us_jurisdiction_matrix;
  return (
    <div className="mt-3 grid grid-cols-2 gap-2 border-t border-line pt-3 xl:grid-cols-4">
      {items.map(([label, value, total]) => (
        <div key={label} className="rounded-[8px] border border-line bg-panel px-2.5 py-2">
          <div className="text-[9px] text-muted">{label}</div>
          <div className="mt-0.5 font-mono text-[13px] font-semibold tabular-nums text-ink">
            {Number(value).toLocaleString()} / {Number(total).toLocaleString()}
          </div>
        </div>
      ))}
      <p className="col-span-2 text-[9px] leading-4 text-muted xl:col-span-4">
        分母仅为当前库内登记门店；不是美国全部门店总数。地址来源、坐标、地图发布与联系信息分别计数。
      </p>
      {usMatrix ? (
        <p className="col-span-2 text-[9px] leading-4 text-muted xl:col-span-4" data-testid="dealer-us-jurisdiction-matrix">
          美国已入库门店实体地理 {Number(usMatrix.covered_count).toLocaleString()} / {Number(usMatrix.jurisdiction_count).toLocaleString()} 州/DC；这是库内实体分布，与来源辖区覆盖分开计数，不代表美国经销商市场全量覆盖。
        </p>
      ) : null}
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
function dealerProductEvidenceLabel(status: string | undefined): string {
  if (status === "current_public_url") return "产品页当前有效";
  if (status === "verified_public_url") return "产品页已核验";
  if (status === "declared_public_url") return "产品页仅登记";
  return "无产品页证据";
}

function dealerProductEvidenceDetail(status: string | undefined): string {
  if (status === "current_public_url") return "零售商产品页已核验且核验时间仍有效；不代表实时库存 / 成交";
  if (status === "verified_public_url") return "零售商产品页已核验，当前时效待服务端确认；不代表实时库存 / 成交";
  if (status === "declared_public_url") return "仅登记零售商产品页 URL，尚不足以称为当前有效证据";
  return "未收集 Viltrox 产品页";
}

export function dealerLocationLabel(
  item: Pick<VkpiDealer, "name" | "city" | "state">,
): string {
  const name = String(item.name || "").trim().toLocaleLowerCase();
  const city = String(item.city || "").trim();
  const state = String(item.state || "").trim().toUpperCase();
  const normalizedCity = city.toLocaleLowerCase();
  const cityAlreadyInName = Boolean(
    normalizedCity
    && (name === normalizedCity
      || name.endsWith(` ${normalizedCity}`)
      || name.endsWith(`- ${normalizedCity}`)
      || name.endsWith(`· ${normalizedCity}`)),
  );
  return [cityAlreadyInName ? "" : city, state].filter(Boolean).join(", ");
}

export function DealerRowLine({ item, index, onOpen }: { item: VkpiDealer; index: number; onOpen: (i: number) => void }) {
  const located = item.lat != null && item.lng != null;
  const cityState = dealerLocationLabel(item);
  const brands = dealerBrandCodes(item);
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
        {cityState ? <span className="ml-1.5 text-[9.5px] text-muted">· {cityState}</span> : null}
      </span>
      {brands.slice(0, 3).map((brand) => (
        <span key={brand} className="flex-none rounded-[5px] border border-line bg-panel px-1.5 py-px text-[8px] font-semibold text-ink-2">
          {brand.replace("_", " ")}
        </span>
      ))}
      {item.publication_status === "published" ? (
        <span className="flex-none rounded-[5px] border border-good bg-good-soft px-1.5 py-px text-[8.5px] text-good">地图已发布</span>
      ) : item.publication_status === "draft" ? (
        <span className="flex-none rounded-[5px] border border-line bg-card px-1.5 py-px text-[8.5px] text-muted">草稿·未上图</span>
      ) : null}
      {item.phone || item.contact_email ? (
        <span className="flex-none rounded-[5px] border border-line bg-panel px-1.5 py-px text-[8.5px] text-ink-2">有联系方式</span>
      ) : null}
      {item.website_url ? (
        <span className="flex-none rounded-[5px] border border-accent bg-accent-soft px-1.5 py-px text-[8.5px] text-accent">已登记网站</span>
      ) : null}
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
  totalCount,
}: {
  items: VkpiDealer[];
  emptyText: string;
  onOpen: (i: number) => void;
  onOpenAll: () => void;
  totalCount?: number | null;
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
          ≡ 查看当前页 {items.length} 家{totalCount != null ? ` / 总计 ${totalCount} 家` : ""} · 点单条连续翻
        </button>
      )}
    </div>
  );
}

/* ============ 单条详情行组(MiniDetailModal rows;时间绝对时间戳) ============ */
export function dealerDetailRows(d: VkpiDealer): Array<[string, React.ReactNode]> {
  const located = d.lat != null && d.lng != null;
  const brands = dealerBrandCodes(d);
  const socialLinks = Array.isArray(d.social_links) ? d.social_links.filter((item) => item?.url) : [];
  const provenanceRows = Object.entries(d.provenance || {})
    .map(([field, evidence]) => `${field}:${String(evidence?.status || "unavailable")}`)
    .join(" · ");
  return [
    ["名称", String(d.name || "—")],
    ["品牌关系", brands.length > 0 ? brands.join(" · ") : "未标注"],
    ["地图发布", d.publication_status === "published" ? `已发布${d.published_at ? ` · ${formatLocal(d.published_at)}` : ""}` : d.publication_status === "draft" ? "草稿 · 未上图" : "旧记录 · 发布状态待迁移"],
    ["地址", `${d.address || "—"}${d.postal_code ? ` · ${d.postal_code}` : ""}`],
    ["城市 / 州", `${d.city || "—"} · ${d.state || "—"}`],
    ["电话", String(d.phone || "—")],
    ["公开邮箱", String(d.contact_email || "—")],
    ["常规营业时间", String(d.store_hours || "—")],
    ["公开服务", String(d.public_services || "—")],
    [
      "官网 / 门店站点",
      d.website_url ? <a href={d.website_url} target="_blank" rel="noreferrer" className="text-accent underline">打开零售商站点</a> : "未收集",
    ],
    [
      "公开社媒",
      socialLinks.length > 0 ? (
        <span className="flex flex-wrap gap-2">
          {socialLinks.map((item, index) => (
            <a key={`${item.url}-${index}`} href={item.url} target="_blank" rel="noreferrer" className="text-accent underline">
              {item.platform || `社媒 ${index + 1}`}
            </a>
          ))}
        </span>
      ) : "未收集（不猜测账号）",
    ],
    ["覆盖口径", d.coverage_scope?.scope === "registered_location_only" ? `${d.coverage_scope.city || d.city || "—"} · ${d.coverage_scope.state || d.state || "—"} · 仅已登记门店` : "未收集服务半径"],
    ["线下状态", d.channel_evidence?.offline_location === "public_listing_verified" ? "公开门店页 + 完整地址已核验" : d.channel_evidence?.physical_location_registered ? "已登记地址，公开来源待核验" : "未收集"],
    ["线上状态", dealerProductEvidenceDetail(d.channel_evidence?.online_product_page)],
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
    ["公开来源", d.source_status === "public_listing_verified" ? "在售页 + 门店页已核验" : "待核验"],
    ["事实分层", `候选:${d.truth_status?.candidate ? "是" : "否"} · 公开门店:${d.truth_status?.public_listing === "verified" ? "已核验" : "待核验"} · ${dealerProductEvidenceLabel(d.truth_status?.product_evidence)}`],
    ["授权状态", d.truth_status?.viltrox_authorization === "confirmed" ? "Viltrox 官方授权已确认（官方 URL + 核验时间齐全）" : "待 Viltrox 官方确认（状态字段不能替代官方证据）"],
    [
      "官方授权证据",
      d.truth_status?.viltrox_authorization === "confirmed" && d.authorization_evidence?.official_viltrox_source_url ? (
        <a href={d.authorization_evidence.official_viltrox_source_url} target="_blank" rel="noreferrer" className="text-accent underline">
          打开 Viltrox 官方证据
        </a>
      ) : "缺少 Viltrox 官方 URL 或有效核验时间",
    ],
    [
      "产品页证据",
      d.brand_listing_url ? <a href={d.brand_listing_url} target="_blank" rel="noreferrer" className="text-accent underline">打开零售商页面</a> : "—",
    ],
    [
      "门店证据",
      d.location_source_url ? <a href={d.location_source_url} target="_blank" rel="noreferrer" className="text-accent underline">打开门店地址页</a> : "—",
    ],
    ["核验说明", String(d.verification_note || "—")],
    ["来源核验时间", d.source_checked_at ? `${formatLocal(d.source_checked_at)}(UTC 存)` : "—"],
    ["最近有效核验", d.last_verified_at ? `${formatLocal(d.last_verified_at)} · ${d.freshness_status || "未标注新鲜度"}` : "无已核验时间"],
    ["活动观测", d.activity?.status === "active" ? `有公开活动${d.activity.next_event_at ? ` · 下一场 ${formatLocal(d.activity.next_event_at)}` : ""}` : d.activity?.status === "none_observed" ? "本次检查未观测到（不等于永久无活动）" : "未检索 / 状态未知"],
    ["活动页", d.activity?.page_url ? <a href={d.activity.page_url} target="_blank" rel="noreferrer" className="text-accent underline">打开活动页</a> : "未收集"],
    ["字段溯源", provenanceRows || "未收集结构化 provenance"],
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
    `更新 ${Number(res.updated) || 0}`,
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
  importAllowed,
  qualityStatus,
  blockReason,
  msg,
  err,
  onPreview,
  onRun,
}: {
  busy: boolean;
  disabled: boolean;
  importAllowed: boolean | null;
  qualityStatus: string;
  blockReason: string;
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
          title="只返回已核验候选计划，不写库、不对外请求"
          className="flex items-center gap-1.5 rounded-lg border border-line bg-card px-3 py-1.5 text-[11.5px] text-muted transition-colors hover:text-ink disabled:cursor-default"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : null}
          预检
        </button>
        <button
          type="button"
          disabled={busy || disabled || importAllowed !== true}
          onClick={onRun}
          title={importAllowed === true ? "最新预检已通过" : "须先预检且 import_allowed=true"}
          className="flex items-center gap-1.5 rounded-lg border border-accent bg-accent-soft px-3 py-1.5 text-[11.5px] text-accent transition-colors hover:border-accent-hover disabled:cursor-default"
        >
          导入已核验候选(≤20)
        </button>
      </div>
      <div
        className={`mt-2 rounded-lg border px-3 py-1.5 text-[10.5px] ${
          importAllowed == null
            ? "border-line bg-card text-muted"
            : importAllowed
              ? "border-good bg-good-soft text-good"
              : "border-crit bg-crit-soft text-crit"
        }`}
        role={importAllowed === false ? "alert" : "status"}
      >
        {importAllowed == null
          ? "导入门禁待预检 · 仅当 import_allowed=true 才可导入。"
          : importAllowed
            ? `质量门禁通过 · import_allowed=true · quality_status=${qualityStatus || "未返回"}`
            : `导入门禁已阻断 · import_allowed=false · quality_status=${qualityStatus || "未返回"}${blockReason ? ` · reason=${blockReason}` : ""}`}
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
  postalCode,
  phone,
  websiteUrl,
  lat,
  lng,
  brands,
  customBrand,
  publishAfterCreate,
  onName,
  onAddress,
  onCity,
  onState,
  onPostalCode,
  onPhone,
  onWebsiteUrl,
  onLat,
  onLng,
  onBrands,
  onCustomBrand,
  onPublishAfterCreate,
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
  postalCode: string;
  phone: string;
  websiteUrl: string;
  lat: string;
  lng: string;
  brands: string[];
  customBrand: string;
  publishAfterCreate: boolean;
  onName: (v: string) => void;
  onAddress: (v: string) => void;
  onCity: (v: string) => void;
  onState: (v: string) => void;
  onPostalCode: (v: string) => void;
  onPhone: (v: string) => void;
  onWebsiteUrl: (v: string) => void;
  onLat: (v: string) => void;
  onLng: (v: string) => void;
  onBrands: (v: string[]) => void;
  onCustomBrand: (v: string) => void;
  onPublishAfterCreate: (v: boolean) => void;
  adding: boolean;
  disabled: boolean;
  msg: string;
  err: string;
  onSubmit: () => void;
}) {
  const coordinatePairInvalid = Boolean(lat.trim()) !== Boolean(lng.trim())
    || (lat.trim() !== "" && (!Number.isFinite(Number(lat)) || !Number.isFinite(Number(lng))));
  const invalid = !name.trim() || !address.trim() || coordinatePairInvalid;
  const toggleBrand = (brand: string) => {
    onBrands(brands.includes(brand) ? brands.filter((value) => value !== brand) : [...brands, brand]);
  };
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        <input value={name} onChange={(ev) => onName(ev.target.value)} placeholder="名称*" className={INPUT_CLS} />
        <input value={address} onChange={(ev) => onAddress(ev.target.value)} placeholder="地址*" className={INPUT_CLS} />
        <input value={city} onChange={(ev) => onCity(ev.target.value)} placeholder="城市" className={INPUT_CLS} />
        <input value={state} onChange={(ev) => onState(ev.target.value)} placeholder="州" className={INPUT_CLS} />
        <input value={postalCode} onChange={(ev) => onPostalCode(ev.target.value)} placeholder="邮编" className={INPUT_CLS} />
        <input value={phone} onChange={(ev) => onPhone(ev.target.value)} placeholder="电话" className={INPUT_CLS} />
        <input value={websiteUrl} onChange={(ev) => onWebsiteUrl(ev.target.value)} placeholder="官网 / 门店 URL" className={`${INPUT_CLS} col-span-2`} />
        <input value={lat} onChange={(ev) => onLat(ev.target.value)} placeholder="纬度（可选）" inputMode="decimal" className={INPUT_CLS} />
        <input value={lng} onChange={(ev) => onLng(ev.target.value)} placeholder="经度（可选）" inputMode="decimal" className={INPUT_CLS} />
      </div>
      <fieldset className="rounded-[8px] border border-line bg-panel p-2">
        <legend className="px-1 text-[9.5px] text-muted">品牌关系（仅登记，不自动声称授权）</legend>
        <div className="flex flex-wrap gap-1.5">
          {DEALER_BRAND_OPTIONS.map((brand) => (
            <label key={brand} className={`cursor-pointer rounded-[6px] border px-2 py-1 text-[9px] ${brands.includes(brand) ? "border-accent bg-accent-soft text-accent" : "border-line bg-card text-muted"}`}>
              <input type="checkbox" className="sr-only" checked={brands.includes(brand)} onChange={() => toggleBrand(brand)} />
              {brand.replace("_", " ")}
            </label>
          ))}
        </div>
        <input value={customBrand} onChange={(ev) => onCustomBrand(ev.target.value)} placeholder="其他厂商（逗号分隔）" className={`${INPUT_CLS} mt-2 w-full`} />
      </fieldset>
      <label className="flex items-start gap-2 rounded-[8px] border border-line bg-panel px-2.5 py-2 text-[9.5px] leading-4 text-muted">
        <input type="checkbox" checked={publishAfterCreate} onChange={(event) => onPublishAfterCreate(event.target.checked)} className="mt-0.5" />
        <span>创建后立即发布到本地地图（需后端核验坐标与必填字段；不通过时保留草稿并显示原因）</span>
      </label>
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
      <p className="text-[10px] leading-[1.6] text-muted">名称 + 地址必填；经纬度必须成对填写。默认先存草稿，只有显式发布才进入本地地图。</p>
    </div>
  );
}

function optionalText(value: string): string | undefined {
  const text = value.trim();
  return text || undefined;
}

function normalizeCustomBrands(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim().toUpperCase().replace(/[^A-Z0-9]+/g, "_"))
    .filter(Boolean);
}

function localDateTimeToIso(value: string): string | undefined {
  const text = value.trim();
  if (!text) return undefined;
  const parsed = new Date(text);
  return Number.isFinite(parsed.getTime()) ? parsed.toISOString() : undefined;
}

export function DealerRecordEditor({
  dealer,
  busy,
  message,
  error,
  onSave,
  onPublish,
  onUnpublish,
}: {
  dealer: VkpiDealer;
  busy: boolean;
  message: string;
  error: string;
  onSave: (payload: VkpiDealerWritePayload) => void;
  onPublish: () => void;
  onUnpublish: () => void;
}) {
  const initialBrands = dealerBrandCodes(dealer);
  const commonInitial = initialBrands.filter((brand) => (DEALER_BRAND_OPTIONS as readonly string[]).includes(brand));
  const customInitial = initialBrands.filter((brand) => !(DEALER_BRAND_OPTIONS as readonly string[]).includes(brand));
  const [name, setName] = React.useState(String(dealer.name || ""));
  const [address, setAddress] = React.useState(String(dealer.address || ""));
  const [city, setCity] = React.useState(String(dealer.city || ""));
  const [state, setState] = React.useState(String(dealer.state || ""));
  const [postalCode, setPostalCode] = React.useState(String(dealer.postal_code || ""));
  const [phone, setPhone] = React.useState(String(dealer.phone || ""));
  const [email, setEmail] = React.useState(String(dealer.contact_email || ""));
  const [website, setWebsite] = React.useState(String(dealer.website_url || ""));
  const [socialUrls, setSocialUrls] = React.useState<Record<string, string>>(() => dealerSocialUrls(dealer));
  const [lat, setLat] = React.useState(dealer.lat == null ? "" : String(dealer.lat));
  const [lng, setLng] = React.useState(dealer.lng == null ? "" : String(dealer.lng));
  const [brands, setBrands] = React.useState<string[]>(commonInitial);
  const [customBrands, setCustomBrands] = React.useState(customInitial.join(", "));
  const [activityStatus, setActivityStatus] = React.useState(String(dealer.activity?.status || "unknown"));
  const [activityUrl, setActivityUrl] = React.useState(String(dealer.activity?.page_url || ""));
  const [nextEventAt, setNextEventAt] = React.useState(String(dealer.activity?.next_event_at || "").slice(0, 16));
  const [activityNote, setActivityNote] = React.useState(String(dealer.activity?.note || ""));

  React.useEffect(() => {
    const codes = dealerBrandCodes(dealer);
    setName(String(dealer.name || ""));
    setAddress(String(dealer.address || ""));
    setCity(String(dealer.city || ""));
    setState(String(dealer.state || ""));
    setPostalCode(String(dealer.postal_code || ""));
    setPhone(String(dealer.phone || ""));
    setEmail(String(dealer.contact_email || ""));
    setWebsite(String(dealer.website_url || ""));
    setSocialUrls(dealerSocialUrls(dealer));
    setLat(dealer.lat == null ? "" : String(dealer.lat));
    setLng(dealer.lng == null ? "" : String(dealer.lng));
    setBrands(codes.filter((brand) => (DEALER_BRAND_OPTIONS as readonly string[]).includes(brand)));
    setCustomBrands(codes.filter((brand) => !(DEALER_BRAND_OPTIONS as readonly string[]).includes(brand)).join(", "));
    setActivityStatus(String(dealer.activity?.status || "unknown"));
    setActivityUrl(String(dealer.activity?.page_url || ""));
    setNextEventAt(String(dealer.activity?.next_event_at || "").slice(0, 16));
    setActivityNote(String(dealer.activity?.note || ""));
  }, [dealer.id]);

  const coordinatePairValid = Boolean(lat.trim()) === Boolean(lng.trim())
    && (!lat.trim() || (Number.isFinite(Number(lat)) && Number.isFinite(Number(lng))));
  const submit = () => {
    if (!name.trim() || !address.trim() || !coordinatePairValid) return;
    const nextBrands = [...new Set([...brands, ...normalizeCustomBrands(customBrands)])];
    const payload: VkpiDealerWritePayload = {
      name: name.trim(),
      address: address.trim(),
      city: optionalText(city),
      state: optionalText(state),
      postal_code: optionalText(postalCode),
      phone: optionalText(phone),
      contact_email: optionalText(email),
      website_url: optionalText(website),
      social_links: DEALER_SOCIAL_PLATFORMS
        .map((platform) => ({ platform, url: String(socialUrls[platform.toLowerCase()] || "").trim() }))
        .filter((item) => item.url),
      brands: nextBrands,
      ...(lat.trim() ? { lat: Number(lat), lng: Number(lng) } : {}),
      activity: {
        status: activityStatus,
        page_url: optionalText(activityUrl) || null,
        checked_at: dealer.activity?.checked_at || null,
        next_event_at: localDateTimeToIso(nextEventAt) || null,
        note: optionalText(activityNote) || null,
      },
    };
    onSave(payload);
  };
  const toggleBrand = (brand: string) => {
    setBrands((current) => current.includes(brand) ? current.filter((item) => item !== brand) : [...current, brand]);
  };

  return (
    <section className="rounded-[10px] border border-line bg-panel p-3" aria-label="编辑 Dealer 记录">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-[11px] font-semibold text-ink">编辑门店信息与活动关联</div>
          <p className="mt-0.5 text-[9px] text-muted">保存只更新记录；地图显示必须另行点击发布。</p>
        </div>
        <span className={`rounded-[6px] border px-2 py-1 text-[9px] ${dealer.publication_status === "published" ? "border-good bg-good-soft text-good" : "border-line bg-card text-muted"}`}>
          {dealer.publication_status === "published" ? "本地地图已发布" : "未发布到地图"}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="名称*" className={INPUT_CLS} />
        <input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="地址*" className={INPUT_CLS} />
        <input value={city} onChange={(event) => setCity(event.target.value)} placeholder="城市" className={INPUT_CLS} />
        <input value={state} onChange={(event) => setState(event.target.value)} placeholder="州 / DC" className={INPUT_CLS} />
        <input value={postalCode} onChange={(event) => setPostalCode(event.target.value)} placeholder="邮编" className={INPUT_CLS} />
        <input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="电话" className={INPUT_CLS} />
        <input value={email} onChange={(event) => setEmail(event.target.value)} placeholder="公开邮箱" className={INPUT_CLS} />
        <input value={website} onChange={(event) => setWebsite(event.target.value)} placeholder="官网" className={INPUT_CLS} />
        <input value={lat} onChange={(event) => setLat(event.target.value)} placeholder="纬度" inputMode="decimal" className={INPUT_CLS} />
        <input value={lng} onChange={(event) => setLng(event.target.value)} placeholder="经度" inputMode="decimal" className={INPUT_CLS} />
      </div>
      {!coordinatePairValid ? <p className="mt-1 text-[9px] text-crit">经纬度必须成对填写且为数字。</p> : null}
      <div className="mt-3 rounded-[8px] border border-line bg-card p-2">
        <div className="text-[9px] text-muted">品牌关系（不代表官方授权）</div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {DEALER_BRAND_OPTIONS.map((brand) => (
            <button key={brand} type="button" onClick={() => toggleBrand(brand)} aria-pressed={brands.includes(brand)} className={`rounded-[6px] border px-2 py-1 text-[8.5px] ${brands.includes(brand) ? "border-accent bg-accent-soft text-accent" : "border-line text-muted"}`}>
              {brand.replace("_", " ")}
            </button>
          ))}
        </div>
        <input value={customBrands} onChange={(event) => setCustomBrands(event.target.value)} placeholder="其他厂商（逗号分隔）" className={`${INPUT_CLS} mt-2 w-full`} />
      </div>
      <fieldset className="mt-3 rounded-[8px] border border-line bg-card p-2">
        <legend className="px-1 text-[9px] text-muted">公开社媒链接（最多 5 项）</legend>
        <div className="grid gap-2 md:grid-cols-2">
          {DEALER_SOCIAL_PLATFORMS.map((platform) => {
            const key = platform.toLowerCase();
            return (
              <label key={platform} className="text-[9px] text-muted">{platform} URL
                <input
                  value={socialUrls[key] || ""}
                  onChange={(event) => setSocialUrls((current) => ({ ...current, [key]: event.target.value }))}
                  placeholder={`https://.../${platform.toLowerCase()}`}
                  className={`${INPUT_CLS} mt-1 w-full`}
                />
              </label>
            );
          })}
        </div>
        <p className="mt-2 text-[8.5px] leading-4 text-muted">链接只证明已登记公开账号；不自动推断影响力、粉丝量或本地覆盖范围。</p>
      </fieldset>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        <label className="text-[9px] text-muted">活动观测状态
          <select value={activityStatus} onChange={(event) => setActivityStatus(event.target.value)} className={`${INPUT_CLS} mt-1 w-full`}>
            <option value="unknown">未检索 / 未知</option>
            <option value="none_observed">本次未观测到</option>
            <option value="active">有公开活动</option>
          </select>
        </label>
        <label className="text-[9px] text-muted">活动页
          <input value={activityUrl} onChange={(event) => setActivityUrl(event.target.value)} placeholder="https://..." className={`${INPUT_CLS} mt-1 w-full`} />
        </label>
        <label className="text-[9px] text-muted">下一场时间
          <input type="datetime-local" value={nextEventAt} onChange={(event) => setNextEventAt(event.target.value)} className={`${INPUT_CLS} mt-1 w-full`} />
        </label>
        <label className="text-[9px] text-muted">活动说明
          <input value={activityNote} onChange={(event) => setActivityNote(event.target.value)} placeholder="活动类型 / 覆盖范围 / 待复查" className={`${INPUT_CLS} mt-1 w-full`} />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy || !name.trim() || !address.trim() || !coordinatePairValid} onClick={submit} className="rounded-[7px] border border-accent bg-accent-soft px-3 py-1.5 text-[10px] text-accent disabled:cursor-default">
          {busy ? "处理中…" : "保存修改"}
        </button>
        {dealer.publication_status === "published" ? (
          <button type="button" disabled={busy} onClick={onUnpublish} className="rounded-[7px] border border-warn bg-warn-soft px-3 py-1.5 text-[10px] text-warn">从地图撤下</button>
        ) : (
          <button type="button" disabled={busy} onClick={onPublish} className="rounded-[7px] border border-good bg-good-soft px-3 py-1.5 text-[10px] text-good">发布到本地地图</button>
        )}
        {message ? <span className="text-[10px] text-good" role="status">{message}</span> : null}
        {error ? <span className="text-[10px] text-crit" role="alert">{error}</span> : null}
      </div>
    </section>
  );
}

export function DealerActivityExtension({ dealers }: { dealers: VkpiDealer[] | null }) {
  if (!dealers) return <LoadingLine text="当前页活动扩展读取中…" />;
  const active = dealers.filter((dealer) => dealer.activity?.status === "active");
  const observedNone = dealers.filter((dealer) => dealer.activity?.status === "none_observed").length;
  const unknown = dealers.length - active.length - observedNone;
  return (
    <div data-testid="dealer-activity-extension" className="space-y-2">
      <div className="grid grid-cols-3 gap-2">
        {[["当前页·有公开活动", active.length], ["当前页·本次未观测", observedNone], ["当前页·未检索 / 未知", unknown]].map(([label, value]) => (
          <div key={String(label)} className="rounded-[8px] border border-line bg-panel px-2.5 py-2">
            <div className="text-[8.5px] text-muted">{label}</div>
            <div className="mt-0.5 font-mono text-[15px] font-semibold text-ink">{value}</div>
          </div>
        ))}
      </div>
      {active.length ? active.slice(0, 5).map((dealer) => (
        <div key={String(dealer.id)} className="flex flex-wrap items-center gap-2 rounded-[8px] border border-line px-2.5 py-2 text-[9.5px]">
          <span className="font-semibold text-ink">{dealer.name}</span>
          <span className="text-muted">{dealer.activity?.next_event_at ? `下一场 ${formatLocal(dealer.activity.next_event_at)}` : "时间待补"}</span>
          {dealer.activity?.page_url ? <a href={dealer.activity.page_url} target="_blank" rel="noreferrer" className="ml-auto text-accent underline">活动页</a> : <span className="ml-auto text-warn">活动页待补</span>}
        </div>
      )) : <PendingCard>当前名录页没有已确认活动。未检索记录保持「未知」，后续可按 dealer_id 接入 Event Radar。</PendingCard>}
    </div>
  );
}
