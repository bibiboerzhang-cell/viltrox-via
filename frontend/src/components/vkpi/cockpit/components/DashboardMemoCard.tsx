import React from "react";
import { Check, NotebookPen, Plus, Trash2 } from "lucide-react";
import {
  DASHBOARD_MEMO_PREFERENCE,
  loadDashboardPreference,
  saveDashboardPreference,
} from "../dashboardPreferenceStore";

const STORAGE_KEY = "vkpi-dashboard-memo-v1";

export interface MemoItem {
  id: string;
  text: string;
  done: boolean;
}

export interface MemoState {
  title: string;
  note: string;
  items: MemoItem[];
}

const DEFAULT_MEMO: MemoState = {
  title: "",
  note: "",
  items: [],
};

function normalizeMemo(value: unknown): MemoState {
  if (!value || typeof value !== "object") return DEFAULT_MEMO;
  const parsed = value as Partial<MemoState>;
  return {
    title: String(parsed.title || ""),
    note: String(parsed.note || ""),
    items: Array.isArray(parsed.items)
      ? parsed.items.map((item: any, index: number) => ({
          id: String(item.id || `memo-${index}`),
          text: String(item.text || ""),
          done: Boolean(item.done),
        }))
      : [],
  };
}

function loadMemo(): MemoState {
  if (typeof window === "undefined") return DEFAULT_MEMO;
  try {
    return normalizeMemo(JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null"));
  } catch {
    return DEFAULT_MEMO;
  }
}

export function DashboardMemoCard({ apiToken = "" }: { apiToken?: string }) {
  const [memo, setMemo] = React.useState<MemoState>(loadMemo);
  const [syncState, setSyncState] = React.useState<"local" | "loading" | "saving" | "saved" | "error">(apiToken ? "loading" : "local");
  const memoRef = React.useRef(memo);

  React.useEffect(() => {
    memoRef.current = memo;
  }, [memo]);

  React.useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(memo));
    } catch {
      // The memo remains editable when browser storage is unavailable.
    }
  }, [memo]);

  React.useEffect(() => {
    if (!apiToken) {
      setSyncState("local");
      return;
    }
    let alive = true;
    setSyncState("loading");
    loadDashboardPreference<MemoState>(apiToken, DASHBOARD_MEMO_PREFERENCE)
      .then((remoteMemo) => {
        if (!alive) return;
        if (remoteMemo) {
          const next = normalizeMemo(remoteMemo);
          memoRef.current = next;
          setMemo(next);
        }
        setSyncState("saved");
      })
      .catch(() => {
        if (alive) setSyncState("error");
      });
    return () => { alive = false; };
  }, [apiToken]);

  const persistMemo = React.useCallback((next: MemoState) => {
    if (!apiToken) return;
    setSyncState("saving");
    saveDashboardPreference(apiToken, DASHBOARD_MEMO_PREFERENCE, next)
      .then(() => setSyncState("saved"))
      .catch(() => setSyncState("error"));
  }, [apiToken]);

  const commitMemo = React.useCallback((updater: (value: MemoState) => MemoState) => {
    const next = updater(memoRef.current);
    memoRef.current = next;
    setMemo(next);
    persistMemo(next);
  }, [persistMemo]);

  const updateItem = (id: string, patch: Partial<MemoItem>) => {
    commitMemo((value) => ({
      ...value,
      items: value.items.map((item) => item.id === id ? { ...item, ...patch } : item),
    }));
  };

  const addItem = () => {
    commitMemo((value) => ({
      ...value,
      items: [...value.items, { id: `memo-${Date.now()}`, text: "", done: false }],
    }));
  };

  return (
    <article className="vkpi-dashboard-memo">
      <header>
        <div>
          <h3><NotebookPen size={14} />备忘录 · 我的计划</h3>
          <span className={`is-${syncState}`}>
            {syncState === "loading"
              ? "读取账户备忘"
              : syncState === "saving"
                ? "正在保存"
                : syncState === "saved"
                  ? "账户偏好已保存"
                  : syncState === "error"
                    ? "后端不可用，已保存本机"
                    : "本机自动保存"}
          </span>
        </div>
      </header>
      <input
        className="vkpi-dashboard-memo__title"
        value={memo.title}
        onChange={(event) => commitMemo((value) => ({ ...value, title: event.target.value }))}
        placeholder="计划标题"
        aria-label="备忘录标题"
      />
      <textarea
        className="vkpi-dashboard-memo__note"
        value={memo.note}
        onChange={(event) => commitMemo((value) => ({ ...value, note: event.target.value }))}
        placeholder="写点什么"
        aria-label="备忘录正文"
        rows={3}
      />
      <div className="vkpi-dashboard-memo__items">
        {memo.items.map((item) => (
          <div className={`vkpi-dashboard-memo__item ${item.done ? "is-done" : ""}`} key={item.id}>
            <button type="button" onClick={() => updateItem(item.id, { done: !item.done })} aria-label={item.done ? "标记未完成" : "标记完成"}>
              {item.done ? <Check size={12} /> : null}
            </button>
            <input
              value={item.text}
              onChange={(event) => updateItem(item.id, { text: event.target.value })}
              placeholder="待办事项"
              aria-label="待办事项"
            />
            <button
              type="button"
              className="is-delete"
              onClick={() => commitMemo((value) => ({ ...value, items: value.items.filter((row) => row.id !== item.id) }))}
              aria-label="删除待办"
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>
      <button type="button" className="vkpi-dashboard-memo__add" onClick={addItem}>
        <Plus size={13} />添加待办
      </button>
    </article>
  );
}
