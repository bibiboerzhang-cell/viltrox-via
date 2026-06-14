import React, { useState } from "react";
import { Check, FileText, Plus, Receipt, X } from "lucide-react";
import {
  addEventExpense, deleteEventExpense, fromUiExpenseCreate,
} from "../../../../../services/vkpi/events-api";
import DeleteConfirmModal from "../modals/DeleteConfirmModal.js";
import ExpenseEntryModal from "../modals/ExpenseEntryModal.js";
import { EXPENSE_CATEGORIES } from "../shared/constants.js";
import { fmtMoney, fmtMoneyShort, sum } from "../shared/helpers.js";
import { ownerById } from "../shared/lookups.js";

const e = React.createElement;
export default function BudgetExpensesTab({ ev, currentUser, token, expenses: rawExpenses = [], loading, error, reload }) {
  const [showModal, setShowModal] = useState(false);
  const [scope, setScope] = useState("mine");  // mine | all (只 owner 能看 all)
  const [deleting, setDeleting] = useState(null);
  const [opError, setOpError] = useState("");
  const allExpenses = rawExpenses;
  const isOwner = ev.ownerId === currentUser.id;

  const onErr = (label) => (err) => {
    setOpError(label + ":" + String(err && err.message ? err.message : err));
    reload && reload();
  };

  function addExpense(data) {
    setShowModal(false);
    addEventExpense(token, ev.id, fromUiExpenseCreate(data))
      .then(() => reload && reload())
      .catch(onErr("录入费用失败"));
  }

  function removeExpense(id) {
    setDeleting(null);
    deleteEventExpense(token, ev.id, id)
      .then(() => reload && reload())
      .catch(onErr("删除费用失败"));
  }
  
  // 权限: 默认只看自己付的, owner 可切换看全部
  const expenses = scope === "all" && isOwner
    ? allExpenses
    : allExpenses.filter(exp => exp.paidBy === currentUser.name);
  
  const categoryEntries = Object.entries(ev.budgetByCategory);
  const totalSpent = sum(ev.budgetByCategory, "spent");
  const totalPlan = ev.budgetTotal;
  
  return e("div", { className: "p-5" },
    (error || opError) && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-rose-500/30 bg-rose-500/10 text-[11px] text-rose-200" }, "⚠ ", opError || error),
    loading && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-white/[0.06] bg-white/[0.02] text-[11px] text-slate-400" }, "加载费用中…"),
    e("div", { className: "grid grid-cols-2 gap-4" },
      // 左侧 - 预算表
      e("div", null,
        e("div", { className: "flex items-center justify-between mb-3" },
          e("h3", { className: "text-[12.5px] font-semibold text-white" }, "预算 vs 实际支出"),
          e("div", { className: "text-[10px] text-slate-400" },
            "总 ", e("span", { className: "text-white tabular-nums" }, fmtMoneyShort(totalSpent)), " / ", fmtMoneyShort(totalPlan),
            " · ", Math.round(totalSpent / totalPlan * 100), "%"
          )
        ),
        e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] overflow-hidden" },
          categoryEntries.filter(([k, v]) => v.plan > 0 || v.spent > 0).map(([k, v]) => {
            const cfg = EXPENSE_CATEGORIES[k];
            const Icon = cfg.icon;
            const pct = v.plan > 0 ? Math.round(v.spent / v.plan * 100) : 0;
            const overBudget = v.spent > v.plan;
            const isDone = v.spent >= v.plan && v.plan > 0;
            return e("div", { key: k, className: "px-3 py-2.5 border-b border-white/[0.04] last:border-0" },
              e("div", { className: "flex items-center justify-between mb-1.5" },
                e("div", { className: "flex items-center gap-2" },
                  e("div", { className: "w-6 h-6 rounded flex items-center justify-center", style: { background: cfg.color + "15" } },
                    e(Icon, { size: 11, style: { color: cfg.color } })
                  ),
                  e("span", { className: "text-[11px] text-white font-medium" }, cfg.label)
                ),
                e("div", { className: "text-[10.5px] tabular-nums" },
                  e("span", { className: overBudget ? "text-red-300" : "text-white" }, fmtMoneyShort(v.spent)),
                  e("span", { className: "text-slate-500" }, " / " + fmtMoneyShort(v.plan)),
                  overBudget && e("span", { className: "text-red-400 ml-1" }, "(超 ", fmtMoneyShort(v.spent - v.plan), ")"),
                  v.plan === 0 && e("span", { className: "text-amber-400 ml-1" }, "未预算")
                )
              ),
              e("div", { className: "h-1 rounded-full bg-white/[0.04] overflow-hidden" },
                e("div", { className: "h-full rounded-full transition-all", style: {
                  width: Math.min(100, pct) + "%",
                  background: overBudget ? "#ef4444" : isDone ? "#10b981" : pct > 80 ? "#fbbf24" : cfg.color
                } })
              ),
              e("div", { className: "flex items-center justify-between text-[9.5px] mt-1" },
                e("span", { className: "text-slate-500" }, "进度 ", pct, "%"),
                isDone && e("span", { className: "text-emerald-400 flex items-center gap-0.5" }, e(Check, { size: 9 }), "已完成"),
                v.spent === 0 && v.plan > 0 && e("span", { className: "text-slate-500" }, "待支出")
              )
            );
          })
        ),
        // total row
        e("div", { className: "mt-3 px-3 py-2 rounded-lg border border-purple-500/20 bg-purple-500/[0.05]" },
          e("div", { className: "flex items-center justify-between mb-1.5" },
            e("span", { className: "text-[11.5px] font-semibold text-white" }, "总计"),
            e("span", { className: "text-[12.5px] font-bold text-white tabular-nums" },
              fmtMoney(totalSpent), e("span", { className: "text-slate-500 text-[10px] font-normal" }, " / " + fmtMoney(totalPlan))
            )
          ),
          e("div", { className: "h-1.5 rounded-full bg-white/[0.04] overflow-hidden" },
            e("div", { className: "h-full rounded-full", style: {
              width: Math.min(100, totalSpent/totalPlan*100) + "%",
              background: "linear-gradient(90deg, #a855f7, #06b6d4)"
            } })
          ),
          e("div", { className: "text-[10px] text-slate-400 mt-1" },
            "已使用 ", Math.round(totalSpent/totalPlan*100), "% · 剩余 ", fmtMoney(totalPlan - totalSpent)
          )
        )
      ),
      
      // 右侧 - 费用流水
      e("div", null,
        e("div", { className: "flex items-center justify-between mb-3 flex-wrap gap-2" },
          e("div", { className: "flex items-center gap-2" },
            e("h3", { className: "text-[12.5px] font-semibold text-white" },
              "费用流水 ", e("span", { className: "text-[10px] text-slate-500 font-normal" }, "· ", expenses.length, " 笔")
            ),
            // 权限切换 (owner 才有 all)
            isOwner && e("div", { className: "flex items-center gap-0.5 rounded-md border border-white/[0.06] p-0.5" },
              [
                ["mine", "我的", currentUser.color],
                ["all",  "全部 (负责人视角)", "#a855f7"],
              ].map(([k, l, c]) => e("button", {
                key: k, onClick: () => setScope(k),
                className: `px-2 py-0.5 rounded text-[9.5px] font-medium transition-all ${scope === k ? "text-white" : "text-slate-500 hover:text-slate-300"}`,
                style: scope === k ? { background: c + "30" } : {}
              }, l))
            ),
            !isOwner && e("span", { className: "text-[9.5px] text-slate-500 px-2 py-0.5 rounded bg-white/[0.04]" },
              "仅我付款的 · ", ev.ownerName || ownerById(ev.ownerId).name, " 可见全部"
            )
          ),
          e("button", {
            onClick: () => setShowModal(true),
            className: "px-2.5 py-1 rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[10.5px] font-medium flex items-center gap-1"
          }, e(Plus, { size: 11 }), "录入费用")
        ),
        expenses.length === 0
          ? e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-8 text-center" },
              e(Receipt, { size: 22, className: "text-slate-600 mx-auto mb-2" }),
              e("div", { className: "text-[11px] text-slate-400" }, currentUser.name, " 还没有任何费用记录")
            )
          : e("div", { className: "space-y-1.5 max-h-[480px] overflow-y-auto pr-1" },
              expenses.map(exp => {
            const cfg = EXPENSE_CATEGORIES[exp.category] || EXPENSE_CATEGORIES.other;
            const Icon = cfg.icon;
            return e("div", { key: exp.id, className: "rounded-lg border border-white/[0.05] bg-white/[0.012] p-2.5 hover:bg-white/[0.025] transition-all group" },
              e("div", { className: "flex items-start gap-2.5" },
                e("div", { className: "w-7 h-7 rounded flex items-center justify-center shrink-0", style: { background: cfg.color + "15" } },
                  e(Icon, { size: 12, style: { color: cfg.color } })
                ),
                e("div", { className: "flex-1 min-w-0" },
                  e("div", { className: "flex items-center justify-between gap-2 mb-0.5" },
                    e("div", { className: "text-[11.5px] text-white font-medium truncate" }, exp.description),
                    e("div", { className: "flex items-center gap-1.5 shrink-0" },
                      e("div", { className: "text-[12px] font-bold text-white tabular-nums" }, fmtMoney(exp.amount)),
                      e("button", {
                        onClick: () => setDeleting({ id: exp.id, description: exp.description }),
                        className: "opacity-0 group-hover:opacity-100 text-slate-500 hover:text-red-300 p-0.5",
                        title: "删除费用"
                      }, e(X, { size: 11 }))
                    )
                  ),
                  e("div", { className: "flex items-center gap-2 text-[9.5px] text-slate-500" },
                    e("span", { className: "tabular-nums font-mono" }, exp.date),
                    e("span", null, "·"),
                    e("span", null, exp.paidBy),
                    e("span", null, "·"),
                    e("span", null,
                      exp.paymentMethod === "company_card" ? "公司卡" :
                      exp.paymentMethod === "personal"     ? "个人垫付" : "对公转账"
                    ),
                    exp.receipt && e(React.Fragment, null,
                      e("span", null, "·"),
                      e("span", { className: "text-cyan-300 flex items-center gap-0.5" },
                        e(FileText, { size: 9 }), "票据"
                      )
                    )
                  ),
                  exp.paymentMethod === "personal" && exp.reimbursementStatus !== "n/a" && e("div", { className: "mt-1" },
                    e("span", {
                      className: "text-[9.5px] px-1.5 py-0.5 rounded font-medium",
                      style: exp.reimbursementStatus === "submitted"
                        ? { background: "#fbbf2420", color: "#fbbf24" }
                        : { background: "#10b98120", color: "#10b981" }
                    },
                      exp.reimbursementStatus === "submitted" ? "已申请报销" : "已报销"
                    )
                  )
                )
              )
            );
          })
        )
      )
    ),
    
    showModal && e(ExpenseEntryModal, {
      onClose: () => setShowModal(false),
      onSubmit: addExpense,
    }),
    deleting && e(DeleteConfirmModal, {
      title: `删除费用 "${deleting.description}"?`,
      subtitle: "这条费用流水将从该 Event 移除 · 不可撤销",
      onClose: () => setDeleting(null),
      onConfirm: () => removeExpense(deleting.id),
    })
  );
}
