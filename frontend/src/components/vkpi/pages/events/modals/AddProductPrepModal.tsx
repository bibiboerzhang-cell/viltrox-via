import React, { useState } from "react";
import { Package, Search, X } from "lucide-react";
import { TEAM } from "../data/team";
import { PRODUCT_CATEGORIES, PRODUCT_SOURCES } from "../shared/constants";
import type { ProductVm, StockItem } from "../shared/types";

const e = React.createElement;

interface AddProductPrepModalProps {
  side: "have" | "need";
  stock?: StockItem[];
  onClose: () => void;
  onSubmit: (p: ProductVm) => void;
}

export default function AddProductPrepModal({ side, stock = [], onClose, onSubmit }: AddProductPrepModalProps) {
  const [mode, setMode] = useState(side === "have" && stock.length > 0 ? "from_stock" : "manual");  // from_stock | manual
  const [selectedStockId, setSelectedStockId] = useState<string | null>(null);
  const [stockSearch, setStockSearch] = useState("");

  const [name, setName] = useState("");
  const [category, setCategory] = useState("lens");
  const [source, setSource] = useState(side === "have" ? "in_stock_sample" : "new_purchase");
  const [qty, setQty] = useState(1);
  const [owner, setOwner] = useState("T");
  const [note, setNote] = useState("");
  const [arriveBy, setArriveBy] = useState("");
  const [returnAfter, setReturnAfter] = useState(side === "have");

  const validSources = Object.entries(PRODUCT_SOURCES).filter(([, v]) => v.side === side);
  const filteredStock = stock.filter(s =>
    s.qty > 0 && (!stockSearch || s.name.toLowerCase().includes(stockSearch.toLowerCase()) || s.sku.toLowerCase().includes(stockSearch.toLowerCase()))
  );

  return e("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4", onClick: onClose },
    e("div", { className: "rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-md p-5", onClick: (ev: React.MouseEvent) => ev.stopPropagation() },
      e("div", { className: "flex items-center justify-between mb-4" },
        e("div", null,
          e("h3", { className: "text-[14px] font-semibold text-white" }, side === "have" ? "添加 — 已有物料/样品" : "添加 — 需新寄/采购"),
          e("p", { className: "text-[10.5px] text-slate-500 mt-0.5" }, side === "have" ? "公司样品库 / 现有库存" : "新采购 / 租赁 / 借用")
        ),
        e("button", { onClick: onClose, className: "text-slate-500 hover:text-white" }, e(X, { size: 16 }))
      ),

      // 模式切换 (仅 side=have)
      side === "have" && stock.length > 0 && e("div", { className: "mb-3 flex items-center gap-0.5 rounded-md border border-white/[0.06] p-0.5" },
        ([
          ["from_stock", "📦 从库存表选 (推荐)"],
          ["manual",     "✏️ 手动输入"],
        ] as const).map(([k, l]) => e("button", { key: k, onClick: () => { setMode(k); setSelectedStockId(null); },
          className: `flex-1 px-2 py-1.5 rounded text-[10.5px] font-medium transition-all ${mode === k ? "bg-emerald-500/20 text-emerald-200" : "text-slate-500 hover:text-slate-300"}`
        }, l))
      ),

      // 模式 A: 从库存表选
      side === "have" && mode === "from_stock" && e("div", { className: "space-y-3" },
        e("div", { className: "relative" },
          e(Search, { size: 12, className: "absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" }),
          e("input", { type: "text", value: stockSearch, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setStockSearch(ev.target.value), placeholder: "搜索 SKU / 名称",
            className: "w-full pl-8 pr-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/40" })
        ),
        e("div", { className: "max-h-64 overflow-y-auto space-y-1 rounded-lg border border-white/[0.04] p-1" },
          filteredStock.length === 0
            ? e("div", { className: "text-center py-6 text-[10.5px] text-slate-500" }, "没有匹配的库存项 · 在 EventsPage 顶部 「公司库存」 里添加")
            : filteredStock.map(s => {
                const cat = PRODUCT_CATEGORIES[s.category];
                const CI = cat?.icon || Package;
                const sel = selectedStockId === s.id;
                return e("button", { key: s.id, onClick: () => {
                  setSelectedStockId(s.id);
                  setName(s.name); setCategory(s.category);
                  setSource(s.isSample ? "in_stock_sample" : "in_stock");
                  setNote(`${s.location} · 库存 ${s.qty} · ${s.note || s.sku}`);
                  if (qty > s.qty) setQty(Math.min(qty, s.qty));
                },
                  className: `w-full px-2.5 py-2 rounded text-left flex items-center gap-2 ${sel ? "bg-emerald-500/15 border border-emerald-500/40" : "hover:bg-white/[0.04] border border-transparent"}`
                },
                  e(CI, { size: 12, style: { color: cat?.color || "#94a3b8" } }),
                  e("div", { className: "flex-1 min-w-0" },
                    e("div", { className: "flex items-center gap-1.5" },
                      e("span", { className: "text-[11px] text-white font-medium" }, s.name),
                      s.isSample && e("span", { className: "text-[8.5px] px-1 py-0.5 rounded bg-purple-500/15 text-purple-300" }, "样品")
                    ),
                    e("div", { className: "text-[9.5px] text-slate-500" }, s.sku, " · ", s.location)
                  ),
                  e("span", { className: `text-[10.5px] tabular-nums font-bold ${s.qty < 3 ? "text-amber-300" : "text-emerald-300"}` }, "库存 ", s.qty)
                );
              })
        ),
        selectedStockId && e("div", { className: "grid grid-cols-2 gap-3" },
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "本次带 (数量)"),
            e("input", { type: "number", value: qty, min: 1, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setQty(parseInt(ev.target.value)||1),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white tabular-nums focus:outline-none focus:border-emerald-500/40" })
          ),
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "负责人"),
            e("select", { value: owner, onChange: (ev: React.ChangeEvent<HTMLSelectElement>) => setOwner(ev.target.value),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-emerald-500/40" },
              TEAM.map(u => e("option", { key: u.id, value: u.initial, style: { background: "#0a0a0d" } }, u.name))
            )
          )
        ),
        selectedStockId && e("label", { className: "flex items-center gap-2 text-[10.5px] text-slate-300 cursor-pointer" },
          e("input", { type: "checkbox", checked: returnAfter, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setReturnAfter(ev.target.checked), className: "accent-emerald-500" }),
          "展会后需要退还 (样品 / 借用 默认勾上)"
        )
      ),

      // 模式 B: 手动 (原表单) — side=need 时总是 manual
      (mode === "manual" || side === "need") && e("div", { className: "space-y-3" },
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "产品名称"),
          e("input", { type: "text", value: name, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setName(ev.target.value),
            placeholder: "例: Viltrox 75mm F1.4 Pro",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11.5px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" })
        ),
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1.5 block" }, "类别"),
          e("div", { className: "grid grid-cols-3 gap-1.5" },
            Object.entries(PRODUCT_CATEGORIES).map(([k, cfg]) => {
              const I = cfg.icon;
              const active = category === k;
              return e("button", { key: k, onClick: () => setCategory(k),
                className: `py-1.5 rounded border flex flex-col items-center gap-0.5 ${active ? "" : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]"}`,
                style: active ? { borderColor: cfg.color + "60", background: cfg.color + "15" } : {}
              },
                e(I, { size: 12, style: { color: active ? cfg.color : "#94a3b8" } }),
                e("span", { className: "text-[9.5px]", style: { color: active ? cfg.color : "#94a3b8" } }, cfg.label)
              );
            })
          )
        ),
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1.5 block" }, "来源"),
          e("div", { className: `grid gap-1.5`, style: { gridTemplateColumns: `repeat(${validSources.length}, 1fr)` } },
            validSources.map(([k, cfg]) => {
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
            e("input", { type: "number", value: qty, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setQty(parseInt(ev.target.value)||1),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white tabular-nums focus:outline-none focus:border-purple-500/40" })
          ),
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "负责人"),
            e("select", { value: owner, onChange: (ev: React.ChangeEvent<HTMLSelectElement>) => setOwner(ev.target.value),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40" },
              TEAM.map(u => e("option", { key: u.id, value: u.initial, style: { background: "#0a0a0d" } }, u.name))
            )
          )
        ),
        side === "need" && e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "预计到货 / 取货日期"),
          e("input", { type: "date", value: arriveBy, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setArriveBy(ev.target.value),
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40 [color-scheme:dark]" })
        ),
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "备注"),
          e("input", { type: "text", value: note, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setNote(ev.target.value),
            placeholder: side === "have" ? "例: 深圳样品仓现有 3 只" : "例: 工厂排产中 · 5/15 出货",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" })
        ),
        e("label", { className: "flex items-center gap-2 text-[10.5px] text-slate-300 cursor-pointer" },
          e("input", { type: "checkbox", checked: returnAfter, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setReturnAfter(ev.target.checked), className: "accent-purple-500" }),
          "展会后需要退还 (样品 / 租赁 / 借用)"
        )
      ),
      e("div", { className: "flex items-center justify-end gap-2 mt-5 pt-4 border-t border-white/[0.05]" },
        e("button", { onClick: onClose, className: "px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "取消"),
        e("button", {
          disabled: mode === "from_stock" ? !selectedStockId : !name,
          onClick: () => onSubmit({
            id: "pp_" + Date.now(), name, category, source, qty, owner, note,
            status: side === "have" ? "ready" : "ordered",
            arriveBy: arriveBy || undefined,
            returnAfter,
          }),
          className: `px-3.5 py-1.5 rounded-md text-[11px] font-medium ${(mode === "from_stock" ? selectedStockId : name) ? "bg-purple-500 hover:bg-purple-400 text-white" : "bg-white/[0.05] text-slate-600 cursor-not-allowed"}`
        }, "添加")
      )
    )
  );
}
