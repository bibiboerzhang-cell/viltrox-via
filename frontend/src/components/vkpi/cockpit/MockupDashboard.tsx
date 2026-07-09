// 1:1 搬版:把 V-KPI-Dashboard mockup 的 board(可编辑看板 / 北极星流动环 / KPI 卡 /
// LLM 队列 / Ask / 下钻弹窗 及全部动效)原样嵌进真 app 的 Dashboard 内容区。
// 做法:注入 mockup 原始 <body> HTML → 调用其原始 <script>(mountMockDashboard),
// 其自带 chrome(rail/top)由 dashboard-mockup.css 隐藏(用真 React 侧栏/顶栏),
// 主题读 <html> data-style/data-theme(与全站 ThemeProvider 同步)。真数据后续注入。
import React from "react";
import "./styles/dashboard-mockup.css";
import { MOCKUP_BODY_HTML, mountMockDashboard } from "./dashboardMockup";

const e = React.createElement;

export function MockupDashboard(_props?: any) {
  const ref = React.useRef<HTMLDivElement | null>(null);
  const mounted = React.useRef(false);
  React.useEffect(() => {
    const root = ref.current;
    if (!root || mounted.current) return; // StrictMode 双触发保护
    mounted.current = true;
    root.innerHTML = MOCKUP_BODY_HTML;
    try {
      mountMockDashboard();
    } catch (err) {
      // 挂载失败不崩 React;留痕便于排查(board 会空,而非白屏)
      console.error("[MockupDashboard] mount failed", err);
    }
    return () => {
      if (root) root.innerHTML = "";
      mounted.current = false;
    };
  }, []);
  return e("div", { className: "mkdash", ref });
}

export default MockupDashboard;
