import React from "react";
import { SrcChip } from "../components/provenance";
import { formatLocal } from "../../lib/timeLocal";
import type { VoiceFeedItem } from "../../../../services/vkpi/marketVoice-api";
import { IDENTITY_META, QuoteDialog, platformBadge, type QuoteRow } from "./MarketVoicePage.dialogs";

// 市场之声 · 板块页范式辅助件(MarketVoicePage 专用,页内拆件不入公共桶)。
//   V0h-c chrome 大扫除:卡头 cnt = demo 同款 accent-soft 短徽 + SrcChip 后「实时」eyebrow;
//   模块卡骨架吃 ds-mod(玻璃三件套 + hover + rise 入场,ds-viz.css);KPI 卡 series-empty
//   退化 demo .spempty 纯虚线、pending 药丸独立 .pt 行、tone 语义色(积压类 warn);
//   引文 QuoteFold 卡面只留一行入口 → QuoteDialog 中央弹窗;空态双轨:管线级「未点火/待接」
//   走 PendingCard(demo .mpend warn 盒),纯「窗口无数据」轻量一行去虚线框。
//   弹窗族(ModalShell/QuoteDialog/FeedDetailModal/ModuleProvModal/FeedListModal)住
//   MarketVoicePage.dialogs.tsx(单向依赖:本文件 → dialogs,反向禁止)。
// 红线:本文件零直连网络(动作走 page 层回调);不触 viltrox_fit_score / rule_v0;
//        颜色全 token 类零写死色;动效只用 ds-viz.css 既有类(自带 reduced-motion 降级)。

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

/* ============ 空态双轨(demo:纯窗口无数据 = 轻量一行;管线未点火/待接 = .mpend warn 盒) ============ */
export function EmptyLine({ text }: { text: string }) {
  return <div className="px-3 py-4 text-center text-[12px] text-muted">{text}</div>;
}

// demo .mpend:warn 边框 + warn-soft 底 + 关键词 warn 粗体(children 里用 <b> 标关键词)
export function PendingCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-warn bg-warn-soft px-[15px] py-[15px] text-[12.5px] leading-[1.75] text-ink-2 [&_b]:font-semibold [&_b]:text-warn">
      {children}
    </div>
  );
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

/* ============ 原声引文:卡面永远一行入口 → QuoteDialog 中央弹窗(V0h-c) ============ */
export function QuoteFold({ quotes, title }: { quotes: Row[]; title: string }) {
  const [open, setOpen] = React.useState(false);
  if (!Array.isArray(quotes) || quotes.length === 0) return null;
  return (
    <>
      <button
        type="button"
        onClick={(ev) => {
          ev.stopPropagation();
          setOpen(true);
        }}
        className="mt-1.5 block text-[10px] text-accent transition-colors hover:text-accent-hover"
      >
        ▸ 原声 ×{quotes.length}(点开)
      </button>
      {open && <QuoteDialog title={title} quotes={quotes as QuoteRow[]} onClose={() => setOpen(false)} />}
    </>
  );
}

/* ============ 模块卡骨架:demo .mod(ds-mod 玻璃三件套)+ 卡头(标题 + cnt 短徽 + SrcChip + 实时) ============ */
export function ModuleCard({
  title,
  cnt,
  srcLabel,
  srcRows,
  onOpenSrc,
  children,
}: {
  title: string;
  /** demo .cnt:accent-soft 药丸短徽,内容只放短计数(『23』『5/6』),长口径进 srcRows */
  cnt?: React.ReactNode;
  srcLabel: string;
  srcRows: Array<[string, string]>;
  onOpenSrc?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="ds-mod ds-rise flex h-full min-h-0 flex-col">
      <header className="flex flex-none items-center justify-between gap-2.5 px-4 pb-2 pt-[13px]">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="truncate text-[13.5px] font-semibold tracking-[-0.01em] text-ink">{title}</h3>
          {cnt != null && (
            <span className="flex-none rounded-md bg-accent-soft px-[6px] py-px text-[9.5px] font-semibold text-accent">{cnt}</span>
          )}
        </div>
        <span className="flex flex-none items-center gap-2">
          <SrcChip label={srcLabel} rows={srcRows} onOpen={onOpenSrc} />
          {/* 数据为请求时实算 → 诚实「实时」eyebrow(demo 卡头右侧固定件) */}
          <span className="text-[9.5px] font-semibold uppercase tracking-[0.16em] text-muted">实时</span>
        </span>
      </header>
      <div className="min-h-0 flex-1 overflow-auto px-4 pb-4">{children}</div>
    </section>
  );
}

/* ============ KPI 卡:demo .kpi(card2 面 + mono 大数 + spempty 虚线 / pending .pt 药丸) ============ */
export function KpiCard({
  label,
  value,
  unit,
  tone = "good",
  pending,
  pendingNote,
}: {
  label: string;
  value: React.ReactNode;
  unit?: string;
  /** 状态点 + 数值语义色成对(积压/告警类走 warn;demo .dot.w + v warn 染色) */
  tone?: "good" | "warn";
  pending?: boolean;
  pendingNote?: string;
}) {
  const dotCls = pending ? "ds-kpi__dot--pend" : tone === "warn" ? "ds-kpi__dot--warn" : "ds-kpi__dot--good";
  const valCls = pending ? " ds-kpi__val--pend" : tone === "warn" ? " ds-kpi__val--warn" : "";
  return (
    <div className="ds-kpi ds-rise">
      <div className="ds-kpi__k">
        <span className={`ds-kpi__dot ${dotCls}`} />
        <span className="ds-kpi__label">{label}</span>
      </div>
      <div className={`ds-kpi__val${valCls}`}>
        {pending ? "—" : value}
        {!pending && unit ? <span className="ds-kpi__u">{unit}</span> : null}
      </div>
      {pending ? (
        pendingNote ? (
          <div>
            <span className="ds-kpi__pt">{pendingNote}</span>
          </div>
        ) : null
      ) : (
        // 无历史序列(voice-report 单窗口计数)→ demo .spempty 纯虚线,零文字
        <div className="ds-kpi__series-empty" aria-hidden="true" />
      )}
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
export function FeedRowLine({
  item,
  index,
  onOpen,
  queued = false,
}: {
  item: VoiceFeedItem;
  index: number;
  onOpen: (i: number) => void;
  queued?: boolean;
}) {
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
      {queued && (
        <span
          className="flex-none rounded-[5px] border border-good bg-good-soft px-1 py-px text-[8px] font-bold text-good"
          title="已转回复队列(vkpi_reply_queue)"
        >
          💬 已入队
        </span>
      )}
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

/* ============ 月报四段 + 分桶 body(逻辑零改动;引文入口 → 弹窗) ============ */

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
            {count > 0 && <QuoteFold quotes={Array.isArray(c.quotes) ? c.quotes : []} title={`抱怨原声 · ${String(c.label || c.key)}`} />}
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
                className="rounded-full border border-accent bg-accent-soft px-2.5 py-1 text-[11px] text-accent transition-colors hover:border-accent-hover"
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
              <span
                key={String(z.range_mm)}
                className="rounded-full border border-info bg-info-soft px-2.5 py-1 text-[11px] text-info transition-colors hover:border-accent"
              >
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
              <span
                key={String(m.mount)}
                className="rounded-full border border-accent-2 px-2.5 py-1 text-[11px] text-accent-2 transition-colors hover:border-accent"
              >
                {m.mount} <span className="opacity-75">×{Number(m.count) || 0}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      <QuoteFold quotes={wishItems} title="愿望原声" />
    </div>
  );
}

export function GapsBody({ gaps }: { gaps: Row }) {
  const gapItems: Row[] = Array.isArray(gaps.items) ? gaps.items : [];
  if (String(gaps.status) === "empty") return <EmptyLine text={String(gaps.reason || "本窗口无目录空白焦段声量。")} />;
  return (
    <div className="space-y-2">
      {gapItems.map((g) => (
        <div key={String(g.focal)} className="rounded-lg border border-line px-3 py-2 transition-colors hover:border-accent">
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <span className="rounded-md border border-crit bg-crit-soft px-2 py-0.5 font-medium text-crit">{g.focal}</span>
            <span className="text-muted">声量 ×{Number(g.voice_count) || 0}</span>
            {Number(g.wish_count) > 0 && <span className="text-good">其中愿望 ×{g.wish_count}</span>}
            <span className="ml-auto text-[10px] text-muted">目录零 SKU</span>
          </div>
          <QuoteFold quotes={Array.isArray(g.quotes) ? g.quotes : []} title={`需求空白原声 · ${String(g.focal)}`} />
        </div>
      ))}
    </div>
  );
}

// 给产品部的建议:卡面一行一条(徽 + 标题 + 证据数),detail 说明段 + 引文全部收进弹窗(V0h-c)
export function RecsBody({ suggestions }: { suggestions: Row }) {
  const suggItems: Row[] = Array.isArray(suggestions.items) ? suggestions.items : [];
  const [openIdx, setOpenIdx] = React.useState<number | null>(null);
  if (String(suggestions.status) === "empty") return <EmptyLine text={String(suggestions.reason || "无过阈值建议。")} />;
  const openItem = openIdx != null ? suggItems[openIdx] : null;
  const metaOf = (s: Row) => KIND_META[String(s.kind)] || { label: String(s.kind || "建议"), tone: "border-line text-ink-2" };
  return (
    <div className="space-y-1.5">
      {suggItems.map((s, i) => (
        <button
          key={i}
          type="button"
          onClick={() => setOpenIdx(i)}
          title="点开看说明与原声引文"
          className="flex w-full items-center gap-2 rounded-lg border border-line px-3 py-2 text-left transition-colors hover:border-accent"
        >
          <span className={`flex-none rounded-md border px-2 py-0.5 text-[10px] ${metaOf(s).tone}`}>{metaOf(s).label}</span>
          <span className="min-w-0 flex-1 truncate text-[12px] text-ink">{s.title}</span>
          <span className="flex-none text-[10px] tabular-nums text-muted">证据 ×{Number(s.evidence_count) || 0}</span>
        </button>
      ))}
      {openItem && (
        <QuoteDialog
          title={String(openItem.title || "建议详情")}
          sub={`${metaOf(openItem).label} · 证据 ×${Number(openItem.evidence_count) || 0} · ${
            CONFIDENCE_LABEL[String(openItem.confidence)] || String(openItem.confidence || "—")
          }`}
          lead={openItem.detail ? String(openItem.detail) : undefined}
          quotes={(Array.isArray(openItem.quotes) ? openItem.quotes : []) as QuoteRow[]}
          onClose={() => setOpenIdx(null)}
        />
      )}
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
          className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors hover:border-accent ${
            Number(b.count) > 0 ? "border-line bg-card text-ink" : "border-line text-muted opacity-60"
          }`}
        >
          {b.label} <span className="text-muted">×{Number(b.count) || 0}</span>
        </span>
      ))}
    </div>
  );
}
