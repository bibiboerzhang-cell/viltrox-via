// Verbatim from vkpi_v6.15.7_integrated.html


import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { m } from "framer-motion";

export function PopoverWrapper({ children, onClose, anchorRef, width = 280 }: any) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState({ top: 60, left: 16 });

  const updatePosition = useCallback(() => {
    const rect = anchorRef?.current?.getBoundingClientRect();
    if (!rect) return;
    const edge = 8;
    const gap = 6;
    const panelWidth = Math.min(width, window.innerWidth - edge * 2);
    const panelHeight = panelRef.current?.offsetHeight || 320;
    const below = rect.bottom + gap;
    setPos({
      top: below + panelHeight <= window.innerHeight - edge
        ? below
        : Math.max(edge, rect.top - panelHeight - gap),
      left: Math.min(
        Math.max(edge, rect.right - panelWidth),
        Math.max(edge, window.innerWidth - panelWidth - edge),
      ),
    });
  }, [anchorRef, width]);

  useLayoutEffect(() => {
    updatePosition();
    const frame = window.requestAnimationFrame(updatePosition);
    return () => window.cancelAnimationFrame(frame);
  }, [updatePosition]);

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!(event.target instanceof Node)) return;
      if (panelRef.current?.contains(event.target) || anchorRef?.current?.contains?.(event.target)) return;
      onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      onClose();
      anchorRef?.current?.focus?.();
    };
    window.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [anchorRef, onClose, updatePosition]);

  if (typeof document === "undefined") return null;
  return createPortal(
    <div className="vkpi-popover-layer">
      <m.div
        ref={panelRef}
        initial={{ opacity: 0, y: -8, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -8, scale: 0.96 }}
        transition={{ duration: 0.15 }}
        className="vkpi-popover-panel overflow-hidden"
        style={{ top: pos.top, left: pos.left, width: `min(${width}px, calc(100vw - 16px))` }}
      >
        {children}
      </m.div>
    </div>,
    document.body,
  );
}
