// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";

const e = React.createElement;

export function CenterModal({ children, onClose, maxWidth = "lg" }) {
  const widthClass = { sm: "max-w-sm", md: "max-w-md", lg: "max-w-lg", xl: "max-w-xl", "2xl": "max-w-2xl" }[maxWidth] || "max-w-lg";
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev) => ev.stopPropagation(),
      className: "relative w-full rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden " + widthClass,
    }, children)
  );
}
