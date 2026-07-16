import React from "react";
import { formatLocal } from "../../lib/timeLocal";
import type {
  IntelligentAction,
  IntelligentAnswer,
  IntelligentEvidence,
} from "../../../../services/vkpi/intelligent-api";

// Intelligent 问答 · 板块页范式辅助件(IntelligentBoardPage 专用,页内拆件不入公共桶)。
//   本文件住:车道徽 META / SrcChip 口径注册表 / 本机留痕仓(localStorage)/
//   引用来源标注(citationChips + EvidenceBlocks,答案卡与详情弹窗共用同一套)/
//   AnswerCard(mode 徽 + 当日缓存徽 + 结论 + 引用行 + 动作直跳)/ HistoryRowLine。
//   引用口径(重点,如实精准):意图查询有列返回 或 检索候选 > 0 = 库内引用;
//   综合(synth)正文由模型生成、不直接读库 —— provider/model 只算生成信息,
//   不冒充库内引用;全组皆无 = 卡面明标「无库内引用」。
// 红线:本文件零直连网络(动作走 page 层回调);不触 viltrox_fit_score / rule_v0;
//   颜色全 token 类零写死色;零 opacity 修饰类;时间 = 绝对时间戳(存 UTC,
//   显示按浏览器时区 formatLocal,禁相对时间当唯一展示)。

export type Row = Record<string, any>;

/* ============ 车道徽:token 语义色;技术口径收进 hover title(卡面零术语) ============ */
export const MODE_META: Record<string, { label: string; cls: string; title: string }> = {
  intent: { label: "秒回", cls: "border-good bg-good-soft text-good", title: "固定问法命中 · 结构化直查(query_planner)" },
  search: { label: "检索", cls: "border-info bg-info-soft text-info", title: "未命中固定问法 · 池内关键词检索(unified_search)" },
  synth: { label: "综合", cls: "border-accent-2 bg-card text-accent-2", title: "模型综合回答(llm_gateway · 预算闸内)" },
  degraded: { label: "降级", cls: "border-warn bg-warn-soft text-warn", title: "综合不可用/超预算 · 已诚实回退到检索结果" },
};

export function modeMeta(mode: string) {
  return MODE_META[mode] || { label: mode || "未知", cls: "border-line bg-card text-ink-2", title: "未知车道" };
}

export function ModeBadge({ mode }: { mode: string }) {
  const meta = modeMeta(mode);
  return (
    <span className={`flex-none rounded-full border px-2 py-0.5 text-[9.5px] font-semibold ${meta.cls}`} title={meta.title}>
      {meta.label}
    </span>
  );
}

/* ============ SrcChip 口径注册表(真实表名/端点/口径;旧页头介绍句收编到 qa 行) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiI: {
    label: "本机留痕 · vkpi_llm_calls",
    rows: [
      ["会话数/今日", "本机 localStorage 留痕(vkpi-intelligent-history-v1)· 非服务端表,换浏览器/清缓存即重置"],
      ["今日日界", "按浏览器时区(与全站显示口径一致)"],
      ["命中引用率", "有库内引用的回答占比 · 库内引用 = 意图查询有列返回 或 检索候选 > 0"],
      ["综合回答", "vkpi_llm_calls(purpose=vkpi_intelligent_ask)服务端真留痕 · UTC 日界"],
      ["留痕范围", "仅成功回答留痕(上限 100 条)· 意图/检索车道服务端不落库,如实分开计"],
    ],
  },
  qa: {
    label: "intelligent/ask · 三车道",
    rows: [
      // 旧页头介绍句(一个问题,三车道分诊…全只读)按「卡面去术语」收编到此,口径不丢
      ["车道", "意图秒回 → 池内检索 → 模型综合(预算不足自动降级检索)· 全只读"],
      ["端点", "POST /api/admin/vkpi/intelligent/ask(30s 综合超时,前端 35s 容错)"],
      ["缓存", "当日同问服务端内存缓存(换日失效)· 命中显「当日缓存」徽"],
      ["意图证据", "query_planner 结构化行(columns/rows,封顶 50 行,完整结果在问数页)"],
      ["检索证据", "unified_search 池内候选(不含外部源,封顶 20 条)"],
      ["综合口径", "正文由模型生成、不直接读库 —— 库内引用 = 附带的检索候选,如实标注"],
    ],
  },
  sugg: {
    label: "intelligent/suggestions",
    rows: [
      ["异动种", "vkpi_alerts 近24h open 告警 + apify_jobs 近24h done 计数"],
      ["兜底", "无当日异动时用内置默认集补齐(3-6 条去重保序)"],
      ["动作", "点一条 = 直接提问(与手动输入同一条 ask 通路)"],
    ],
  },
  history: {
    label: "本机 localStorage",
    rows: [
      ["留痕仓", "vkpi-intelligent-history-v1 · 最近 100 条成功回答"],
      ["口径", "非服务端留痕 —— 换浏览器/清缓存不同步;仅成功回答入仓"],
      ["时间", "存 UTC · 按浏览器时区显示(绝对时间戳)"],
    ],
  },
  advisor: {
    label: "marketing-advisor · 服务端持久化",
    rows: [
      ["会话归属", "vkpi_advisor_threads/messages · organization_id + staff_id 双重隔离"],
      ["顾问回答", "POST /api/admin/vkpi/marketing-advisor/threads/{id}/messages · 未就绪时 HTTP 200 诚实降级"],
      ["动作边界", "外发/联系/写业务/费用只落 vkpi_advisor_action_drafts，不可执行"],
      ["证据口径", "结论保留 provider/status/provenance；无真实数据时继续 descriptive_only"],
    ],
  },
  memory: {
    label: "advisor-memory · 当前员工",
    rows: [
      ["私有范围", "vkpi_advisor_memory_* · 仅当前 organization_id + staff_id 可读写"],
      ["学习机制", "用户输入→候选→用户显式确认→生效 fact；禁止静默自动记忆"],
      ["控制", "记忆总开关可暂停/恢复，单条 fact 也可暂停/恢复，变更入审计事件"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiI: "问答总览",
  qa: "问答",
  sugg: "建议问题",
  history: "历史会话",
  advisor: "AI 营销顾问",
  memory: "我的记忆与学习",
};

/* ============ 本机留痕仓:localStorage(仅成功回答;失败/加载中不入仓) ============ */
export const HISTORY_KEY = "vkpi-intelligent-history-v1";
export const HISTORY_CAP = 100;

export interface AskHistoryEntry {
  id: string;
  q: string;
  at: string; // ISO UTC
  mode: string;
  cached: boolean;
  answer: string;
  evidence: IntelligentEvidence[];
  actions: IntelligentAction[];
}

export function loadHistory(): AskHistoryEntry[] {
  try {
    const raw = window.localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (x): x is AskHistoryEntry => !!x && typeof x === "object" && typeof x.q === "string" && typeof x.answer === "string",
    );
  } catch {
    return [];
  }
}

export function persistHistory(list: AskHistoryEntry[]): void {
  try {
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_CAP)));
  } catch {
    /* 配额/隐私模式:留痕失败不拖垮问答本体 */
  }
}

// 入仓前裁证据体积(与后端封顶同口径:intent 50 行 / search 20 条),防 localStorage 撑爆
export function trimEvidence(evidence: IntelligentEvidence[]): IntelligentEvidence[] {
  return (Array.isArray(evidence) ? evidence : []).map((ev) => {
    if (ev.kind === "intent_result") {
      return { ...ev, rows: Array.isArray(ev.rows) ? (ev.rows as unknown[]).slice(0, 50) : [] };
    }
    if (ev.kind === "search_results") {
      return { ...ev, results: Array.isArray(ev.results) ? (ev.results as unknown[]).slice(0, 20) : [] };
    }
    return ev;
  });
}

export function historyEntryOf(id: string, q: string, at: string, res: IntelligentAnswer): AskHistoryEntry {
  return {
    id,
    q,
    at,
    mode: String(res.mode || ""),
    cached: !!res.cached,
    answer: String(res.answer || ""),
    evidence: trimEvidence(res.evidence || []),
    actions: Array.isArray(res.actions) ? res.actions : [],
  };
}

// 今日口径:浏览器时区日界(与 formatLocal 显示口径一致)
export function isLocalToday(iso: string): boolean {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

/* ============ 引用来源标注(重点,口径精准):库内引用判定 + 引用 chips ============ */
// 库内引用 = 意图查询有列返回 或 检索候选 > 0;synth 组只是生成信息,不算库内引用
export function hasLibCitation(evidence: IntelligentEvidence[]): boolean {
  return (Array.isArray(evidence) ? evidence : []).some((ev) => {
    if (ev.kind === "intent_result") return Array.isArray(ev.columns) && (ev.columns as unknown[]).length > 0;
    if (ev.kind === "search_results") {
      const n = Number((ev as Row).count ?? (Array.isArray((ev as Row).results) ? (ev as Row).results.length : 0)) || 0;
      return n > 0;
    }
    return false;
  });
}

export interface CitationChip {
  key: string;
  label: string;
  lib: boolean; // 是否库内引用(true = accent 可点样式;false = 生成信息/空候选)
}

export function citationChips(evidence: IntelligentEvidence[]): CitationChip[] {
  const chips: CitationChip[] = [];
  (Array.isArray(evidence) ? evidence : []).forEach((ev, i) => {
    if (ev.kind === "intent_result") {
      const cols = Array.isArray(ev.columns) ? (ev.columns as unknown[]).length : 0;
      const rows = Array.isArray(ev.rows) ? (ev.rows as unknown[]).length : 0;
      chips.push({ key: `intent-${i}`, label: `⛁ 意图查询「${String(ev.title || ev.intent || "结构化结果")}」· ${rows} 行`, lib: cols > 0 });
    } else if (ev.kind === "search_results") {
      const n = Number((ev as Row).count ?? (Array.isArray((ev as Row).results) ? (ev as Row).results.length : 0)) || 0;
      chips.push({ key: `search-${i}`, label: `⛁ 池内检索 · ${n} 候选`, lib: n > 0 });
    } else if (ev.kind === "synth") {
      // provider/model 属内部口径 → 卡面只留入口,细节进引用弹窗
      chips.push({ key: `synth-${i}`, label: "⚙ 生成信息", lib: false });
    } else {
      chips.push({ key: `${String(ev.kind || "ev")}-${i}`, label: `⛁ ${String(ev.kind || "证据")}`, lib: false });
    }
  });
  return chips;
}

export function libCitationCount(evidence: IntelligentEvidence[]): number {
  return citationChips(evidence).filter((c) => c.lib).length;
}

/* ============ 证据渲染(旧页 EvidenceBlock 三轨零丢失,token 化 + 身份跳升级) ============
   intent_result 小表格(50 行封顶,无列如实)/ search_results 候选列表(20 条封顶,
   带 kol_pool_id 的候选可跳档案)/ synth 生成信息行 + 诚实口径注 / 未知 kind JSON 折叠。 */
export function EvidenceBlocks({
  evidence,
  onOpenKol,
}: {
  evidence: IntelligentEvidence[];
  /** 检索候选身份跳(kol_pool_id → KOL 档案);缺省 = 候选纯文本,如实不可点 */
  onOpenKol?: (kolPoolId: number) => void;
}) {
  const list = Array.isArray(evidence) ? evidence : [];
  if (list.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-line px-3 py-3 text-center text-[11.5px] text-muted">
        无库内引用 —— 本回答未携带任何库内证据,如实标注。
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {list.map((ev, i) => (
        <EvidenceBlock key={i} ev={ev} onOpenKol={onOpenKol} />
      ))}
    </div>
  );
}

function EvidenceBlock({ ev, onOpenKol }: { ev: IntelligentEvidence; onOpenKol?: (kolPoolId: number) => void }) {
  if (ev.kind === "intent_result") {
    const columns = Array.isArray(ev.columns) ? (ev.columns as string[]) : [];
    const rows = Array.isArray(ev.rows) ? (ev.rows as Array<Record<string, unknown>>) : [];
    return (
      <div>
        <div className="mb-1.5 text-[10.5px] text-muted">
          意图查询「{String(ev.title || ev.intent || "结构化结果")}」· {rows.length} 行(封顶 50,完整结果在问数页)
        </div>
        {columns.length === 0 ? (
          <div className="text-[11.5px] text-muted">无列返回。</div>
        ) : (
          <div className="overflow-auto rounded-lg border border-line">
            <table className="w-full border-collapse text-[11px]">
              <thead>
                <tr className="border-b border-line bg-panel">
                  {columns.map((c) => (
                    <th key={c} className="px-2 py-1.5 text-left font-medium text-accent">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 50).map((row, ri) => (
                  <tr key={ri} className="border-b border-line last:border-0">
                    {columns.map((c) => {
                      const v = row[c];
                      return (
                        <td key={c} className="px-2 py-1 text-ink-2">
                          {v === null || v === undefined ? "—" : String(v)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  if (ev.kind === "search_results") {
    const results = Array.isArray(ev.results) ? (ev.results as Array<Record<string, unknown>>) : [];
    const n = Number((ev as Row).count ?? results.length) || 0;
    return (
      <div>
        <div className="mb-1.5 text-[10.5px] text-muted">池内检索 · {n} 候选(展示前 20 · vkpi_kol_pool 召回)</div>
        {results.length === 0 ? (
          <div className="text-[11.5px] text-muted">无候选。</div>
        ) : (
          <ul className="space-y-1">
            {results.slice(0, 20).map((r, i) => {
              const name = String(r.name ?? r.handle ?? r.username ?? r.kol_pool_id ?? `候选 ${i + 1}`);
              const platform = r.platform ? String(r.platform) : "";
              const kolId = r.kol_pool_id != null && Number.isFinite(Number(r.kol_pool_id)) ? Number(r.kol_pool_id) : null;
              return (
                <li key={i} className="flex items-center gap-2 rounded-md border border-line px-2 py-1 text-[11.5px] text-ink-2">
                  <span className="min-w-0 flex-1 truncate">{name}</span>
                  {platform ? <span className="flex-none text-[9.5px] text-muted">{platform}</span> : null}
                  {kolId != null && onOpenKol ? (
                    <button
                      type="button"
                      onClick={() => onOpenKol(kolId)}
                      title={`打开 KOL 档案(vkpi_kol_pool #${kolId})`}
                      className="flex-none rounded-md border border-line px-1.5 py-0.5 text-[9.5px] text-muted transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
                    >
                      档案 →
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    );
  }

  if (ev.kind === "synth") {
    return (
      <div className="rounded-lg border border-line bg-panel px-3 py-2 text-[11.5px]">
        <div className="flex items-center justify-between gap-3 border-b border-line py-1.5 text-ink-2">
          <span className="text-muted">生成提供方</span>
          <span className="font-mono">{String(ev.provider || "—")}</span>
        </div>
        <div className="flex items-center justify-between gap-3 py-1.5 text-ink-2">
          <span className="text-muted">模型</span>
          <span className="font-mono">{String(ev.model || "—")}</span>
        </div>
        <div className="mt-1 text-[10px] leading-[1.6] text-muted">
          正文由模型生成,未直接读库 —— 库内引用以检索候选组为准;如无候选组,即为无库内引用。
        </div>
      </div>
    );
  }

  // 未知 kind:JSON 折叠展示(旧页零丢失)
  return (
    <pre className="overflow-auto rounded-lg border border-line bg-panel p-2 text-[10px] text-muted">
      {JSON.stringify(ev, null, 2)}
    </pre>
  );
}

/* ============ 答案卡:mode 徽 + 当日缓存徽 + 结论 + 引用来源行(可点)+ 动作直跳 ============ */
export function AnswerCard({
  answer,
  at,
  onOpenEvidence,
  onAction,
}: {
  answer: IntelligentAnswer;
  at: string;
  /** 引用 chip 点击 → 引用来源弹窗(page 层持弹窗状态) */
  onOpenEvidence: () => void;
  /** 动作按钮直跳 cockpit 路由(page 层委托 onNavigate) */
  onAction: (action: IntelligentAction) => void;
}) {
  const chips = citationChips(answer.evidence || []);
  const lib = hasLibCitation(answer.evidence || []);
  return (
    <div className="rounded-[11px] border border-line bg-panel px-3.5 py-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <ModeBadge mode={answer.mode} />
        {answer.cached ? (
          <span
            className="flex-none rounded-full border border-line bg-card px-2 py-0.5 text-[9.5px] text-muted"
            title="当日同问命中服务端缓存(换日失效)"
          >
            当日缓存
          </span>
        ) : null}
        <span className="ml-auto flex-none font-mono text-[9.5px] text-muted" title="按浏览器时区显示(存 UTC)">
          {formatLocal(at)}
        </span>
      </div>

      {/* 结论加粗(旧页同款 pre-wrap) */}
      <div className="mt-2 whitespace-pre-wrap text-[13px] font-semibold leading-[1.7] text-ink">{answer.answer}</div>

      {/* 引用来源标注:真来源可点开;无库内引用如实标(重点口径) */}
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-line pt-2">
        {!lib ? (
          <span className="flex-none rounded-md border border-dashed border-line px-2 py-0.5 text-[9.5px] text-muted" title="本回答无库内引用(意图零列返回 / 检索零候选 / 纯生成)">
            无库内引用
          </span>
        ) : null}
        {chips.map((chip) => (
          <button
            key={chip.key}
            type="button"
            onClick={onOpenEvidence}
            title="点开看底层证据(引用来源弹窗)"
            className={`flex-none rounded-md border px-2 py-0.5 text-[9.5px] transition-colors ${
              chip.lib
                ? "border-accent bg-accent-soft text-accent hover:border-accent-hover"
                : "border-line text-muted hover:border-line-strong hover:text-ink-2"
            }`}
          >
            {chip.label}
          </button>
        ))}
      </div>

      {/* 动作按钮直跳路由(旧页零丢失) */}
      {Array.isArray(answer.actions) && answer.actions.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {answer.actions.map((a, i) => (
            <button
              key={i}
              type="button"
              onClick={() => onAction(a)}
              className="rounded-md border border-line px-2.5 py-1 text-[11px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
            >
              {a.label} →
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ============ 历史会话行:mode 徽 + 问题 + 引用计数 + 绝对时间(点开详情连续翻) ============ */
export function HistoryRowLine({
  entry,
  index,
  onOpen,
}: {
  entry: AskHistoryEntry;
  index: number;
  onOpen: (i: number) => void;
}) {
  const nLib = libCitationCount(entry.evidence || []);
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
      <ModeBadge mode={entry.mode} />
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent">{entry.q}</span>
      <span
        className={`flex-none rounded-md border px-1.5 py-px text-[8.5px] font-semibold ${
          nLib > 0 ? "border-accent bg-accent-soft text-accent" : "border-dashed border-line text-muted"
        }`}
        title={nLib > 0 ? "库内引用组数(意图查询/检索候选)" : "本回答无库内引用"}
      >
        {nLib > 0 ? `引用 ×${nLib}` : "无引用"}
      </span>
      <span className="flex-none font-mono text-[9.5px] text-muted" title={`${entry.at}(UTC 存 · 按浏览器时区显示)`}>
        {formatLocal(entry.at)}
      </span>
    </div>
  );
}

/* ============ 服务端统计 → 14 天零填齐序列(UTC 日界,与后端 by_day 同口径) ============ */
export function statsSeries(byDay: Array<{ date: string; count: number }> | undefined, days = 14): number[] {
  const map = new Map((byDay || []).map((d) => [String(d.date), Number(d.count) || 0]));
  const out: number[] = [];
  const now = new Date();
  for (let i = days - 1; i >= 0; i -= 1) {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - i));
    out.push(map.get(d.toISOString().slice(0, 10)) || 0);
  }
  return out;
}
