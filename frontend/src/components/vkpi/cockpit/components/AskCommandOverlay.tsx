import React from "react";
import { createPortal } from "react-dom";
import { ArrowRight, Briefcase, Calendar, Database, Loader2, Search, Sparkles, Users, X } from "lucide-react";
import { askIntelligent, fetchSuggestions, type IntelligentAnswer } from "../../../../services/vkpi/intelligent-api";
import { globalSearch, type GlobalSearchResult } from "../../../../services/vkpi/globalSearch-api";

interface AskCommandOverlayProps {
  open: boolean;
  onClose: () => void;
  apiToken?: string;
  onNavigate?: (key: string) => void;
}

const EMPTY_SEARCH: GlobalSearchResult = { kols: [], projects: [], events: [] };

export function AskCommandOverlay({ open, onClose, apiToken = "", onNavigate }: AskCommandOverlayProps) {
  const [query, setQuery] = React.useState("");
  const [suggestions, setSuggestions] = React.useState<string[]>([]);
  const [searchResult, setSearchResult] = React.useState<GlobalSearchResult>(EMPTY_SEARCH);
  const [searching, setSearching] = React.useState(false);
  const [answer, setAnswer] = React.useState<IntelligentAnswer | null>(null);
  const [answering, setAnswering] = React.useState(false);
  const [error, setError] = React.useState("");
  const [typedAnswer, setTypedAnswer] = React.useState("");
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setQuery("");
    setSearchResult(EMPTY_SEARCH);
    setAnswer(null);
    setTypedAnswer("");
    setError("");
    window.setTimeout(() => inputRef.current?.focus(), 40);
  }, [open]);

  React.useEffect(() => {
    if (!open || !apiToken) return;
    let cancelled = false;
    void fetchSuggestions(apiToken)
      .then((items) => { if (!cancelled) setSuggestions(items); })
      .catch(() => { if (!cancelled) setSuggestions([]); });
    return () => { cancelled = true; };
  }, [apiToken, open]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, open]);

  React.useEffect(() => {
    if (!open || !query.trim()) {
      setSearchResult(EMPTY_SEARCH);
      setSearching(false);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearching(true);
      void globalSearch(query.trim(), { signal: controller.signal })
        .then((result) => setSearchResult(result))
        .catch(() => { if (!controller.signal.aborted) setSearchResult(EMPTY_SEARCH); })
        .finally(() => { if (!controller.signal.aborted) setSearching(false); });
    }, 260);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [open, query]);

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
    if (!question || !apiToken) return;
    setAnswering(true);
    setAnswer(null);
    setTypedAnswer("");
    setError("");
    void askIntelligent(apiToken, question)
      .then(setAnswer)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "提问失败"))
      .finally(() => setAnswering(false));
  }, [apiToken, query]);

  const openKol = (id: number) => {
    try { window.localStorage.setItem("vkpi:pending-kolpool-open-id", String(id)); } catch { /* ignore */ }
    window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-item", { detail: { kolPoolId: id } }));
    onClose();
  };
  const openProject = (id: string | number) => {
    window.dispatchEvent(new CustomEvent("vkpi:open-project-task", { detail: { projectId: String(id) } }));
    onClose();
  };
  const openEvent = () => {
    onNavigate?.("events");
    onClose();
  };

  if (!open || typeof document === "undefined") return null;
  const resultCount = searchResult.kols.length + searchResult.projects.length + searchResult.events.length;

  return createPortal(
    <div className="vkpi-ask-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="vkpi-ask-dialog" role="dialog" aria-modal="true" aria-label="V-KPI Intelligent 问答与搜索">
        <div className="vkpi-ask-dialog__input-row">
          <Sparkles size={18} />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => { setQuery(event.target.value); setAnswer(null); setTypedAnswer(""); }}
            onKeyDown={(event) => { if (event.key === "Enter" && !answering) ask(); }}
            placeholder="问市场、KOL、项目，或直接搜索"
            aria-label="Intelligent 问答与全局搜索"
          />
          {(searching || answering) ? <Loader2 size={15} className="animate-spin" /> : <kbd>⌘K</kbd>}
          <button type="button" onClick={onClose} aria-label="关闭 Intelligent 问答"><X size={16} /></button>
        </div>

        {!query && suggestions.length > 0 ? (
          <div className="vkpi-ask-dialog__suggestions">
            {suggestions.slice(0, 6).map((suggestion) => (
              <button key={suggestion} type="button" onClick={() => { setQuery(suggestion); ask(suggestion); }}>{suggestion}</button>
            ))}
          </div>
        ) : null}

        {query && resultCount > 0 && !answer ? (
          <div className="vkpi-ask-dialog__results">
            {searchResult.kols.length > 0 ? <div className="vkpi-ask-result-group"><h3><Users size={12} />KOL</h3>{searchResult.kols.slice(0, 4).map((kol) => <button type="button" key={kol.id} onClick={() => openKol(kol.id)}><span>{kol.display_name || kol.handle || `KOL #${kol.id}`}</span><small>{[kol.platform, kol.handle].filter(Boolean).join(" · ")}</small><ArrowRight size={12} /></button>)}</div> : null}
            {searchResult.projects.length > 0 ? <div className="vkpi-ask-result-group"><h3><Briefcase size={12} />项目</h3>{searchResult.projects.slice(0, 4).map((project) => <button type="button" key={String(project.id)} onClick={() => openProject(project.id)}><span>{project.project_name || project.project_uid || `项目 #${project.id}`}</span><small>{project.stage || ""}</small><ArrowRight size={12} /></button>)}</div> : null}
            {searchResult.events.length > 0 ? <div className="vkpi-ask-result-group"><h3><Calendar size={12} />活动</h3>{searchResult.events.slice(0, 4).map((event) => <button type="button" key={String(event.id)} onClick={openEvent}><span>{event.title || `活动 #${event.id}`}</span><small>{event.start_date || ""}</small><ArrowRight size={12} /></button>)}</div> : null}
          </div>
        ) : null}

        {query && !answer && !answering ? (
          <button type="button" className="vkpi-ask-dialog__ask" onClick={() => ask()} disabled={!apiToken}>
            <Sparkles size={14} />让 V-KPI 分析这个问题<ArrowRight size={14} />
          </button>
        ) : null}

        {answering ? <div className="vkpi-ask-dialog__thinking"><span /><strong>正在读取内部数据并组织答案</strong></div> : null}
        {error ? <div className="vkpi-ask-dialog__error">{error}</div> : null}

        {answer ? (
          <div className="vkpi-ask-dialog__answer">
            <div className="vkpi-ask-dialog__answer-meta"><span><Database size={11} />基于内部真实数据</span><small>{answer.mode}</small></div>
            <p>{typedAnswer}<i className={typedAnswer.length < String(answer.answer || "").length ? "is-typing" : ""} /></p>
            {answer.actions.length > 0 ? <div className="vkpi-ask-dialog__actions">{answer.actions.map((action, index) => <button type="button" key={`${action.route}-${index}`} onClick={() => { if (action.route) onNavigate?.(action.route); onClose(); }}>{action.label}<ArrowRight size={12} /></button>)}</div> : null}
            {answer.evidence.length > 0 ? <details><summary>查看证据（{answer.evidence.length}）</summary><pre>{JSON.stringify(answer.evidence, null, 2)}</pre></details> : null}
          </div>
        ) : null}
      </section>
    </div>,
    document.body,
  );
}

