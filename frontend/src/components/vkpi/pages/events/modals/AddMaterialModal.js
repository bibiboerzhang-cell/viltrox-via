import React, { useState } from "react";
import { Edit3, X } from "lucide-react";
import { TEAM } from "../data/team.js";
import { MATERIAL_CATEGORIES, MATERIAL_SOURCE } from "../shared/constants.js";

const e = React.createElement;
export default function AddMaterialModal({ onClose, onSubmit }) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("display");
  const [customLabel, setCustomLabel] = useState("");
  const [source, setSource] = useState("ship");
  const [qty, setQty] = useState(1);
  const [owner, setOwner] = useState("M");
  const [note, setNote] = useState("");
  
  const isCustom = category === "custom";
  const finalCategory = isCustom && customLabel ? "_custom_" + customLabel : category;
  const isReady = name && (!isCustom || customLabel);
  
  return e("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4", onClick: onClose },
    e("div", { className: "rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-md p-5", onClick: ev => ev.stopPropagation() },
      e("div", { className: "flex items-center justify-between mb-4" },
        e("div", null,
          e("h3", { className: "text-[14px] font-semibold text-white" }, "添加物料"),
          e("p", { className: "text-[10.5px] text-slate-500 mt-0.5" }, "营销物料 / 礼品 / 媒体 kit 等")
        ),
        e("button", { onClick: onClose, className: "text-slate-500 hover:text-white" }, e(X, { size: 16 }))
      ),
      e("div", { className: "space-y-3" },
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "物料名称"),
          e("input", { type: "text", value: name, onChange: ev => setName(ev.target.value),
            placeholder: "例: KOL 答谢卡 (200 张)",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11.5px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" })
        ),
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1.5 block" }, "类别"),
          e("div", { className: "grid grid-cols-6 gap-1.5" },
            Object.entries(MATERIAL_CATEGORIES).map(([k, cfg]) => {
              const I = cfg.icon;
              const active = category === k;
              return e("button", { key: k, onClick: () => setCategory(k),
                className: `py-1.5 rounded border flex flex-col items-center gap-0.5 ${active ? "" : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]"}`,
                style: active ? { borderColor: cfg.color + "60", background: cfg.color + "15" } : {}
              },
                e(I, { size: 12, style: { color: active ? cfg.color : "#94a3b8" } }),
                e("span", { className: "text-[9px]", style: { color: active ? cfg.color : "#94a3b8" } }, cfg.label)
              );
            }),
            // 自定义按钮
            (() => {
              const active = category === "custom";
              return e("button", { onClick: () => setCategory("custom"),
                className: `py-1.5 rounded border flex flex-col items-center gap-0.5 ${active ? "border-cyan-500/60 bg-cyan-500/15" : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]"}`
              },
                e(Edit3, { size: 12, style: { color: active ? "#06b6d4" : "#94a3b8" } }),
                e("span", { className: "text-[9px]", style: { color: active ? "#06b6d4" : "#94a3b8" } }, "自定义")
              );
            })()
          ),
          // 自定义 label 输入
          isCustom && e("div", { className: "mt-2 rounded-lg border border-cyan-500/30 bg-cyan-500/[0.04] p-2.5" },
            e("label", { className: "text-[10px] text-cyan-200 mb-1 block flex items-center gap-1" },
              e(Edit3, { size: 9 }), "自定义类别名称"
            ),
            e("input", { type: "text", value: customLabel, onChange: ev => setCustomLabel(ev.target.value),
              placeholder: "例: 现场签到二维码 / 互动屏内容 / 答谢卡片...",
              autoFocus: true,
              className: "w-full px-2.5 py-1.5 rounded bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500/40" })
          )
        ),
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1.5 block" }, "来源"),
          e("div", { className: "grid grid-cols-3 gap-1.5" },
            Object.entries(MATERIAL_SOURCE).map(([k, cfg]) => {
              const active = source === k;
              return e("button", { key: k, onClick: () => setSource(k),
                className: `py-1.5 rounded border text-[10px] font-medium ${active ? "" : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]"}`,
                style: active ? { borderColor: cfg.color + "60", background: cfg.color + "15", color: cfg.color } : { color: "#94a3b8" }
              }, cfg.label);
            })
          )
        ),
        e("div", { className: "grid grid-cols-2 gap-3" },
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "数量"),
            e("input", { type: "number", value: qty, onChange: ev => setQty(parseInt(ev.target.value)||1),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white tabular-nums focus:outline-none focus:border-purple-500/40" })
          ),
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "负责人"),
            e("select", { value: owner, onChange: ev => setOwner(ev.target.value),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40" },
              TEAM.map(u => e("option", { key: u.id, value: u.initial, style: { background: "#0a0a0d" } }, u.name))
            )
          )
        ),
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "备注 (可选)"),
          e("input", { type: "text", value: note, onChange: ev => setNote(ev.target.value),
            placeholder: "例: 工厂 5/29 出货 · DHL 寄 LA",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" })
        )
      ),
      e("div", { className: "flex items-center justify-end gap-2 mt-5 pt-4 border-t border-white/[0.05]" },
        e("button", { onClick: onClose, className: "px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "取消"),
        e("button", {
          disabled: !isReady,
          onClick: () => onSubmit({ id: "m_" + Date.now(), name, category: finalCategory, source, qty, status: "pending", owner, note: note || "", updatedAt: "刚刚" }),
          className: `px-3.5 py-1.5 rounded-md text-[11px] font-medium ${isReady ? "bg-purple-500 hover:bg-purple-400 text-white" : "bg-white/[0.05] text-slate-600 cursor-not-allowed"}`
        }, "添加")
      )
    )
  );
}
