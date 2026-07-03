import React, { useEffect, useRef, useState } from "react";
import { Camera, Loader2, X } from "lucide-react";
import { uploadMarketingEvidenceFile } from "../../../../../services/vkpi/evidence-api";
import { enqueueEventInvoiceExtract } from "../../../../../services/vkpi/events-api";
import { getInvoiceExtract } from "../../../../../services/vkpi/projects-api";
import { EXPENSE_CATEGORIES } from "../shared/constants";
import type { UiStaff } from "../shared/types";

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
  staff?: UiStaff[];
  defaultPaidBy?: string;
  // E2 发票 AI 识别:传 token+eventId 才启用(复用项目侧 invoice-extract 队列管线);缺任一保持诚实禁用。
  token?: string;
  eventId?: string;
  onClose: () => void;
  onSubmit: (data: ExpenseFormSubmit) => void;
}

export default function ExpenseEntryModal({ staff = [], defaultPaidBy = "", token = "", eventId = "", onClose, onSubmit }: ExpenseEntryModalProps) {
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState("travel");
  const [date, setDate] = useState("");
  const [description, setDescription] = useState("");
  // 付款人 = 真实员工姓名(费用流水按 paidBy===currentUser.name 过滤,故存姓名);默认当前用户。
  const [paidBy, setPaidBy] = useState(defaultPaidBy || (staff[0] ? String(staff[0].name) : ""));
  const [paymentMethod, setPaymentMethod] = useState("company_card");
  const [reimbursementStatus, setReimbursementStatus] = useState("n/a");

  // 【E2】发票 AI 识别真接线(2026-07-03):上传 → 入队(events invoice-extract)→ 轮询产物 → 回填金额/日期。
  // 全链真实:LLM 经 apify_jobs 队列(与项目合同发票同管线),失败/超时如实报错,绝不假装成功。
  const aiEnabled = Boolean(token && eventId);
  const [invoiceState, setInvoiceState] = useState<"idle" | "uploading" | "extracting" | "done" | "failed">("idle");
  const [invoiceError, setInvoiceError] = useState("");
  const [invoiceNote, setInvoiceNote] = useState("");
  const invoiceFileRef = useRef<HTMLInputElement | null>(null);
  const cancelledRef = useRef(false);
  useEffect(() => () => { cancelledRef.current = true; }, []);

  const applyInvoiceResult = (result: Record<string, unknown>) => {
    // 后端 cache 产物把字段嵌在 result.extracted 下(invoice_extract_v1 schema);容错兼取顶层,
    // 防未来读端拍平后这里读空。
    const extracted = (result.extracted && typeof result.extracted === "object" && !Array.isArray(result.extracted)
      ? result.extracted
      : result) as Record<string, unknown>;
    const filled: string[] = [];
    const rawAmount = extracted.amount;
    const parsedAmount = rawAmount === null || rawAmount === undefined || rawAmount === "" ? NaN : Number(rawAmount);
    if (Number.isFinite(parsedAmount) && parsedAmount > 0) {
      setAmount(String(parsedAmount));
      filled.push("金额");
    }
    // 日期只认后端约定的 YYYY-MM-DD(prompt 规定;LLM 给不出就为空),格式不符不硬塞进 date input。
    const rawDate = String(extracted.invoice_date || "").trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(rawDate)) {
      setDate(rawDate);
      filled.push("日期");
    }
    const currency = String(extracted.currency || "").trim().toUpperCase();
    const currencyNote = currency && currency !== "USD" && currency !== "$" ? ` · 识别到货币 ${currency},表单按 USD 记账请自行换算` : "";
    if (!filled.length) {
      setInvoiceState("failed");
      setInvoiceError("发票解析完成,但没有识别出可回填的金额/日期——请手动填写。");
      return;
    }
    setInvoiceState("done");
    setInvoiceNote(`已从发票回填:${filled.join("、")}${currencyNote} · 请核对后保存`);
  };

  const runInvoiceExtract = async (file?: File | null) => {
    if (!file || !aiEnabled) return;
    if (!/\.(pdf|png|jpe?g|webp|gif)$/i.test(file.name)) {
      setInvoiceState("failed");
      setInvoiceError("只支持 PDF / PNG / JPG / WEBP / GIF 发票文件。");
      return;
    }
    setInvoiceError("");
    setInvoiceNote("");
    setInvoiceState("uploading");
    try {
      const uploaded = await uploadMarketingEvidenceFile(token, file, { entityType: "event", entityId: String(eventId), purpose: "invoice" });
      if (cancelledRef.current) return;
      const fileUrl = String((uploaded as Record<string, unknown>).file_url || "");
      if (!fileUrl) throw new Error("发票已上传但未返回文件 URL,无法发起识别。");
      const enq = await enqueueEventInvoiceExtract(token, String(eventId), fileUrl, file.name);
      if (cancelledRef.current) return;
      const extractKey = String(enq.extract_key || "");
      if (!extractKey) throw new Error("发票识别入队失败:后端未返回 extract_key。");
      setInvoiceState("extracting");
      // 5s × 18 = 90s 封顶;超时如实说,不假装成功(与项目侧发票回填同口径)。
      for (let i = 0; i < 18; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        if (cancelledRef.current) return;
        const resp = await getInvoiceExtract(token, extractKey);
        if (cancelledRef.current) return;
        const state = String(resp.state || resp.status || "");
        if (state === "ready") {
          applyInvoiceResult((resp.result || {}) as Record<string, unknown>);
          return;
        }
        if (state === "failed") throw new Error(String(resp.error || "发票识别失败(LLM 未产出结果)。"));
      }
      throw new Error("发票识别超时(90 秒)——任务仍在队列,完成后重开本窗上传同一文件可直接复用结果。");
    } catch (extractError) {
      if (cancelledRef.current) return;
      setInvoiceState("failed");
      setInvoiceError(extractError instanceof Error ? extractError.message : "发票识别失败");
    } finally {
      if (invoiceFileRef.current) invoiceFileRef.current.value = "";
    }
  };

  const invoiceBusy = invoiceState === "uploading" || invoiceState === "extracting";

  return e("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4", onClick: onClose },
    e("div", { className: "rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-lg p-5 max-h-[92vh] overflow-y-auto", onClick: (ev: React.MouseEvent) => ev.stopPropagation() },
      e("div", { className: "flex items-center justify-between mb-4" },
        e("div", null,
          e("h3", { className: "text-[14px] font-semibold text-white" }, "录入费用"),
          e("p", { className: "text-[10.5px] text-slate-500 mt-0.5" }, aiEnabled ? "上传发票 AI 回填,或手动填写金额 / 类目 / 付款人" : "手动填写金额 / 类目 / 付款人")
        ),
        e("button", { onClick: onClose, className: "text-slate-500 hover:text-white" }, e(X, { size: 16 }))
      ),

      // 【E2】发票 AI 识别:token+eventId 齐备时真接线(上传→队列 LLM 提取→回填金额/日期);
      // 缺 token/eventId(如未登录)保持诚实禁用,不留假交互。
      e("input", {
        ref: invoiceFileRef,
        type: "file",
        accept: ".pdf,.png,.jpg,.jpeg,.webp,.gif",
        className: "hidden",
        onChange: (ev: React.ChangeEvent<HTMLInputElement>) => { void runInvoiceExtract(ev.target.files && ev.target.files[0]); },
      }),
      e("div", {
        role: aiEnabled ? "button" : undefined,
        "aria-disabled": !aiEnabled || invoiceBusy,
        onClick: () => { if (aiEnabled && !invoiceBusy) invoiceFileRef.current?.click(); },
        className: `mb-2 rounded-xl border-2 border-dashed py-5 flex flex-col items-center gap-2 select-none transition-all ${
          aiEnabled
            ? invoiceBusy
              ? "border-purple-400/30 bg-purple-500/[0.04] cursor-wait"
              : "border-white/[0.1] bg-white/[0.015] cursor-pointer hover:border-purple-400/40 hover:bg-purple-500/[0.04]"
            : "border-white/[0.08] bg-white/[0.015] cursor-not-allowed opacity-70"
        }`
      },
        invoiceBusy
          ? e(Loader2, { size: 18, className: "text-purple-300 animate-spin" })
          : e(Camera, { size: 18, className: aiEnabled ? "text-purple-300" : "text-slate-500" }),
        e("div", { className: "text-[11.5px] font-medium " + (aiEnabled ? "text-slate-200" : "text-slate-400") },
          !aiEnabled
            ? "发票 AI 识别不可用 — 请手动填写"
            : invoiceState === "uploading"
              ? "发票上传中…"
              : invoiceState === "extracting"
                ? "AI 识别中(队列任务,最长约 90 秒)…"
                : "上传发票 → AI 识别回填金额 / 日期"
        ),
        e("div", { className: "text-[9.5px] text-slate-600" },
          aiEnabled ? "支持 PDF / PNG / JPG / WEBP / GIF · LLM 走队列,结果仅回填表单,保存前请核对" : "缺少登录态或活动 ID,无法发起识别"
        )
      ),
      invoiceState === "failed" && invoiceError && e("div", { className: "mb-2 px-3 py-2 rounded-lg border border-rose-500/30 bg-rose-500/10 text-[10.5px] text-rose-200" }, "⚠ ", invoiceError),
      invoiceState === "done" && invoiceNote && e("div", { className: "mb-2 px-3 py-2 rounded-lg border border-emerald-500/25 bg-emerald-500/10 text-[10.5px] text-emerald-200" }, invoiceNote),

      e("div", { className: "space-y-3 mt-2" },
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
            // 真实员工名单(mock 四人组退役);名单加载失败时退化为手输,不再提供假选项。
            staff.length > 0
              ? e("select", { value: paidBy, onChange: (ev: React.ChangeEvent<HTMLSelectElement>) => setPaidBy(ev.target.value),
                  className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40" },
                  // 兜底:默认值(当前用户)不在名单里也能显示/保留
                  paidBy && !staff.some(u => String(u.name) === paidBy) && e("option", { value: paidBy, style: { background: "#0a0a0d" } }, paidBy),
                  staff.map(u => e("option", { key: u.id, value: String(u.name), style: { background: "#0a0a0d" } }, u.name))
                )
              : e("input", { type: "text", value: paidBy, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setPaidBy(ev.target.value),
                  placeholder: "员工名单未加载 · 手动输入姓名",
                  className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" })
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
