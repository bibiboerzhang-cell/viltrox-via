// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useRef, useState } from "react";
import { ImageIcon, X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";
import { submitV615Feedback } from "../../api";

const e = React.createElement;

export function FeedbackModal({ onClose, apiToken, onSubmitted }: any) {
  const { t } = useT();
  const fileInputRef = useRef<any>(null);
  const [category, setCategory] = useState("bug");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [screenshot, setScreenshot] = useState<any>(null);
  const onPickScreenshot = (event: any) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setStatus("只支持图片截图");
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      setStatus("截图请控制在 2MB 内");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setScreenshot({
        name: file.name,
        type: file.type,
        size: file.size,
        dataUrl: String(reader.result || ""),
      });
      setStatus("截图已添加");
    };
    reader.onerror = () => setStatus("截图读取失败");
    reader.readAsDataURL(file);
  };
  const submit = async () => {
    if (!apiToken) {
      setStatus("未登录，无法提交到后端");
      return;
    }
    if (!title.trim() || !description.trim()) {
      setStatus("请填写标题和详细描述");
      return;
    }
    setSubmitting(true);
    setStatus("");
    try {
      const result = await submitV615Feedback(apiToken, {
        category,
        title: title.trim(),
        description: description.trim(),
        screenshot,
        metadata: { source_page: "v615Replica" },
      });
      onSubmitted && onSubmitted(result, { category, title: title.trim(), description: description.trim(), screenshot });
      setStatus("已提交，并同步到管理通知");
      setTimeout(onClose, 700);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  };
  return e(CenterModal, { onClose, maxWidth: "md" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("div", null,
        e("h2", { className: "text-sm font-semibold text-white" }, "提交反馈 / 报 bug"),
        e("div", { className: "text-[10px] text-slate-500 mt-0.5" }, "POST /api/admin/vkpi/feedback")
      ),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    e("div", { className: "p-5 space-y-3" },
      e("div", null,
        e("label", { className: "text-[10px] text-slate-500 mb-1.5 block" }, t("类型")),
        e("div", { className: "flex gap-2" },
          [
            { key: "bug",     label: "🐛 Bug",        color: "#ef4444" },
            { key: "feature", label: "💡 功能建议",   color: "#a855f7" },
            { key: "ux",      label: "🎨 UX 优化",    color: "#06b6d4" },
            { key: "other",   label: "其他",          color: "#64748b" },
          ].map((c: any) => e("button", {
            key: c.key, onClick: () => setCategory(c.key),
            className: "flex-1 text-[10px] py-1.5 rounded-md border transition-colors",
            style: category === c.key ? {
              borderColor: c.color, background: c.color + "20", color: "#fff"
            } : { borderColor: "rgba(255,255,255,0.08)", color: "rgba(255,255,255,0.5)" }
          }, c.label))
        )
      ),
      e("div", null,
        e("label", { className: "text-[10px] text-slate-500 mb-1.5 block" }, "标题"),
        e("input", { type: "text", value: title, onChange: (event: any) => setTitle(event.target.value), placeholder: "简短描述...", className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-[12px] text-white outline-none" })
      ),
      e("div", null,
        e("label", { className: "text-[10px] text-slate-500 mb-1.5 block" }, "详细描述"),
        e("textarea", { value: description, onChange: (event: any) => setDescription(event.target.value), placeholder: "请描述操作步骤、期望结果、实际结果...", rows: 5, className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-2 text-[11px] text-white outline-none resize-none" })
      ),
      e("div", null,
        e("input", {
          id: "v615-feedback-screenshot-input",
          ref: fileInputRef,
          type: "file",
          accept: "image/*",
          style: { position: "absolute", width: 1, height: 1, opacity: 0, pointerEvents: "none" },
          onChange: onPickScreenshot,
        }),
        e("div", { className: "flex items-center gap-2" },
          e("button", {
            type: "button",
            onClick: () => fileInputRef.current?.click(),
            className: "flex items-center gap-1 rounded-md border border-purple-500/20 bg-purple-500/[0.06] px-2 py-1 text-[10px] text-purple-300 hover:bg-purple-500/[0.1] hover:text-purple-200"
          },
            e(ImageIcon, { size: 11 }), screenshot ? t("更换截图") : t("添加截图")),
          screenshot && e("div", { className: "flex items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[10px] text-slate-300" },
            e("span", { className: "max-w-[180px] truncate" }, screenshot.name),
            e("button", { type: "button", onClick: () => setScreenshot(null), className: "text-slate-500 hover:text-white" }, "×")
          )
        ),
        screenshot && e("div", { className: "mt-2 flex items-center gap-2 rounded-md border border-white/[0.06] bg-white/[0.025] p-2" },
          e("img", {
            src: screenshot.dataUrl,
            alt: screenshot.name,
            className: "h-12 w-16 rounded object-cover"
          }),
          e("div", { className: "min-w-0 text-[10px] text-slate-400" },
            e("div", { className: "truncate text-slate-300" }, screenshot.name),
            e("div", null, `${Math.ceil((screenshot.size || 0) / 1024)} KB · 将随反馈进入管理通知`)
          )
        )
      ),
      status && e("div", { className: "text-[10px] text-slate-400" }, status)
    ),
    e("div", { className: "px-5 py-2.5 border-t border-white/[0.06] flex justify-end gap-2" },
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 px-3 py-1 text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "取消"),
      e("button", { onClick: submit, disabled: submitting, className: "rounded-md bg-purple-600 hover:bg-purple-500 px-3 py-1 text-[11px] font-medium text-white disabled:opacity-50" }, submitting ? "提交中..." : "提交反馈")
    )
  );
}
