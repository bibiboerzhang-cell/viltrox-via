import React from "react";
import { createPortal } from "react-dom";

// 页级 overlay(抽屉 / 弹窗)统一挂 body(员工反馈 #2:弹层被父容器裁切、内容只露一半)。
//   根因:板块页挂在 framer 页面舞台(m.div 带 transform)与 react-grid-layout 的
//   transform/overflow 祖先之下,position:fixed 的子树会被这些祖先当作包含块 → 裁切/错位。
//   ModalShell(MarketVoicePage.modal-shell)与 CenterModal 已各自 portal;本件是给
//   用 createElement 写的遗留 overlay(KOLDetailDrawer / ContactModal …)的共用壳。
//   皮肤作用域:cockpit-reference.routes.css 的 Kit 皮肤规则只认 .vkpi-page-stage,
//   portal 后脱离舞台会掉皮 —— 所以壳自己带 vkpi-page-stage + --{stage} 类,并用
//   vkpi-page-stage--overlay 抵消舞台的 position/overflow/background(见 routes.css)。
//   AnimatePresence 的退场动画经 context 穿透 portal,子树里 m.* 的 exit 照常生效。
export function OverlayPortal({ children, stage }: { children?: React.ReactNode; stage: string }) {
  if (typeof document === "undefined") return <>{children}</>;
  return createPortal(
    <div
      data-vkpi-modal-layer="body-portal"
      className={`vkpi-page-stage vkpi-page-stage--${stage} vkpi-page-stage--overlay`}
    >
      {children}
    </div>,
    document.body,
  );
}
