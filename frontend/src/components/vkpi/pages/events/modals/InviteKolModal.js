import React, { useState } from "react";
import { Search, X } from "lucide-react";
import { KOL_POOL } from "../data/kol-pool.js";

const e = React.createElement;
export default function InviteKolModal({ ev, existingKolIds, onClose, onSubmit }) {
  const [selected, setSelected] = useState(new Set());
  const [search, setSearch] = useState("");
  const available = KOL_POOL.filter(k => !existingKolIds.has(k.id));
  const filtered = available.filter(k => 
    !search || k.name.toLowerCase().includes(search.toLowerCase()) || k.handle.toLowerCase().includes(search.toLowerCase())
  );
  
  const toggle = (id) => setSelected(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });
  
  return e("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4", onClick: onClose },
    e("div", { className: "rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-xl max-h-[92vh] p-5 flex flex-col", onClick: ev2 => ev2.stopPropagation() },
      e("div", { className: "flex items-center justify-between mb-4" },
        e("div", null,
          e("h3", { className: "text-[14px] font-semibold text-white" }, "邀请 KOL 到场"),
          e("p", { className: "text-[10.5px] text-slate-500 mt-0.5" }, "从 KOL Pool 选 · 已选 ", selected.size)
        ),
        e("button", { onClick: onClose, className: "text-slate-500 hover:text-white" }, e(X, { size: 16 }))
      ),
      e("div", { className: "relative mb-3" },
        e(Search, { size: 12, className: "absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" }),
        e("input", { type: "text", value: search, onChange: ev2 => setSearch(ev2.target.value), placeholder: "搜索 KOL / handle",
          className: "w-full pl-8 pr-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" })
      ),
      e("div", { className: "flex-1 overflow-y-auto space-y-1.5" },
        filtered.length === 0
          ? e("div", { className: "text-center py-8 text-[11px] text-slate-500" }, "没有可邀请的 KOL")
          : filtered.map(k => {
              const isSel = selected.has(k.id);
              return e("label", { key: k.id, className: `flex items-center gap-3 px-3 py-2 rounded cursor-pointer ${isSel ? "bg-purple-500/10 border border-purple-500/30" : "border border-white/[0.04] hover:bg-white/[0.02]"}` },
                e("input", { type: "checkbox", checked: isSel, onChange: () => toggle(k.id), className: "accent-purple-500" }),
                e("div", { className: "w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold text-white shrink-0", style: { background: "linear-gradient(135deg, #a855f7, #06b6d4)" } }, k.name.charAt(0)),
                e("div", { className: "flex-1" },
                  e("div", { className: "flex items-center gap-2" },
                    e("span", { className: "text-[12px] text-white font-medium" }, k.name),
                    e("span", { className: "text-[10px] text-slate-500" }, k.handle)
                  ),
                  e("div", { className: "text-[10px] text-slate-400 mt-0.5" }, k.platform, " · ", (k.followers/1000).toFixed(0), "K followers")
                )
              );
            })
      ),
      e("div", { className: "flex items-center justify-end gap-2 mt-4 pt-3 border-t border-white/[0.05]" },
        e("button", { onClick: onClose, className: "px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "取消"),
        e("button", {
          disabled: selected.size === 0,
          onClick: () => onSubmit(Array.from(selected).map(id => ({ id, status: "pending" }))),
          className: `px-3.5 py-1.5 rounded-md text-[11px] font-medium ${selected.size > 0 ? "bg-purple-500 hover:bg-purple-400 text-white" : "bg-white/[0.05] text-slate-600 cursor-not-allowed"}`
        }, "发送邀请 (", selected.size, ")")
      )
    )
  );
}
