import React from "react";
import { ProvChain, RecordPreview, SrcChip, type ProvStep } from "../components/provenance";
import { formatLocal } from "../../lib/timeLocal";
import type { VoiceFeedItem } from "../../../../services/vkpi/marketVoice-api";

// 市场之声 · 板块页范式 V0a 辅助件(MarketVoicePage 专用,页内拆件不入公共桶)。
//   - 月报四段(抱怨/愿望/空白/建议)+ 分桶:自旧版 340 行原样搬入,仅把写死色换成
//     token 类(bg-card/text-ink/text-muted/border-line/text-accent…),逻辑零改动。
//   - KPI 带:demo KPI 卡样式 = ds-viz.css 的 ds-kpi 类;无趋势序列 → 诚实
//     ds-kpi__series-empty(单窗口计数,不画假 sparkline);情感 0 行 → pending 诚实卡。
//   - 覆盖行 coverrow / 反馈行 fb / 详情弹窗 fnav:1:1 对照
//     ~/.claude/skills/vkpi-dashboard-demo/assets/V-KPI-板块页范式-Demo.html。
//   - 溯源 = 复用 SrcChip / ProvChain / RecordPreview 三组件(components/provenance)。
// 红线:纯展示零执行;不触 viltrox_fit_score / rule_v0;颜色全 token 类零写死色;
//        动效只用 ds-viz.css 既有类(自带 reduced-motion 降级),不新增动画。

export type Row = Record<string, any>;

export const KIND_META: Record<string, { label: string; tone: string }> = {
  new_product: { label: "新品评估", tone: "border-good bg-good-soft text-good" },
  iteration: { label: "迭代评估", tone: "border-accent bg-accent-soft text-accent" },
  mount_expansion: { label: "卡口扩展", tone: "border-accent-2 text-accent-2" },
  improvement: { label: "改进关注", tone: "border-warn bg-warn-soft text-warn" },
  watch: { label: "声量关注", tone: "border-line bg-card text-ink-2" },
};

export const CONFIDENCE_LABEL: Record<string, string> = { high: "置信高", medium: "置信中", low: "置信低" };

export const SOURCE_LABEL: Record<string, string> = {
  comments: "评论库",
  intent_queue: "意向队列",
  bh_reviews: "B&H 口碑",
  brand_signal: "需求信号",
  sentiment: "情感结果",
};

// 覆盖模块的固定源顺序(voice-report sources 键)
export const SOURCE_ORDER = ["comments", "intent_queue", "bh_reviews", "brand_signal", "sentiment"] as const;

export function EmptyLine({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-[12px] text-muted">{text}</div>;
}

export function ErrorCard({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-lg border border-crit bg-crit-soft px-3 py-2 text-[12px] text-crit">
      <div className="font-semibold">{title}</div>
      {text ? <div className="mt-0.5 text-[11px] opacity-90">{text}</div> : null}
    </div>
  );
}

export function LoadingLine({ text = "市场之声聚合中…" }: { text?: string }) {
  return <div className="py-6 text-center text-[12px] text-muted">{text}</div>;
}

// 原声引文列表(可展开):summary 显示条数,展开逐条带作者/平台/时间/来源表。(旧版原样保留)
export function QuoteFold({ quotes, summary }: { quotes: Row[]; summary?: string }) {
  if (!Array.isArray(quotes) || quotes.length === 0) return null;
  return (
    <details className="mt-1.5">
      <summary className="cursor-pointer select-none text-[10px] text-accent hover:text-accent-hover">
        {summary || `原声引文 ×${quotes.length}(点开)`}
      </summary>
      <div className="mt-1.5 space-y-1.5">
        {quotes.map((q, i) => (
          <div key={i} className="rounded-lg border border-line bg-panel px-3 py-2">
            <div className="text-[11.5px] leading-relaxed text-ink-2">“{q.text}”</div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-muted">
              <span>{q.author || "匿名"}</span>
              {q.platform ? <span>{q.platform}</span> : null}
              {q.at ? <span>{String(q.at).slice(0, 10)}</span> : null}
              {Number(q.likes) > 0 ? <span>♥ {q.likes}</span> : null}
              {q.source ? <span className="font-mono opacity-80">{q.source}</span> : null}
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}

/* ============ 模块卡骨架:卡头(标题 + 计数 + SrcChip)+ 可滚 body ============ */
export function ModuleCard({
  title,
  cnt,
  srcLabel,
  srcRows,
  onOpenSrc,
  children,
}: {
  title: string;
  cnt?: React.ReactNode;
  srcLabel: string;
  srcRows: Array<[string, string]>;
  onOpenSrc?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="flex h-full min-h-0 flex-col rounded-ds-lg border border-line bg-card shadow-ds-sm">
      <header className="flex flex-none items-center justify-between gap-2 px-4 pb-2 pt-3">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="truncate text-[13px] font-semibold text-ink">{title}</h3>
          {cnt != null && <span className="flex-none text-[10px] text-muted">{cnt}</span>}
        </div>
        <SrcChip label={srcLabel} rows={srcRows} onOpen={onOpenSrc} />
      </header>
      <div className="min-h-0 flex-1 overflow-auto px-4 pb-4">{children}</div>
    </section>
  );
}

/* ============ KPI 带:demo KPI 卡样式(ds-kpi 类) ============ */
// 无历史序列(voice-report 为单窗口计数)→ 诚实 series-empty,不画假 sparkline。
export function KpiCard({
  label,
  value,
  unit,
  seriesNote,
  pending,
  pendingNote,
}: {
  label: string;
  value: React.ReactNode;
  unit?: string;
  seriesNote: string;
  pending?: boolean;
  pendingNote?: string;
}) {
  return (
    <div className="ds-kpi ds-kpi-rise">
      <div className="ds-kpi__k">
        <span className={`ds-kpi__dot ${pending ? "ds-kpi__dot--pend" : "ds-kpi__dot--good"}`} />
        <span className="ds-kpi__label">{label}</span>
      </div>
      <div className={`ds-kpi__val${pending ? " ds-kpi__val--pend" : ""}`}>
        {pending ? "—" : value}
        {!pending && unit ? <span className="ds-kpi__u">{unit}</span> : null}
        {pending && pendingNote ? <span className="ds-kpi__delta ds-kpi__delta--pend">{pendingNote}</span> : null}
      </div>
      <div className="ds-kpi__series-empty">{seriesNote}</div>
    </div>
  );
}

/* ============ 覆盖行:demo .coverrow(绿点 + 名称 + mono 状态) ============ */
export function CoverRow({ on, name, table, value, note }: { on: boolean; name: string; table: string; value: string; note?: string }) {
  return (
    <div className="flex items-center gap-2 border-b border-line py-1.5 text-[11.5px] last:border-0" title={note || table}>
      <span
        className={`h-[7px] w-[7px] flex-none rounded-full ${on ? "bg-good" : "bg-muted opacity-50"}`}
        style={on ? { boxShadow: "0 0 5px var(--ds-good)" } : undefined}
      />
      <span className={`min-w-0 flex-1 truncate ${on ? "text-ink-2" : "text-muted"}`}>
        {name} <span className="font-mono text-[9px] opacity-80">{table}</span>
      </span>
      <span className={`flex-none font-mono text-[9.5px] ${on ? "text-muted" : "text-warn"}`}>{value}</span>
    </div>
  );
}

/* ============ 反馈流行:demo .fb(平台徽 + 身份徽 + 摘要 + likes + 绝对时间 + ↗) ============ */
export const IDENTITY_META: Record<string, { label: string; cls: string; text: string }> = {
  kol: { label: "KOL", cls: "border-accent text-accent", text: "text-accent" },
  owned: { label: "官号", cls: "border-accent-2 text-accent-2", text: "text-accent-2" },
  user: { label: "路人", cls: "border-line text-muted", text: "text-muted" },
};

const PLATFORM_SHORT: Record<string, string> = {
  youtube: "YT",
  instagram: "IG",
  tiktok: "TT",
  reddit: "REDDIT",
  amazon: "AMZN",
  bilibili: "BILI",
  facebook: "FB",
  twitter: "X",
  x: "X",
};

export function platformBadge(platform: string) {
  const key = String(platform || "").trim().toLowerCase();
  return PLATFORM_SHORT[key] || (key ? key.toUpperCase().slice(0, 6) : "—");
}

export function FeedRowLine({ item, index, onOpen }: { item: VoiceFeedItem; index: number; onOpen: (i: number) => void }) {
  const idn = IDENTITY_META[item.identity] || IDENTITY_META.user;
  return (
    <div
      className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(index)}
      onKeyDown={(ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          onOpen(index);
        }
      }}
    >
      <span className="min-w-[46px] flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-center text-[8.5px] font-semibold text-ink-2">
        {platformBadge(item.platform)}
      </span>
      <span
        className={`flex-none rounded-[5px] border px-1 py-px text-[8px] font-bold tracking-[0.05em] ${idn.cls}`}
        title={item.identity_ref || idn.label}
      >
        {idn.label}
      </span>
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent">{item.text || "—"}</span>
      {Number(item.likes) > 0 && <span className="flex-none font-mono text-[9.5px] text-muted">♥ {item.likes}</span>}
      <span className="flex-none font-mono text-[9.5px] text-muted" title={item.created_at ? `${item.created_at}(UTC 存 · 按浏览器时区显示)` : "无时间戳"}>
        {formatLocal(item.created_at)}
      </span>
      {item.post_url ? (
        <a
          className="vkpi-prov-pchip vkpi-prov-pchip--ext vkpi-prov-pchip--mini flex-none"
          href={item.post_url}
          target="_blank"
          rel="noopener noreferrer"
          title={`${item.platform || "原帖"} · 直跳原帖`}
          onClick={(ev) => ev.stopPropagation()}
        >
          ↗
        </a>
      ) : null}
    </div>
  );
}

/* ============ 弹窗骨架(中央,token 化;Escape 由调用方/详情键盘钩子处理) ============ */
export function ModalShell({
  title,
  sub,
  onClose,
  children,
  maxWidth = "max-w-2xl",
}: {
  title: React.ReactNode;
  sub?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  maxWidth?: string;
}) {
  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className="cockpit-modal fixed inset-0 flex items-center justify-center bg-black/75 p-4 backdrop-blur-md"
      style={{ zIndex: 999 }}
      role="presentation"
      onMouseDown={(ev) => {
        if (ev.target === ev.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" className={`relative flex max-h-[88vh] w-full ${maxWidth} flex-col overflow-hidden rounded-2xl border border-line bg-card shadow-2xl`}>
        <div className="flex flex-none items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <div className="text-[14px] font-semibold text-ink">{title}</div>
            {sub ? <div className="mt-0.5 text-[10.5px] text-muted">{sub}</div> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="flex-none rounded-lg border border-line px-2 py-1 text-[12px] text-muted hover:border-line-strong hover:text-ink"
          >
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="mb-1.5 text-[9px] font-semibold uppercase tracking-[0.13em] text-muted">{children}</div>;
}

export function Drow({ k, v, tone }: { k: string; v: React.ReactNode; tone?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line py-1.5 text-[11px] last:border-0">
      <span className="flex-none text-muted">{k}</span>
      <span className={`min-w-0 truncate text-right font-mono ${tone || "text-ink"}`}>{v}</span>
    </div>
  );
}

/* ============ 反馈详情弹窗:‹ #n/N › + ↑↓ 连续翻 + 溯源链 + 库记录预览 ============ */
const NAV_BTN = "rounded-lg border border-line px-2.5 py-1 text-[11px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:cursor-default disabled:opacity-40";

function feedRecordRows(item: VoiceFeedItem, rec: string): Array<[string, string]> {
  const prov = item.prov || ({} as VoiceFeedItem["prov"]);
  if (rec === "post") {
    return [
      ["表", prov.post_table || "—"],
      ["post_id", prov.post_id != null ? `#${prov.post_id}` : "—"],
      ["fetched_at", prov.fetched_at || "—"],
      ["说明", "评论经 post_table + post_id 挂靠原帖"],
    ];
  }
  const idn = IDENTITY_META[item.identity] || IDENTITY_META.user;
  return [
    ["表", item.source_table || "vkpi_comments"],
    ["id", `#${item.id}`],
    ["platform", item.platform || "—"],
    ["language", item.language || "—"],
    ["likes", String(item.likes ?? 0)],
    ["identity", `${idn.label}${item.identity_ref ? ` · ${item.identity_ref}` : ""}`],
    ["created_at", item.created_at || "—"],
    ["fetched_at", prov.fetched_at || "—"],
    ["post_table", prov.post_table || "—"],
    ["post_id", prov.post_id != null ? `#${prov.post_id}` : "—"],
  ];
}

export function FeedDetailModal({
  item,
  index,
  total,
  loadingNext,
  onNav,
  onClose,
}: {
  item: VoiceFeedItem;
  index: number;
  total: number;
  loadingNext: boolean;
  onNav: (i: number) => void;
  onClose: () => void;
}) {
  const [rec, setRec] = React.useState<string | null>(null);

  // 切条时收起记录预览(每条用自己的记录链)
  React.useEffect(() => {
    setRec(null);
  }, [item?.id]);

  // ↑↓(以及 ←→)方向键连续翻(demo 同款);Escape 交给 ModalShell。
  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowRight") {
        ev.preventDefault();
        onNav(index + 1);
      } else if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") {
        ev.preventDefault();
        onNav(index - 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, onNav]);

  const idn = IDENTITY_META[item.identity] || IDENTITY_META.user;
  const prov = item.prov || ({} as VoiceFeedItem["prov"]);
  const steps: ProvStep[] = [];
  if (item.post_url) steps.push({ label: `原帖 · ${platformBadge(item.platform)}`, href: item.post_url });
  else steps.push({ label: "原帖 URL 缺失(该源无帖级链接)" });
  steps.push({
    label: `帖子 ${prov.post_table || "—"}${prov.post_id != null ? ` #${prov.post_id}` : ""}`,
    rec: "post",
  });
  steps.push({ label: `库记录 ${item.source_table || "vkpi_comments"} #${item.id}`, rec: "comments", rid: String(item.id) });

  return (
    <ModalShell
      title={`反馈详情 · ${platformBadge(item.platform)}`}
      sub={
        <>
          captured {formatLocal(item.created_at, { year: "numeric" })}(按浏览器时区)· 身份 <span className={idn.text}>{idn.label}{item.identity_ref ? ` ${item.identity_ref}` : ""}</span>
        </>
      }
      onClose={onClose}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button type="button" className={NAV_BTN} disabled={index <= 0} onClick={() => onNav(index - 1)}>
          ‹ 上一条
        </button>
        <span className="font-mono text-[10.5px] font-bold text-accent">
          #{index + 1} / {total}
        </span>
        <button type="button" className={NAV_BTN} disabled={index >= total - 1 || loadingNext} onClick={() => onNav(index + 1)}>
          {loadingNext ? "加载中…" : "下一条 ›"}
        </button>
        <button type="button" className={NAV_BTN} onClick={onClose}>
          ≡ 回列表
        </button>
        <span className="ml-auto font-mono text-[9px] text-muted">↑↓ 方向键连续翻</span>
      </div>

      <div className="mb-4">
        <SectionLabel>原文</SectionLabel>
        <div className="text-[13px] leading-[1.8] text-ink-2">{item.text || "—"}</div>
      </div>

      <div className="mb-4">
        <SectionLabel>数值行</SectionLabel>
        <Drow k="来源身份" v={`${idn.label}${item.identity_ref ? ` · ${item.identity_ref}` : ""}`} tone={idn.text} />
        <Drow k="语言" v={item.language || "—"} />
        <Drow k="likes" v={String(item.likes ?? 0)} />
        <Drow k="captured(本地)" v={formatLocal(item.created_at, { year: "numeric" })} />
        <Drow k="created_at(UTC)" v={item.created_at || "—"} />
        <Drow k="来源表" v={item.source_table || "vkpi_comments"} />
      </div>

      <div>
        <SectionLabel>溯源链 · 每一跳可点(本条专属记录)</SectionLabel>
        <ProvChain steps={steps} onRecord={(key) => setRec(key)} />
        {rec ? <RecordPreview title="库记录预览 · 点其他节点切换" rows={feedRecordRows(item, rec)} /> : null}
      </div>
    </ModalShell>
  );
}

/* ============ 模块溯源说明弹窗:口径 + 来源表 + ProvChain 通用链 ============ */
// 通用链末节点按真表名/真实口径标注(board-paradigm:无 market_insights 表,
// voice-report = lexicon_v0 实时聚合,无独立聚合表 —— 诚实口径,不编表名)。
const GENERIC_CHAIN: ProvStep[] = [
  { label: "原帖 ↗ · 逐条见反馈流" },
  { label: "采集 · Apify 抓取 / 官号同步", rec: "collect" },
  { label: "库记录 · vkpi_comments 等四源", rec: "store" },
  { label: "聚合 · voice-report(lexicon_v0 实时)", rec: "aggregate" },
];

const GENERIC_RECS: Record<string, Array<[string, string]>> = {
  collect: [
    ["链路", "Apify actor → raw 落库"],
    ["帖子表", "vkpi_kol_video_evidence / vkpi_employee_channels"],
    ["身份口径", "post_table 三分类:kol / owned / user"],
  ],
  store: [
    ["评论库", "vkpi_comments"],
    ["意向队列", "vkpi_reply_queue"],
    ["B&H 口碑", "vkpi_bh_reviews"],
    ["需求信号", "vkpi_brand_signal"],
    ["情感", "vkpi_sentiment_results(已建未跑)"],
  ],
  aggregate: [
    ["方法", "lexicon_v0 纯词表聚合 · 零 LLM"],
    ["端点", "GET /market/voice-report"],
    ["聚合表", "无独立聚合表 · 请求时实时计算"],
    ["下游", "本板块 / 产品部月报"],
  ],
};

export function ModuleProvModal({
  title,
  caliber,
  onClose,
}: {
  title: string;
  caliber: Array<[string, string]>;
  onClose: () => void;
}) {
  const [rec, setRec] = React.useState<string | null>(null);
  return (
    <ModalShell title={`${title} · 数据溯源`} sub="口径 + 来源表 + 通用溯源链(链上每跳可点)" onClose={onClose}>
      <div className="mb-4">
        <SectionLabel>口径与来源表</SectionLabel>
        {caliber.map(([k, v], i) => (
          <Drow key={`${k}-${i}`} k={k} v={v} />
        ))}
      </div>
      <div>
        <SectionLabel>溯源链 · 通用(逐条溯源见反馈流单条详情)</SectionLabel>
        <ProvChain steps={GENERIC_CHAIN} onRecord={(key) => setRec(key)} />
        {rec ? <RecordPreview title="口径预览 · 点其他节点切换" rows={GENERIC_RECS[rec] || []} /> : null}
      </div>
    </ModalShell>
  );
}

/* ============ 月报四段 + 分桶 body(自旧版原样搬入,写死色 → token 类) ============ */

export function ComplaintsBody({ complaints }: { complaints: Row }) {
  const complaintCats: Row[] = Array.isArray(complaints.categories) ? complaints.categories : [];
  const maxComplaint = complaintCats.reduce((acc, c) => Math.max(acc, Number(c.count) || 0), 0);
  if (String(complaints.status) === "empty") return <EmptyLine text={String(complaints.reason || "无抱怨命中。")} />;
  return (
    <div className="space-y-2">
      {complaintCats.map((c) => {
        const count = Number(c.count) || 0;
        const widthPct = maxComplaint > 0 ? Math.max(6, Math.round((count / maxComplaint) * 100)) : 0;
        return (
          <div key={String(c.key)} className={count === 0 ? "opacity-40" : ""}>
            <div className="flex items-center gap-2 text-[11.5px]">
              <span className="w-[92px] shrink-0 text-ink-2">{c.label}</span>
              <div className="relative h-[8px] flex-1 overflow-hidden rounded-full bg-line">
                <div
                  className="absolute inset-y-0 left-0 rounded-full"
                  style={{
                    width: `${count > 0 ? widthPct : 0}%`,
                    background:
                      "linear-gradient(90deg, color-mix(in srgb, var(--ds-crit) 35%, transparent), color-mix(in srgb, var(--ds-crit) 75%, transparent))",
                  }}
                />
              </div>
              <span className="w-[40px] shrink-0 text-right tabular-nums text-muted">×{count}</span>
            </div>
            {count > 0 && <QuoteFold quotes={Array.isArray(c.quotes) ? c.quotes : []} summary={`原声 top${Math.min(3, count)}(点开)`} />}
          </div>
        );
      })}
    </div>
  );
}

export function WishlistBody({ wishlist }: { wishlist: Row }) {
  const focalReqs: Row[] = Array.isArray(wishlist.focal_requests) ? wishlist.focal_requests : [];
  const zoomReqs: Row[] = Array.isArray(wishlist.zoom_requests) ? wishlist.zoom_requests : [];
  const mountReqs: Row[] = Array.isArray(wishlist.mount_requests) ? wishlist.mount_requests : [];
  const wishItems: Row[] = Array.isArray(wishlist.items) ? wishlist.items : [];
  if (String(wishlist.status) === "empty") return <EmptyLine text={String(wishlist.reason || "无愿望命中。")} />;
  return (
    <div className="space-y-3">
      {focalReqs.length > 0 && (
        <div>
          <div className="mb-1.5 text-[11px] text-muted">想要的焦段</div>
          <div className="flex flex-wrap gap-1.5">
            {focalReqs.map((f) => (
              <span
                key={String(f.focal)}
                title={(Array.isArray(f.quotes) && f.quotes[0]?.text) || ""}
                className="rounded-full border border-accent bg-accent-soft px-2.5 py-1 text-[11px] text-accent"
              >
                {f.focal} <span className="opacity-75">×{Number(f.count) || 0}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      {zoomReqs.length > 0 && (
        <div>
          <div className="mb-1.5 text-[11px] text-muted">想要的变焦段</div>
          <div className="flex flex-wrap gap-1.5">
            {zoomReqs.map((z) => (
              <span key={String(z.range_mm)} className="rounded-full border border-info bg-info-soft px-2.5 py-1 text-[11px] text-info">
                {z.range_mm} <span className="opacity-75">×{Number(z.count) || 0}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      {mountReqs.length > 0 && (
        <div>
          <div className="mb-1.5 text-[11px] text-muted">想要的卡口</div>
          <div className="flex flex-wrap gap-1.5">
            {mountReqs.map((m) => (
              <span key={String(m.mount)} className="rounded-full border border-accent-2 px-2.5 py-1 text-[11px] text-accent-2">
                {m.mount} <span className="opacity-75">×{Number(m.count) || 0}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      <QuoteFold quotes={wishItems} summary={`愿望原声 top${wishItems.length}(点开)`} />
    </div>
  );
}

export function GapsBody({ gaps }: { gaps: Row }) {
  const gapItems: Row[] = Array.isArray(gaps.items) ? gaps.items : [];
  if (String(gaps.status) === "empty") return <EmptyLine text={String(gaps.reason || "本窗口无目录空白焦段声量。")} />;
  return (
    <div className="space-y-2">
      {gapItems.map((g) => (
        <div key={String(g.focal)} className="rounded-lg border border-line px-3 py-2">
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <span className="rounded-md border border-crit bg-crit-soft px-2 py-0.5 font-medium text-crit">{g.focal}</span>
            <span className="text-muted">声量 ×{Number(g.voice_count) || 0}</span>
            {Number(g.wish_count) > 0 && <span className="text-good">其中愿望 ×{g.wish_count}</span>}
            <span className="ml-auto text-[10px] text-muted">目录零 SKU</span>
          </div>
          <QuoteFold quotes={Array.isArray(g.quotes) ? g.quotes : []} />
        </div>
      ))}
    </div>
  );
}

export function RecsBody({ suggestions }: { suggestions: Row }) {
  const suggItems: Row[] = Array.isArray(suggestions.items) ? suggestions.items : [];
  if (String(suggestions.status) === "empty") return <EmptyLine text={String(suggestions.reason || "无过阈值建议。")} />;
  return (
    <div className="space-y-2">
      {suggItems.map((s, i) => {
        const meta = KIND_META[String(s.kind)] || { label: String(s.kind || "建议"), tone: "border-line text-ink-2" };
        return (
          <div key={i} className="rounded-lg border border-line px-3 py-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-md border px-2 py-0.5 text-[10px] ${meta.tone}`}>{meta.label}</span>
              <span className="text-[12px] text-ink">{s.title}</span>
              <span className="ml-auto shrink-0 text-[10px] tabular-nums text-muted">
                证据 ×{Number(s.evidence_count) || 0} · {CONFIDENCE_LABEL[String(s.confidence)] || String(s.confidence || "—")}
              </span>
            </div>
            {s.detail ? <div className="mt-1 text-[11px] leading-relaxed text-muted">{s.detail}</div> : null}
            <QuoteFold quotes={Array.isArray(s.quotes) ? s.quotes : []} />
          </div>
        );
      })}
    </div>
  );
}

// 产品线声量分桶(旧版功能保留;palette 可选模块)
export function BucketsBody({ data }: { data: Row | null }) {
  const lineBuckets: Row[] = Array.isArray(data?.buckets?.product_lines) ? data!.buckets.product_lines : [];
  if (lineBuckets.length === 0) return <EmptyLine text="本窗口无产品线声量分桶(focal_matrix 词表零命中)。" />;
  return (
    <div className="flex flex-wrap gap-1.5">
      {lineBuckets.map((b) => (
        <span
          key={String(b.key)}
          title={(Array.isArray(b.example) && b.example[0]?.text) || ""}
          className={`rounded-full border px-2.5 py-1 text-[11px] ${Number(b.count) > 0 ? "border-line bg-card text-ink" : "border-line text-muted opacity-60"}`}
        >
          {b.label} <span className="text-muted">×{Number(b.count) || 0}</span>
        </span>
      ))}
    </div>
  );
}
