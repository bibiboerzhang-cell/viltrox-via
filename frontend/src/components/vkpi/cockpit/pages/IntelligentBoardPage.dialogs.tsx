import React from "react";
import { ProvChain, RecordPreview, type ProvStep } from "../components/provenance";
import { formatLocal } from "../../lib/timeLocal";
import { Drow, ModalShell, SectionLabel } from "./MarketVoicePage.dialogs";
import {
  EvidenceBlocks,
  ModeBadge,
  libCitationCount,
  modeMeta,
  type AskHistoryEntry,
} from "./IntelligentBoardPage.modules";
import type { IntelligentAction, IntelligentEvidence } from "../../../../services/vkpi/intelligent-api";

// Intelligent 问答 · 弹窗族(金样板 = MarketVoicePage.dialogs 同构;骨架/分区件直接
// 复用 ModalShell/SectionLabel/Drow,零自造样式)。
//   EvidenceModal      引用来源弹窗(答案卡引用 chip 点开):EvidenceBlocks 三轨 +
//                      检索候选身份跳(kol_pool_id → KOL 档案,page 层管道)。
//   HistoryListModal   历史会话全量(本机留痕全在内存,零分页)+ 两步确认清空。
//   HistoryDetailModal 单条详情:‹ #n/N › + ↑↓ 连续翻 + 问题/回答/数值行/引用来源 +
//                      真动作(重新提问 = 按当前库数据重跑;动作直跳;删除本条)。
//   IntelligentProvModal 模块溯源(SrcChip 点开):口径行 + 三车道通用链(真端点/真表名)。
// 红线:零直连网络(动作走调用方回调);不触 viltrox_fit_score / rule_v0;颜色全
//   token 类零写死色;零 opacity 修饰类(disabled 态用 token 降级);时间 = 绝对
//   时间戳(存 UTC,显示按浏览器时区 formatLocal;UTC 原值进数值行)。

const NAV_BTN =
  "rounded-lg border border-line px-2.5 py-1 text-[11px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:cursor-default disabled:text-muted disabled:hover:border-line";

/* ============ 引用来源弹窗(答案卡 chip 点开) ============ */
export function EvidenceModal({
  question,
  evidence,
  onOpenKol,
  onClose,
}: {
  question: string;
  evidence: IntelligentEvidence[];
  /** 检索候选身份跳(kol_pool_id → KOL 档案);缺省 = 候选纯文本不可点 */
  onOpenKol?: (kolPoolId: number) => void;
  onClose: () => void;
}) {
  const n = Array.isArray(evidence) ? evidence.length : 0;
  return (
    <ModalShell
      title="引用来源"
      sub={`${n} 组证据 · 库内引用 ×${libCitationCount(evidence || [])} · 每组按真实来源渲染,无来源如实标`}
      onClose={onClose}
    >
      <div className="mb-[22px]">
        <SectionLabel>提问</SectionLabel>
        <div className="text-[12.5px] leading-[1.7] text-ink-2">{question || "—"}</div>
      </div>
      <div>
        <SectionLabel>证据组 ×{n}</SectionLabel>
        <EvidenceBlocks evidence={evidence || []} onOpenKol={onOpenKol} />
      </div>
    </ModalShell>
  );
}

/* ============ 历史会话全量弹窗(本机留痕,零分页)+ 两步确认清空 ============ */
export function HistoryListModal({
  count,
  onClearAll,
  onClose,
  children,
}: {
  count: number;
  onClearAll: () => void;
  onClose: () => void;
  children: React.ReactNode;
}) {
  // 两步确认(可反悔):第一次点只上膛,再点才清;点别处/关窗即解除
  const [armed, setArmed] = React.useState(false);
  return (
    <ModalShell
      title="历史会话 · 全量"
      sub={`共 ${count} 条 · 本机留痕(换浏览器不同步)· 点单条看详情(详情内 ↑↓ 连续翻)`}
      onClose={onClose}
    >
      <SectionLabel>全部留痕(新在前)</SectionLabel>
      {count === 0 ? <div className="px-3 py-4 text-center text-[12px] text-muted">本机暂无留痕。</div> : children}
      {count > 0 ? (
        <div className="mt-[22px] border-t border-line pt-3.5">
          <button
            type="button"
            onClick={() => {
              if (armed) onClearAll();
              else setArmed(true);
            }}
            className={`w-full rounded-[9px] border px-3 py-2 text-center text-[10.5px] transition-colors ${
              armed
                ? "border-crit bg-crit-soft text-crit"
                : "border-dashed border-line-strong text-muted hover:border-crit hover:text-crit"
            }`}
          >
            {armed ? `确认清空本机 ${count} 条留痕?再点一次执行(点别处取消)` : "清空本机留痕"}
          </button>
          {armed ? (
            <button type="button" onClick={() => setArmed(false)} className="mt-1.5 w-full text-center text-[9.5px] text-muted transition-colors hover:text-ink-2">
              取消
            </button>
          ) : null}
        </div>
      ) : null}
    </ModalShell>
  );
}

/* ============ 历史单条详情:‹ #n/N › + ↑↓ 连续翻 + 引用来源 + 真动作 ============ */
export function HistoryDetailModal({
  entry,
  index,
  total,
  onNav,
  onClose,
  onReAsk,
  onDelete,
  onAction,
  onOpenKol,
}: {
  entry: AskHistoryEntry;
  index: number;
  total: number;
  onNav: (i: number) => void;
  onClose: () => void;
  /** 重新提问(真动作:走当前 ask 通路按最新库数据重跑,不回放旧答案) */
  onReAsk: (q: string) => void;
  /** 删除本条(仅本机留痕) */
  onDelete: () => void;
  /** 留痕里的动作按钮直跳路由 */
  onAction: (action: IntelligentAction) => void;
  onOpenKol?: (kolPoolId: number) => void;
}) {
  // ↑↓(以及 ←→)方向键连续翻(金样板同款);Escape 交给 ModalShell
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

  const nLib = libCitationCount(entry.evidence || []);
  return (
    <ModalShell
      title="会话详情"
      sub={
        <>
          提问于 {formatLocal(entry.at, { year: "numeric" })}(按浏览器时区)· <ModeBadge mode={entry.mode} />
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
        <button type="button" className={NAV_BTN} disabled={index >= total - 1} onClick={() => onNav(index + 1)}>
          下一条 ›
        </button>
        <button type="button" className={NAV_BTN} onClick={onClose}>
          ≡ 回列表
        </button>
        <span className="ml-auto font-mono text-[9px] text-muted">↑↓ 方向键连续翻</span>
      </div>

      <div className="mb-[22px]">
        <SectionLabel>问题</SectionLabel>
        <div className="text-[13px] leading-[1.8] text-ink">{entry.q || "—"}</div>
      </div>

      <div className="mb-[22px]">
        <SectionLabel>回答(留痕原文 · 重新提问按最新库数据重跑)</SectionLabel>
        <div className="whitespace-pre-wrap text-[12.5px] leading-[1.75] text-ink-2">{entry.answer || "—"}</div>
      </div>

      <div className="mb-[22px]">
        <SectionLabel>数值行</SectionLabel>
        <Drow k="车道" v={modeMeta(entry.mode).label} />
        <Drow k="当日缓存命中" v={entry.cached ? "是" : "否"} />
        <Drow k="库内引用" v={nLib > 0 ? `${nLib} 组` : "无(如实)"} tone={nLib > 0 ? "text-accent" : "text-muted"} />
        <Drow k="提问(本地)" v={formatLocal(entry.at, { year: "numeric" })} />
        <Drow k="at(UTC)" v={entry.at || "—"} />
        <Drow k="留痕位置" v="本机 localStorage · vkpi-intelligent-history-v1" />
      </div>

      <div>
        <SectionLabel>引用来源(留痕快照 · 与回答同批)</SectionLabel>
        <EvidenceBlocks evidence={entry.evidence || []} onOpenKol={onOpenKol} />
      </div>

      {/* 真动作:重新提问(走 ask 通路)/ 留痕动作直跳 / 删除本条(仅本机) */}
      <div className="mt-[22px] flex flex-wrap gap-2 border-t border-line pt-3.5">
        <button
          type="button"
          onClick={() => onReAsk(entry.q)}
          title="用同一问题重新提问(按当前库数据重新分诊,不回放旧答案)"
          className="flex-1 rounded-lg border border-line px-3 py-2 text-center text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
        >
          ↻ 重新提问
        </button>
        {(entry.actions || []).map((a, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onAction(a)}
            className="flex-1 rounded-lg border border-line px-3 py-2 text-center text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
          >
            {a.label} →
          </button>
        ))}
        <button
          type="button"
          onClick={onDelete}
          title="删除这条本机留痕(不影响服务端任何数据)"
          className="flex-none rounded-lg border border-line px-3 py-2 text-center text-[11.5px] text-muted transition-colors hover:border-crit hover:bg-crit-soft hover:text-crit"
        >
          删除本条
        </button>
      </div>
      <div className="mt-1.5 text-right text-[9.5px] text-muted">留痕仅存本机浏览器 · 删除/清空只动本机,零写服务端</div>
    </ModalShell>
  );
}

/* ============ 模块溯源说明弹窗(SrcChip 点开):口径 + 三车道通用链(真端点/真表名) ============ */
const GENERIC_CHAIN: ProvStep[] = [
  { label: "提问 · 本机输入 / 建议 chip", rec: "ask" },
  { label: "车道① 意图 · query_planner", rec: "intent" },
  { label: "车道② 检索 · unified_search", rec: "search" },
  { label: "车道③ 综合 · llm_gateway", rec: "synth" },
  { label: "留痕 · 本机 + vkpi_llm_calls", rec: "log" },
];

const GENERIC_RECS: Record<string, Array<[string, string]>> = {
  ask: [
    ["端点", "POST /api/admin/vkpi/intelligent/ask(只读,不拼 SQL)"],
    ["分诊顺序", "意图命中秒回 → 检索兜底 → 开放式提问才尝试综合"],
    ["缓存", "当日同问服务端内存缓存(换日失效,命中显「当日缓存」徽)"],
  ],
  intent: [
    ["方法", "query_planner 白名单意图 · 结构化直查"],
    ["证据", "intent_result(columns/rows 封顶 50 行 + sql_explain)"],
    ["完整结果", "问数页(答案卡动作直跳)"],
  ],
  search: [
    ["方法", "unified_search 池内候选(include_external=False)"],
    ["证据", "search_results 封顶 20 条 · vkpi_kol_pool 召回"],
    ["身份跳", "候选带 kol_pool_id → KOL 档案可点进"],
  ],
  synth: [
    ["网关", "llm_gateway.invoke(purpose=vkpi_intelligent_ask)"],
    ["预算闸", "check_budget 先行 · 失败/超时(30s)/降级 → 诚实回退检索(mode=degraded)"],
    ["引用口径", "正文不直接读库 —— 库内引用 = 附带检索候选,无候选即无引用(如实标)"],
  ],
  log: [
    ["服务端", "vkpi_llm_calls(仅综合车道,purpose=vkpi_intelligent_ask)· GET /stats 读它"],
    ["本机", "localStorage vkpi-intelligent-history-v1 · 最近 100 条成功回答"],
    ["口径差", "意图/检索车道服务端不落库 —— 会话数/今日问答只有本机口径,KPI 带如实分开"],
  ],
};

export function IntelligentProvModal({
  title,
  caliber,
  onOpenSamples,
  onClose,
}: {
  title: string;
  caliber: Array<[string, string]>;
  /** 底部「底层样本」按钮回调(跳历史会话全量);缺省 = 不渲染按钮 */
  onOpenSamples?: () => void;
  onClose: () => void;
}) {
  const [rec, setRec] = React.useState<string | null>(null);
  return (
    <ModalShell title={`${title} · 数据溯源`} sub="口径 + 来源 + 三车道通用链(链上每跳可点)" onClose={onClose}>
      <div className="mb-[22px]">
        <SectionLabel>口径与来源</SectionLabel>
        {caliber.map(([k, v], i) => (
          <Drow key={`${k}-${i}`} k={k} v={v} />
        ))}
      </div>
      <div>
        <SectionLabel>溯源链 · 通用(逐条证据见答案卡引用/会话详情)</SectionLabel>
        <ProvChain steps={GENERIC_CHAIN} onRecord={(key) => setRec(key)} />
        {rec ? <RecordPreview title="口径预览 · 点其他节点切换" rows={GENERIC_RECS[rec] || []} /> : null}
      </div>
      {onOpenSamples ? (
        <div className="mt-[22px] border-t border-line pt-3.5">
          <button
            type="button"
            onClick={onOpenSamples}
            className="w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
          >
            ≡ 查看底层样本 · 历史会话全量
          </button>
        </div>
      ) : null}
    </ModalShell>
  );
}
