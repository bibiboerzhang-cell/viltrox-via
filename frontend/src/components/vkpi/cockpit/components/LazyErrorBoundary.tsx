// 隔离懒加载页面的崩溃:① 部署后 chunk 哈希变化导致旧浏览器加载失败(白屏)——
// 自动硬刷新一次拉取新资源;② 任何渲染异常——优雅兜底,只挂这一块,不连累整个 app。
import React from "react";

function isChunkError(err: any) {
  const msg = String((err && err.message) || err || "");
  const name = String((err && err.name) || "");
  return (
    name === "ChunkLoadError" ||
    /Loading chunk|Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|dynamically imported module/i.test(msg)
  );
}

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
    const reload = () => {
      try { window.sessionStorage.removeItem("vkpi:chunk-reload-once"); } catch (_) {}
      window.location.reload();
    };
    return React.createElement(
      "div",
      { className: "min-h-[60vh] p-8 flex flex-col items-center justify-center text-center" },
      React.createElement(
        "div",
        { className: "rounded-2xl border border-white/[0.08] bg-white/[0.02] p-8 max-w-md w-full" },
        React.createElement("div", { className: "text-[14px] font-semibold text-white mb-2" }, chunk ? "页面有新版本" : `${name} 暂时出错`),
        React.createElement(
          "div",
          { className: "text-[12px] text-slate-400 mb-4" },
          chunk ? "检测到前端已更新,正在刷新加载最新版本…若未自动刷新请点下方按钮。" : "这个页面渲染出错了,可刷新重试。错误已隔离,不影响其它页面。"
        ),
        !chunk &&
          React.createElement(
            "div",
            { className: "text-[10px] text-slate-500 mb-4 break-all font-mono text-left" },
            String((error && error.message) || error).slice(0, 240)
          ),
        React.createElement(
          "button",
          { onClick: reload, className: "px-4 py-2 rounded-lg bg-purple-500/90 hover:bg-purple-500 text-white text-[12px] font-medium" },
          "刷新页面"
        )
      )
    );
  }
}

export default LazyErrorBoundary;
