// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";

const e = React.createElement;

export function KPAvatar({ name, color, size = 32, onClick = undefined, onAvatarClick = undefined, title = undefined }: any) {
  const initial = (name || "?").trim()[0]?.toUpperCase() || "?";
  const handler = onAvatarClick || onClick;
  const interactive = typeof handler === "function";
  const base = "shrink-0 rounded-full flex items-center justify-center font-bold text-white";
  const style = { width: size, height: size, fontSize: size * 0.4, background: color || "#a855f7" };
  if (!interactive) {
    return e("div", { className: base, style }, initial);
  }
  return e("div", {
    role: "button",
    tabIndex: 0,
    title: title || (name ? "查看 " + name + " 详情" : "查看详情"),
    "aria-label": title || (name ? "查看 " + name + " 详情" : "查看详情"),
    className: base + " cursor-pointer transition hover:ring-2 hover:ring-white/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-white/60",
    style,
    onClick: (ev) => {
      ev.stopPropagation();
      handler(ev);
    },
    onKeyDown: (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        ev.stopPropagation();
        handler(ev);
      }
    }
  }, initial);
}
