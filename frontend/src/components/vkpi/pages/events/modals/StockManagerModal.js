import React, { useState } from "react";
import { Edit3, History, Package, Plus, Search, X } from "lucide-react";
import { PRODUCT_CATEGORIES } from "../shared/constants.js";
import { useAuth } from "../../../../../hooks/useAuth";
import {
  listInventory, createInventoryItem, updateInventoryItem, deleteInventoryItem,
  getInventoryMovements, toUiStock,
} from "../../../../../services/vkpi/inventory-api";

const e = React.createElement;

// action → 展示标签 / 颜色(调动记录视图用)
const ACTION_META = {
  add:    { label: "新建",   color: "#34d399" },
  edit:   { label: "改字段", color: "#a78bfa" },
  adjust: { label: "调量",   color: "#fbbf24" },
  delete: { label: "删除",   color: "#f87171" },
};

function fmtTime(raw) {
  if (!raw) return "—";
  const d = new Date(String(raw));
  if (Number.isNaN(d.getTime())) return String(raw);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export default function StockManagerModal({ stock, setStock, token: tokenProp, onClose }) {
  const auth = useAuth();
  const token = tokenProp || (auth && auth.token) || "";

  const [search, setSearch] = useState("");
  const [catFilter, setCatFilter] = useState("all");
  const [editingId, setEditingId] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [view, setView] = useState("stock"); // "stock" | "movements"
  const [err, setErr] = useState("");

  // 调动记录(全局:逐 SKU 拉后合并;懒加载)
  const [movements, setMovements] = useState([]);
  const [movLoading, setMovLoading] = useState(false);
  const [movFocusSku, setMovFocusSku] = useState(null); // 非空 → 只看某 SKU 的调动

  // 新建项 form
  const [newName, setNewName] = useState("");
  const [newCategory, setNewCategory] = useState("lens");
  const [newQty, setNewQty] = useState(0);
  const [newLocation, setNewLocation] = useState("深圳量产仓");
  const [newSku, setNewSku] = useState("");
  const [newNote, setNewNote] = useState("");
  const [newIsSample, setNewIsSample] = useState(false);

  const filtered = stock.filter(s => {
    if (catFilter !== "all" && s.category !== catFilter) return false;
    if (search && !s.name.toLowerCase().includes(search.toLowerCase()) && !(s.sku || "").toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  // 拉真数据回填整表(乐观写后对账)
  function refetch() {
    if (!token) return;
    listInventory(token)
      .then(res => { if (Array.isArray(res.items)) setStock(res.items.map(toUiStock)); })
      .catch(e2 => setErr("刷新失败:" + String(e2 && e2.message ? e2.message : e2)));
  }

  // 拉调动记录:movFocusSku 给定 → 单 SKU;否则把当前库存所有 SKU 逐个拉后合并按时间倒序。
  function loadMovements(focusSku) {
    if (!token) { setMovements([]); return; }
    setMovLoading(true);
    setErr("");
    const skus = focusSku ? [focusSku] : Array.from(new Set(stock.map(s => s.sku).filter(Boolean)));
    Promise.all(skus.map(sku =>
      getInventoryMovements(token, sku, 100)
        .then(res => Array.isArray(res.items) ? res.items : [])
        .catch(() => [])
    ))
      .then(lists => {
        const all = [].concat(...lists);
        all.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
        setMovements(all);
      })
      .finally(() => setMovLoading(false));
  }

  function openMovements(focusSku = null) {
    setMovFocusSku(focusSku);
    setView("movements");
    loadMovements(focusSku);
  }

  // ── Mutations:乐观更新 + 后端 + 对账 ──────────────────────────────────────
  function updateQty(item, delta) {
    const newQty = Math.max(0, (item.qty || 0) + delta);
    setStock(prev => prev.map(s => s.id === item.id ? { ...s, qty: newQty } : s)); // 乐观
    if (!token) return;
    updateInventoryItem(token, item.sku, { qty: newQty })
      .then(() => refetch())
      .catch(e2 => { setErr("调整数量失败:" + String(e2 && e2.message ? e2.message : e2)); refetch(); });
  }

  function updateField(item, field, value) {
    setStock(prev => prev.map(s => s.id === item.id ? { ...s, [field]: value } : s)); // 乐观(本地即时)
    if (!token) return;
    const fieldMap = { location: "location", note: "note", name: "name" };
    const key = fieldMap[field] || field;
    updateInventoryItem(token, item.sku, { [key]: value })
      .catch(e2 => { setErr("修改失败:" + String(e2 && e2.message ? e2.message : e2)); refetch(); });
  }

  function deleteItem(item) {
    if (!confirm("删除这个库存项? 不可撤销.")) return;
    setStock(prev => prev.filter(s => s.id !== item.id)); // 乐观
    if (!token) return;
    deleteInventoryItem(token, item.sku, { reason: "在公司库存表手动删除" })
      .then(() => refetch())
      .catch(e2 => { setErr("删除失败:" + String(e2 && e2.message ? e2.message : e2)); refetch(); });
  }

  function addItem() {
    if (!newName || !newSku) return;
    const optimistic = { id: "s_" + Date.now(), name: newName, category: newCategory, qty: newQty, location: newLocation, sku: newSku, note: newNote, isSample: newIsSample };
    setStock(prev => [optimistic, ...prev]); // 乐观
    setNewName(""); setNewSku(""); setNewNote(""); setNewQty(0); setNewIsSample(false);
    setShowAdd(false);
    if (!token) return;
    createInventoryItem(token, {
      sku: newSku, name: optimistic.name, category: optimistic.category, qty: optimistic.qty,
      location: optimistic.location, note: optimistic.note, is_sample: optimistic.isSample,
      reason: "在公司库存表手动新建",
    })
      .then(() => refetch())
      .catch(e2 => { setErr("新建失败:" + String(e2 && e2.message ? e2.message : e2)); refetch(); });
  }

  // ── 调动记录视图 ─────────────────────────────────────────────────────────
  function renderMovements() {
    return e("div", { className: "flex-1 overflow-y-auto" },
      movLoading
        ? e("div", { className: "text-center py-12 text-[11px] text-slate-500" }, "加载调动记录中…")
        : movements.length === 0
          ? e("div", { className: "text-center py-12 text-[11px] text-slate-500" }, movFocusSku ? "该产品暂无调动记录" : "暂无调动记录")
          : e("table", { className: "w-full text-[11px]" },
              e("thead", { className: "sticky top-0 bg-[#0b1220] z-10" },
                e("tr", { className: "text-[9.5px] text-slate-500 uppercase tracking-wider" },
                  e("th", { className: "text-left px-2 py-2 font-medium" }, "时间"),
                  e("th", { className: "text-left px-2 py-2 font-medium" }, "调整者"),
                  e("th", { className: "text-left px-2 py-2 font-medium" }, "产品 (SKU)"),
                  e("th", { className: "text-center px-2 py-2 font-medium" }, "动作"),
                  e("th", { className: "text-center px-2 py-2 font-medium" }, "数量变化"),
                  e("th", { className: "text-center px-2 py-2 font-medium" }, "前 → 后"),
                  e("th", { className: "text-left px-2 py-2 font-medium" }, "事由")
                )
              ),
              e("tbody", null,
                movements.map(m => {
                  const meta = ACTION_META[m.action] || { label: m.action, color: "#94a3b8" };
                  const delta = m.delta_qty;
                  const newQty = m.new_qty;
                  const before = (typeof delta === "number" && typeof newQty === "number") ? (newQty - delta) : null;
                  return e("tr", { key: m.id, className: "border-t border-white/[0.04] hover:bg-white/[0.012]" },
                    e("td", { className: "px-2 py-2 text-[10px] text-slate-400 whitespace-nowrap" }, fmtTime(m.created_at)),
                    e("td", { className: "px-2 py-2 text-[10.5px] text-slate-300" },
                      m.moved_by_staff_id != null ? ("#" + m.moved_by_staff_id) : "系统"),
                    e("td", { className: "px-2 py-2 text-[10px] text-slate-400 font-mono" }, m.inventory_sku),
                    e("td", { className: "px-2 py-2 text-center" },
                      e("span", { className: "text-[9.5px] px-1.5 py-0.5 rounded font-medium", style: { background: meta.color + "20", color: meta.color } }, meta.label)
                    ),
                    e("td", { className: "px-2 py-2 text-center tabular-nums" },
                      typeof delta === "number"
                        ? e("span", { className: delta > 0 ? "text-emerald-300" : delta < 0 ? "text-red-300" : "text-slate-400" },
                            delta > 0 ? ("+" + delta) : String(delta))
                        : e("span", { className: "text-slate-600" }, "—")
                    ),
                    e("td", { className: "px-2 py-2 text-center text-[10.5px] text-slate-300 tabular-nums" },
                      before != null ? (before + " → " + newQty) : (typeof newQty === "number" ? ("→ " + newQty) : "—")
                    ),
                    e("td", { className: "px-2 py-2 text-[10px] text-slate-400" }, m.reason || "—")
                  );
                })
              )
            )
    );
  }

  // ── 库存表视图 ───────────────────────────────────────────────────────────
  function renderStock() {
    return e(React.Fragment, null,
      // 搜索 + 筛选 + 添加
      e("div", { className: "flex items-center gap-2 mb-3" },
        e("div", { className: "relative flex-1" },
          e(Search, { size: 12, className: "absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" }),
          e("input", { type: "text", value: search, onChange: ev => setSearch(ev.target.value), placeholder: "搜索名称 / SKU",
            className: "w-full pl-8 pr-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/40" })
        ),
        e("select", { value: catFilter, onChange: ev => setCatFilter(ev.target.value),
          className: "px-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-slate-300 focus:outline-none focus:border-emerald-500/40" },
          e("option", { value: "all", style: { background: "#0a0a0d" } }, "全部类别"),
          Object.entries(PRODUCT_CATEGORIES).map(([k, c]) => e("option", { key: k, value: k, style: { background: "#0a0a0d" } }, c.label))
        ),
        e("button", { onClick: () => setShowAdd(!showAdd),
          className: "px-2.5 py-1.5 rounded-md bg-emerald-500/90 hover:bg-emerald-500 text-white text-[11px] font-medium flex items-center gap-1"
        }, e(Plus, { size: 11 }), showAdd ? "取消" : "添加产品")
      ),

      // 添加表单 (inline)
      showAdd && e("div", { className: "rounded-lg border border-emerald-500/30 bg-emerald-500/[0.04] p-3 mb-3" },
        e("div", { className: "text-[10.5px] text-emerald-200 font-medium mb-2" }, "新建库存项"),
        e("div", { className: "grid grid-cols-2 lg:grid-cols-3 gap-2" },
          e("input", { type: "text", value: newName, onChange: ev => setNewName(ev.target.value), placeholder: "名称 (例: Viltrox 28mm F1.8 Pro)",
            className: "col-span-2 px-2.5 py-1.5 rounded bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/40" }),
          e("input", { type: "text", value: newSku, onChange: ev => setNewSku(ev.target.value), placeholder: "SKU (例: VTX-28-PRO)",
            className: "px-2.5 py-1.5 rounded bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/40 font-mono" }),
          e("select", { value: newCategory, onChange: ev => setNewCategory(ev.target.value),
            className: "px-2.5 py-1.5 rounded bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-emerald-500/40" },
            Object.entries(PRODUCT_CATEGORIES).map(([k, c]) => e("option", { key: k, value: k, style: { background: "#0a0a0d" } }, c.label))
          ),
          e("input", { type: "number", value: newQty, onChange: ev => setNewQty(parseInt(ev.target.value)||0), placeholder: "数量",
            className: "px-2.5 py-1.5 rounded bg-white/[0.02] border border-white/[0.06] text-[11px] text-white tabular-nums focus:outline-none focus:border-emerald-500/40" }),
          e("input", { type: "text", value: newLocation, onChange: ev => setNewLocation(ev.target.value), placeholder: "位置",
            className: "px-2.5 py-1.5 rounded bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-emerald-500/40" }),
          e("input", { type: "text", value: newNote, onChange: ev => setNewNote(ev.target.value), placeholder: "备注 (可选)",
            className: "col-span-2 lg:col-span-3 px-2.5 py-1.5 rounded bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/40" })
        ),
        e("div", { className: "flex items-center justify-between mt-2" },
          e("label", { className: "flex items-center gap-1.5 text-[10.5px] text-slate-300 cursor-pointer" },
            e("input", { type: "checkbox", checked: newIsSample, onChange: ev => setNewIsSample(ev.target.checked), className: "accent-emerald-500" }),
            "样品库存 (用于展会演示)"
          ),
          e("button", { onClick: addItem, disabled: !newName || !newSku,
            className: `px-3 py-1 rounded text-[10.5px] font-medium ${newName && newSku ? "bg-emerald-500 hover:bg-emerald-400 text-white" : "bg-white/[0.05] text-slate-600 cursor-not-allowed"}`
          }, "保存")
        )
      ),

      // 列表
      e("div", { className: "flex-1 overflow-y-auto" },
        filtered.length === 0
          ? e("div", { className: "text-center py-12 text-[11px] text-slate-500" }, "没有匹配的库存项")
          : e("table", { className: "w-full text-[11px]" },
              e("thead", { className: "sticky top-0 bg-[#0b1220] z-10" },
                e("tr", { className: "text-[9.5px] text-slate-500 uppercase tracking-wider" },
                  e("th", { className: "text-left px-2 py-2 font-medium" }, "产品"),
                  e("th", { className: "text-left px-2 py-2 font-medium" }, "SKU"),
                  e("th", { className: "text-left px-2 py-2 font-medium" }, "类别"),
                  e("th", { className: "text-center px-2 py-2 font-medium" }, "库存"),
                  e("th", { className: "text-left px-2 py-2 font-medium" }, "位置"),
                  e("th", { className: "text-left px-2 py-2 font-medium" }, "备注"),
                  e("th", { className: "w-16" })
                )
              ),
              e("tbody", null,
                filtered.map(s => {
                  const cat = PRODUCT_CATEGORIES[s.category];
                  const CI = cat?.icon || Package;
                  const editing = editingId === s.id;
                  return e("tr", { key: s.id, className: "border-t border-white/[0.04] hover:bg-white/[0.012]" },
                    e("td", { className: "px-2 py-2" },
                      e("div", { className: "flex items-center gap-2" },
                        e(CI, { size: 12, style: { color: cat?.color || "#94a3b8" } }),
                        e("div", null,
                          e("div", { className: "text-[11.5px] text-white font-medium flex items-center gap-1.5" },
                            s.name,
                            s.isSample && e("span", { className: "text-[9px] px-1 py-0.5 rounded bg-purple-500/15 text-purple-300" }, "样品")
                          )
                        )
                      )
                    ),
                    e("td", { className: "px-2 py-2 text-[10px] text-slate-400 font-mono" }, s.sku),
                    e("td", { className: "px-2 py-2" },
                      cat && e("span", { className: "text-[9.5px] px-1.5 py-0.5 rounded font-medium", style: { background: cat.color + "20", color: cat.color } }, cat.label)
                    ),
                    e("td", { className: "px-2 py-2" },
                      e("div", { className: "flex items-center justify-center gap-1" },
                        e("button", { onClick: () => updateQty(s, -1), className: "w-5 h-5 rounded text-[11px] hover:bg-white/[0.05] text-slate-500" }, "−"),
                        e("span", { className: `text-[12px] font-bold tabular-nums w-8 text-center ${s.qty === 0 ? "text-red-300" : s.qty < 5 ? "text-amber-300" : "text-white"}` }, s.qty),
                        e("button", { onClick: () => updateQty(s, 1), className: "w-5 h-5 rounded text-[11px] hover:bg-white/[0.05] text-slate-500" }, "+")
                      )
                    ),
                    e("td", { className: "px-2 py-2" },
                      editing
                        ? e("input", { type: "text", value: s.location, onChange: ev => updateField(s, "location", ev.target.value),
                            className: "w-full px-1.5 py-0.5 rounded bg-white/[0.02] border border-white/[0.06] text-[10.5px] text-white focus:outline-none focus:border-emerald-500/40" })
                        : e("span", { className: "text-[10.5px] text-slate-300" }, s.location)
                    ),
                    e("td", { className: "px-2 py-2 text-[10px] text-slate-500" }, s.note || "—"),
                    e("td", { className: "px-2 py-2" },
                      e("div", { className: "flex items-center gap-0.5" },
                        e("button", { onClick: () => openMovements(s.sku), className: "p-1 rounded hover:bg-white/[0.05] text-slate-500 hover:text-cyan-300", title: "查看该产品调动记录" },
                          e(History, { size: 11 })
                        ),
                        e("button", { onClick: () => setEditingId(editing ? null : s.id), className: "p-1 rounded hover:bg-white/[0.05] text-slate-500 hover:text-purple-300", title: "编辑" },
                          e(Edit3, { size: 10 })
                        ),
                        e("button", { onClick: () => deleteItem(s), className: "p-1 rounded hover:bg-red-500/15 text-slate-500 hover:text-red-300", title: "删除" },
                          e(X, { size: 11 })
                        )
                      )
                    )
                  );
                })
              )
            )
      )
    );
  }

  return e("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4", onClick: onClose },
    e("div", { className: "rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-4xl max-h-[92vh] p-5 flex flex-col", onClick: ev => ev.stopPropagation() },
      e("div", { className: "flex items-center justify-between mb-4" },
        e("div", { className: "flex items-center gap-2.5" },
          e("div", { className: "w-9 h-9 rounded-lg bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center" },
            e(Package, { size: 16, className: "text-emerald-300" })
          ),
          e("div", null,
            e("h3", { className: "text-[14px] font-semibold text-white" }, "公司库存表"),
            e("p", { className: "text-[10.5px] text-slate-500 mt-0.5" }, "样品库 + 量产库存 + 配件 + 设备 · 跨 Event 共享 · 真后端 · 每次变更留痕")
          )
        ),
        e("button", { onClick: onClose, className: "text-slate-500 hover:text-white" }, e(X, { size: 16 }))
      ),

      // 视图切换:库存表 / 调动记录
      e("div", { className: "flex items-center gap-0.5 rounded-md border border-white/[0.06] p-0.5 mb-3 w-fit" },
        e("button", {
          onClick: () => setView("stock"),
          className: `px-3 py-1 rounded text-[10.5px] font-medium flex items-center gap-1.5 transition-all ${view === "stock" ? "bg-emerald-500/20 text-emerald-200" : "text-slate-500 hover:text-slate-300"}`
        }, e(Package, { size: 11 }), "库存表"),
        e("button", {
          onClick: () => openMovements(null),
          className: `px-3 py-1 rounded text-[10.5px] font-medium flex items-center gap-1.5 transition-all ${view === "movements" ? "bg-cyan-500/20 text-cyan-200" : "text-slate-500 hover:text-slate-300"}`
        }, e(History, { size: 11 }), "📋 调动记录")
      ),

      // 聚焦某 SKU 的调动记录时的返回条
      view === "movements" && movFocusSku && e("div", { className: "flex items-center gap-2 mb-2 text-[10.5px]" },
        e("span", { className: "text-slate-500" }, "仅看 "),
        e("span", { className: "text-cyan-300 font-mono" }, movFocusSku),
        e("button", { onClick: () => openMovements(null), className: "text-slate-500 hover:text-slate-300 underline" }, "查看全部调动记录")
      ),

      err && e("div", { className: "mb-2 px-3 py-1.5 rounded-md border border-rose-500/30 bg-rose-500/10 text-[10.5px] text-rose-200" }, "⚠ ", err),

      view === "stock" ? renderStock() : renderMovements(),

      e("div", { className: "flex items-center justify-between pt-3 mt-3 border-t border-white/[0.05]" },
        view === "stock"
          ? e("div", { className: "text-[10px] text-slate-500" },
              "共 ", e("span", { className: "text-white" }, stock.length), " 项 · 样品 ",
              e("span", { className: "text-purple-300" }, stock.filter(s => s.isSample).length),
              " · 缺货 ",
              e("span", { className: "text-red-300" }, stock.filter(s => s.qty === 0).length)
            )
          : e("div", { className: "text-[10px] text-slate-500" },
              "共 ", e("span", { className: "text-white" }, movements.length), " 条调动流水"
            ),
        e("button", { onClick: onClose, className: "px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "完成")
      )
    )
  );
}
