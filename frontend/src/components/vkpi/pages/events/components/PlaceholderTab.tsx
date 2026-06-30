import React from "react";
import type { LucideIcon } from "lucide-react";

const e = React.createElement;

interface PlaceholderTabProps {
  icon: LucideIcon;
  title: string;
  message: string;
}

export default function PlaceholderTab({ icon: Icon, title, message }: PlaceholderTabProps) {
  return e("div", { className: "p-12 text-center" },
    e(Icon, { size: 32, className: "text-slate-600 mx-auto mb-3" }),
    e("div", { className: "text-[12px] text-slate-300 font-medium mb-1" }, title),
    e("div", { className: "text-[10.5px] text-slate-500" }, message)
  );
}
