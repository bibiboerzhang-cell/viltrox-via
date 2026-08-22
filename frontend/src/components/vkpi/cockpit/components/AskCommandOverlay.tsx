// 顶栏 Ask ⌘K 壳(2026-08-22 P1「命令面板 + 实体直达」):输入行 + 键盘流 + 问答请求。
// 候选引擎 ask/useAskCandidates · 语法 ask/askGrammar · 动作 ask/AskActions;
// 候选列表与答案卡懒加载(ask/AskResultList · ask/AskAnswerCard),防 chunk 红线。
// 键盘:Tab 在「候选 | 问 AI | 答案」三区循环;↑↓ 行内;Enter 主动作;⌘Enter 问 AI;Esc 逐层回退。

import React from "react";
import { createPortal } from "react-dom";
import { AlertCircle, ArrowRight, Loader2, ShieldAlert, Sparkles, X } from "lucide-react";
import { queryIntelligent, type IntelligentAction, type IntelligentQueryAnswer } from "../../../../services/vkpi/intelligent-api";
import { NAV_ITEMS } from "../data/navItems";
import { useT } from "../lib/i18n";
import { isRunnableIntelligentAction, runAskCandidate, runIntelligentAction } from "./ask/AskActions";
import { ASK_PREFIX_HINTS, type AskCandidate } from "./ask/askGrammar";
import { useAskCandidates } from "./ask/useAskCandidates";

const AskResultList = React.lazy(() => import("./ask/AskResultList"));
const AskAnswerCard = React.lazy(() => import("./ask/AskAnswerCard"));

interface AskCommandOverlayProps {
  open: boolean;
  onClose: () => void;
  apiToken?: string;
  onNavigate?: (key: string) => void;
}

type FailureKind = "permission" | "network" | "service";
type Zone = "candidates" | "askcard" | "answer";

function failureKind(error: unknown): FailureKind {
  const status = Number((error as { status?: unknown } | null)?.status);
  if (status === 401 || status === 403) return "permission";
  const message = String((error as { message?: unknown } | null)?.message || "").toLowerCase();
  if (/network|fetch|网络|offline/.test(message)) return "network";
  return "service";
}

export function AskCommandOverlay({ open, onClose, apiToken = "", onNavigate }: AskCommandOverlayProps) {
  const { t, lang } = useT();
  const locale = lang === "en" ? "en-US" : "zh-CN";
  const [query, setQuery] = React.useState("");
  const [zone, setZone] = React.useState<Zone>("candidates");
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [activeActionIndex, setActiveActionIndex] = React.useState(0);
  const [answer, setAnswer] = React.useState<IntelligentQueryAnswer | null>(null);
  const [answering, setAnswering] = React.useState(false);
  const [failure, setFailure] = React.useState<{ kind: FailureKind; message: string } | null>(null);
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const answerAbortRef = React.useRef<AbortController | null>(null);
  const answerGenerationRef = React.useRef(0);

  const candidates = useAskCandidates({ open, query, apiToken, lang, navItems: NAV_ITEMS, t });
  const { flat, parsed } = candidates;
  const runnableActions = React.useMemo(() => (answer?.actions || []).filter(isRunnableIntelligentAction), [answer]);

  const cancelAnswer = React.useCallback(() => {
    answerGenerationRef.current += 1;
    answerAbortRef.current?.abort();
    answerAbortRef.current = null;
    setAnswering(false);
  }, []);

  const resetAnswer = React.useCallback(() => {
    cancelAnswer();
    setAnswer(null);
    setFailure(null);
    setActiveActionIndex(0);
  }, [cancelAnswer]);

  React.useEffect(() => {
    if (!open) { cancelAnswer(); return; }
    setQuery("");
    setZone("candidates");
    setActiveIndex(0);
    setActiveActionIndex(0);
    setAnswer(null);
    setFailure(null);
    window.setTimeout(() => inputRef.current?.focus(), 40);
  }, [cancelAnswer, open]);

  React.useEffect(() => () => answerAbortRef.current?.abort(), []);
  React.useEffect(() => { resetAnswer(); }, [apiToken, resetAnswer]);
  React.useEffect(() => { setActiveIndex(0); }, [parsed.term, parsed.prefix]);

  const ask = React.useCallback((value = query) => {
    const question = value.trim();
    if (!question) return;
    if (!apiToken) {
      setFailure({ kind: "permission", message: t("请先登录后再使用智能问答") });
      setZone("answer");
      return;
    }
    cancelAnswer();
    const generation = ++answerGenerationRef.current;
    const controller = new AbortController();
    answerAbortRef.current = controller;
    setAnswering(true);
    setAnswer(null);
    setFailure(null);
    setActiveActionIndex(0);
    void queryIntelligent(apiToken, question, { signal: controller.signal, locale, threadId: "ask-find-topbar" })
      .then((result) => {
        if (controller.signal.aborted || generation !== answerGenerationRef.current) return;
        setAnswer(result);
        setZone("answer");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || generation !== answerGenerationRef.current) return;
        const kind = failureKind(error);
        // 不把 provider / 数据库原始错误串透到门面;三类稳定状态可安全重试。
        setFailure({
          kind,
          message: kind === "permission" ? t("你没有查看这部分数据的权限") : kind === "network" ? t("网络连接中断，请检查连接后重试") : t("查询服务暂时不可用，请稍后重试"),
        });
        setZone("answer");
      })
      .finally(() => {
        if (generation !== answerGenerationRef.current) return;
        answerAbortRef.current = null;
        setAnswering(false);
      });
  }, [apiToken, cancelAnswer, locale, query, t]);

  const onQueryChange = React.useCallback((value: string) => {
    // 新输入即作废旧答案与旧候选;候选 generation 由 useAskCandidates 内部递增。
    resetAnswer();
    setQuery(value);
    setZone("candidates");
    setActiveIndex(0);
  }, [resetAnswer]);

  const actionContext = React.useMemo(() => ({ onNavigate, onClose, ask, setQuery: onQueryChange }), [ask, onClose, onNavigate, onQueryChange]);
  const activateCandidate = React.useCallback((candidate: AskCandidate) => runAskCandidate(candidate, actionContext), [actionContext]);
  const runAction = React.useCallback((action: IntelligentAction) => runIntelligentAction(action, actionContext), [actionContext]);

  const hasQuery = Boolean(query.trim());
  const askcardVisible = hasQuery && !parsed.prefix;
  const answerVisible = Boolean(answer || failure);
  const zones = React.useMemo<Zone[]>(() => {
    const list: Zone[] = [];
    if (flat.length > 0 || !hasQuery || !askcardVisible) list.push("candidates");
    if (askcardVisible) list.push("askcard");
    if (answerVisible) list.push("answer");
    return list;
  }, [answerVisible, askcardVisible, flat.length, hasQuery]);
  const effectiveZone: Zone = zones.includes(zone) ? zone : zones[0] || "candidates";

  // Esc 逐层回退:答案 → 清输入 → 关闭。挂在 window 上,焦点落在答案按钮时同样生效。
  React.useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (answer || failure || answering) { resetAnswer(); setZone("candidates"); inputRef.current?.focus(); return; }
      if (query) { onQueryChange(""); return; }
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [answer, answering, failure, onClose, onQueryChange, open, query, resetAnswer]);

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      if (!answering) ask();
      return;
    }
    if (event.key === "Tab") {
      if (zones.length < 2) return;
      event.preventDefault();
      const current = Math.max(0, zones.indexOf(effectiveZone));
      const step = event.shiftKey ? -1 : 1;
      setZone(zones[(current + step + zones.length) % zones.length]);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const step = event.key === "ArrowDown" ? 1 : -1;
      if (effectiveZone === "candidates" && flat.length > 0) {
        event.preventDefault();
        setActiveIndex((index) => (index + step + flat.length) % flat.length);
      } else if (effectiveZone === "answer" && runnableActions.length > 0) {
        event.preventDefault();
        setActiveActionIndex((index) => (index + step + runnableActions.length) % runnableActions.length);
      }
      return;
    }
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (effectiveZone === "askcard") { if (!answering) ask(); return; }
    if (effectiveZone === "answer") { const action = runnableActions[activeActionIndex]; if (action) runAction(action); return; }
    const candidate = flat[activeIndex];
    if (candidate) activateCandidate(candidate);
    else if (hasQuery && !parsed.prefix && !answering) ask();
  };

  if (!open || typeof document === "undefined") return null;

  const prefixHintLabels = { kol: "KOL", project: t("项目 / 活动"), sku: t("SKU / 镜头"), nav: t("板块") } as const;

  const activeDescendant = effectiveZone === "candidates" && flat[activeIndex]
    ? `vkpi-ask-result-${activeIndex}`
    : effectiveZone === "answer" && runnableActions[activeActionIndex]
      ? `vkpi-ask-action-${activeActionIndex}`
      : undefined;

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
            aria-activedescendant={activeDescendant}
            aria-controls="vkpi-ask-result-list"
            aria-expanded={flat.length > 0}
            aria-autocomplete="list"
            role="combobox"
          />
          {(candidates.loading || answering) ? <Loader2 size={15} className="animate-spin" aria-label={t("正在查询")} /> : <kbd>⌘K</kbd>}
          <button type="button" onClick={onClose} aria-label={t("关闭智能问答")}><X size={16} /></button>
        </div>

        {!hasQuery ? (
          <div className="vkpi-ask-dialog__prefix-hints" aria-label={t("前缀语法")}>
            {ASK_PREFIX_HINTS.map(({ prefix, kind }) => (
              <button type="button" key={prefix} onClick={() => { onQueryChange(prefix); inputRef.current?.focus(); }}>
                <kbd>{prefix}</kbd>{prefixHintLabels[kind]}
              </button>
            ))}
          </div>
        ) : null}

        <div className={`vkpi-ask-dialog__body ${answerVisible ? "has-answer" : ""}`}>
          <React.Suspense fallback={<div className="vkpi-ask-dialog__loading">{t("读取中")}</div>}>
            <AskResultList
              groups={candidates.groups}
              activeIndex={activeIndex}
              zoneActive={effectiveZone === "candidates"}
              emptyKind={candidates.emptyKind}
              hasPrefix={Boolean(parsed.prefix)}
              loading={candidates.loading}
              t={t}
              onHover={(index) => { setZone("candidates"); setActiveIndex(index); }}
              onActivate={activateCandidate}
            />
            {answer ? (
              <AskAnswerCard
                answer={answer}
                locale={locale}
                t={t}
                zoneActive={effectiveZone === "answer"}
                activeActionIndex={activeActionIndex}
                onRunAction={runAction}
                onHoverAction={(index) => { setZone("answer"); setActiveActionIndex(index); }}
              />
            ) : null}
          </React.Suspense>
        </div>

        {askcardVisible && !answering ? (
          <button
            type="button"
            className={`vkpi-ask-dialog__ask ${effectiveZone === "askcard" ? "is-zone-active" : ""}`}
            onClick={() => ask()}
            onMouseEnter={() => setZone("askcard")}
            disabled={!apiToken}
          >
            <Sparkles size={14} />{t("让 V-KPI 回答这个问题")}<ArrowRight size={14} />
          </button>
        ) : null}
        {hasQuery && !answering ? (
          <div className="vkpi-ask-dialog__key-hint">
            <span>Tab {t("切区")}</span><span>↑↓ {t("选择")}</span><span>Enter {t("打开")}</span><span>⌘/Ctrl+Enter {t("问答")}</span><span>Esc {t("返回")}</span>
          </div>
        ) : null}

        {answering ? <div className="vkpi-ask-dialog__thinking"><span /><strong>{t("正在读取可见数据并核对证据")}</strong></div> : null}

        {failure ? (
          <div className={`vkpi-ask-dialog__failure is-${failure.kind}`} role="alert">
            {failure.kind === "permission" ? <ShieldAlert size={17} /> : <AlertCircle size={17} />}
            <div><strong>{failure.kind === "permission" ? t("权限不足") : failure.kind === "network" ? t("连接中断") : t("查询失败")}</strong><span>{failure.message}</span></div>
            {failure.kind !== "permission" ? <button type="button" onClick={() => ask()}>{t("重试")}</button> : null}
          </div>
        ) : null}
      </section>
    </div>,
    document.body,
  );
}
