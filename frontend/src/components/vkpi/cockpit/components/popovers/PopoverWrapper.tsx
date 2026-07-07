// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useEffect, useState } from "react";
import { m } from "framer-motion";

const e = React.createElement;

export function PopoverWrapper({ children, onClose, anchorRef, width = 280 }: any) {
  const [pos, setPos] = useState({ top: 60, right: 16 });
  useEffect(() => {
    if (anchorRef && anchorRef.current) {
      const rect = anchorRef.current.getBoundingClientRect();
      // popover 顶部对齐按钮底部,右边对齐按钮右边 → 防止超出屏幕
      const rightOffset = window.innerWidth - rect.right;
      setPos({
        top: rect.bottom + 6,
        right: Math.max(8, rightOffset),
      });
    }
  }, [anchorRef]);
  return e("div", { 
    className: "cockpit-shell fixed inset-0", 
    style: { zIndex: 1000 },
    onClick: onClose 
  },
    e(m.div, {
      initial: { opacity: 0, y: -8, scale: 0.96 },
      animate: { opacity: 1, y: 0, scale: 1 },
      exit: { opacity: 0, y: -8, scale: 0.96 },
      transition: { duration: 0.15 },
      onClick: (ev) => ev.stopPropagation(),
      className: "absolute rounded-xl border border-white/[0.08] bg-[#0b1220]/95 backdrop-blur-xl shadow-2xl overflow-hidden",
      style: { top: pos.top, right: pos.right, maxWidth: width }
    }, children)
  );
}
