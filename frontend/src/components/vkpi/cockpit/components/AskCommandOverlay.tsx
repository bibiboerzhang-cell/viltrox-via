import React from "react";
import { createPortal } from "react-dom";
import {
  AlertCircle,
  ArrowRight,
  BookOpenCheck,
  Briefcase,
  Calendar,
  CheckCircle2,
  Clock3,
  Database,
  HelpCircle,
  Loader2,
  Search,
  ShieldAlert,
  Sparkles,
  Users,
  X,
} from "lucide-react";
import {
  queryIntelligent,
  type IntelligentAction,
  type IntelligentEvidence,
  type IntelligentFact,
  type IntelligentQueryAnswer,
} from "../../../../services/vkpi/intelligent-api";
import { kolHumanDisplayName, kolHumanPublicHandle } from "../lib/kolIdentity";
import {
  globalSearch,
  type GlobalSearchEvent,
  type GlobalSearchKol,
  type GlobalSearchProject,
  type GlobalSearchResult,
} from "../../../../services/vkpi/globalSearch-api";
import { useT } from "../lib/i18n";

interface AskCommandOverlayProps {
  open: boolean;
  onClose: () => void;
  apiToken?: string;
  onNavigate?: (key: string) => void;
}

type SearchStatus = "idle" | "loading" | "ready" | "partial" | "empty" | "forbidden" | "error";
type FailureKind = "permission" | "network" | "service";
type SearchEntry =
  | { key: string; kind: "kol"; item: GlobalSearchKol }
  | { key: string; kind: "project"; item: GlobalSearchProject }
  | { key: string; kind: "event"; item: GlobalSearchEvent };

const EMPTY_SEARCH: GlobalSearchResult = { kols: [], projects: [], events: [] };

const SUPPORTED_SUGGESTIONS = {
  "zh-CN": [
    "目前 KOL 数量是多少？",
    "多少 KOL 做过 26mm EVO 视频？",
    "搜索 26mm EVO 项目",
    "总结本周市场对于 Viltrox 的评价",
  ],
  "en-US": [
    "How many KOLs are in the pool?",
    "How many KOLs reviewed 26mm EVO?",
    "Find 26mm EVO projects",
    "Summarize this week's market feedback on Viltrox",
  ],
} as const;

function failureKind(error: unknown): FailureKind {
  const status = Number((error as { status?: unknown } | null)?.status);
  if (status === 401 || status === 403) return "permission";
  const message = String((error as { message?: unknown } | null)?.message || "").toLowerCase();
  if (/network|fetch|网络|offline/.test(message)) return "network";
  return "service";
}

function resolvedSearchStatus(result: GlobalSearchResult, resultCount: number): SearchStatus {
  const states = Object.values(result.source_status || {}).filter(Boolean).map((item) => item!.status);
  if (states.length === 0) return resultCount > 0 ? "ready" : "empty";
  if (states.every((status) => status === "blocked")) return "forbidden";
  if (states.every((status) => status === "error" || status === "blocked")) return "error";
  if (states.some((status) => status !== "ready")) return "partial";
  return resultCount > 0 ? "ready" : "empty";
}

function hasFactValue(fact: IntelligentFact): boolean {
  if (fact.value === null || fact.value === undefined || fact.value === "") return false;
  return !Array.isArray(fact.value) || fact.value.length > 0;
}

function formatFactValue(fact: IntelligentFact, locale: "zh-CN" | "en-US"): string {
  const raw = fact.value;
  if (Array.isArray(raw)) return raw.join(locale === "en-US" ? ", " : "、");
  if (typeof raw === "number") {
    const value = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(raw);
    return fact.unit ? `${value} ${fact.unit}` : value;
  }
  return `${String(raw ?? "")}${fact.unit ? ` ${fact.unit}` : ""}`;
}

function safeEvidenceUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value, window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function intentLabel(intent: string, t: (text: string) => string): string {
  const labels: Record<string, string> = {
    "kol.pool.overview": "KOL数量",
    "kol.video_topic.count": "KOL视频主题",
    "project.search": "项目搜索",
    "market.viltrox.weekly_voice": "本周市场评价",
    unknown: "待澄清问题",
  };
  return t(labels[intent] || "智能查询");
}

export function AskCommandOverlay({ open, onClose, apiToken = "", onNavigate }: AskCommandOverlayProps) {
  const { t, lang } = useT();
  const locale = lang === "en" ? "en-US" : "zh-CN";
  const [query, setQuery] = React.useState("");
  // Only show prompts supported by the deterministic v2 router.  The legacy
  // Intelligence page keeps its broader suggestion feed, but those prompts
  // must not lead this command surface straight into clarification errors.
  const suggestions = React.useMemo<string[]>(
    () => [...SUPPORTED_SUGGESTIONS[locale]],
    [locale],
  );
  const [searchResult, setSearchResult] = React.useState<GlobalSearchResult>(EMPTY_SEARCH);
  const [searchStatus, setSearchStatus] = React.useState<SearchStatus>("idle");
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [answer, setAnswer] = React.useState<IntelligentQueryAnswer | null>(null);
  const [answering, setAnswering] = React.useState(false);
  const [failure, setFailure] = React.useState<{ kind: FailureKind; message: string } | null>(null);
  const [typedAnswer, setTypedAnswer] = React.useState("");
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const answerAbortRef = React.useRef<AbortController | null>(null);
  const answerGenerationRef = React.useRef(0);
  const searchGenerationRef = React.useRef(0);

  const entries = React.useMemo<SearchEntry[]>(() => [
    ...searchResult.kols.slice(0, 4).map((item) => ({ key: `kol-${item.id}`, kind: "kol" as const, item })),
    ...searchResult.projects.slice(0, 4).map((item) => ({ key: `project-${item.id}`, kind: "project" as const, item })),
    ...searchResult.events.slice(0, 4).map((item) => ({ key: `event-${item.id}`, kind: "event" as const, item })),
  ], [searchResult]);

  const cancelAnswer = React.useCallback(() => {
    answerGenerationRef.current += 1;
    answerAbortRef.current?.abort();
    answerAbortRef.current = null;
    setAnswering(false);
  }, []);

  React.useEffect(() => {
    if (!open) {
      cancelAnswer();
      return;
    }
    setQuery("");
    setSearchResult(EMPTY_SEARCH);
    setSearchStatus("idle");
    setActiveIndex(0);
    setAnswer(null);
    setTypedAnswer("");
    setFailure(null);
    window.setTimeout(() => inputRef.current?.focus(), 40);
  }, [cancelAnswer, open]);

  React.useEffect(() => () => answerAbortRef.current?.abort(), []);

  React.useEffect(() => {
    cancelAnswer();
    setAnswer(null);
    setTypedAnswer("");
    setFailure(null);
  }, [apiToken, cancelAnswer]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, open]);

  React.useEffect(() => {
    const generation = ++searchGenerationRef.current;
    if (!open || !query.trim()) {
      setSearchResult(EMPTY_SEARCH);
      setSearchStatus("idle");
      setActiveIndex(0);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearchStatus("loading");
      void globalSearch(query.trim(), { token: apiToken, signal: controller.signal })
        .then((result) => {
          if (controller.signal.aborted || generation !== searchGenerationRef.current) return;
          const count = result.kols.length + result.projects.length + result.events.length;
          setSearchResult(result);
          setSearchStatus(resolvedSearchStatus(result, count));
          setActiveIndex(0);
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted || generation !== searchGenerationRef.current) return;
          setSearchResult(EMPTY_SEARCH);
          setSearchStatus(failureKind(error) === "permission" ? "forbidden" : "error");
        });
    }, 220);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [apiToken, open, query]);

  React.useEffect(() => {
    const text = String(answer?.answer || "");
    if (!text) {
      setTypedAnswer("");
      return;
    }
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setTypedAnswer(text);
      return;
    }
    let index = 0;
    const timer = window.setInterval(() => {
      index = Math.min(text.length, index + 3);
      setTypedAnswer(text.slice(0, index));
      if (index >= text.length) window.clearInterval(timer);
    }, 12);
    return () => window.clearInterval(timer);
  }, [answer]);

  const ask = React.useCallback((value = query) => {
    const question = value.trim();
    if (!question) return;
    if (!apiToken) {
      setFailure({ kind: "permission", message: t("请先登录后再使用智能问答") });
      return;
    }
    cancelAnswer();
    const generation = ++answerGenerationRef.current;
    const controller = new AbortController();
    answerAbortRef.current = controller;
    setAnswering(true);
    setAnswer(null);
    setTypedAnswer("");
    setFailure(null);
    void queryIntelligent(apiToken, question, {
      signal: controller.signal,
      locale,
      threadId: "ask-find-topbar",
    })
      .then((result) => {
        if (controller.signal.aborted || generation !== answerGenerationRef.current) return;
        setAnswer(result);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || generation !== answerGenerationRef.current) return;
        const kind = failureKind(error);
        const fallback = kind === "permission"
          ? t("你没有查看这部分数据的权限")
          : kind === "network"
            ? t("网络连接中断，请检查连接后重试")
            : t("查询服务暂时不可用，请稍后重试");
        // Do not surface raw provider/database messages in the command UI.
        // The correlation ID stays in successful response trace; transport
        // failures get a stable user-facing state and can be retried safely.
        setFailure({ kind, message: fallback });
      })
      .finally(() => {
        if (generation !== answerGenerationRef.current) return;
        answerAbortRef.current = null;
        setAnswering(false);
      });
  }, [apiToken, cancelAnswer, locale, query, t]);

  const openKol = React.useCallback((id: number) => {
    try { window.localStorage.setItem("vkpi:pending-kolpool-open-id", String(id)); } catch { /* ignore */ }
    window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-item", { detail: { kolPoolId: id } }));
    onClose();
  }, [onClose]);

  const openProject = React.useCallback((id: string | number) => {
    window.dispatchEvent(new CustomEvent("vkpi:open-project-task", { detail: { projectId: String(id) } }));
    onClose();
  }, [onClose]);

  const openEvent = React.useCallback(() => {
    onNavigate?.("events");
    onClose();
  }, [onClose, onNavigate]);

  const openEntry = React.useCallback((entry: SearchEntry) => {
    if (entry.kind === "kol") openKol(entry.item.id);
    else if (entry.kind === "project") openProject(entry.item.id);
    else openEvent();
  }, [openEvent, openKol, openProject]);

  const onQueryChange = (value: string) => {
    cancelAnswer();
    // Invalidate both the visible candidates and any transport that happens to
    // resolve before the effect cleanup runs. A new query must never open a
    // stale entity from the previous query.
    searchGenerationRef.current += 1;
    setQuery(value);
    setSearchResult(EMPTY_SEARCH);
    setSearchStatus(value.trim() ? "loading" : "idle");
    setAnswer(null);
    setTypedAnswer("");
    setFailure(null);
    setActiveIndex(0);
  };

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      if (!answering) ask();
      return;
    }
    if (event.key === "ArrowDown" && entries.length > 0) {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % entries.length);
      return;
    }
    if (event.key === "ArrowUp" && entries.length > 0) {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + entries.length) % entries.length);
      return;
    }
    if (event.key === "Enter" && entries[activeIndex]) {
      event.preventDefault();
      openEntry(entries[activeIndex]);
    }
  };

  const runAction = (action: IntelligentAction) => {
    // Approval-required actions are proposals only. The command palette never
    // turns a proposal into a mutation (or even an execution attempt).
    if (action.requires_approval) return;
    const suggestedQuery = action.type === "suggest_query" && typeof action.params?.query === "string"
      ? action.params.query.trim()
      : "";
    if (suggestedQuery) {
      onQueryChange(suggestedQuery);
      ask(suggestedQuery);
      return;
    }
    if (action.type === "navigate" && action.route) {
      const pendingKolQuery = action.route === "kol-pool" && typeof action.params?.query === "string"
        ? action.params.query.trim()
        : "";
      if (pendingKolQuery) {
        try { window.localStorage.setItem("vkpi:pending-kolpool-search", pendingKolQuery); } catch { /* ignore */ }
        window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-search", { detail: { query: pendingKolQuery } }));
      }
      onNavigate?.(action.route);
      onClose();
    }
  };

  if (!open || typeof document === "undefined") return null;

  const visibleFacts = (answer?.facts || []).filter(hasFactValue);
  const coverage = answer?.coverage;
  const querySourceStates = Object.values(answer?.trace.source_status || {}).map((item) => item.status);
  const hasUnreadyQuerySource = querySourceStates.some((status) => !["ready", "ok"].includes(status));
  const trustworthyZeroCoverage = Boolean(coverage
    && ["complete", "empty"].includes(coverage.status)
    && !hasUnreadyQuerySource);
  const coverageSummary = coverage ? [
    coverage.matched_entities !== undefined
      && (coverage.matched_entities > 0 || trustworthyZeroCoverage)
      ? `${coverage.matched_entities.toLocaleString(locale)} ${t("个匹配")}`
      : "",
    coverage.evidence_count !== undefined
      && (coverage.evidence_count > 0 || trustworthyZeroCoverage)
      ? `${coverage.evidence_count.toLocaleString(locale)} ${t("条证据")}`
      : "",
  ].filter(Boolean) : [];
  const showCoverage = coverageSummary.length > 0;
  const freshness = answer?.freshness;
  const freshnessText = freshness?.data_updated_at || freshness?.generated_at || "";
  const answerState = answer?.status === "ready" && answer.degraded_reason
    ? "degraded"
    : answer?.status || "ready";
  const answerStateLabel: Record<string, string> = {
    ready: t("已完成"),
    partial: t("结果不完整"),
    degraded: t("部分数据源不可用"),
    empty: t("没有匹配数据"),
    needs_clarification: t("需要补充条件"),
    error: t("查询未完成"),
    blocked: t("数据访问受限"),
    unavailable: t("数据暂不可用"),
  };
  const degradedNotice = answerState === "blocked"
    ? t("查询被权限范围阻止，未读取受限数据。")
    : answerState === "error" || answerState === "unavailable"
      ? t("查询暂时不可用，本次没有把故障当成零结果。")
      : t("部分数据源暂不可用，以下内容可能不完整。");

  const renderResultButton = (entry: SearchEntry, index: number) => {
    const selected = index === activeIndex;
    let label = "";
    let detail = "";
    let Icon = Search;
    if (entry.kind === "kol") {
      label = kolHumanDisplayName(entry.item as unknown as Record<string, unknown>);
      detail = [entry.item.platform, kolHumanPublicHandle(entry.item as unknown as Record<string, unknown>)].filter(Boolean).join(" · ");
      Icon = Users;
    } else if (entry.kind === "project") {
      label = entry.item.project_name || entry.item.project_uid || `${t("项目")} #${entry.item.id}`;
      detail = entry.item.stage || "";
      Icon = Briefcase;
    } else {
      label = entry.item.title || `${t("活动")} #${entry.item.id}`;
      detail = entry.item.start_date || "";
      Icon = Calendar;
    }
    return (
      <button
        id={`vkpi-ask-result-${index}`}
        type="button"
        role="option"
        key={entry.key}
        className={selected ? "is-active" : ""}
        aria-selected={selected}
        onMouseEnter={() => setActiveIndex(index)}
        onClick={() => openEntry(entry)}
      >
        <Icon size={13} />
        <span>{label}</span>
        <small>{detail}</small>
        <ArrowRight size={12} />
      </button>
    );
  };

  return createPortal(
    <div className="vkpi-ask-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="vkpi-ask-dialog" role="dialog" aria-modal="true" aria-label={t("V-KPI 智能问答与搜索")}>
        <div className="vkpi-ask-dialog__input-row">
          <Sparkles size={18} aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder={t("问市场、KOL、项目，或直接搜索")}
            aria-label={t("智能问答与全局搜索")}
            aria-activedescendant={entries[activeIndex] ? `vkpi-ask-result-${activeIndex}` : undefined}
            aria-controls="vkpi-ask-result-list"
            aria-expanded={entries.length > 0}
            aria-autocomplete="list"
            role="combobox"
          />
          {(searchStatus === "loading" || answering) ? <Loader2 size={15} className="animate-spin" aria-label={t("正在查询")} /> : <kbd>⌘K</kbd>}
          <button type="button" onClick={onClose} aria-label={t("关闭智能问答")}><X size={16} /></button>
        </div>

        {!query && suggestions.length > 0 ? (
          <div className="vkpi-ask-dialog__suggestions" aria-label={t("建议问题")}>
            {suggestions.slice(0, 6).map((suggestion) => (
              <button key={suggestion} type="button" onClick={() => { onQueryChange(suggestion); ask(suggestion); }}>{suggestion}</button>
            ))}
          </div>
        ) : null}

        {query && entries.length > 0 && !answer ? (
          <div id="vkpi-ask-result-list" className="vkpi-ask-dialog__results" role="listbox" aria-label={t("搜索结果")}>
            <div className="vkpi-ask-result-group">
              <h3><Search size={12} />{t("即时搜索")}</h3>
              {entries.map(renderResultButton)}
            </div>
          </div>
        ) : null}

        {query && searchStatus === "empty" && !answer && !answering ? (
          <div className="vkpi-ask-dialog__state is-empty"><Search size={16} /><strong>{t("没有找到匹配的KOL、项目或活动")}</strong><span>{t("仍可执行智能问答，系统会按可见数据范围回答")}</span></div>
        ) : null}
        {query && searchStatus === "forbidden" && !answer ? (
          <div className="vkpi-ask-dialog__state is-warning"><ShieldAlert size={16} /><strong>{t("没有搜索权限")}</strong><span>{t("请联系管理员确认你的数据访问范围")}</span></div>
        ) : null}
        {query && searchStatus === "partial" && !answer ? (
          <div className="vkpi-ask-dialog__state is-warning"><AlertCircle size={16} /><strong>{t("部分搜索来源暂不可用")}</strong><span>{t("当前候选可能不完整，这不是完整的零结果")}</span></div>
        ) : null}
        {query && searchStatus === "error" && !answer ? (
          <div className="vkpi-ask-dialog__state is-error"><AlertCircle size={16} /><strong>{t("即时搜索暂时不可用")}</strong><span>{t("这不是零结果，你仍可稍后重试")}</span></div>
        ) : null}

        {query && !answer && !answering ? (
          <>
            <button type="button" className="vkpi-ask-dialog__ask" onClick={() => ask()} disabled={!apiToken}>
              <Sparkles size={14} />{t("让 V-KPI 回答这个问题")}<ArrowRight size={14} />
            </button>
            <div className="vkpi-ask-dialog__key-hint"><span>↑↓ {t("选择")}</span><span>Enter {t("打开")}</span><span>⌘/Ctrl+Enter {t("问答")}</span></div>
          </>
        ) : null}

        {answering ? <div className="vkpi-ask-dialog__thinking"><span /><strong>{t("正在读取可见数据并核对证据")}</strong></div> : null}

        {failure ? (
          <div className={`vkpi-ask-dialog__failure is-${failure.kind}`} role="alert">
            {failure.kind === "permission" ? <ShieldAlert size={17} /> : <AlertCircle size={17} />}
            <div><strong>{failure.kind === "permission" ? t("权限不足") : failure.kind === "network" ? t("连接中断") : t("查询失败")}</strong><span>{failure.message}</span></div>
            {failure.kind !== "permission" ? <button type="button" onClick={() => ask()}>{t("重试")}</button> : null}
          </div>
        ) : null}

        {answer ? (
          <div className={`vkpi-ask-dialog__answer is-${answerState}`}>
            <div className="vkpi-ask-dialog__answer-meta">
              <span>{answerState === "ready" ? <CheckCircle2 size={12} /> : answerState === "needs_clarification" ? <HelpCircle size={12} /> : <AlertCircle size={12} />}{answerStateLabel[answerState] || answerStateLabel.error}</span>
              <small title={answer.request_id}>{intentLabel(answer.intent, t)} · {Math.max(0, answer.trace.took_ms)}ms</small>
            </div>
            {answer.degraded_reason ? <div className="vkpi-ask-dialog__degraded">{degradedNotice}</div> : null}
            <p>{typedAnswer}<i className={typedAnswer.length < String(answer.answer || "").length ? "is-typing" : ""} /></p>

            {visibleFacts.length > 0 ? (
              <div className="vkpi-ask-dialog__facts" aria-label={t("关键数据")}>
                {visibleFacts.map((fact) => (
                  <article key={fact.key}>
                    <span>{fact.label}</span>
                    <strong>{formatFactValue(fact, locale)}</strong>
                    {fact.basis ? <small title={fact.basis}>{t("计算口径已记录")}</small> : null}
                    <em className={`is-${fact.confidence}`}>{t(fact.confidence === "high" ? "高可信" : fact.confidence === "medium" ? "中等可信" : "低可信")}</em>
                  </article>
                ))}
              </div>
            ) : null}

            {showCoverage || freshnessText ? (
              <div className="vkpi-ask-dialog__quality">
                {showCoverage && coverage ? (
                  <div><BookOpenCheck size={14} /><span>{t("数据覆盖")}</span><strong>{coverageSummary.join(" · ")}</strong>{coverage.ratio !== undefined ? <small>{Math.round(Math.max(0, Math.min(100, coverage.ratio <= 1 ? coverage.ratio * 100 : coverage.ratio)))}%</small> : null}</div>
                ) : null}
                {freshnessText ? <div><Clock3 size={14} /><span>{t("数据时间")}</span><strong>{freshnessText}</strong><small>{freshness?.status === "stale" ? t("可能已过期") : t("已标注时间")}</small></div> : null}
              </div>
            ) : null}

            {answer.missing_fields.length > 0 ? (
              <section className="vkpi-ask-dialog__missing" aria-label={t("数据缺口")}>
                <h3><AlertCircle size={13} />{t("数据缺口")}</h3>
                {answer.missing_fields.map((item) => <div key={item.field}><strong>{item.field}</strong><span>{item.reason}</span>{item.impact ? <small>{item.impact}</small> : null}</div>)}
              </section>
            ) : null}

            {answer.evidence.length > 0 ? <EvidenceList evidence={answer.evidence} t={t} /> : null}

            {answer.actions.length > 0 ? (
              <div className="vkpi-ask-dialog__actions">
                {answer.actions.map((action, index) => {
                  const key = `${action.type || action.route || "action"}-${index}`;
                  if (action.requires_approval) {
                    return <div className="vkpi-ask-dialog__proposal" key={key}><span>{action.label}</span><small>{t("待人工审批的提案")}</small></div>;
                  }
                  const direct = action.type === "navigate" && Boolean(action.route);
                  const suggestion = action.type === "suggest_query" && typeof action.params?.query === "string";
                  return (
                    <button type="button" key={key} onClick={() => runAction(action)} disabled={!direct && !suggestion}>
                      {action.label}<ArrowRight size={12} />
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>,
    document.body,
  );
}

function EvidenceList({ evidence, t }: { evidence: IntelligentEvidence[]; t: (text: string) => string }) {
  return (
    <details className="vkpi-ask-dialog__evidence">
      <summary><Database size={12} />{t("来源与证据")}（{evidence.length}）</summary>
      <div>
        {evidence.map((item, index) => {
          const href = safeEvidenceUrl(item.url);
          const title = item.title || item.source || item.kind || `${t("证据")} ${index + 1}`;
          return (
            <article key={item.id || `${item.kind}-${index}`}>
              <header><strong>{title}</strong>{item.confidence ? <small>{t(item.confidence === "high" ? "高可信" : item.confidence === "medium" ? "中等可信" : "低可信")}</small> : null}</header>
              {item.snippet ? <p>{item.snippet}</p> : null}
              <footer>{item.source ? <span>{t("来源")}：{item.source}</span> : null}{item.observed_at ? <time>{item.observed_at}</time> : null}{href ? <a href={href} target="_blank" rel="noreferrer">{t("打开来源")}</a> : null}</footer>
            </article>
          );
        })}
      </div>
    </details>
  );
}
