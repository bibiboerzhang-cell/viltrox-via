import React from "react";
import { PencilLine } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { decodeDashboardLayoutPreference, encodeDashboardLayoutPreference } from "../dashboardPreferenceStore";
import {
  askIntelligent,
  fetchIntelligentStats,
  fetchSuggestions,
  type IntelligentAction,
  type IntelligentAnswer,
  type IntelligentStats,
} from "../../../../services/vkpi/intelligent-api";
import { EmptyLine, ErrorCard, KpiCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import {
  AnswerCard,
  HISTORY_CAP,
  HistoryRowLine,
  MODULE_SOURCES,
  PROV_TITLES,
  hasLibCitation,
  historyEntryOf,
  isLocalToday,
  loadHistory,
  persistHistory,
  statsSeries,
  type AskHistoryEntry,
} from "./IntelligentBoardPage.modules";
import { EvidenceModal, HistoryDetailModal, HistoryListModal, IntelligentProvModal } from "./IntelligentBoardPage.dialogs";
import { AdvisorMemoryBody, MarketingAdvisorBody } from "./MarketingAdvisorWorkspace";

// Intelligent 问答 → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage 手法)。
//   旧 IntelligentPage 全功能块零丢失:问答输入(Enter/按钮/思考中)、无 token 与空问题
//   诚实错误、建议 chips(点击即问;旧页加载失败静默 → 升级为诚实错误行)、答案卡
//   (车道徽 秒回/检索/综合/降级 + 当日缓存徽 + 结论加粗 pre-wrap + 动作按钮直跳路由)、
//   证据三轨(intent 表格 50 行封顶 / search 候选 20 条封顶 / synth·未知 JSON)、空态行;
//   旧页头介绍句(三车道分诊…全只读)按「卡面去术语」收进 qa SrcChip 口径行。
//   问答主体未走 embeds 收编:旧页通体写死 slate/emerald/sky 色 + opacity 修饰类
//   (亮色主题即穿帮),且引用来源标注/留痕记录都要挂在答案渲染上 —— 非侵入包装装不进,
//   故按新 token 原生重写(逻辑 1:1 对照,旧页原文件零改动留作回滚垫)。
//   新增(范式六要素):页级 KPI 带 4 卡(会话数/今日问答/命中引用率 = 本机留痕真数,
//   0 也如实 0;综合回答 = vkpi_llm_calls 服务端真留痕 + 14 天真按日 sparkline;本机三卡
//   无时序端点 → 诚实虚线零环比药丸)、对话流(本会话多轮上屏,新在前)、
//   **引用来源标注(重点)**:回答旁真来源 chip 可点开证据弹窗,检索候选带 kol_pool_id
//   可跳 KOL 档案,无库内引用如实明标(综合正文不读库 —— 口径精准不冒充)、
//   历史会话模块(本机留痕 行式 + 全量弹窗 + 详情 ‹ #n/N › + ↑↓ 连续翻 + 重新提问/
//   删除/两步清空)、建议问题模块、可编辑看板(注册表 + palette)、SrcChip 溯源可点进。
// 红线:旧问答与本机历史保持原有读路径;新增顾问/记忆只写按 org + staff
//   校验的专用服务端表,外发/业务写入/费用动作只能生成草稿;绝不写
//   viltrox fit 分 / 不触 rule_v0;颜色全 token 零写死色;时间 = 绝对时间戳(存 UTC
//   显示按浏览器时区);端点失败 = 诚实错误卡。布局只走本机 storageKey,不传 apiToken
//   给 EditableDashboardBoard(其账户级持久化写死 dashboard_layout_v1 键,金样板同注释)。

const STORAGE_KEY = "vkpi-intelligent-layout-v1";
const ADVISOR_LAYOUT_MIGRATION_KEY = `${STORAGE_KEY}:advisor-memory-added-v1`;
const FACE_ROWS = 6; // 历史会话卡面收敛条数(全量走弹窗)

// 默认布局(12 列 · 默认简四卡):kpiI(12) → qa(8) + [sugg(4) / history(4)]
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiI", span: 12 },
  { moduleKey: "advisor", span: 8 },
  { moduleKey: "memory", span: 4 },
  { moduleKey: "qa", span: 8 },
  { moduleKey: "sugg", span: 4 },
  { moduleKey: "history", span: 4 },
];

function migrateAdvisorModulesIntoStoredLayout() {
  if (typeof window === "undefined") return;
  try {
    if (window.localStorage.getItem(ADVISOR_LAYOUT_MIGRATION_KEY) === "1") return;
    const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
    const items = decodeDashboardLayoutPreference<Record<string, unknown>>(raw);
    if (items !== null) {
      const moduleKeys = new Set(items.map((item) => String(item.moduleKey ?? item.type ?? "")));
      const bottom = items.reduce((value, item) => {
        const y = Number(item.y ?? item.row ?? 0);
        const height = Number(item.height ?? item.h ?? 9);
        return Math.max(value, (Number.isFinite(y) ? y : 0) + (Number.isFinite(height) ? height : 9));
      }, 0);
      const additions: Array<Record<string, unknown>> = [];
      if (!moduleKeys.has("advisor")) {
        additions.push({ instanceId: "migration-advisor-v1", moduleKey: "advisor", span: 8, height: 13, x: 0, y: bottom });
      }
      if (!moduleKeys.has("memory")) {
        additions.push({ instanceId: "migration-memory-v1", moduleKey: "memory", span: 4, height: 13, x: 8, y: bottom });
      }
      if (additions.length > 0) {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(encodeDashboardLayoutPreference([...items, ...additions])));
      }
    }
    // A separate marker means a user can deliberately remove either module
    // later without the app silently adding it back on every visit.
    window.localStorage.setItem(ADVISOR_LAYOUT_MIGRATION_KEY, "1");
  } catch {
    // Storage-disabled browsers still receive the fresh default layout.
  }
}

function useAdvisorLayoutMigration() {
  React.useState(() => {
    migrateAdvisorModulesIntoStoredLayout();
    return true;
  });
}

// demo .ph-b:pagehead 药丸徽(金样板同款)
const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

interface ThreadEntry {
  id: string;
  q: string;
  at: string; // ISO UTC
  status: "pending" | "done" | "error";
  answer?: IntelligentAnswer;
  error?: string;
}

export function IntelligentBoardPage({
  apiToken = "",
  onNavigate,
  embeddedModuleKey,
}: {
  apiToken?: string;
  onNavigate?: (navKey: string) => void;
  embeddedModuleKey?: string;
}) {
  useAdvisorLayoutMigration();
  const [editing, setEditing] = React.useState(false);

  // 问答主体:输入 + 本会话对话流(新在前;旧页单答案卡 → 多轮上屏,单飞防并发同旧页)
  const [question, setQuestion] = React.useState("");
  const [thread, setThread] = React.useState<ThreadEntry[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [askError, setAskError] = React.useState("");
  const busyRef = React.useRef(false);

  // 建议 chips(旧页加载失败静默 → 诚实错误行)
  const [suggestions, setSuggestions] = React.useState<string[]>([]);
  const [suggLoading, setSuggLoading] = React.useState(false);
  const [suggError, setSuggError] = React.useState("");

  // 本机留痕(历史会话)+ 服务端综合车道统计
  const [history, setHistory] = React.useState<AskHistoryEntry[]>(() => loadHistory());
  const [stats, setStats] = React.useState<IntelligentStats | null>(null);
  const [statsError, setStatsError] = React.useState("");
  const [statsTick, setStatsTick] = React.useState(0);

  // 弹窗:引用来源(对话流)/ 历史全量 / 历史详情 / 模块溯源
  const [evEntry, setEvEntry] = React.useState<ThreadEntry | null>(null);
  const [histListOpen, setHistListOpen] = React.useState(false);
  const [histIndex, setHistIndex] = React.useState<number | null>(null);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setSuggLoading(true);
    setSuggError("");
    fetchSuggestions(apiToken)
      .then((list) => {
        if (alive) setSuggestions(list);
      })
      .catch((err: any) => {
        if (alive) setSuggError(String(err?.detail || err?.message || "加载失败"));
      })
      .finally(() => {
        if (alive) setSuggLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken]);

  // 服务端综合车道统计(每次成功提问后重拉,读服务器真数,绝不本地 +1 编数)
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setStatsError("");
    fetchIntelligentStats(apiToken)
      .then((res) => {
        if (alive) setStats(res && typeof res === "object" ? res : null);
      })
      .catch((err: any) => {
        if (alive) setStatsError(String(err?.detail || err?.message || "加载失败"));
      });
    return () => {
      alive = false;
    };
  }, [apiToken, statsTick]);

  const ask = React.useCallback(
    (qRaw: string) => {
      const text = qRaw.trim();
      if (!apiToken) {
        setAskError("未登录 / 无 token");
        return;
      }
      if (!text) {
        setAskError("请输入一个问题");
        return;
      }
      if (busyRef.current) return;
      busyRef.current = true;
      const id = `ask-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const at = new Date().toISOString();
      setLoading(true);
      setAskError("");
      setThread((prev) => [{ id, q: text, at, status: "pending" }, ...prev]);
      askIntelligent(apiToken, text)
        .then((res) => {
          setThread((prev) => prev.map((t) => (t.id === id ? { ...t, status: "done", answer: res } : t)));
          // 留痕:仅成功回答入仓(本机,新在前,封顶 HISTORY_CAP)
          setHistory((prev) => {
            const next = [historyEntryOf(id, text, at, res), ...prev].slice(0, HISTORY_CAP);
            persistHistory(next);
            return next;
          });
          setStatsTick((tick) => tick + 1);
        })
        .catch((err: any) => {
          const msg = String(err?.detail || err?.message || "提问失败");
          setThread((prev) => prev.map((t) => (t.id === id ? { ...t, status: "error", error: msg } : t)));
          setAskError(msg);
        })
        .finally(() => {
          busyRef.current = false;
          setLoading(false);
        });
    },
    [apiToken],
  );

  // 动作按钮直跳 cockpit 路由(旧页同款:route = nav key,委托父级 onNavigate)
  const onAction = React.useCallback(
    (action: IntelligentAction) => {
      if (onNavigate && action.route) onNavigate(action.route);
    },
    [onNavigate],
  );

  // 检索候选身份跳:kol_pool_id → KOL 档案页(sessionStorage + 既有事件管道,金样板同口径)
  const jumpKol = React.useCallback(
    (kolPoolId: number) => {
      try {
        window.sessionStorage.setItem("vkpi:kol-profile-id", String(kolPoolId));
      } catch {
        /* sessionStorage 不可用忽略,事件管道仍会切页 */
      }
      if (onNavigate) onNavigate("kolProfile");
      window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile"));
    },
    [onNavigate],
  );

  // 历史详情连续翻(留痕全在内存,零分页)
  const gotoHist = (i: number) => {
    if (i < 0 || i >= history.length) return;
    setHistIndex(i);
  };

  // 重新提问(真动作:走 ask 通路按当前库数据重跑)/ 删除本条 / 两步清空(仅本机)
  const reAsk = (q: string) => {
    setHistIndex(null);
    setHistListOpen(false);
    setQuestion(q);
    ask(q);
  };
  const deleteHistAt = (i: number) => {
    setHistory((prev) => {
      const next = prev.filter((_, idx) => idx !== i);
      persistHistory(next);
      return next;
    });
    setHistIndex(null);
  };
  const clearHistory = () => {
    setHistory([]);
    persistHistory([]);
    setHistListOpen(false);
    setHistIndex(null);
  };

  /* ---------- 卡头 props(金样板 cardProps 同构:静态口径 + 动态 extraRows,合并记 ref) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const mergedRowsRef = React.useRef<Record<string, Array<[string, string]>>>({});
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    mergedRowsRef.current[key] = rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows, onOpenSrc: () => setProvKey(key) };
  };

  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后即可提问(全只读,不改任何业务数据)。
    </PendingCard>
  );

  /* ---------- 模块 body ---------- */

  // KPI 带:本机三卡真数(0 也如实 0;无时序端点 → 诚实虚线零环比药丸)+
  // 综合回答服务端真数(vkpi_llm_calls,14 天真按日 sparkline;端点三轨诚实降级)
  const renderKpiBand = () => {
    const todayCount = history.filter((h) => isLocalToday(h.at)).length;
    const libCount = history.filter((h) => hasLibCitation(h.evidence || [])).length;
    const citeRate = history.length > 0 ? Math.round((libCount / history.length) * 100) : null;
    const statsReady = !!stats && String(stats.status) === "ready";
    const extraRows: Array<[string, string]> = [
      ["本机留痕", `${history.length} 条(仅成功回答 · 上限 ${HISTORY_CAP})`],
      ["有引用回答", `${libCount} / ${history.length} 条`],
      ...(statsReady
        ? ([
            ["服务端综合累计", `${Number(stats!.total) || 0} 次${stats!.last_at ? ` · 最近一次 ${stats!.last_at}(UTC)` : ""}`],
          ] as Array<[string, string]>)
        : []),
      ...(statsError ? ([["统计端点", `GET /intelligent/stats → ${statsError}`]] as Array<[string, string]>) : []),
    ];
    return (
      <ModuleCard {...cardProps("kpiI", "问答总览", `${history.length}`, extraRows)}>
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <KpiCard label="会话数" value={history.length.toLocaleString()} unit="条" seriesColor="var(--ds-accent)" />
          <KpiCard label="今日问答" value={todayCount.toLocaleString()} unit="条" seriesColor="var(--ds-info)" />
          {citeRate == null ? (
            <KpiCard label="命中引用率" value="—" pending pendingNote="本机暂无留痕,成功问答后点亮" />
          ) : (
            <KpiCard label="命中引用率" value={citeRate} unit="%" seriesColor="var(--ds-good)" />
          )}
          {!apiToken ? (
            <KpiCard label="综合回答" value="—" pending pendingNote="未登录 / 无 token" />
          ) : statsError ? (
            <KpiCard label="综合回答" value="—" pending pendingNote="统计端点读取失败" />
          ) : !stats ? (
            <KpiCard label="综合回答" value="—" pending pendingNote="统计读取中…" />
          ) : !statsReady ? (
            <KpiCard label="综合回答" value="—" pending pendingNote={String(stats.reason || "服务端暂无留痕")} />
          ) : (
            <KpiCard
              label="综合回答"
              value={(Number(stats.total) || 0).toLocaleString()}
              unit="次"
              seriesColor="var(--ds-accent-2)"
              series={statsSeries(stats.by_day)}
            />
          )}
        </div>
      </ModuleCard>
    );
  };

  // 问答主体:输入 + 对话流(旧页全功能 + 引用来源标注升级;新在前)
  const renderQa = () => {
    const extraRows: Array<[string, string]> = thread.length > 0 ? [["本次会话", `${thread.length} 轮(仅本页在屏,刷新即清;成功回答另入本机留痕)`]] : [];
    return (
      <ModuleCard {...cardProps("qa", "问答", thread.length > 0 ? `${thread.length} 轮` : undefined, extraRows)}>
        {!apiToken ? (
          noTokenCard
        ) : (
          <div>
            <div className="flex items-center gap-2">
              <input
                value={question}
                onChange={(ev) => setQuestion(ev.target.value)}
                onKeyDown={(ev) => {
                  if (ev.key === "Enter" && !loading) ask(question);
                }}
                placeholder="问点什么…(如:为什么最近转化率下降,给点建议)"
                className="min-w-0 flex-1 rounded-xl border border-line bg-card px-3 py-2 text-[12.5px] text-ink outline-none placeholder:text-muted focus:border-accent"
              />
              <button
                type="button"
                onClick={() => ask(question)}
                disabled={loading || !question.trim()}
                className="flex-none rounded-xl border border-accent bg-accent-soft px-4 py-2 text-[12.5px] font-semibold text-accent transition-colors hover:border-accent-hover disabled:cursor-default disabled:border-line disabled:bg-card disabled:text-muted"
              >
                {loading ? "思考中…" : "提问"}
              </button>
            </div>
            {askError ? (
              <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">{askError}</div>
            ) : null}
            {thread.length === 0 ? (
              <EmptyLine text="输入问题或点一条建议开始。" />
            ) : (
              <div className="mt-3 space-y-3">
                {thread.map((entry) => (
                  <div key={entry.id}>
                    <div className="mb-1 flex min-w-0 items-center gap-2">
                      <span className="flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-[8.5px] font-bold text-accent">问</span>
                      <span className="min-w-0 flex-1 truncate text-[12px] text-ink" title={entry.q}>
                        {entry.q}
                      </span>
                    </div>
                    {entry.status === "pending" ? (
                      <div className="rounded-[11px] border border-dashed border-line px-3.5 py-2.5 text-[11.5px] text-muted">思考中…</div>
                    ) : entry.status === "error" ? (
                      <div className="rounded-[11px] border border-crit bg-crit-soft px-3.5 py-2.5 text-[11.5px] text-crit">提问失败:{entry.error}</div>
                    ) : entry.answer ? (
                      <AnswerCard answer={entry.answer} at={entry.at} onOpenEvidence={() => setEvEntry(entry)} onAction={onAction} />
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </ModuleCard>
    );
  };

  // 建议问题:当日异动种 chips(点一条 = 直接提问;来源如实进 SrcChip)
  const renderSugg = () => {
    let body: React.ReactNode;
    if (!apiToken) body = noTokenCard;
    else if (suggError) body = <ErrorCard title="建议读取失败" text={`GET /intelligent/suggestions → ${suggError}`} />;
    else if (suggLoading && suggestions.length === 0) body = <LoadingLine text="建议生成中…" />;
    else if (suggestions.length === 0) body = <EmptyLine text="暂无建议问题。" />;
    else
      body = (
        <div className="flex flex-wrap gap-1.5">
          {suggestions.map((s, i) => (
            <button
              key={i}
              type="button"
              onClick={() => {
                setQuestion(s);
                ask(s);
              }}
              disabled={loading}
              className="rounded-full border border-line bg-card px-2.5 py-1 text-left text-[11px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent disabled:cursor-default disabled:text-muted"
            >
              {s}
            </button>
          ))}
        </div>
      );
    return <ModuleCard {...cardProps("sugg", "建议问题", suggestions.length > 0 ? `${suggestions.length}` : undefined)}>{body}</ModuleCard>;
  };

  // 历史会话:本机留痕 行式 + 卡面 6 条 + 全量弹窗入口(详情连续翻)
  const renderHistory = () => {
    let body: React.ReactNode;
    if (history.length === 0) body = <EmptyLine text="本机暂无留痕 —— 成功回答后自动记录(仅本机)。" />;
    else
      body = (
        <div>
          {history.slice(0, FACE_ROWS).map((entry, i) => (
            <HistoryRowLine key={entry.id} entry={entry} index={i} onOpen={gotoHist} />
          ))}
          {history.length > FACE_ROWS ? (
            <button
              type="button"
              onClick={() => setHistListOpen(true)}
              className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
            >
              ≡ 查看全量 {history.length} 条 · 点单条连续翻
            </button>
          ) : null}
        </div>
      );
    return <ModuleCard {...cardProps("history", "历史会话", history.length > 0 ? `${history.length}` : undefined)}>{body}</ModuleCard>;
  };

  // 服务端持久化营销顾问 + 当前员工私有记忆。两者均按 org/staff 隔离；
  // 个人记忆必须候选→显式确认，外发/写业务/费用动作只能留草稿。
  const renderAdvisor = () => (
    <ModuleCard {...cardProps("advisor", "顾问")}>
      <MarketingAdvisorBody apiToken={apiToken} />
    </ModuleCard>
  );

  const renderMemory = () => (
    <ModuleCard {...cardProps("memory", "记忆")}>
      <AdvisorMemoryBody apiToken={apiToken} />
    </ModuleCard>
  );

  /* ---------- 模块注册表(palette 全量可选;默认简四卡全进默认布局) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiI", label: "问答总览带", description: "会话数 / 今日问答 / 命中引用率(本机)+ 综合回答(服务端 14 天趋势)", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "advisor", label: "AI 营销顾问", description: "持久会话 · KOL/产品/项目/活动/Dealer 直接咨询 · 动作只生成草稿", category: "核心模块", defaultSpan: 8, minSpan: 5, defaultHeight: 13, minHeight: 8, maxHeight: 30, render: renderAdvisor },
    { key: "memory", label: "我的记忆与学习", description: "当前员工私有记忆 · 候选必须显式确认 · 可暂停/恢复", category: "核心模块", defaultSpan: 4, minSpan: 4, defaultHeight: 13, minHeight: 8, maxHeight: 30, render: renderMemory },
    { key: "qa", label: "问答", description: "输入提问 + 对话流 · 回答旁引用真来源可点,无来源如实标", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 7, maxHeight: 30, render: renderQa },
    { key: "sugg", label: "建议问题", description: "当日异动种 chips · 点一条直接提问", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 5, minHeight: 3, maxHeight: 12, render: renderSugg },
    { key: "history", label: "历史会话", description: "本机留痕一行一条 · 点开连续翻 · 可重新提问", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 24, render: renderHistory },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="Intelligent 问答" />;
  }

  const histEntry = histIndex != null ? history[histIndex] : null;

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 药丸徽 + 实时辉光点 + 编辑布局;
          旧页头介绍句按「卡面去术语」收进 qa SrcChip 口径行 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">Intelligent 问答</span>
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            实时
          </span>
          <button
            type="button"
            onClick={() => setEditing((value) => !value)}
            aria-pressed={editing}
            className={`vkpi-layout-edit-button flex items-center gap-1.5 ${editing ? "is-active" : ""}`}
          >
            <PencilLine size={13} />
            <span>{editing ? "完成布局" : "编辑布局"}</span>
          </button>
        </div>
      </div>

      {!apiToken && <div className="mb-3">{noTokenCard}</div>}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 引用来源弹窗(对话流答案卡 chip 点开):证据三轨 + 候选档案跳 */}
      {evEntry && evEntry.answer && (
        <EvidenceModal
          question={evEntry.q}
          evidence={evEntry.answer.evidence || []}
          onOpenKol={jumpKol}
          onClose={() => setEvEntry(null)}
        />
      )}

      {/* 历史会话全量弹窗(本机留痕零分页)+ 两步确认清空 */}
      {histListOpen && (
        <HistoryListModal count={history.length} onClearAll={clearHistory} onClose={() => setHistListOpen(false)}>
          {history.map((entry, i) => (
            <HistoryRowLine key={entry.id} entry={entry} index={i} onOpen={gotoHist} />
          ))}
        </HistoryListModal>
      )}

      {/* 历史单条详情:‹ #n/N › + ↑↓ 连续翻 + 引用来源 + 重新提问/动作直跳/删除本条 */}
      {histEntry && (
        <HistoryDetailModal
          entry={histEntry}
          index={histIndex as number}
          total={history.length}
          onNav={gotoHist}
          onClose={() => setHistIndex(null)}
          onReAsk={reAsk}
          onDelete={() => deleteHistAt(histIndex as number)}
          onAction={onAction}
          onOpenKol={jumpKol}
        />
      )}

      {/* 模块溯源说明弹窗(SrcChip 点开):合并口径行 + 三车道通用链 + 底层样本入口 */}
      {provKey && (
        <IntelligentProvModal
          title={PROV_TITLES[provKey] || provKey}
          caliber={(mergedRowsRef.current[provKey] || srcOf(provKey).rows) as Array<[string, string]>}
          onOpenSamples={
            history.length > 0
              ? () => {
                  setProvKey(null);
                  setHistListOpen(true);
                }
              : undefined
          }
          onClose={() => setProvKey(null)}
        />
      )}
    </div>
  );
}
