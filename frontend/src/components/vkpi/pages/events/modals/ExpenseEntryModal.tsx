import React, { useState } from "react";
import { Camera, Sparkles, X } from "lucide-react";
import { TEAM } from "../data/team";
import { EXPENSE_CATEGORIES } from "../shared/constants";

const e = React.createElement;

export interface ExpenseFormSubmit {
  amount: number;
  category: string;
  date: string;
  description: string;
  paidBy: string;
  paymentMethod: string;
  reimbursementStatus: string;
}

interface ExpenseEntryModalProps {
  onClose: () => void;
  onSubmit: (data: ExpenseFormSubmit) => void;
}

export default function ExpenseEntryModal({ onClose, onSubmit }: ExpenseEntryModalProps) {
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState("travel");
  const [date, setDate] = useState("");
  const [description, setDescription] = useState("");
  const [paidBy, setPaidBy] = useState("Jianbo");
  const [paymentMethod, setPaymentMethod] = useState("company_card");
  const [reimbursementStatus, setReimbursementStatus] = useState("n/a");
  const [showAiPanel, setShowAiPanel] = useState(false);

  function handleFakeAiUpload() {
    // 假装 AI 识别
    setShowAiPanel(true);
    setTimeout(() => {
      setAmount("485");
      setCategory("food");
      setDate("2026-05-26");
      setDescription("Onsite catering · The Bowery NYC");
      setPaidBy("Maya");
      setShowAiPanel(false);
    }, 1500);
  }

  return e("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4", onClick: onClose },
    e("div", { className: "rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-lg p-5 max-h-[92vh] overflow-y-auto", onClick: (ev: React.MouseEvent) => ev.stopPropagation() },
      e("div", { className: "flex items-center justify-between mb-4" },
        e("div", null,
          e("h3", { className: "text-[14px] font-semibold text-white" }, "录入费用"),
          e("p", { className: "text-[10.5px] text-slate-500 mt-0.5" }, "拍照上传发票, LLM 自动识别金额 / 商家 / 类目")
        ),
        e("button", { onClick: onClose, className: "text-slate-500 hover:text-white" }, e(X, { size: 16 }))
      ),

      // AI 上传区
      e("div", {
        onClick: handleFakeAiUpload,
        className: "mb-4 rounded-xl border-2 border-dashed border-purple-500/30 bg-purple-500/[0.04] hover:bg-purple-500/[0.08] hover:border-purple-500/50 transition-all cursor-pointer py-5 flex flex-col items-center gap-2"
      },
        showAiPanel
          ? e(React.Fragment, null,
              e(Sparkles, { size: 18, className: "text-purple-300 animate-pulse" }),
              e("div", { className: "text-[11.5px] text-purple-200" }, "AI 正在识别发票..."),
              e("div", { className: "text-[9.5px] text-slate-400" }, "提取金额 / 商家 / 日期 / 类目")
            )
          : e(React.Fragment, null,
              e(Camera, { size: 18, className: "text-purple-300" }),
              e("div", { className: "text-[11.5px] text-white font-medium" }, "📷 拍照 / 上传发票"),
              e("div", { className: "text-[9.5px] text-slate-400" }, "自动识别")
            )
      ),

      e("div", { className: "space-y-3" },
        e("div", { className: "grid grid-cols-2 gap-3" },
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "金额 (USD)"),
            e("div", { className: "relative" },
              e("span", { className: "absolute left-3 top-1/2 -translate-y-1/2 text-[11px] text-slate-500" }, "$"),
              e("input", { type: "number", value: amount, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setAmount(ev.target.value), placeholder: "0",
                className: "w-full pl-6 pr-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11.5px] text-white tabular-nums focus:outline-none focus:border-purple-500/40" })
            )
          ),
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "日期"),
            e("input", { type: "date", value: date, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setDate(ev.target.value),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40 [color-scheme:dark]" })
          )
        ),

        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1.5 block" }, "类目"),
          e("div", { className: "grid grid-cols-5 gap-1.5" },
            Object.entries(EXPENSE_CATEGORIES).slice(0, 10).map(([k, cfg]) => {
              const I = cfg.icon;
              const active = category === k;
              return e("button", { key: k, onClick: () => setCategory(k),
                className: `py-1.5 rounded border flex flex-col items-center gap-0.5 transition-all ${active ? "" : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]"}`,
                style: active ? { borderColor: cfg.color + "60", background: cfg.color + "15" } : {}
              },
                e(I, { size: 11, style: { color: active ? cfg.color : "#94a3b8" } }),
                e("span", { className: "text-[8.5px]", style: { color: active ? cfg.color : "#94a3b8" } }, cfg.label)
              );
            })
          )
        ),

        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "描述"),
          e("input", { type: "text", value: description, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setDescription(ev.target.value),
            placeholder: "例: Maya 机票 SFO→LAX",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" })
        ),

        e("div", { className: "grid grid-cols-2 gap-3" },
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "付款人"),
            e("select", { value: paidBy, onChange: (ev: React.ChangeEvent<HTMLSelectElement>) => setPaidBy(ev.target.value),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40" },
              TEAM.map(u => e("option", { key: u.id, value: u.name, style: { background: "#0a0a0d" } }, u.name))
            )
          ),
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "付款方式"),
            e("select", { value: paymentMethod, onChange: (ev: React.ChangeEvent<HTMLSelectElement>) => setPaymentMethod(ev.target.value),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40" },
              ([
                ["company_card", "公司卡"],
                ["personal", "个人垫付"],
                ["wire_transfer", "对公转账"],
              ] as const).map(([v, l]) => e("option", { key: v, value: v, style: { background: "#0a0a0d" } }, l))
            )
          )
        ),

        paymentMethod === "personal" && e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "报销状态"),
          e("div", { className: "flex gap-1.5" },
            ([["pending", "未申请", "#94a3b8"], ["submitted", "已申请", "#fbbf24"], ["paid", "已报销", "#10b981"]] as const).map(([v, l, c]) =>
              e("button", { key: v, onClick: () => setReimbursementStatus(v),
                className: `flex-1 px-2 py-1 rounded text-[10.5px] border transition-all ${reimbursementStatus === v ? "" : "border-white/[0.06] bg-white/[0.02] text-slate-400 hover:bg-white/[0.04]"}`,
                style: reimbursementStatus === v ? { borderColor: c + "60", background: c + "15", color: c } : {}
              }, l)
            )
          )
        )
      ),

      e("div", { className: "flex items-center justify-end gap-2 mt-5 pt-4 border-t border-white/[0.05]" },
        e("button", { onClick: onClose, className: "px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "取消"),
        e("button", {
          disabled: !description || !(parseFloat(amount) > 0),
          onClick: () => onSubmit({ amount: parseFloat(amount) || 0, category, date, description, paidBy, paymentMethod, reimbursementStatus }),
          className: `px-3.5 py-1.5 rounded-md text-[11px] font-medium ${amount && description ? "bg-purple-500 hover:bg-purple-400 text-white" : "bg-white/[0.05] text-slate-600 cursor-not-allowed"}`
        }, "保存")
      )
    )
  );
}
