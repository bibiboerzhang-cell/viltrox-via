// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { createPortal } from "react-dom";
import { motion } from "framer-motion";

const e = React.createElement;

export function CenterModal({ children, onClose, maxWidth = "lg" }: { children?: any; onClose?: () => void; maxWidth?: string }) {
  const widthClass = ({
    sm: "max-w-sm",
    md: "max-w-md",
    lg: "max-w-lg",
    xl: "max-w-xl",
    "2xl": "max-w-2xl",
    "3xl": "max-w-3xl",
    "4xl": "max-w-4xl",
    "5xl": "max-w-5xl",
    "6xl": "max-w-6xl",
    "7xl": "max-w-7xl",
  } as any)[maxWidth] || "max-w-lg";
  const modal = e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4",
    style: { zIndex: 9999 },
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev: any) => ev.stopPropagation(),
      style: { zIndex: 1 },
      className: "relative z-10 flex max-h-[92vh] w-full flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl " + widthClass,
    }, children)
  );
  return typeof document === "undefined" ? modal : createPortal(modal, document.body);
}
