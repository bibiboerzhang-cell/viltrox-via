import React from "react";
import { formatLocal } from "../../lib/timeLocal";
import type { ReplyQueueItem } from "../../../../services/vkpi/replyQueue-api";
import { EmptyLine, KpiCard, type Row } from "./MarketVoicePage.modules";
import { BarRow } from "./MarketVoicePage.charts";
import { platformBadge } from "./MarketVoicePage.dialogs";

// 回复队列 · 板块页辅助件(ReplyQueueBoardPage 专用,页内拆件不入公共桶;
//   金样板 = MarketVoicePage.modules / EventsBoardPage.charts 同构,图形件全复用:
//   KpiCard(demo .kpi)/ BarRow(demo mplatrow)/ platformBadge 零自造样式)。
//   数据 = 页层一次全量拉取的真队列行(GET /api/admin/vkpi/reply-queue → vkpi_reply_queue,
//   132 行 2026-07-12 核实),本文件纯组合零网络。
//   KPI 四卡:待起草 / 待回复 / 已回复 / 购买意向 —— 全真值;vkpi_reply_queue 无
//   历史快照表 → 四卡无时序,KpiCard 渲染 demo .spempty 纯虚线(诚实无 sparkline,
//   绝不编序列);环比同理诚实省略药丸。
// 红线:纯展示零网络;不触 viltrox_fit_score / rule_v0;颜色全 token 零写死色;
//   零 opacity 修饰类;绝对时间戳(存 UTC · formatLocal 按浏览器时区);
//   显示层宪法(后端本就不回传 author_* 字段,门面零个人字段)。

/* ============ 状态 / 意向元数据(徽=token 类;label 门面零术语) ============ */

export const STATUS_META: Record<string, { label: string; cls: string; bar: string }> = {
  pending: { label: "待起草", cls: "border-warn text-warn", bar: "var(--ds-warn)" },
  drafted: { label: "待回复", cls: "border-accent text-accent", bar: "var(--ds-accent)" },
  replied: { label: "已回复", cls: "border-good text-good", bar: "var(--ds-good)" },
  dismissed: { label: "已忽略", cls: "border-line text-muted", bar: "var(--ds-muted)" },
};

export const STATUS_ORDER = ["pending", "drafted", "replied", "dismissed"] as const;

export const INTENT_META: Record<string, { label: string; cls: string }> = {
  price: { label: "价格/购买", cls: "border-good text-good" },
  compat: { label: "兼容/型号", cls: "border-accent text-accent" },
  question: { label: "问询", cls: "border-warn text-warn" },
  // 市场之声单条「转回复队列」且词表未命中 → 后端如实落 manual(人工点名入队)
  manual: { label: "手动入队", cls: "border-accent-2 text-accent-2" },
};

export const intentMeta = (tag: string) =>
  INTENT_META[String(tag || "").toLowerCase()] || { label: String(tag || "意向"), cls: "border-line text-muted" };

const LANG_LABEL: Record<string, string> = {
  en: "英语", es: "西语", de: "德语", fr: "法语", ja: "日语", ko: "韩语",
  zh: "中文", pt: "葡语", it: "意语", ru: "俄语",
};

export const langLabel = (code: string) => {
  const key = String(code || "").toLowerCase();
  return LANG_LABEL[key] || (key ? key.toUpperCase() : "未检");
};

/* ============ 口径计数(页层/KPI/图表共用同一份;纯函数可单测) ============ */

export interface QueueCounts {
  total: number;
  byStatus: Record<string, number>;
  byIntent: Array<{ key: string; label: string; count: number }>;
  byPlatform: Array<{ key: string; count: number }>;
  byLang: Array<{ key: string; count: number }>;
}

export function queueCounts(items: ReplyQueueItem[]): QueueCounts {
  const byStatus: Record<string, number> = {};
  const intent = new Map<string, number>();
  const plat = new Map<string, number>();
  const lang = new Map<string, number>();
  for (const it of items) {
    const st = String(it.status || "").toLowerCase();
    byStatus[st] = (byStatus[st] || 0) + 1;
    const tag = String(it.intent_tag || "").toLowerCase() || "unknown";
    intent.set(tag, (intent.get(tag) || 0) + 1);
    const p = String(it.platform || "").toLowerCase() || "unknown";
    plat.set(p, (plat.get(p) || 0) + 1);
    const l = String(it.lang || "").toLowerCase() || "und";
    lang.set(l, (lang.get(l) || 0) + 1);
  }
  const desc = (a: { count: number }, b: { count: number }) => b.count - a.count;
  return {
    total: items.length,
    byStatus,
    byIntent: [...intent.entries()].map(([key, count]) => ({ key, label: intentMeta(key).label, count })).sort(desc),
    byPlatform: [...plat.entries()].map(([key, count]) => ({ key, count })).sort(desc),
    byLang: [...lang.entries()].map(([key, count]) => ({ key, count })).sort(desc),
  };
}

/* ============ 每模块 SrcChip 口径(label=真实表名;rows=真实底表/口径,禁编造) ============ */

export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiQ: {
    label: "vkpi_reply_queue · 实时",
    rows: [
      // 原页头介绍句(卡面去文案,口径不丢):筛意向 → 起草 → 人工复制回复 → 标记
      ["链路", "从评论筛购买意向 → 起草 → 一键复制人工回 → 标记(绝不自动发帖)"],
      ["队列表", "vkpi_reply_queue"],
      ["源评论", "vkpi_comments(platform + external_comment_id 回链)"],
      ["可见性", "管理层全量 · 员工=未认领池 + 自己认领(服务端收敛)"],
      ["上限", "单次拉取 500 条 · 超出如实截断"],
      ["时序", "无历史快照表 · 迷你趋势诚实虚线"],
      ["时间", "存 UTC · 按浏览器时区显示"],
    ],
  },
  queue: {
    label: "vkpi_reply_queue",
    rows: [
      ["排序", "待起草 → 待回复 → 终态 · 同级按建队时间倒序(服务端)"],
      ["过滤", "状态 chips 本地过滤(同一次全量拉取,零二次请求)"],
      ["动作", "起草 / 复制 / 标记全走真端点 · 端点真实返回才落状态"],
      ["溯源", "单条详情链回 vkpi_comments 源评论(幂等键回链)"],
    ],
  },
  intent: {
    label: "vkpi_reply_queue.intent_tag",
    rows: [
      ["口径", "规则词表分类:价格/购买 · 兼容/型号 · 问询(零模型评分)"],
      ["手动入队", "市场之声单条转入且词表未命中 → 如实标 manual"],
    ],
  },
  funnel: {
    label: "vkpi_reply_queue.status",
    rows: [
      ["状态机", "待起草 → 待回复 → 已回复 / 已忽略"],
      ["终态", "已回复 / 已忽略不回炉(后端状态机拒绝重起草)"],
      ["乐观锁", "标记带 expected_status · 他人已改动 → 诚实冲突提示"],
    ],
  },
  plat: {
    label: "vkpi_reply_queue.platform",
    rows: [
      ["口径", "队列行按平台计数(意向命中子集,非全评论库)"],
    ],
  },
  lang: {
    label: "vkpi_reply_queue.lang",
    rows: [
      ["口径", "入队时语种:源评论 language_detected 优先 · 缺省按字符集判定"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiQ: "队列总览",
  queue: "回复队列",
  intent: "意向构成",
  funnel: "处理进度",
  plat: "平台分布",
  lang: "语言分布",
};

/* ============ KPI 带四卡(待起草/待回复/已回复/购买意向;无时序 → spempty 诚实虚线) ============ */

export function QueueKpiBand({ counts }: { counts: QueueCounts }) {
  const n = (key: string) => counts.byStatus[key] || 0;
  const price = counts.byIntent.find((it) => it.key === "price")?.count || 0;
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {/* 待起草 = 积压 → demo .dot.w + warn 染色 */}
      <KpiCard label="待起草" value={n("pending").toLocaleString()} unit="条" tone="warn" />
      <KpiCard label="待回复" value={n("drafted").toLocaleString()} unit="条" tone="warn" />
      <KpiCard label="已回复" value={n("replied").toLocaleString()} unit="条" />
      <KpiCard label="价格/购买意向" value={price.toLocaleString()} unit="条" />
    </div>
  );
}

/* ============ 队列行(demo .fb 同构:平台徽 + 意向徽 + 摘要 + 状态徽 + 绝对时间) ============ */

export function QueueRowLine({
  item,
  index,
  onOpen,
}: {
  item: ReplyQueueItem;
  index: number;
  onOpen: (i: number) => void;
}) {
  const st = STATUS_META[String(item.status || "").toLowerCase()] || STATUS_META.pending;
  const intent = intentMeta(item.intent_tag);
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
      <span className={`flex-none rounded-[5px] border px-1 py-px text-[8px] font-bold tracking-[0.05em] ${intent.cls}`}>
        {intent.label}
      </span>
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent">
        {item.comment_text || "—"}
      </span>
      {item.draft_reply ? (
        <span
          className="flex-none rounded-[5px] border border-accent-2 px-1 py-px text-[8px] font-bold text-accent-2"
          title="已有草稿(vkpi_reply_queue.draft_reply)"
        >
          ✎ 草稿
        </span>
      ) : null}
      <span className={`flex-none rounded-[5px] border px-1 py-px text-[8px] font-bold ${st.cls}`}>{st.label}</span>
      <span
        className="flex-none font-mono text-[9.5px] text-muted"
        title={item.created_at ? `${item.created_at}(UTC 存 · 按浏览器时区显示)` : "无时间戳"}
      >
        {formatLocal(item.created_at)}
      </span>
    </div>
  );
}

/* ============ 状态过滤 chips(旧页四态+全部 零丢失;计数=同一份口径函数) ============ */

export const STATUS_FILTERS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "pending", label: "待起草" },
  { value: "drafted", label: "待回复" },
  { value: "replied", label: "已回复" },
  { value: "dismissed", label: "已忽略" },
  { value: "", label: "全部" },
];

export function StatusChips({
  active,
  counts,
  onChange,
}: {
  active: string;
  counts: QueueCounts | null;
  onChange: (value: string) => void;
}) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5">
      {STATUS_FILTERS.map((f) => {
        const on = active === f.value;
        const n = counts ? (f.value ? counts.byStatus[f.value] || 0 : counts.total) : null;
        return (
          <button
            key={f.value || "all"}
            type="button"
            onClick={() => onChange(f.value)}
            className={`rounded-full border px-2.5 py-0.5 text-[10.5px] transition-colors ${
              on ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-ink"
            }`}
          >
            {f.label}
            {n != null ? <span className="ml-1 font-mono text-[9px]">{n}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

/* ============ 处理进度(状态漏斗;BarRow 复用,色=状态语义 token) ============ */

export function FunnelBody({ counts }: { counts: QueueCounts }) {
  const max = STATUS_ORDER.reduce((acc, key) => Math.max(acc, counts.byStatus[key] || 0), 0);
  if (counts.total === 0) return <EmptyLine text="队列 0 条,漏斗诚实不画。" />;
  return (
    <div>
      {STATUS_ORDER.map((key) => {
        const meta = STATUS_META[key];
        const n = counts.byStatus[key] || 0;
        return (
          <BarRow
            key={key}
            name={meta.label}
            widthPct={max > 0 ? (n / max) * 100 : 0}
            color={meta.bar}
            dashed={key === "dismissed"}
            value={`${n} · ${counts.total > 0 ? Math.round((n / counts.total) * 100) : 0}%`}
            title={`vkpi_reply_queue.status=${key}`}
          />
        );
      })}
      <div className="mt-[7px] font-mono text-[9px] text-muted">
        状态机:待起草 → 待回复 → 已回复 / 已忽略 · 终态不回炉
      </div>
    </div>
  );
}

/* ============ 平台 / 语言分布(BarRow 复用;点行下钻由页层注入) ============ */

export function PlatBody({ counts, onRow }: { counts: QueueCounts; onRow?: (key: string) => void }) {
  if (counts.byPlatform.length === 0) return <EmptyLine text="队列 0 条,无平台分布。" />;
  return (
    <div>
      {counts.byPlatform.map((it) => (
        <BarRow
          key={it.key}
          name={platformBadge(it.key)}
          widthPct={(it.count / counts.total) * 100}
          value={`${it.count} · ${Math.round((it.count / counts.total) * 100)}%`}
          title={`platform=${it.key}${onRow ? " · 点击查看该平台队列" : ""}`}
          onClick={onRow ? () => onRow(it.key) : undefined}
        />
      ))}
    </div>
  );
}

export function LangBody({ counts, onRow }: { counts: QueueCounts; onRow?: (key: string) => void }) {
  if (counts.byLang.length === 0) return <EmptyLine text="队列 0 条,无语言分布。" />;
  return (
    <div>
      {counts.byLang.map((it) => (
        <BarRow
          key={it.key}
          name={langLabel(it.key)}
          widthPct={(it.count / counts.total) * 100}
          dashed={it.key === "und"}
          color={it.key === "und" ? "var(--ds-muted)" : undefined}
          value={`${it.count} · ${Math.round((it.count / counts.total) * 100)}%`}
          title={`lang=${it.key}${onRow ? " · 点击查看该语种队列" : ""}`}
          onClick={onRow ? () => onRow(it.key) : undefined}
        />
      ))}
    </div>
  );
}

/* ============ 模块通用空态/说明行 re-export(页层零重复 import 源) ============ */
export type { Row };
