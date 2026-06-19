import React, { useEffect, useState } from "react";
import { AlertCircle, Download, Edit3, ExternalLink, Package, Plus, X } from "lucide-react";
import { addEventMaterial, deleteEventMaterial } from "../../../../../services/vkpi/events-api";
import AddMaterialModal from "../modals/AddMaterialModal.js";
import DeleteConfirmModal from "../modals/DeleteConfirmModal.js";
import { ITEM_STATUS, MATERIAL_CATEGORIES, MATERIAL_SOURCE } from "../shared/constants.js";
import { ownerByInitial } from "../shared/lookups.js";

const e = React.createElement;
export default function MarketingMaterialsPanel({ ev, token, materials: materialsProp = [], loading, error, reload }) {
  const [materials, setMaterials] = useState(materialsProp);
  const [filter, setFilter] = useState("all");
  const [showAdd, setShowAdd] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [opError, setOpError] = useState("");

  // 父级 reload 后 props.materials 变化 → 同步本地乐观态(镜像 TasksTab)
  useEffect(() => { setMaterials(materialsProp); }, [materialsProp]);

  const msg = (err) => String(err && err.message ? err.message : err);

  function deleteMat(id) {
    setDeleting(null);
    setMaterials(prev => prev.filter(m => m.id !== id));  // 乐观
    deleteEventMaterial(token, ev.id, id)
      .then(() => reload && reload())
      .catch(err => { setOpError("删除物料失败:" + msg(err)); reload && reload(); });
  }
  
  function exportCsv() {
    const headers = ["名称", "类别", "数量", "来源", "状态", "负责人", "更新时间", "备注", "追踪号"];
    const rows = materials.map(m => [
      m.name, MATERIAL_CATEGORIES[m.category]?.label || m.category, m.qty,
      MATERIAL_SOURCE[m.source]?.label || m.source, ITEM_STATUS[m.status]?.label || m.status,
      ownerByInitial(m.owner).name || m.owner, m.updatedAt, m.note || "", m.trackingNo || "",
    ]);
    const csv = [headers, ...rows].map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${ev.title}-物料清单.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }
  
  const CAT_KEYS = ["all", ...Object.keys(MATERIAL_CATEGORIES)];
  const filtered = filter === "all" ? materials : materials.filter(m => m.category === filter);
  const readyCount = materials.filter(m => m.status === "ready" || m.status === "shipped").length;
  
  return e("div", { className: "p-5" },
    (error || opError) && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-rose-500/30 bg-rose-500/10 text-[11px] text-rose-200" }, "⚠ ", opError || error),
    loading && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-white/[0.06] bg-white/[0.02] text-[11px] text-slate-400" }, "加载物料中…"),
    e("div", { className: "mb-4 rounded-lg border border-purple-500/20 bg-purple-500/[0.04] p-3 flex items-center justify-between" },
      e("div", { className: "flex items-center gap-2" },
        e(Package, { size: 14, className: "text-purple-300" }),
        e("div", null,
          e("div", { className: "text-[11.5px] text-purple-200 font-medium" }, "营销物料"),
          e("div", { className: "text-[10px] text-slate-400" }, "共 ", materials.length, " 项 · 已就绪 ", readyCount, " · 来源: 自带 / 快递 / 库存 / 新采购 / 现场印 / 数字")
        )
      ),
      e("button", {
        onClick: () => setShowAdd(true),
        className: "px-2.5 py-1 rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[10.5px] font-medium flex items-center gap-1"
      }, e(Plus, { size: 11 }), "添加物料")
    ),
    
    e("div", { className: "flex items-center gap-1.5 mb-3 flex-wrap" },
      CAT_KEYS.map(k => {
        const active = filter === k;
        if (k === "all") {
          return e("button", { key: k, onClick: () => setFilter(k),
            className: `px-2.5 py-1 rounded text-[10.5px] border ${active ? "border-purple-500/40 bg-purple-500/15 text-purple-200" : "border-white/[0.06] bg-white/[0.02] text-slate-400 hover:bg-white/[0.04]"}`
          }, "全部 (", materials.length, ")");
        }
        const cfg = MATERIAL_CATEGORIES[k];
        const I = cfg.icon;
        const cnt = materials.filter(m => m.category === k).length;
        return e("button", { key: k, onClick: () => setFilter(k),
          className: `px-2.5 py-1 rounded text-[10.5px] border flex items-center gap-1 ${active ? "" : "border-white/[0.06] bg-white/[0.02] text-slate-400 hover:bg-white/[0.04]"}`,
          style: active ? { borderColor: cfg.color + "60", background: cfg.color + "15", color: cfg.color } : {}
        }, e(I, { size: 10 }), cfg.label, " (", cnt, ")");
      })
    ),
    
    e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] overflow-hidden" },
      e("table", { className: "w-full text-[11px]" },
        e("thead", null,
          e("tr", { className: "text-[9.5px] text-slate-500 uppercase tracking-wider bg-white/[0.012]" },
            e("th", { className: "text-left px-3 py-2 font-medium" }, "物料"),
            e("th", { className: "text-left px-3 py-2 font-medium" }, "类别"),
            e("th", { className: "text-right px-3 py-2 font-medium" }, "数量"),
            e("th", { className: "text-left px-3 py-2 font-medium" }, "来源"),
            e("th", { className: "text-left px-3 py-2 font-medium" }, "状态"),
            e("th", { className: "text-left px-3 py-2 font-medium" }, "负责人"),
            e("th", { className: "text-right px-3 py-2 font-medium" }, "更新"),
            e("th", { className: "w-8" })
          )
        ),
        e("tbody", null,
          filtered.map(m => {
            // 支持 "_custom_xxx" 自定义类别
            const isCustom = m.category && m.category.startsWith("_custom_");
            const customName = isCustom ? m.category.slice("_custom_".length) : null;
            const cat = isCustom
              ? { label: customName, icon: Edit3, color: "#06b6d4" }
              : MATERIAL_CATEGORIES[m.category];
            const CI = cat?.icon || Package;
            const catLabel = cat?.label || m.category;
            const catColor = cat?.color || "#94a3b8";
            const src = MATERIAL_SOURCE[m.source] || { label: m.source, color: "#94a3b8" };
            const st = ITEM_STATUS[m.status] || { label: m.status, color: "#94a3b8" };
            const owner = ownerByInitial(m.owner);
            return e("tr", { key: m.id, className: "border-t border-white/[0.04] hover:bg-white/[0.02] group" },
              e("td", { className: "px-3 py-2.5" },
                e("div", { className: "flex items-center gap-2" },
                  e("div", { className: "flex-1 min-w-0" },
                    e("div", { className: "flex items-center gap-1.5" },
                      e("span", { className: "text-[11.5px] text-white font-medium" }, m.name),
                      m.alert === "warn" && e(AlertCircle, { size: 10, className: "text-amber-400" })
                    ),
                    m.note && e("div", { className: "text-[9.5px] text-slate-500 mt-0.5" }, m.note),
                    m.trackingNo && e("div", { className: "text-[9.5px] text-cyan-400 font-mono mt-0.5 flex items-center gap-0.5" },
                      e(Package, { size: 9 }), m.trackingNo,
                      e("a", {
                        href: `https://www.google.com/search?q=${encodeURIComponent(m.trackingNo + " tracking")}`,
                        target: "_blank", rel: "noopener noreferrer",
                        className: "ml-1 text-cyan-300 hover:text-cyan-200 flex items-center gap-0.5"
                      }, e(ExternalLink, { size: 9 }), "追踪")
                    )
                  )
                )
              ),
              e("td", { className: "px-3 py-2.5" },
                e("span", { className: "text-[10px] px-1.5 py-0.5 rounded font-medium flex items-center gap-1 w-fit", style: { background: catColor + "20", color: catColor } },
                  e(CI, { size: 9 }), catLabel
                )
              ),
              e("td", { className: "px-3 py-2.5 text-right text-[11px] text-white tabular-nums" }, m.qty),
              e("td", { className: "px-3 py-2.5" },
                e("span", { className: "text-[10px] px-1.5 py-0.5 rounded font-medium", style: { background: src.color + "20", color: src.color } }, src.label)
              ),
              e("td", { className: "px-3 py-2.5" },
                e("span", { className: "text-[10px] px-1.5 py-0.5 rounded font-medium", style: { background: st.color + "20", color: st.color } }, st.label)
              ),
              e("td", { className: "px-3 py-2.5" },
                e("div", { className: "flex items-center gap-1.5" },
                  e("div", { className: "w-4 h-4 rounded-full flex items-center justify-center text-[8.5px] font-bold text-white", style: { background: owner.color } }, owner.initial),
                  e("span", { className: "text-[10.5px] text-slate-300" }, owner.name || owner.initial)
                )
              ),
              e("td", { className: "px-3 py-2.5 text-right text-[10px] text-slate-500 tabular-nums" }, m.updatedAt),
              e("td", { className: "px-2 py-2.5" },
                e("button", {
                  onClick: () => setDeleting({ id: m.id, name: m.name }),
                  className: "opacity-0 group-hover:opacity-100 text-slate-500 hover:text-red-300 p-1",
                  title: "删除"
                }, e(X, { size: 11 }))
              )
            );
          })
        )
      )
    ),
    
    // footer summary + 真 CSV 导出
    e("div", { className: "mt-3 flex items-center justify-between text-[10px] text-slate-500" },
      e("div", null, "已就绪 ", e("span", { className: "text-emerald-300 font-medium" }, readyCount), " · 待处理 ",
        e("span", { className: "text-amber-300 font-medium" }, materials.filter(m => m.status === "pending" || m.status === "in_production" || m.status === "printing").length),
        " · 已寄出 ", e("span", { className: "text-cyan-300 font-medium" }, materials.filter(m => m.status === "shipped").length)
      ),
      materials.length > 0 && e("button", {
        onClick: exportCsv,
        className: "text-purple-300 hover:text-purple-200 flex items-center gap-1"
      }, e(Download, { size: 10 }), "导出 CSV")
    ),
    
    showAdd && e(AddMaterialModal, {
      onClose: () => setShowAdd(false),
      onSubmit: (m) => {
        setShowAdd(false);
        setMaterials(prev => [m, ...prev]);  // 乐观
        addEventMaterial(token, ev.id, m)
          .then(() => reload && reload())
          .catch(err => { setOpError("添加物料失败:" + msg(err)); reload && reload(); });
      },
    }),
    deleting && e(DeleteConfirmModal, {
      title: `删除物料 "${deleting.name}"?`,
      onClose: () => setDeleting(null),
      onConfirm: () => deleteMat(deleting.id),
    })
  );
}
