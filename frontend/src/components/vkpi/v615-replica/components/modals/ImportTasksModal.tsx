// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { FileText, MessageCircle, Plus, X } from "lucide-react";
import { CenterModal } from "./CenterModal";

const e = React.createElement;

export function ImportTasksModal({ onClose, t }) {
  const [mode, setMode] = useState("upload");
  return e(CenterModal, { onClose, maxWidth: "lg" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("div", null,
        e("h2", { className: "text-sm font-semibold text-white" }, t("+ 导入任务")),
        e("div", { className: "text-[10px] text-slate-500 mt-0.5" }, t("把表格 / 邮件 / 文档拆解成工作提醒"))
      ),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    // Mode tabs
    e("div", { className: "px-5 pt-3 flex gap-3 border-b border-white/[0.04]" },
      [
        { key: "upload", label: "上传文件",   icon: FileText },
        { key: "email",  label: "邮箱关联",   icon: MessageCircle },
        { key: "link",   label: "链接导入",   icon: Globe2 },
      ].map(x => e("button", {
        key: x.key, onClick: () => setMode(x.key),
        className: "flex items-center gap-1.5 text-[11px] py-2 px-0.5 border-b-2",
        style: { borderColor: mode === x.key ? "#a855f7" : "transparent", color: mode === x.key ? "#fff" : "rgba(255,255,255,0.5)" }
      }, e(x.icon, { size: 11 }), x.label))
    ),
    e("div", { className: "p-5" },
      mode === "upload" && e("div", { className: "space-y-3" },
        // Drag drop area
        e("div", { className: "rounded-xl border-2 border-dashed border-white/[0.1] p-8 text-center hover:border-purple-500/30 hover:bg-purple-500/[0.03] cursor-pointer transition-colors" },
          e(Plus, { size: 28, className: "mx-auto text-slate-500 mb-2" }),
          e("div", { className: "text-[12px] text-white mb-1" }, t("拖拽文件到这里 或 点击选择")),
          e("div", { className: "text-[10px] text-slate-500" }, t("Excel (.xlsx, .xls) · CSV · PDF · 最大 10MB"))
        ),
        // 后端解析方案
        e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3" },
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, t("解析方案")),
          e("div", { className: "space-y-1.5 text-[11px] text-slate-300" },
            e("div", { className: "flex gap-2" }, e("span", { style: { color: "#10b981" } }, "·"), t("Excel/CSV → openpyxl 直接读 · 列:任务标题/负责人/截止日/备注")),
            e("div", { className: "flex gap-2" }, e("span", { style: { color: "#a855f7" } }, "·"), t("PDF → pypdf 抽文本 + LLM 提取任务点(可关闭 LLM 降级用规则)")),
            e("div", { className: "flex gap-2" }, e("span", { style: { color: "#06b6d4" } }, "·"), t("解析后预览候选 → 你选哪几行入提醒"))
          )
        )
      ),
      mode === "email" && e("div", { className: "space-y-3" },
        e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3" },
          e("div", { className: "text-[10px] text-slate-500 mb-2" }, t("你的专属转发邮箱")),
          e("div", { className: "flex items-center gap-2" },
            e("code", { className: "flex-1 text-[11px] text-emerald-300 bg-emerald-500/[0.06] border border-emerald-500/[0.15] rounded px-3 py-2" }, 
              "kevin+todo@v-kpi.viltrox.com"),
            e("button", { className: "rounded-md border border-white/[0.12] px-3 py-2 text-[10px] text-slate-300 hover:bg-white/[0.04]" }, t("复制"))
          )
        ),
        e("div", { className: "text-[11px] text-slate-300 space-y-1.5" },
          e("div", null, t("把要跟进的邮件转发到这个地址,会自动:")),
          e("div", { className: "pl-3 text-slate-400 space-y-1" },
            e("div", null, "1. IMAP 监听 inbox(MailGun/SendGrid receive webhook)"),
            e("div", null, "2. LLM 提取「这封邮件需要做什么」"),
            e("div", null, "3. 生成工作提醒(source: 邮件)")
          ),
          e("div", { className: "text-[10px] text-amber-300 mt-2" }, t("工作量:1.5 天 · 不需要 OAuth"))
        )
      ),
      mode === "link" && e("div", { className: "space-y-3" },
        e("input", { 
          type: "text", placeholder: "Notion / Feishu / Google Doc 链接",
          className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-2 text-[12px] text-white outline-none"
        }),
        e("div", { className: "text-[11px] text-slate-400" }, t("粘贴链接,后端 fetch 内容 + LLM 提取任务 · 后期支持")),
        e("button", { className: "w-full rounded-md bg-purple-600 hover:bg-purple-500 px-3 py-2 text-[11px] font-medium text-white opacity-50 cursor-not-allowed" }, t("解析(预留)"))
      )
    )
  );
}
