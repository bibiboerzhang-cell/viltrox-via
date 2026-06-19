import React, { useEffect, useState } from "react";
import { Camera, Check, ExternalLink, Package, Plus, RefreshCw, X } from "lucide-react";
import { addEventProduct, deleteEventProduct } from "../../../../../services/vkpi/events-api";
import AddProductPrepModal from "../modals/AddProductPrepModal.js";
import DeleteConfirmModal from "../modals/DeleteConfirmModal.js";
import { ITEM_STATUS, PRODUCT_CATEGORIES, PRODUCT_SOURCES } from "../shared/constants.js";
import { ownerByInitial } from "../shared/lookups.js";

const e = React.createElement;
export default function ProductPrepPanel({ ev, stock, token, products: productsProp = [], loading, error, reload }) {
  const [products, setProducts] = useState(productsProp);
  const [addingSide, setAddingSide] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [opError, setOpError] = useState("");

  // 父级 reload 后 props.products 变化 → 同步本地乐观态(镜像 TasksTab)
  useEffect(() => { setProducts(productsProp); }, [productsProp]);

  const msg = (err) => String(err && err.message ? err.message : err);

  function deleteProduct(id) {
    setDeleting(null);
    setProducts(prev => prev.filter(p => p.id !== id));  // 乐观
    deleteEventProduct(token, ev.id, id)
      .then(() => reload && reload())
      .catch(err => { setOpError("删除产品失败:" + msg(err)); reload && reload(); });
  }
  
  const have = products.filter(p => PRODUCT_SOURCES[p.source]?.side === "have");
  const need = products.filter(p => PRODUCT_SOURCES[p.source]?.side === "need");
  
  function renderRow(p) {
    const cat = PRODUCT_CATEGORIES[p.category];
    const CI = cat.icon;
    const src = PRODUCT_SOURCES[p.source];
    const st = ITEM_STATUS[p.status] || { label: p.status, color: "#94a3b8" };
    const owner = ownerByInitial(p.owner);
    
    return e("div", { key: p.id, className: "px-3 py-2.5 border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02] group" },
      e("div", { className: "flex items-start gap-3" },
        e("div", { className: "w-8 h-8 rounded-lg flex items-center justify-center shrink-0", style: { background: cat.color + "15" } },
          e(CI, { size: 14, style: { color: cat.color } })
        ),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "flex items-center gap-2 flex-wrap mb-0.5" },
            e("span", { className: "text-[11.5px] text-white font-medium" }, p.name),
            e("span", { className: "text-[10px] tabular-nums text-slate-400" }, "× ", p.qty),
            e("span", { className: "text-[9.5px] px-1.5 py-0.5 rounded font-medium", style: { background: src.color + "20", color: src.color } }, src.label),
            e("span", { className: "text-[9.5px] px-1.5 py-0.5 rounded font-medium", style: { background: st.color + "20", color: st.color } }, st.label),
            p.returnAfter && e("span", { className: "text-[9px] px-1.5 py-0.5 rounded font-medium bg-amber-500/15 text-amber-300 flex items-center gap-0.5" },
              e(RefreshCw, { size: 8 }), "展会后退还"
            )
          ),
          p.note && e("div", { className: "text-[10px] text-slate-500" }, p.note),
          p.trackingNo && p.trackingNo !== "未发货" && e("div", { className: "text-[10px] text-cyan-400 font-mono mt-1 flex items-center gap-0.5" },
            e(Package, { size: 9 }), p.trackingNo,
            e("a", {
              href: `https://www.google.com/search?q=${encodeURIComponent(p.trackingNo + " tracking")}`,
              target: "_blank", rel: "noopener noreferrer",
              className: "ml-1 text-cyan-300 hover:text-cyan-200 flex items-center gap-0.5"
            }, e(ExternalLink, { size: 9 }), "追踪")
          )
        ),
        e("div", { className: "text-right shrink-0 flex flex-col items-end gap-1" },
          e("div", { className: "flex items-center gap-1.5" },
            e("div", { className: "w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white", style: { background: owner.color } }, owner.initial),
            e("span", { className: "text-[10.5px] text-slate-300" }, owner.name || owner.initial),
            e("button", {
              onClick: () => setDeleting({ id: p.id, name: p.name }),
              className: "opacity-0 group-hover:opacity-100 text-slate-500 hover:text-red-300 p-0.5",
              title: "删除"
            }, e(X, { size: 11 }))
          ),
          p.arriveBy && e("div", { className: "text-[9.5px] text-slate-500 tabular-nums" }, "到货 ", p.arriveBy)
        )
      )
    );
  }
  
  return e("div", { className: "p-5" },
    (error || opError) && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-rose-500/30 bg-rose-500/10 text-[11px] text-rose-200" }, "⚠ ", opError || error),
    loading && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-white/[0.06] bg-white/[0.02] text-[11px] text-slate-400" }, "加载产品中…"),
    // 顶部 summary
    e("div", { className: "mb-4 rounded-lg border border-purple-500/20 bg-purple-500/[0.04] p-3 flex items-center justify-between flex-wrap gap-3" },
      e("div", { className: "flex items-center gap-2" },
        e(Camera, { size: 14, className: "text-purple-300" }),
        e("div", null,
          e("div", { className: "text-[11.5px] text-purple-200 font-medium" }, "产品准备 (镜头 + 设备)"),
          e("div", { className: "text-[10px] text-slate-400" }, "已有 ", e("span", { className: "text-emerald-300" }, have.length), " · 需新寄 ", e("span", { className: "text-purple-300" }, need.length), " · 共 ", products.length, " 项")
        )
      )
    ),
    
    // 二分布局
    e("div", { className: "grid grid-cols-1 lg:grid-cols-2 gap-4" },
      // 左侧: 已有
      e("div", { className: "rounded-lg border border-emerald-500/20 bg-emerald-500/[0.02] overflow-hidden" },
        e("div", { className: "px-3 py-2.5 flex items-center justify-between border-b border-emerald-500/15", style: { background: "rgba(16,185,129,0.05)" } },
          e("div", { className: "flex items-center gap-2" },
            e(Check, { size: 13, className: "text-emerald-400" }),
            e("div", null,
              e("div", { className: "text-[12px] font-semibold text-emerald-200" }, "已有物料 / 样品"),
              e("div", { className: "text-[9.5px] text-slate-400" }, "公司样品库 + 现有库存 · ", have.length, " 项")
            )
          ),
          e("button", {
            onClick: () => setAddingSide("have"),
            className: "px-2 py-1 rounded text-[10px] font-medium bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 flex items-center gap-1 border border-emerald-500/20"
          }, e(Plus, { size: 10 }), "添加")
        ),
        have.length === 0
          ? e("div", { className: "p-6 text-center text-[10.5px] text-slate-500" }, "暂无 · 点 + 添加现有物料")
          : e("div", null, have.map(renderRow))
      ),
      
      // 右侧: 需新寄
      e("div", { className: "rounded-lg border border-purple-500/20 bg-purple-500/[0.02] overflow-hidden" },
        e("div", { className: "px-3 py-2.5 flex items-center justify-between border-b border-purple-500/15", style: { background: "rgba(168,85,247,0.05)" } },
          e("div", { className: "flex items-center gap-2" },
            e(Plus, { size: 13, className: "text-purple-300" }),
            e("div", null,
              e("div", { className: "text-[12px] font-semibold text-purple-200" }, "需新寄 / 采购 / 租赁"),
              e("div", { className: "text-[9.5px] text-slate-400" }, "新采购 + 租赁 + 借用 · ", need.length, " 项")
            )
          ),
          e("button", {
            onClick: () => setAddingSide("need"),
            className: "px-2 py-1 rounded text-[10px] font-medium bg-purple-500/15 hover:bg-purple-500/25 text-purple-300 flex items-center gap-1 border border-purple-500/20"
          }, e(Plus, { size: 10 }), "添加")
        ),
        need.length === 0
          ? e("div", { className: "p-6 text-center text-[10.5px] text-slate-500" }, "暂无 · 点 + 添加新采购 / 租赁")
          : e("div", null, need.map(renderRow))
      )
    ),
    
    // 底部 summary
    e("div", { className: "mt-4 grid grid-cols-3 gap-3" },
      e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3 text-center" },
        e("div", { className: "text-[10px] text-slate-400 mb-1" }, "镜头"),
        e("div", { className: "text-[18px] font-bold text-white tabular-nums" }, products.filter(p => p.category === "lens").reduce((s, p) => s + p.qty, 0)),
        e("div", { className: "text-[9.5px] text-slate-500" }, "支 · ", products.filter(p => p.category === "lens").length, " SKU")
      ),
      e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3 text-center" },
        e("div", { className: "text-[10px] text-slate-400 mb-1" }, "设备"),
        e("div", { className: "text-[18px] font-bold text-white tabular-nums" }, products.filter(p => p.category === "equipment").reduce((s, p) => s + p.qty, 0)),
        e("div", { className: "text-[9.5px] text-slate-500" }, "件")
      ),
      e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3 text-center" },
        e("div", { className: "text-[10px] text-slate-400 mb-1" }, "配件"),
        e("div", { className: "text-[18px] font-bold text-white tabular-nums" }, products.filter(p => p.category === "accessory").reduce((s, p) => s + p.qty, 0)),
        e("div", { className: "text-[9.5px] text-slate-500" }, "件")
      )
    ),
    
    addingSide && e(AddProductPrepModal, {
      side: addingSide,
      stock,
      onClose: () => setAddingSide(null),
      onSubmit: (p) => {
        setAddingSide(null);
        setProducts(prev => [...prev, p]);  // 乐观
        addEventProduct(token, ev.id, p)
          .then(() => reload && reload())
          .catch(err => { setOpError("添加产品失败:" + msg(err)); reload && reload(); });
      },
    }),
    deleting && e(DeleteConfirmModal, {
      title: `从清单移除 "${deleting.name}"?`,
      subtitle: "公司库存表里的产品不受影响",
      onClose: () => setDeleting(null),
      onConfirm: () => deleteProduct(deleting.id),
    })
  );
}
