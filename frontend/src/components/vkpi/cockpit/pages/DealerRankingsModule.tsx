import React from "react";
import { EmptyLine, ErrorCard, LoadingLine, PendingCard } from "./MarketVoicePage.modules";
import { formatLocal } from "../../lib/timeLocal";
import type { VkpiDealerRankings, VkpiDealerRankingRow } from "../../../../services/vkpi/dealers-api";

// Dealers · 排名与知名度模块(rankD)—— vkpi_dealer_web_verification 最新回执/店。
//   目标路径 frontend/src/components/vkpi/cockpit/pages/DealerRankingsModule.tsx;
//   金样板 = DealersBoardPage.modules.tsx 同构(本文件零直连网络,取数在 page 层)。
//   卡面:核验汇总条(已核验 x/y · Viltrox 在售 z)+ 排名行(名次 / 店名+城市州 /
//   体量档徽 / 知名度分条 / Viltrox 在售徽 / 官网核验状态徽),face 10 行 +
//   「≡ 查看全量」;诚实空态(未核验 → 「尚未核验」,迁移未到 → migration_pending)。
// 红线:卡面零内部术语(只说「官网核验」「知名度」;核验通道口径全进 SrcChip/溯源);
//   颜色全 token 零写死色;禁 token 色 opacity 修饰类;时间绝对时间戳(存 UTC 按浏览
//   器时区);知名度分是描述性公开信号,绝不写 viltrox_fit_score / 不触 rule_v0;
//   「Viltrox 在售」徽只代表核验时点零售商自有页面出现过 Viltrox,不代表实时库存 /
//   成交 / 官方授权(授权口径另在 authorization_evidence)。

export const RANK_FACE_ROWS = 10;

/* ============ modules.tsx 注册表段(rankD 口径;拷入 MODULE_SOURCES / PROV_TITLES) ============ */
export const RANK_MODULE_SOURCE: { label: string; rows: Array<[string, string]> } = {
  label: "vkpi_dealer_web_verification · dealers/rankings",
  rows: [
    ["读取", "GET /dealers/rankings 每店最新一条核验回执(历史全保留,只取 MAX(verified_at))"],
    ["核验通道", "Gemini 联网搜索逐店判定(官网在营 / 仍售相机器材 / Viltrox 在售 / 体量档 / 知名度);证据 URL 逐条落库可溯"],
    ["Viltrox 在售", "confirmed 必须带零售商自有域名页面 URL;只代表核验时点页面出现过 Viltrox,不代表实时库存、成交或官方授权"],
    ["体量档", "全国连锁 / 区域连锁 / 本地单店 / 以线上为主 / 待定 —— 依零售商自有门店页 + 公开来源判定"],
    ["知名度分", "0-100 公开知名度信号(连锁店数 / 媒体口碑 / 本地评价规模),附判定依据;描述性排序用,绝不进任何评分管线"],
    ["空态", "未跑核验 → 「尚未核验」如实;数据表未迁移 → migration_pending;绝不编名次"],
  ],
};

export const RANK_PROV_TITLE = "排名与知名度";

/* ============ 徽/文案映射(卡面零术语;全 token 色) ============ */
const TIER_LABELS: Record<string, { text: string; cls: string }> = {
  national_chain: { text: "全国连锁", cls: "border-accent bg-accent-soft text-accent" },
  regional_chain: { text: "区域连锁", cls: "border-good bg-good-soft text-good" },
  local_store: { text: "本地单店", cls: "border-line bg-panel text-ink-2" },
  online_focus: { text: "以线上为主", cls: "border-line bg-panel text-ink-2" },
  unknown: { text: "规模待定", cls: "border-line bg-card text-muted" },
};

const SITE_LABELS: Record<string, { text: string; cls: string }> = {
  alive: { text: "官网在营", cls: "border-good bg-good-soft text-good" },
  dead: { text: "官网失效", cls: "border-crit bg-crit-soft text-crit" },
  unreachable: { text: "暂无法核验", cls: "border-warn bg-warn-soft text-warn" },
};

function tierBadge(tier: string | null | undefined) {
  const item = TIER_LABELS[String(tier || "unknown")] || TIER_LABELS.unknown;
  return (
    <span className={`flex-none rounded-[5px] border px-1.5 py-px text-[8.5px] font-semibold ${item.cls}`}>
      {item.text}
    </span>
  );
}

function siteBadge(status: string | null | undefined) {
  const item = SITE_LABELS[String(status || "unreachable")] || SITE_LABELS.unreachable;
  return (
    <span className={`flex-none rounded-[5px] border px-1.5 py-px text-[8.5px] font-bold ${item.cls}`}>
      {item.text}
    </span>
  );
}

function viltroxBadge(row: Pick<VkpiDealerRankingRow, "carries_viltrox" | "viltrox_evidence_url">) {
  if (row.carries_viltrox === "confirmed") {
    const badge = (
      <span className="flex-none rounded-[5px] border border-accent bg-accent-soft px-1.5 py-px text-[8.5px] font-bold text-accent">
        Viltrox 在售
      </span>
    );
    return row.viltrox_evidence_url ? (
      <a
        href={row.viltrox_evidence_url}
        target="_blank"
        rel="noreferrer"
        title="打开零售商自有页面证据(核验时点观测;不代表实时库存 / 授权)"
        onClick={(ev) => ev.stopPropagation()}
        className="flex-none"
      >
        {badge}
      </a>
    ) : badge;
  }
  if (row.carries_viltrox === "not_found") {
    return (
      <span className="flex-none rounded-[5px] border border-line bg-card px-1.5 py-px text-[8.5px] text-muted">
        未见 Viltrox
      </span>
    );
  }
  return (
    <span className="flex-none rounded-[5px] border border-line bg-card px-1.5 py-px text-[8.5px] text-muted">
      待确认
    </span>
  );
}

/* ============ 知名度分条(0-100;无分 → 「未评」如实) ============ */
function ProminenceBar({ score }: { score: number | null | undefined }) {
  if (score == null) {
    return <span className="w-[88px] flex-none text-right text-[9px] text-muted">未评</span>;
  }
  return (
    <span className="flex w-[88px] flex-none items-center gap-1.5" title={`知名度 ${score}/100`}>
      <span className="relative h-[8px] min-w-0 flex-1 overflow-hidden rounded-[3px] border border-line bg-card">
        <span
          className="absolute inset-y-0 left-0 rounded-[2px] bg-accent"
          style={{ width: `${Math.max(3, Math.min(100, score))}%` }}
        />
      </span>
      <span className="w-[24px] flex-none text-right font-mono text-[9.5px] tabular-nums text-ink">{score}</span>
    </span>
  );
}

/* ============ 排名单行:名次 / 店名+城市州 / 体量徽 / 知名度条 / Viltrox 徽 / 官网状态 ============ */
export function DealerRankingRowLine({
  row,
  onOpen,
}: {
  row: VkpiDealerRankingRow;
  onOpen?: (row: VkpiDealerRankingRow) => void;
}) {
  const cityState = [String(row.city || "").trim(), String(row.state || "").trim().toUpperCase()]
    .filter(Boolean)
    .join(", ");
  return (
    <div
      className={`group flex min-w-0 items-center gap-2 border-b border-line py-2 last:border-0 ${onOpen ? "cursor-pointer" : ""}`}
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
      onClick={onOpen ? () => onOpen(row) : undefined}
      onKeyDown={
        onOpen
          ? (ev: React.KeyboardEvent) => {
              if (ev.key === "Enter" || ev.key === " ") {
                ev.preventDefault();
                onOpen(row);
              }
            }
          : undefined
      }
    >
      <span className="w-[26px] flex-none text-right font-mono text-[10.5px] font-semibold tabular-nums text-ink">
        {row.rank != null ? `#${row.rank}` : "—"}
      </span>
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent">
        {String(row.name || "—")}
        {cityState ? <span className="ml-1.5 text-[9.5px] text-muted">· {cityState}</span> : null}
        {row.location_count && row.location_count > 1 ? (
          <span className="ml-1.5 rounded-[5px] border border-line bg-panel px-1.5 py-px text-[8.5px] text-muted">
            连锁 ×{row.location_count}{row.states && row.states.length ? ` · ${row.states.join("/")}` : ""}
          </span>
        ) : null}
      </span>
      {row.is_camera_retailer === false ? (
        <span className="flex-none rounded-[5px] border border-warn bg-warn-soft px-1.5 py-px text-[8.5px] text-warn">
          疑似不再售器材
        </span>
      ) : null}
      {tierBadge(row.scale_tier)}
      <ProminenceBar score={row.prominence_score} />
      {viltroxBadge(row)}
      {siteBadge(row.website_status)}
    </div>
  );
}

/* ============ 单条详情行组(MiniDetailModal rows;时间绝对时间戳) ============ */
export function dealerRankingDetailRows(row: VkpiDealerRankingRow): Array<[string, React.ReactNode]> {
  const tier = TIER_LABELS[String(row.scale_tier || "unknown")] || TIER_LABELS.unknown;
  const site = SITE_LABELS[String(row.website_status || "unreachable")] || SITE_LABELS.unreachable;
  return [
    ["名次", row.rank != null ? `#${row.rank}` : "未评知名度分 · 不参与名次"],
    ["名称", String(row.name || "—")],
    ["城市 / 州", [row.city || "—", row.state || "—"].join(" · ")],
    ...(row.location_count && row.location_count > 1
      ? [[
          `连锁门店(×${row.location_count})`,
          (row.locations || [])
            .map((loc) => `${loc.city || "—"}, ${loc.state || "—"}${loc.carries_viltrox === "confirmed" ? " ✓在售" : ""}`)
            .join(" · ") || (row.states || []).join(" / "),
        ]] as Array<[string, React.ReactNode]>
      : []),
    ["官网核验", site.text],
    ["仍售相机器材", row.is_camera_retailer === true ? "是(零售商自有页面可见)" : row.is_camera_retailer === false ? "本次核验未见在售器材" : "本次未能确认"],
    ["体量档", tier.text],
    ["知名度", row.prominence_score != null ? `${row.prominence_score} / 100` : "未评"],
    ["判定依据", String(row.prominence_rationale || "—")],
    [
      "Viltrox 在售",
      row.carries_viltrox === "confirmed" ? (
        row.viltrox_evidence_url ? (
          <a href={row.viltrox_evidence_url} target="_blank" rel="noreferrer" className="text-accent underline">
            打开零售商页面证据
          </a>
        ) : "已确认(证据 URL 缺失)"
      ) : row.carries_viltrox === "not_found" ? "已检索,未见 Viltrox" : "待确认",
    ],
    ["口径", "「在售」只代表核验时点零售商自有页面出现过 Viltrox;不代表实时库存、成交或官方授权"],
    [
      "官网",
      row.website_url ? (
        <a href={row.website_url} target="_blank" rel="noreferrer" className="text-accent underline">
          打开零售商站点
        </a>
      ) : "未登记",
    ],
    ["核验时间", row.verified_at ? `${formatLocal(row.verified_at)}(UTC 存 · 按浏览器时区显示)` : "—"],
    ["库记录", `vkpi_dealer_web_verification · dealer #${row.dealer_id}`],
  ];
}

/* ============ 模块 body(page 层闸 token/error;本件只按 status 诚实渲染) ============ */
export function DealerRankingsBody({
  data,
  error,
  onOpen,
  onOpenAll,
}: {
  data: VkpiDealerRankings | null;
  error: string;
  onOpen?: (row: VkpiDealerRankingRow) => void;
  onOpenAll?: () => void;
}) {
  if (error) return <ErrorCard title="排名读取失败" text={error} />;
  if (!data) return <LoadingLine text="核验回执读取中…" />;
  if (data.status === "migration_pending") {
    return (
      <PendingCard>
        <b>数据表待迁移</b> —— 官网核验回执表尚未建立;{Number(data.published_total || 0).toLocaleString()} 家已发布经销商全部「尚未核验」。
      </PendingCard>
    );
  }
  if (data.status !== "ready" || data.rankings.length === 0) {
    return (
      <PendingCard>
        <b>尚未核验</b> —— 已发布 {Number(data.published_total || 0).toLocaleString()} 家经销商还没有官网核验回执;跑一轮核验后名次自动点亮,绝不编名次。
      </PendingCard>
    );
  }
  const summary = [
    ["已核验", `${Number(data.verified_count).toLocaleString()} / ${Number(data.published_total).toLocaleString()}`],
    ["Viltrox 在售", Number(data.viltrox_confirmed_count).toLocaleString()],
    ["待核验", Number(data.unverified_count).toLocaleString()],
  ] as const;
  return (
    <div>
      <div className="mb-2 grid grid-cols-3 gap-2">
        {summary.map(([label, value]) => (
          <div key={label} className="rounded-[8px] border border-line bg-panel px-2.5 py-2">
            <div className="text-[9px] text-muted">{label}</div>
            <div className="mt-0.5 font-mono text-[13px] font-semibold tabular-nums text-ink">{value}</div>
          </div>
        ))}
      </div>
      {data.rankings.length === 0 ? (
        <EmptyLine text="0 行 · 暂无排名数据(如实)。" />
      ) : (
        <div>
          {data.rankings.slice(0, RANK_FACE_ROWS).map((row) => (
            <DealerRankingRowLine key={String(row.dealer_id)} row={row} onOpen={onOpen} />
          ))}
          {data.rankings.length > RANK_FACE_ROWS && onOpenAll ? (
            <button
              type="button"
              onClick={onOpenAll}
              className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
            >
              ≡ 查看全部 {data.rankings.length} 家排名
            </button>
          ) : null}
        </div>
      )}
      <p className="mt-2 text-[9px] leading-4 text-muted">
        知名度分为公开信号(连锁规模 / 媒体口碑 / 本地评价规模)排序用;「Viltrox 在售」为核验时点页面观测,不代表实时库存或官方授权。
      </p>
    </div>
  );
}

/* ======================================================================
 * 接线手册(不随本文件编译;拷贝到对应文件后删除本段亦可保留作口径注释)
 *
 * ① DealersBoardPage.modules.tsx —— MODULE_SOURCES 注册表加一段(照 kpiD 风格):
 *
 *   rankD: {
 *     label: "vkpi_dealer_web_verification · dealers/rankings",
 *     rows: [
 *       ["读取", "GET /dealers/rankings 每店最新一条核验回执(历史全保留,只取 MAX(verified_at))"],
 *       ["核验通道", "Gemini 联网搜索逐店判定(官网在营 / 仍售相机器材 / Viltrox 在售 / 体量档 / 知名度);证据 URL 逐条落库可溯"],
 *       ["Viltrox 在售", "confirmed 必须带零售商自有域名页面 URL;只代表核验时点页面出现过 Viltrox,不代表实时库存、成交或官方授权"],
 *       ["体量档", "全国连锁 / 区域连锁 / 本地单店 / 以线上为主 / 待定 —— 依零售商自有门店页 + 公开来源判定"],
 *       ["知名度分", "0-100 公开知名度信号(连锁店数 / 媒体口碑 / 本地评价规模),附判定依据;描述性排序用,绝不进任何评分管线"],
 *       ["空态", "未跑核验 → 「尚未核验」如实;数据表未迁移 → migration_pending;绝不编名次"],
 *     ],
 *   },
 *
 *   PROV_TITLES 加:  rankD: "排名与知名度",
 *   (或改为 import { RANK_MODULE_SOURCE, RANK_PROV_TITLE } from "./DealerRankingsModule"
 *    后写 rankD: RANK_MODULE_SOURCE / rankD: RANK_PROV_TITLE,二选一,别双写。)
 *
 * ② DealersBoardPage.tsx —— 五处:
 *
 *   a) import:
 *      import { DealerRankingsBody, dealerRankingDetailRows } from "./DealerRankingsModule";
 *      import { getDealerRankings, type VkpiDealerRankingRow } from "../../../../services/vkpi/dealers-api";
 *      (getDealerRankings 并入现有 dealers-api import 块)
 *
 *   b) DealerReadSource 加 "rankings";EMBEDDED_MODULE_SOURCES 加:
 *      rankD: ["rankings"],
 *
 *   c) 只读端点区(kpiSeriesResp 之后)加:
 *      const rankingsResp = useEndpoint(
 *        needsSource("rankings") ? token : "",
 *        reloadTick,
 *        React.useCallback((t: string) => getDealerRankings(t), []),
 *      );
 *      弹窗状态(rosterIdx 一排)加:
 *      const [rankRow, setRankRow] = React.useState<VkpiDealerRankingRow | null>(null);
 *      const [rankListOpen, setRankListOpen] = React.useState(false);
 *
 *   d) 模块 body 区加:
 *      const renderRankings = () => (
 *        <ModuleCard {...cardProps("rankD", "排名与知名度",
 *            rankingsResp.data?.status === "ready" ? `${rankingsResp.data.verified_count} 家` : undefined,
 *            rankingsResp.data?.generated_at
 *              ? [["生成时间", `${formatLocal(rankingsResp.data.generated_at)}(UTC 存 · 按浏览器时区显示)`]]
 *              : undefined)}>
 *          {!token ? noTokenCard : (
 *            <DealerRankingsBody
 *              data={rankingsResp.data ?? null}
 *              error={rankingsResp.error}
 *              onOpen={(row) => setRankRow(row)}
 *              onOpenAll={() => setRankListOpen(true)}
 *            />
 *          )}
 *        </ModuleCard>
 *      );
 *
 *   e) modules 注册表数组加(palette 全量可选;默认布局可暂不加,让用户自选):
 *      { key: "rankD", label: "排名与知名度", description: "官网核验回执排名 · 体量档 / 知名度分 / Viltrox 在售 · 未核验如实空态", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 14, minHeight: 7, maxHeight: 26, render: renderRankings },
 *      若要进默认布局:DEFAULT_LAYOUT 在 mapD 之后插 { moduleKey: "rankD", span: 12 }
 *      并把 STORAGE_KEY 升 "vkpi-dealers-layout-v3-truth"、旧键并入 LEGACY_STORAGE_KEYS。
 *
 *   f) 返回 JSX 弹窗区(rosterListOpen 一排)加:
 *      {rankListOpen && rankingsResp.data && (
 *        <MiniListModal title="排名与知名度 · 全量" total={rankingsResp.data.rankings.length} unit="家" onClose={() => setRankListOpen(false)}>
 *          {rankingsResp.data.rankings.map((row) => (
 *            <DealerRankingRowLine key={String(row.dealer_id)} row={row} onOpen={(r) => setRankRow(r)} />
 *          ))}
 *        </MiniListModal>
 *      )}
 *      {rankRow && (
 *        <MiniDetailModal
 *          title={String(rankRow.name || `经销商 #${rankRow.dealer_id}`)}
 *          rows={dealerRankingDetailRows(rankRow)}
 *          index={0}
 *          total={1}
 *          onNav={() => undefined}
 *          onClose={() => setRankRow(null)}
 *        />
 *      )}
 *      (DealerRankingRowLine 也要出现在 import;MiniListModal / MiniDetailModal 现成。)
 * ====================================================================== */
