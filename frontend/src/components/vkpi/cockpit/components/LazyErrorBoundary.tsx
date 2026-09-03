// 隔离懒加载页面的崩溃:① 部署后 chunk 哈希变化导致旧浏览器加载失败(白屏)——
// 自动硬刷新一次拉取新资源;② 任何渲染异常——优雅兜底,只挂这一块,不连累整个 app。
// 2026-09-02(U-B1):兜底卡全走 --ds-* token 类(text-ink/text-ink-2/bg-panel/border-line),
// 不再写死 white/slate/purple——写死色在浅色主题下是白字白底,「坏了」反而看不见。
import React from "react";

function isChunkError(err: any) {
  const msg = String((err && err.message) || err || "");
  const name = String((err && err.name) || "");
  return (
    name === "ChunkLoadError" ||
    /Loading chunk|Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|dynamically imported module/i.test(msg)
  );
}

export const ERROR_CARD_CLASS = "rounded-ds-lg border border-line bg-panel shadow-ds p-8 max-w-md w-full";
export const ERROR_BUTTON_SECONDARY_CLASS = "px-4 py-2 rounded-ds border border-line bg-card text-ink-2 text-[12px] font-medium hover:bg-accent-soft hover:text-ink";
export const ERROR_BUTTON_PRIMARY_CLASS = "px-4 py-2 rounded-ds bg-accent text-[color:var(--ds-on-accent)] text-[12px] font-medium hover:bg-accent-hover";

export class LazyErrorBoundary extends React.Component<any, any> {
  constructor(props: any) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: any) {
    return { error };
  }

  componentDidCatch(error: any) {
    // 旧版本 chunk 失效:一次性硬刷新拉新资源(sessionStorage 防刷新死循环)。
    if (isChunkError(error)) {
      try {
        const KEY = "vkpi:chunk-reload-once";
        if (typeof window !== "undefined" && !window.sessionStorage.getItem(KEY)) {
          window.sessionStorage.setItem(KEY, "1");
          window.location.reload();
        }
      } catch (_) {
        /* ignore */
      }
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const chunk = isChunkError(error);
    const name = this.props.name || "页面";
    const overlay = this.props.variant === "overlay";
    const reload = () => {
      try { window.sessionStorage.removeItem("vkpi:chunk-reload-once"); } catch (_) {}
      window.location.reload();
    };
    return React.createElement(
      "div",
      {
        className: overlay
          ? "fixed inset-0 z-[1400] flex items-center justify-center p-5 text-center backdrop-blur-sm"
          : "min-h-[60vh] p-8 flex flex-col items-center justify-center text-center",
        style: overlay ? { background: "var(--ds-scrim)" } : undefined,
        role: overlay ? "dialog" : "alert",
        "aria-label": `${name} 加载失败`,
      },
      React.createElement(
        "div",
        { className: ERROR_CARD_CLASS },
        React.createElement("div", { className: "text-[14px] font-semibold text-ink mb-2" }, chunk ? "页面有新版本" : `${name} 暂时出错`),
        React.createElement(
          "div",
          { className: "text-[12px] text-ink-2 mb-4" },
          chunk ? "检测到前端已更新,正在刷新加载最新版本…若未自动刷新请点下方按钮。" : "这个页面渲染出错了,可刷新重试。错误已隔离,不影响其它页面。"
        ),
        !chunk &&
          React.createElement(
            "div",
            { className: "text-[11px] text-ink-2 mb-4 break-all font-mono text-left" },
            String((error && error.message) || error).slice(0, 240)
          ),
        React.createElement(
          "div",
          { className: "flex items-center justify-center gap-2" },
          typeof this.props.onDismiss === "function" && React.createElement(
            "button",
            { onClick: this.props.onDismiss, className: ERROR_BUTTON_SECONDARY_CLASS },
            "关闭"
          ),
          React.createElement(
            "button",
            { onClick: reload, className: ERROR_BUTTON_PRIMARY_CLASS },
            "刷新页面"
          )
        )
      )
    );
  }
}

export default LazyErrorBoundary;
