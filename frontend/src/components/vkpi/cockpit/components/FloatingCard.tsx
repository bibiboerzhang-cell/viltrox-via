// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useRef, useState } from "react";
import { m } from "framer-motion";
import { X } from "lucide-react";

const e = React.createElement;

export function FloatingCard({ 
  title, icon, iconColor, children, defaultPos, defaultSize, dragConstraintsRef, 
  collapsedSummary, minWidth = 200, minHeight = 100, initialDelay = 0
}: any) {
  const [collapsed, setCollapsed] = useState(false);
  const [size, setSize] = useState<any>(defaultSize || { width: 260, height: "auto" });
  const cardRef = useRef<any>(null);
  const resizingRef = useRef(false);
  const [isResizing, setIsResizing] = useState(false);  // V6.13.1: trigger re-render
  
  // Resize handle 拖拽逻辑
  const startResize = (e: any) => {
    e.stopPropagation();
    e.preventDefault();
    resizingRef.current = true;
    setIsResizing(true);
    const startX = e.clientX;
    const startY = e.clientY;
    const startW = cardRef.current?.offsetWidth || 260;
    const startH = cardRef.current?.offsetHeight || 200;
    
    const onMove = (ev: any) => {
      if (!resizingRef.current) return;
      const newW = Math.max(minWidth, startW + (ev.clientX - startX));
      const newH = Math.max(minHeight, startH + (ev.clientY - startY));
      setSize({ width: newW, height: newH });
    };
    const onUp = () => {
      resizingRef.current = false;
      setIsResizing(false);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };
  
  // 折叠态:小 chip
  if (collapsed) {
    return e(m.div, {
      drag: true,
      dragConstraints: dragConstraintsRef,
      dragMomentum: false,
      dragElastic: 0.05,
      initial: { opacity: 0, scale: 0.9 },
      animate: { opacity: 1, scale: 1 },
      whileDrag: { scale: 1.05, cursor: "grabbing" },
      onClick: () => setCollapsed(false),
      className: "cursor-pointer rounded-xl border border-line bg-panel px-3 py-2 backdrop-blur-xl shadow-ds-sm hover:border-line-strong",
      style: { zIndex: 800, cursor: "grab" }
    },
      e("div", { className: "flex items-center gap-2" },
        // color-mix 软底:iconColor 既可是 mode 数据色(hex)也可是 token var(),两者皆成立
        icon && e("span", {
          className: "rounded-md p-1.5",
          style: { background: `color-mix(in srgb, ${iconColor} 13%, transparent)`, color: iconColor }
        }, e(icon, { size: 12 })),
        e("div", { className: "min-w-0" },
          e("div", { className: "text-[10px] uppercase tracking-wider text-muted" }, "Card"),
          e("div", { className: "text-xs font-medium text-ink truncate max-w-[140px]" }, title)
        )
      )
    );
  }
  
  // V6.13.1: m.div 处理 drag,但 size 通过 inline style + isResizing 状态保护
  // 关键修复:resize 期间禁用 drag,且用 isResizing state 触发 re-render 确保 drag prop 同步
  return e(m.div, {
    ref: cardRef,
    drag: !isResizing,
    dragConstraints: dragConstraintsRef,
    dragMomentum: false,
    dragElastic: 0.05,
    dragListener: !isResizing,
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { delay: initialDelay },
    whileDrag: { scale: 1.02, zIndex: 999 },
    className: "rounded-xl border border-line bg-panel backdrop-blur-xl shadow-ds overflow-hidden",
    style: { 
      zIndex: 800,
      width: size.width,
      height: size.height,
      position: "relative",
    }
  },
    // Header (drag handle)
    e("div", { 
      className: "flex items-center justify-between gap-2 px-3 py-2 cursor-grab active:cursor-grabbing select-none border-b border-line"
    },
      e("div", { className: "flex items-center gap-1.5 flex-1 min-w-0" },
        // 拖拽指示 dots
        e("div", { className: "flex gap-0.5" },
          e("span", { className: "h-1 w-1 rounded-full bg-line-strong" }),
          e("span", { className: "h-1 w-1 rounded-full bg-line-strong" }),
          e("span", { className: "h-1 w-1 rounded-full bg-line-strong" })
        ),
        icon && e(icon, { size: 12, style: { color: iconColor }, className: "ml-1" }),
        e("h3", { className: "text-xs font-semibold text-ink truncate" }, title)
      ),
      // 控件 - 折叠按钮
      e("div", { className: "flex items-center gap-1 shrink-0" },
        e("button", {
          onClick: (ev: any) => { ev.stopPropagation(); setCollapsed(true); },
          className: "p-1 rounded text-muted hover:text-ink hover:bg-accent-soft"
        }, e(X, { size: 12 }))
      )
    ),
    // 内容
    e("div", { 
      className: "p-3 overflow-auto",
      style: { 
        maxHeight: size.height === "auto" ? "none" : `${size.height - 40}px`,
      }
    }, children),
    // Resize handle 右下角 — V6.13.1: 用 onPointerDownCapture 拦截 framer-motion
    e("div", {
      onMouseDown: startResize,
      onPointerDownCapture: (ev: any) => { ev.stopPropagation(); },
      className: "absolute bottom-0 right-0 w-4 h-4 cursor-nwse-resize",
      style: {
        background: "linear-gradient(135deg, transparent 50%, var(--ds-line-strong) 50%, var(--ds-line-strong) 60%, transparent 60%, transparent 70%, var(--ds-line-strong) 70%, var(--ds-line-strong) 80%, transparent 80%)",
        zIndex: 10,
      }
    })
  );
}
