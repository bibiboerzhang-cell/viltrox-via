import React from "react";
import { AlertCircle } from "lucide-react";

const e = React.createElement;
export default function DeleteConfirmModal({ title, subtitle, onClose, onConfirm }) {
  return e("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4", onClick: onClose },
    e("div", { className: "rounded-2xl border border-red-500/30 bg-[#0b1220] w-full max-w-sm p-5", onClick: ev => ev.stopPropagation() },
      e("div", { className: "flex items-start gap-3 mb-4" },
        e("div", { className: "w-9 h-9 rounded-lg bg-red-500/15 border border-red-500/30 flex items-center justify-center shrink-0" },
          e(AlertCircle, { size: 16, className: "text-red-300" })
        ),
        e("div", null,
          e("h3", { className: "text-[14px] font-semibold text-white" }, "确认删除"),
          e("p", { className: "text-[11px] text-slate-400 mt-1" }, title),
          subtitle && e("p", { className: "text-[10px] text-slate-500 mt-1" }, subtitle)
        )
      ),
      e("div", { className: "flex items-center justify-end gap-2" },
        e("button", { onClick: onClose, className: "px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "取消"),
        e("button", { onClick: onConfirm, className: "px-3.5 py-1.5 rounded-md bg-red-500 hover:bg-red-400 text-white text-[11px] font-medium" }, "删除")
      )
    )
  );
}
