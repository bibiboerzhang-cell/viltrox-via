import React from "react";
import { formatLocal } from "../../lib/timeLocal";
import { EmptyLine, KpiCard } from "./MarketVoicePage.modules";
import { boardSeriesVals, type VkpiBoardSeriesResponse } from "../../../../services/vkpi/boardSeries-api";

// KOL 档案 · 图表模块族(KolProfileBoardPage 专用,页内拆件不入公共桶)。
//   金样板 = MarketVoicePage.charts / MyKolBoardPage.charts 图形语言逐件同构
//   (不跨页引它们的私有件,防纠缠):BarRow 条形行(信用/评论/装备共用)、
//   KPI 带四卡(KpiCard 现成件:粉丝/均播放/互动率三指标是点时快照无历史时序 →
//   诚实 spempty 虚线;已深析视频卡趋势线 = board-series?board=kol-profile&kol_id=
//   该 KOL evidence 采集/日真序列(关联指标,卡面大数是存量 → 不挂环比药丸))。
// 红线:纯展示零网络;fit 分只读展示绝不写回;颜色全 token var(--ds-*) 零写死色;
//   零 opacity 修饰类(条形弱化走内联 style,金样板同款);诚实空态,绝不编数。

export type Row = Record<string, unknown>;

/* ============ 小工具(金样板同构;宽进严出,远端契约不齐一律防御) ============ */

export function asRow(v: unknown): Row | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : null;
}

export function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

export function str(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return "";
}

export function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

// 0-1 或 0-100 的比例统一到 0-100(存量列口径不一,读侧容错)
export function pct(v: number | null): number | null {
  if (v === null) return null;
  const p = v <= 1 ? v * 100 : v;
  return p >= 0 && p <= 100 ? Math.round(p * 10) / 10 : null;
}

// 中文紧凑数(20.5亿 / 25.1万;金样板 fmtZhCompact 同构)
export function fmtZhCompact(value: number | null | undefined): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(1)}亿`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  return n.toLocaleString();
}

export function ProvNote({ children }: { children: React.ReactNode }) {
  return <div className="mt-[7px] font-mono text-[9px] text-muted">{children}</div>;
}

/* ============ mplatrow 条形行(金样板 BarRow 同构:名 + 条 + mono 右注) ============ */
export function BarRow({
  name,
  widthPct,
  color,
  value,
  highlight = false,
  dashed = false,
  title,
}: {
  name: string;
  /** 条宽 0-100;0 也画空槽(demo .pbar 底槽常驻) */
  widthPct: number;
  /** 条色(CSS var 串;缺省 accent→accent-2 渐变) */
  color?: string;
  value: React.ReactNode;
  highlight?: boolean;
  /** 未判定/弱信号桶:muted 弱化条 */
  dashed?: boolean;
  title?: string;
}) {
  const barBg = color || "linear-gradient(90deg, var(--ds-accent), var(--ds-accent-2))";
  return (
    <div
      className="grid grid-cols-[minmax(64px,96px)_1fr_minmax(66px,auto)] items-center gap-2.5 py-[4.5px] text-[11.5px]"
      title={title}
    >
      <span className={`truncate ${highlight ? "font-bold text-accent" : "text-ink-2"}`}>{name}</span>
      <span className="h-[6px] overflow-hidden rounded-[3px] bg-line">
        <i
          className="block h-full rounded-[3px]"
          style={{
            width: `${Math.max(0, Math.min(100, widthPct))}%`,
            background: barBg,
            opacity: dashed ? 0.45 : 1,
          }}
        />
      </span>
      <span className="text-right font-mono text-[10.5px] text-muted">{value}</span>
    </div>
  );
}

/* ============ 分布条组({label: count} 字典 → BarRow 行;评论情绪/品牌态度共用) ============ */
export function DistBars({ dist, title, tone }: { dist: Row; title: string; tone?: (label: string) => string | undefined }) {
  const entries = Object.entries(dist)
    .map(([k, v]) => [k, num(v) ?? 0] as [string, number])
    .filter(([k, v]) => v > 0 && k !== "")
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  const total = entries.reduce((s, [, v]) => s + v, 0);
  if (total === 0) return null;
  return (
    <div>
      <div className="mb-0.5 text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">{title}</div>
      {entries.map(([label, count]) => (
        <BarRow
          key={label}
          name={label}
          widthPct={(count / total) * 100}
          color={tone ? tone(label) : undefined}
          value={`${count} · ${Math.round((count / total) * 100)}%`}
        />
      ))}
    </div>
  );
}

/* ============ KPI 带四卡:粉丝 / 均播放 / 互动率 / 已深析视频。
   前三指标 = 档案点时快照(抓取时刻读数)无历史时序 → series 缺席 = spempty
   诚实虚线;已深析视频卡趋势线 = board-series evidence_new(该 KOL evidence
   采集/日,关联指标),端点失败 boardSeriesVals=null → 虚线让位,永不编趋势。 ============ */
export function ProfileKpiBand({
  followers,
  avgViews,
  engagementPct,
  deepReady,
  evidenceCount,
  pending,
  pendingNote,
  boardSeries,
}: {
  followers: number | null;
  avgViews: number | null;
  engagementPct: number | null;
  /** video_analysis.summary.ready_count(已有深析产物的视频数) */
  deepReady: number | null;
  /** video_analysis.summary.evidence_count(本地视频证据总数) */
  evidenceCount: number | null;
  /** 档案主体未就绪(加载中/失败)→ 四卡诚实 pending */
  pending: boolean;
  pendingNote: string;
  /** board-series?board=kol-profile&kol_id= 响应(null=未就绪/失败 → spempty) */
  boardSeries?: VkpiBoardSeriesResponse | null;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard
        label="粉丝"
        value={fmtZhCompact(followers)}
        pending={pending || followers === null}
        pendingNote={pending ? pendingNote : "档案未收录粉丝数"}
      />
      <KpiCard
        label="均播放"
        value={fmtZhCompact(avgViews)}
        pending={pending || avgViews === null}
        pendingNote={pending ? pendingNote : "档案未收录均播放"}
      />
      <KpiCard
        label="互动率"
        value={engagementPct !== null ? engagementPct : "—"}
        unit="%"
        pending={pending || engagementPct === null}
        pendingNote={pending ? pendingNote : "档案未收录互动率"}
      />
      <KpiCard
        label="已深析视频"
        value={
          deepReady !== null ? (
            <>
              {deepReady.toLocaleString()}
              {evidenceCount !== null ? <span className="ds-kpi__u">/{evidenceCount.toLocaleString()} 条</span> : null}
            </>
          ) : (
            "—"
          )
        }
        tone={deepReady !== null && deepReady === 0 ? "warn" : "good"}
        pending={pending}
        pendingNote={pendingNote}
        series={boardSeriesVals(boardSeries ?? null, "evidence_new")}
        seriesColor="var(--ds-accent)"
      />
    </div>
  );
}

/* ============ 信用 · 竞业与真实度 ============ */

const RISK_TIER_TONE: Record<string, string> = {
  avoid: "var(--ds-crit)",
  caution: "var(--ds-warn)",
  safe: "var(--ds-good)",
  opportunity: "var(--ds-info)",
};
const RISK_TIER_LABEL: Record<string, string> = { avoid: "回避", caution: "谨慎", safe: "安全", opportunity: "机会" };

// 兜底源(/competitors):已落库品牌关系 risk_score 榜
export function CompetitorRelationsBody({ relations }: { relations: Row[] }) {
  const rows = relations
    .filter((r) => (num(r.risk_score) ?? 0) > 0)
    .sort((a, b) => (num(b.risk_score) ?? 0) - (num(a.risk_score) ?? 0))
    .slice(0, 6);
  if (rows.length === 0) return <EmptyLine text="无竞品品牌关联记录。" />;
  const max = rows.reduce((a, r) => Math.max(a, num(r.risk_score) ?? 0), 0) || 1;
  return (
    <div>
      {rows.map((r, i) => {
        const tier = str(r.risk_tier);
        const score = num(r.risk_score) ?? 0;
        return (
          <BarRow
            key={i}
            name={str(r.competitor_brand) || "—"}
            widthPct={(score / max) * 100}
            color={RISK_TIER_TONE[tier] || "var(--ds-muted)"}
            value={`${RISK_TIER_LABEL[tier] || tier || "—"} · ${Math.round(score * 100) / 100}`}
          />
        );
      })}
    </div>
  );
}

// 主源(/competing-activity):近窗品牌露出计数 + 最近露出行(带原帖外链)
export function CompetingActivityBody({ data }: { data: Row }) {
  const brands = asArray(data.brands).map((b) => asRow(b)).filter((b): b is Row => Boolean(b));
  const items = asArray(data.items).map((it) => asRow(it)).filter((it): it is Row => Boolean(it));
  const undated = asArray(data.undated_items).length;
  const windowDays = num(data.window_days) ?? 90;
  if (brands.length === 0 && undated === 0) return <EmptyLine text={`近 ${windowDays} 天无竞品露出。`} />;
  const max = brands.reduce((a, b) => Math.max(a, num(b.count) ?? 0), 0) || 1;
  return (
    <div>
      {brands.slice(0, 6).map((b, i) => (
        <BarRow
          key={i}
          name={str(b.brand) || "—"}
          widthPct={((num(b.count) ?? 0) / max) * 100}
          color="var(--ds-warn)"
          value={`×${num(b.count) ?? 0}`}
        />
      ))}
      {items.slice(0, 3).map((it, i) => {
        const ref = asRow(it.evidence_ref);
        const url = str(ref?.content_url);
        return (
          <div key={`it-${i}`} className="flex items-center gap-2 border-b border-line py-1.5 text-[10.5px] last:border-0">
            <span className="flex-none rounded-[5px] border border-warn bg-warn-soft px-1 py-px text-[8px] font-bold text-warn">{str(it.brand)}</span>
            <span className="min-w-0 flex-1 truncate text-ink-2" title={str(ref?.title)}>
              {str(it.scene) || str(ref?.title) || "—"}
            </span>
            <span className="flex-none font-mono text-[9px] text-muted" title="发布时间(UTC 存 · 按浏览器时区显示)">
              {formatLocal(str(it.when) || null)}
            </span>
            {url ? (
              <a className="vkpi-prov-pchip vkpi-prov-pchip--ext vkpi-prov-pchip--mini flex-none" href={url} target="_blank" rel="noopener noreferrer" title="直跳原帖">
                ↗
              </a>
            ) : null}
          </div>
        );
      })}
      {undated > 0 ? <ProvNote>另有 {undated} 条露出无发布日期 · 不计入近窗</ProvNote> : null}
    </div>
  );
}

// 真实度:实测互动率(评论样本回算)vs 档案互动率 + 虚高嫌疑判定行
export function AuthenticityBody({ item }: { item: Row }) {
  const realEr = pct(num(item.real_er));
  const claimedEr = pct(num(item.engagement_rate));
  const sampleN = num(item.real_er_sample_n);
  const suspect = item.suspect_inflation === true || item.suspect_inflation === 1;
  const checked = item.suspect_inflation !== null && item.suspect_inflation !== undefined;
  const reason = str(item.inflation_reason);
  return (
    <div>
      <div className="mb-0.5 mt-2 text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">真实度</div>
      {realEr !== null ? (
        <BarRow
          name="实测互动率"
          widthPct={Math.min(100, realEr * 10)}
          color="var(--ds-good)"
          value={`${realEr}%${sampleN ? ` · 样本 ${sampleN}` : ""}`}
          title="评论样本回算口径,非平台自报"
        />
      ) : (
        <div className="py-1 text-[10.5px] text-muted">实测互动率未回算。</div>
      )}
      {claimedEr !== null && realEr !== null ? (
        <BarRow name="档案互动率" widthPct={Math.min(100, claimedEr * 10)} color="var(--ds-muted)" dashed value={`${claimedEr}%`} title="档案抓取值(对照行)" />
      ) : null}
      {checked ? (
        <div className={`mt-1.5 flex items-center gap-2 rounded-[9px] border px-2.5 py-1.5 text-[10.5px] ${suspect ? "border-warn bg-warn-soft text-warn" : "border-line text-ink-2"}`} title={reason || undefined}>
          <span className={`h-[7px] w-[7px] flex-none rounded-full ${suspect ? "bg-warn" : "bg-good"}`} />
          {suspect ? `虚高嫌疑${reason ? ` · ${reason.slice(0, 60)}` : ""}` : "无虚高嫌疑"}
        </div>
      ) : (
        <div className="py-1 text-[10.5px] text-muted">虚高检测未跑。</div>
      )}
    </div>
  );
}

/* ============ 制作周期(签收→发布):中位天数大数 + 样本行 ============ */
export function LeadtimeBody({ data }: { data: Row }) {
  const median = num(data.median_days);
  const sampleCount = num(data.sample_count) ?? 0;
  const samples = asArray(data.samples).map((s) => asRow(s)).filter((s): s is Row => Boolean(s));
  if (String(data.status) === "empty" || sampleCount === 0) {
    return <EmptyLine text="无「签收 → 发布」完整样本。" />;
  }
  return (
    <div>
      <div className="mb-2 mt-0.5 flex flex-wrap items-end gap-6">
        <div className="flex flex-col">
          <span className="font-mono text-[21px] font-bold leading-none tracking-[-0.02em] text-ink">
            {median !== null ? median : "—"}
            <span className="text-[13px]"> 天</span>
          </span>
          <span className="mt-[5px] text-[10px] text-muted">签收 → 发布中位</span>
        </div>
        <div className="flex flex-col">
          <span className="font-mono text-[21px] font-bold leading-none tracking-[-0.02em] text-ink-2">{sampleCount}</span>
          <span className="mt-[5px] text-[10px] text-muted">完整样本</span>
        </div>
      </div>
      {samples.slice(0, 5).map((s, i) => {
        const url = str(s.content_url);
        return (
          <div key={i} className="flex items-center gap-2 border-b border-line py-1.5 text-[10.5px] last:border-0">
            <span className="flex-none font-mono text-[9.5px] text-muted" title="签收时间(UTC 存 · 按浏览器时区显示)">
              {formatLocal(str(s.delivered_at) || null)}
            </span>
            <span className="flex-none text-muted">→</span>
            <span className="flex-none font-mono text-[9.5px] text-muted" title="发布时间">
              {formatLocal(str(s.published_at) || null)}
            </span>
            <span className="min-w-0 flex-1 truncate text-right font-mono text-[10.5px] text-ink">{num(s.days) ?? "—"} 天</span>
            {url ? (
              <a className="vkpi-prov-pchip vkpi-prov-pchip--ext vkpi-prov-pchip--mini flex-none" href={url} target="_blank" rel="noopener noreferrer" title="直跳发布贴">
                ↗
              </a>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/* ============ 创作装备(档案抽取:bio/raw + 深析聚合;非采集字段) ============ */
export function GearBody({ item }: { item: Row }) {
  const primary = str(item.device_primary);
  const lenses = asArray(item.device_lenses).map((l) => str(l)).filter(Boolean);
  const usesViltrox = item.device_uses_viltrox === true;
  const windowTier = str(item.upgrade_window);
  const WINDOW_LABEL: Record<string, string> = { low: "换镜窗口 低", medium: "换镜窗口 中", high: "换镜窗口 高" };
  if (!primary && lenses.length === 0) return <EmptyLine text="装备信息未提取。" />;
  return (
    <div className="space-y-2">
      {primary ? (
        <div className="flex items-center justify-between gap-3 border-b border-line py-2 text-[12px]">
          <span className="flex-none text-muted">机身</span>
          <span className="min-w-0 truncate text-right text-ink">{primary}</span>
        </div>
      ) : null}
      {lenses.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {lenses.slice(0, 8).map((l, i) => (
            <span
              key={i}
              className={`rounded-full border px-2.5 py-1 text-[11px] ${/viltrox/i.test(l) ? "border-accent bg-accent-soft text-accent" : "border-line text-ink-2"}`}
            >
              {l}
            </span>
          ))}
        </div>
      ) : null}
      <div className="flex flex-wrap gap-1.5">
        <span className={`rounded-full border px-2.5 py-1 text-[10.5px] ${usesViltrox ? "border-good bg-good-soft text-good" : "border-line text-muted"}`}>
          {usesViltrox ? "已在用 Viltrox" : "未见 Viltrox 在用"}
        </span>
        {WINDOW_LABEL[windowTier] ? <span className="rounded-full border border-line px-2.5 py-1 text-[10.5px] text-ink-2">{WINDOW_LABEL[windowTier]}</span> : null}
      </div>
    </div>
  );
}
