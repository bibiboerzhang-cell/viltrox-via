import React from "react";
import { ProvChain, RecordPreview, type ProvStep } from "../components/provenance";
import { formatLocal } from "../../lib/timeLocal";
import type { VoiceFeedItem } from "../../../../services/vkpi/marketVoice-api";

// 市场之声 · 弹窗族(V0h-c 自 MarketVoicePage.modules 拆出,行数纪律 ≤700/文件)。
//   ModalShell 对齐 demo .scrim/.drawer:遮罩 var(--ds-scrim)+blur(3px)、容器 18px 圆角
//   /86vh/标题 17px/680/关闭钮 30×30,入场 scale(.97)→1 两态过渡(ds-viz.css,自带
//   reduced-motion 降级);弹窗内分区密度 = demo .dsec/.dl/.drow(22px/9.5px/12.5px)。
//   color-scheme:本壳表面全 token 跟主题 → 挂 cockpit-modal--themed 修饰类
//   (global.css:基类 .cockpit-modal 钉死 dark 供写死暗表面的遗留弹窗保真)。
//   依赖单向:本文件不 import MarketVoicePage.modules(feed 行由调用方以 children 传入);
//   身份徽/平台徽元数据(IDENTITY_META/platformBadge)住这里,modules 反向复用。
// 红线:零直连网络(动作走调用方回调);不触 viltrox_fit_score / rule_v0;
//        颜色全 token 类零写死色;动效只用 ds-viz.css 既有类。

/* ============ 反馈身份/平台徽元数据(FeedDetailModal 与 modules 的 FeedRowLine 共用) ============ */
// 四类身份徽:kol / owned / user 由 voice-feed post_table 三分类真实产出;
// ec(电商,warn/amber token 系)为第四类预留 —— 数据源 vkpi_bh_reviews 未来接入
// (feed 当前不产出 ec 条目,徽章族先补齐,接入后零改动点亮)。
export const IDENTITY_META: Record<string, { label: string; cls: string; text: string }> = {
  kol: { label: "KOL", cls: "border-accent text-accent", text: "text-accent" },
  owned: { label: "官号", cls: "border-accent-2 text-accent-2", text: "text-accent-2" },
  user: { label: "路人", cls: "border-line text-muted", text: "text-muted" },
  ec: { label: "电商", cls: "border-warn text-warn", text: "text-warn" },
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

/* ============ 弹窗骨架:demo .scrim/.drawer 1:1(遮罩/圆角/标题/关闭钮/入场过渡) ============ */
// 弹窗栈:叠层时(全量列表下再开单条详情)Escape 只关最上层。
const MODAL_STACK: symbol[] = [];

export function ModalShell({
  title,
  sub,
  onClose,
  children,
  maxWidth = "max-w-[700px]",
}: {
  title: React.ReactNode;
  sub?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  maxWidth?: string;
}) {
  const idRef = React.useRef<symbol | null>(null);
  if (!idRef.current) idRef.current = Symbol("vkpi-modal");
  // 入场两态:挂载后下一帧置 on(demo .scrim/.drawer .on;reduced-motion 由 CSS 降级)
  const [on, setOn] = React.useState(false);
  React.useEffect(() => {
    const raf = requestAnimationFrame(() => setOn(true));
    return () => cancelAnimationFrame(raf);
  }, []);
  React.useEffect(() => {
    const id = idRef.current as symbol;
    MODAL_STACK.push(id);
    return () => {
      const at = MODAL_STACK.indexOf(id);
      if (at >= 0) MODAL_STACK.splice(at, 1);
    };
  }, []);
  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape" && MODAL_STACK[MODAL_STACK.length - 1] === idRef.current) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className={`cockpit-modal cockpit-modal--themed ds-modal-fade ${on ? "is-on" : ""} fixed inset-0 flex items-center justify-center bg-[var(--ds-scrim)] p-4 backdrop-blur-[3px]`}
      style={{ zIndex: 999 }}
      role="presentation"
      onMouseDown={(ev) => {
        if (ev.target === ev.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className={`ds-dialog-pop ${on ? "is-on" : ""} relative flex max-h-[86vh] w-full ${maxWidth} flex-col overflow-hidden rounded-[18px] border border-line bg-[var(--ds-overlay-surface)] shadow-ds`}
      >
        <div className="flex flex-none items-start justify-between gap-3 border-b border-line px-[22px] pb-3.5 pt-[18px]">
          <div className="min-w-0">
            <div className="text-[17px] font-[680] tracking-[-0.02em] text-ink">{title}</div>
            {sub ? <div className="mt-[3px] text-[11px] text-muted">{sub}</div> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="grid h-[30px] w-[30px] flex-none place-items-center rounded-[9px] border border-line text-[15px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink"
          >
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-[22px] pb-10 pt-[18px]">{children}</div>
      </div>
    </div>
  );
}

/* ============ 弹窗内分区件:demo .dsec .dl / .drow 密度 ============ */
export function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="mb-2.5 text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">{children}</div>;
}

export function Drow({ k, v, tone }: { k: string; v: React.ReactNode; tone?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line py-2 text-[12.5px] last:border-0">
      <span className="flex-none text-muted">{k}</span>
      <span className={`min-w-0 truncate text-right font-mono ${tone || "text-ink"}`}>{v}</span>
    </div>
  );
}

/* ============ 原声引文弹窗(卡面 QuoteFold 一行入口 → 全量引文进这里) ============ */
export interface QuoteRow {
  text?: string;
  author?: string;
  platform?: string;
  at?: string;
  likes?: number;
  source?: string;
}

export function QuoteDialog({
  title,
  sub,
  lead,
  quotes,
  onClose,
}: {
  title: React.ReactNode;
  sub?: React.ReactNode;
  /** 可选说明段(recs 的 detail 多行说明收进弹窗) */
  lead?: string;
  quotes: QuoteRow[];
  onClose: () => void;
}) {
  return (
    <ModalShell title={title} sub={sub ?? `原声引文 ×${quotes.length} · 词表命中原文`} onClose={onClose}>
      {lead ? (
        <div className="mb-[22px]">
          <SectionLabel>说明</SectionLabel>
          <div className="text-[12.5px] leading-[1.75] text-ink-2">{lead}</div>
        </div>
      ) : null}
      <div>
        <SectionLabel>原声引文 ×{quotes.length}</SectionLabel>
        {quotes.length === 0 ? (
          <div className="px-3 py-4 text-center text-[12px] text-muted">本条无原声引文。</div>
        ) : (
          <div className="space-y-2">
            {quotes.map((q, i) => (
              <div key={i} className="rounded-[11px] border border-line bg-panel px-3.5 py-2.5">
                <div className="text-[12.5px] leading-relaxed text-ink-2">“{q.text}”</div>
                <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-muted">
                  <span>{q.author || "匿名"}</span>
                  {q.platform ? <span>{q.platform}</span> : null}
                  {q.at ? <span>{String(q.at).slice(0, 10)}</span> : null}
                  {Number(q.likes) > 0 ? <span>♥ {q.likes}</span> : null}
                  {q.source ? <span className="font-mono opacity-80">{q.source}</span> : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </ModalShell>
  );
}

/* ============ 反馈流全量弹窗(demo feedmore → drawer FULLLIST;行由调用方传 children) ============ */
export function FeedListModal({
  total,
  loadedCount,
  hasMore,
  loading,
  error = "",
  onLoadMore,
  onClose,
  children,
}: {
  total: number;
  loadedCount: number;
  hasMore: boolean;
  loading: boolean;
  /** 「载入更多」失败原因(如实展示在按钮下方,不吞;已加载列表照常渲染) */
  error?: string;
  onLoadMore: () => void;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <ModalShell title="反馈流 · 全量" sub={`共 ${total} 条 · 点单条看详情(详情内 ↑↓ 连续翻)· 每条「↗」直跳原帖`} onClose={onClose}>
      <SectionLabel>全量反馈</SectionLabel>
      {children}
      {hasMore ? (
        <button
          type="button"
          onClick={onLoadMore}
          disabled={loading}
          className="mt-2.5 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft disabled:opacity-50"
        >
          {loading ? "加载中…" : `≡ 载入更多(已加载 ${loadedCount}/${total})`}
        </button>
      ) : null}
      {error ? <div className="mt-2 text-[10.5px] text-crit">载入更多失败:{error}</div> : null}
    </ModalShell>
  );
}

/* ============ 反馈详情弹窗:‹ #n/N › + ↑↓ 连续翻 + 溯源链 + 库记录预览 ============ */
const NAV_BTN =
  "rounded-lg border border-line px-2.5 py-1 text-[11px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:cursor-default disabled:opacity-40";

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
  queued = false,
  queueBusy = false,
  queueError = "",
  onEnqueueReply,
  referred = false,
  referBusy = false,
  referError = "",
  onReferPrd,
  onIdentityJump,
}: {
  item: VoiceFeedItem;
  index: number;
  total: number;
  loadingNext: boolean;
  onNav: (i: number) => void;
  onClose: () => void;
  /** V0e 闭环动作:该条是否已入回复队列(本次会话真实结果 / already_queued) */
  queued?: boolean;
  /** 入队请求进行中(按钮防双击,不假装瞬时成功) */
  queueBusy?: boolean;
  /** 入队失败原因(如实展示,不吞) */
  queueError?: string;
  /** 「转回复队列」点击回调(page 层持 token 真调端点);缺省 = 按钮 disabled */
  onEnqueueReply?: () => void;
  /** V1.1 真闭环:该条是否已转产品部(端点真实返回 ok / already_referred 才为 true) */
  referred?: boolean;
  /** 转交请求进行中(按钮防双击,不假装瞬时成功) */
  referBusy?: boolean;
  /** 转交失败原因(如实展示,不吞) */
  referError?: string;
  /** 「转产品部」点击回调(page 层真调 POST /market/prd-referrals);缺省 = 按钮 disabled */
  onReferPrd?: () => void;
  /** 溯源身份跳:kol → KOL 档案页 / owned → 官号矩阵(MY KOL);缺省 = 身份节点纯文本不可点 */
  onIdentityJump?: () => void;
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
  // 身份跳节点(波D 验收遗留补全):kol → KOL 档案 / owned → 官号矩阵;跳不了
  // (user 身份 / kol 缺 identity_id)→ 纯文本节点,如实不可点,不装按钮。
  const identityLabel = `身份 ${idn.label}${item.identity_ref ? ` ${item.identity_ref}` : ""}`;
  if (item.identity === "kol" || item.identity === "owned") {
    steps.push(
      onIdentityJump
        ? { label: `${identityLabel} →跳${item.identity === "kol" ? "档案" : "矩阵"}`, rec: "identity" }
        : { label: identityLabel },
    );
  }
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
          captured {formatLocal(item.created_at, { year: "numeric" })}(按浏览器时区)· 身份{" "}
          <span className={idn.text}>
            {idn.label}
            {item.identity_ref ? ` ${item.identity_ref}` : ""}
          </span>
        </>
      }
      onClose={onClose}
    >
      <div className="mb-3.5 flex flex-wrap items-center gap-2">
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

      <div className="mb-[22px]">
        <SectionLabel>原文</SectionLabel>
        <div className="text-[13px] leading-[1.8] text-ink-2">{item.text || "—"}</div>
      </div>

      <div className="mb-[22px]">
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
        <ProvChain
          steps={steps}
          onRecord={(key) => {
            // 身份节点点击 = 真跳转(kol → KOL 档案 / owned → 官号矩阵),其余节点开记录预览
            if (key === "identity") onIdentityJump?.();
            else setRec(key);
          }}
        />
        {rec ? <RecordPreview title="库记录预览 · 点其他节点切换" rows={feedRecordRows(item, rec)} /> : null}
      </div>

      {/* 闭环动作(V0e+V1.1 · demo .aacts 行):转回复队列 / 转产品部均为真端点,成功才置绿 */}
      <div className="mt-[22px] flex gap-2 border-t border-line pt-3.5">
        <button
          type="button"
          disabled={queued || queueBusy || !onEnqueueReply}
          onClick={onEnqueueReply}
          title={
            queued
              ? "该评论已在回复队列(vkpi_reply_queue),不重复入队"
              : "手动把这条评论转入回复队列(POST /reply-queue/enqueue-comment,幂等)"
          }
          className={`flex-1 rounded-lg border px-3 py-2 text-center text-[11.5px] transition-colors disabled:cursor-default ${
            queued
              ? "border-good bg-good-soft text-good"
              : "border-line text-ink-2 hover:border-accent hover:bg-accent-soft hover:text-accent disabled:opacity-50"
          }`}
        >
          {queued ? "✓ 已入队" : queueBusy ? "入队中…" : "💬 转回复队列"}
        </button>
        <button
          type="button"
          disabled={referred || referBusy || !onReferPrd}
          onClick={onReferPrd}
          title={
            referred
              ? "该条已转产品部(vkpi_market_prd_referrals),不重复转交"
              : "把这条声音转交产品部(POST /market/prd-referrals → vkpi_market_prd_referrals,幂等)"
          }
          className={`flex-1 rounded-lg border px-3 py-2 text-center text-[11.5px] transition-colors disabled:cursor-default ${
            referred
              ? "border-good bg-good-soft text-good"
              : "border-line text-ink-2 hover:border-accent hover:bg-accent-soft hover:text-accent disabled:opacity-50"
          }`}
        >
          {referred ? "✓ 已转交" : referBusy ? "转交中…" : "⚙ 转产品部"}
        </button>
      </div>
      {queueError ? (
        <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">
          转回复队列失败:{queueError}
        </div>
      ) : null}
      {referError ? (
        <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">
          转产品部失败:{referError}
        </div>
      ) : null}
      <div className="mt-1.5 text-right text-[9.5px] text-muted">
        转产品部:落 vkpi_market_prd_referrals(迁移 234)· 幂等,已转返回已有行 · 端点真实返回才置绿
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
    ["情感", "vkpi_sentiment_results(已批注 · sentiment_id 回链)"],
    ["PRD 转交", "vkpi_market_prd_referrals(转产品部账本 · 迁移 234)"],
  ],
  aggregate: [
    ["方法", "lexicon_v0 纯词表聚合 · 零 LLM"],
    ["端点", "GET /market/voice-report"],
    ["聚合表", "无独立聚合表 · 请求时实时计算"],
    ["下游", "本板块 / 产品部月报 / vkpi_market_prd_referrals(转产品部)"],
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
      <div className="mb-[22px]">
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
