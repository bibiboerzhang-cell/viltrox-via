// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useEffect, useRef, useState } from "react";
import { AnimatePresence, m } from "framer-motion";
import { Check, ChevronDown } from "lucide-react";

const e = React.createElement;

export function HierarchyDropdown({ label, value, placeholder, options, onChange, color, disabled, accent }: any) {
  const [open, setOpen] = useState(false);
  const ref = useRef<any>(null);

  useEffect(() => {
    const handle = (ev: any) => {
      if (ref.current && !ref.current.contains(ev.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, []);

  const selected = options.find((o: any) => o.key === value);

  return e("div", { ref, className: "relative min-w-[160px]" },
    e("button", {
      onClick: () => !disabled && setOpen(!open),
      disabled,
      className: `flex w-full items-center justify-between gap-2 rounded-lg border bg-panel px-3 py-2.5 backdrop-blur-xl transition-colors ${
        disabled ? "cursor-not-allowed opacity-40" :
        accent ? "border-warn hover:border-line-strong animate-pulse" :
        "border-line hover:border-line-strong"
      }`,
    },
      e("div", { className: "min-w-0 text-left" },
        e("div", { className: `text-[9px] uppercase tracking-[0.12em] ${accent ? "text-warn" : "text-muted"}` }, label),
        e("div", { className: "truncate text-xs font-semibold text-ink" }, selected ? selected.label : (placeholder || "Select"))
      ),
      e(ChevronDown, { size: 12, className: `shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}` })
    ),
    e(AnimatePresence, null, open && e(m.div, {
      initial: { opacity: 0, y: -6, scale: 0.96 },
      animate: { opacity: 1, y: 0, scale: 1 },
      exit: { opacity: 0, y: -6, scale: 0.96 },
      transition: { duration: 0.14 },
      className: "absolute left-0 top-full z-50 mt-1.5 w-[280px] max-h-[360px] overflow-y-auto overflow-hidden rounded-lg border border-line bg-panel shadow-ds backdrop-blur-xl"
    },
      options.map((opt: any) => {
        const isSelected = value === opt.key;
        const itemDisabled = opt.disabled;
        return e("button", {
          key: opt.key,
          onClick: () => { if (itemDisabled) return; onChange(opt.key); setOpen(false); },
          disabled: itemDisabled,
          className: `flex w-full items-center justify-between gap-3 border-b border-line px-4 py-2.5 text-left transition-colors last:border-b-0 ${
            itemDisabled ? "opacity-50 cursor-not-allowed" : "hover:bg-accent-soft"
          } ${isSelected ? "bg-accent-soft" : ""}`
        },
          e("div", { className: "min-w-0" },
            e("div", { className: "text-sm text-ink truncate flex items-center gap-2" },
              opt.label,
              isSelected && e(Check, { size: 12, className: "text-good shrink-0" })
            ),
            opt.sub && e("div", { className: "text-[10px] text-muted truncate" }, opt.sub)
          ),
          opt.badge && e("span", {
            className: "shrink-0 rounded-md px-2 py-0.5 text-[10px] font-medium",
            style: itemDisabled
              ? { background: "var(--ds-warn-soft)", color: "var(--ds-warn)" }
              : { background: `color-mix(in srgb, ${color} 13%, transparent)`, color }
          }, opt.badge)
        );
      })
    ))
  );
}
