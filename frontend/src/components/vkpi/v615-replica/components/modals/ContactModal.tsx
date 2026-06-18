// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { AlertTriangle, Check, Copy, ExternalLink, Loader2, Sparkles, Wand2, X } from "lucide-react";
import { KPAvatar } from "../KPAvatar";
import { genEmailBody, genEmailSubject } from "../../lib/email";
import { apiFetch } from "../../../../../services/http";

const e = React.createElement;

// d6:关窗丢稿修复——模块级内存草稿(按 KOL id),重开同一 KOL 自动恢复;
// 刷新页面即清(内存级,后端联系人写端点落地前的最低保障)。
const CONTACT_DRAFTS = new Map();

export function ContactModal({ item, onClose, apiToken }: any) {
  if (!item) return null;
  const hasEmail = !!item.email;
  const recommended = item.recommended_product_lines || [];
  const defaultProduct = recommended[0] || "Viltrox 镜头";
  const draft = CONTACT_DRAFTS.get(item.id) || null;
  const [tab, setTab] = useState(draft?.tab ?? (hasEmail ? "email" : "add"));
  const [selectedProduct, setSelectedProduct] = useState(draft?.selectedProduct ?? defaultProduct);
  const [customProduct, setCustomProduct] = useState(draft?.customProduct ?? "");
  const [showCustom, setShowCustom] = useState(draft?.showCustom ?? false);
  const [templateApplying, setTemplateApplying] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [subject, setSubject] = useState(draft?.subject ?? (hasEmail ? genEmailSubject(defaultProduct, item) : ""));
  const [body, setBody] = useState(draft?.body ?? (hasEmail ? genEmailBody(defaultProduct, item) : ""));
  const [newEmail, setNewEmail] = useState(draft?.newEmail ?? "");
  const [newPlatform, setNewPlatform] = useState(draft?.newPlatform ?? "ig_dm");
  const [newHandle, setNewHandle] = useState(draft?.newHandle ?? "");
  React.useEffect(() => {
    CONTACT_DRAFTS.set(item.id, { tab, selectedProduct, customProduct, showCustom, subject, body, newEmail, newPlatform, newHandle });
  }, [item.id, tab, selectedProduct, customProduct, showCustom, subject, body, newEmail, newPlatform, newHandle]);
  
  // 切换产品时,主题立即同步;正文需要用户主动点本地模板重写才覆盖。
  const onPickProduct = (p: any) => {
    setSelectedProduct(p);
    setShowCustom(false);
    setSubject(genEmailSubject(p, item));
  };
  const applyLocalTemplate = () => {
    setTemplateApplying(true);
    setTimeout(() => {
      const p = showCustom && customProduct ? customProduct : selectedProduct;
      setSubject(genEmailSubject(p, item));
      setBody(genEmailBody(p, item));
      setTemplateApplying(false);
    }, 700);
  };
  // AI 优化:把当前草稿交给 LLM 润色成更自然/更高回复率的英文外联(只产文案,不外发)。失败保留原文。
  const aiOptimize = async () => {
    if (!apiToken || optimizing) return;
    setOptimizing(true);
    try {
      const prod = showCustom && customProduct ? customProduct : selectedProduct;
      const res: any = await apiFetch(
        "/api/admin/vkpi/kol-pool/outreach-optimize",
        { method: "POST", body: JSON.stringify({ kol_pool_id: item.id, kol_name: item.display_name || item.handle, product: prod, subject, body }) },
        apiToken,
      );
      if (res && res.subject) setSubject(String(res.subject));
      if (res && res.body) setBody(String(res.body));
    } catch (_e) { /* 失败保留原文 */ }
    finally { setOptimizing(false); }
  };
  // 复制主题+正文,去自己邮箱/DM 手动发送(本系统不替你外发=安全)。
  const copyEmail = async () => {
    try {
      await navigator.clipboard.writeText(`Subject: ${subject}\n\n${body}`);
      setCopied(true); setTimeout(() => setCopied(false), 1500);
    } catch (_e) { /* ignore */ }
  };
  
  return e("div", {
    className: "v615-modal fixed inset-0 z-[60] flex items-center justify-center p-4",
    style: { background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" },
    onClick: onClose
  },
    e("div", {
      className: "w-full max-w-[520px] rounded-xl border border-white/[0.08] bg-[#0a1020] shadow-2xl overflow-hidden",
      onClick: (ev: any) => ev.stopPropagation()
    },
      // Header
      e("div", { className: "px-5 py-3 border-b border-white/[0.06] flex items-center gap-3" },
        e(KPAvatar, { name: item.display_name || item.handle, color: item.avatar_color, size: 36 }),
        e("div", { className: "flex-1 min-w-0" },
          e("h3", { className: "text-[13px] font-semibold text-white" }, hasEmail ? "发起合作邀请" : "添加联系方式"),
          e("p", { className: "text-[10px] text-slate-500" }, item.handle, " · ", item.display_name)
        ),
        e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" },
          e(X, { size: 13 })
        )
      ),
      // Tab(只有 has email 才显示)
      hasEmail && e("div", { className: "px-5 pt-3 flex items-center gap-1 border-b border-white/[0.04]" },
        ["email", "add"].map(t => e("button", {
          key: t,
          onClick: () => setTab(t),
          className: "px-3 py-1.5 text-[11px] border-b-2 transition-colors",
          style: tab === t 
            ? { borderColor: "#a855f7", color: "#fff" }
            : { borderColor: "transparent", color: "#94a3b8" }
        }, t === "email" ? "邮件邀请" : "添加额外渠道"))
      ),
      // Email tab
      tab === "email" && hasEmail && e("div", { className: "px-5 py-4 space-y-3" },
        e("div", { className: "flex items-center gap-2 text-[11px]" },
          e("span", { className: "text-slate-500 w-[40px]" }, "To"),
          e("span", { className: "text-cyan-300 flex-1 px-2 py-1 rounded bg-cyan-500/[0.05] border border-cyan-500/20" }, item.email),
        ),
        e("div", { className: "flex items-center gap-2 text-[11px]" },
          e("span", { className: "text-slate-500 w-[40px]" }, "From"),
          e("span", { className: "text-slate-300 flex-1 px-2 py-1 rounded bg-white/[0.02] border border-white/[0.06]" }, "jianbo@viltrox.com"),
        ),
        // ── 产品选择器(chip + 自定义) ──
        e("div", { className: "flex items-start gap-2 text-[11px]" },
          e("span", { className: "text-slate-500 w-[40px] pt-1" }, "产品"),
          e("div", { className: "flex-1 flex flex-wrap items-center gap-1.5" },
            recommended.length > 0 
              ? recommended.map((p: any, i: number) => e("button", {
                  key: i,
                  onClick: () => onPickProduct(p),
                  className: "px-2 py-1 rounded text-[10.5px] border transition-colors",
                  style: (!showCustom && selectedProduct === p)
                    ? { background: "rgba(168,85,247,0.18)", borderColor: "rgba(168,85,247,0.45)", color: "#ddd6fe" }
                    : { background: "rgba(255,255,255,0.02)", borderColor: "rgba(255,255,255,0.08)", color: "rgba(203,213,225,0.85)" }
                }, p))
              : e("span", { className: "text-[10px] text-slate-500 italic py-1" }, "未配置推荐产品 · 请用自定义"),
            // + 自定义
            !showCustom && e("button", {
              onClick: () => { setShowCustom(true); setSelectedProduct(""); },
              className: "px-2 py-1 rounded text-[10.5px] border border-dashed border-white/[0.15] text-slate-400 hover:border-purple-500/30 hover:text-purple-200"
            }, "+ 自定义"),
            showCustom && e("input", {
              value: customProduct,
              onChange: (ev: any) => { setCustomProduct(ev.target.value); setSubject(genEmailSubject(ev.target.value, item)); },
              placeholder: "如:Air 28mm F2.8、新品系列等",
              autoFocus: true,
              className: "px-2 py-1 rounded text-[10.5px] bg-white/[0.02] border border-purple-500/30 text-white outline-none placeholder-slate-600 min-w-[180px]"
            }),
            showCustom && e("button", {
              onClick: () => { setShowCustom(false); setCustomProduct(""); onPickProduct(defaultProduct); },
              className: "text-[10px] text-slate-500 hover:text-slate-300 px-1"
            }, "取消")
          )
        ),
        e("div", { className: "flex items-start gap-2 text-[11px]" },
          e("span", { className: "text-slate-500 w-[40px] pt-1" }, "主题"),
          e("input", {
            value: subject, onChange: (ev: any) => setSubject(ev.target.value),
            className: "flex-1 px-2 py-1 rounded bg-white/[0.02] border border-white/[0.06] text-white text-[11px] outline-none focus:border-purple-500/40"
          })
        ),
        // ── 正文 + 本地模板重写 ──
        e("div", { className: "flex items-start gap-2 text-[11px]" },
          e("span", { className: "text-slate-500 w-[40px] pt-1" }, "正文"),
          e("div", { className: "flex-1 space-y-1" },
            e("div", { className: "flex items-center justify-between" },
              e("span", { className: "text-[10px] text-slate-500" }, 
                templateApplying ? "正在套用本地模板..." : "可手动编辑或套用本地模板"
              ),
              e("button", {
                onClick: applyLocalTemplate,
                disabled: templateApplying,
                title: "本地模板重写",
                className: "flex items-center gap-1 px-2 py-0.5 rounded text-[10px] border border-purple-500/30 bg-purple-500/[0.1] text-purple-200 hover:bg-purple-500/[0.18] disabled:opacity-60 transition-colors"
              },
                templateApplying 
                  ? e(Loader2, { size: 10, className: "animate-spin" })
                  : e(Sparkles, { size: 10 }),
                templateApplying ? "套用中" : "套用模板"
              )
            ),
            e("textarea", {
              value: body, onChange: (ev: any) => setBody(ev.target.value),
              rows: 9,
              className: "w-full px-2 py-1.5 rounded bg-white/[0.02] border text-white text-[11px] outline-none focus:border-purple-500/40 resize-none font-mono transition-all",
              style: templateApplying 
                ? { borderColor: "rgba(168,85,247,0.4)", boxShadow: "0 0 0 1px rgba(168,85,247,0.2), 0 0 12px rgba(168,85,247,0.15)" }
                : { borderColor: "rgba(255,255,255,0.06)" }
            })
          )
        ),
        e("div", { className: "flex items-center gap-2 pt-2" },
          e("button", {
            onClick: aiOptimize,
            disabled: optimizing || !apiToken,
            title: apiToken ? "用 AI 把这封外联文案润色得更自然、更高回复率(只产文案,不外发)" : "缺 API token",
            className: "flex items-center justify-center gap-1.5 rounded-md border border-purple-500/40 bg-purple-500/[0.12] px-3 py-2 text-[11px] font-medium text-purple-100 hover:bg-purple-500/[0.2] disabled:opacity-60"
          }, optimizing ? e(Loader2, { size: 11, className: "animate-spin" }) : e(Wand2, { size: 11 }), optimizing ? "优化中" : "AI 优化"),
          e("button", {
            onClick: copyEmail,
            title: "复制主题+正文,去你的邮箱/DM 手动发送(本系统不替你外发)",
            className: "flex-1 flex items-center justify-center gap-1.5 rounded-md bg-purple-600/80 px-3 py-2 text-[11px] font-medium text-white hover:bg-purple-600"
          }, copied ? e(Check, { size: 11 }) : e(Copy, { size: 11 }), copied ? "已复制" : "复制文案"),
          e("button", {
            onClick: onClose,
            className: "rounded-md border border-white/[0.1] px-3 py-2 text-[11px] text-slate-400 hover:bg-white/[0.04]"
          }, "取消")
        )
      ),
      // Add contact tab(没邮箱时默认显示)
      (tab === "add" || !hasEmail) && e("div", { className: "px-5 py-4 space-y-3" },
        !hasEmail && e("div", { className: "rounded-md border border-amber-500/30 bg-amber-500/[0.06] p-2.5 text-[11px] text-amber-200 flex items-start gap-2" },
          e(AlertTriangle, { size: 12, className: "shrink-0 mt-0.5" }),
          e("div", null,
            e("div", { className: "font-medium mb-0.5" }, "此 KOL 尚未收集联系方式"),
            e("div", { className: "text-amber-300/80" }, "建议先去主页 DM,或填入下方任一渠道后再发起邀请。")
          )
        ),
        e("div", { className: "flex items-center gap-2 text-[11px]" },
          e("span", { className: "text-slate-500 w-[60px]" }, "邮箱"),
          e("input", {
            value: newEmail, onChange: (ev: any) => setNewEmail(ev.target.value),
            placeholder: "name@example.com",
            className: "flex-1 px-2 py-1 rounded bg-white/[0.02] border border-white/[0.06] text-white text-[11px] outline-none focus:border-purple-500/40 placeholder-slate-600"
          })
        ),
        e("div", { className: "flex items-center gap-2 text-[11px]" },
          e("span", { className: "text-slate-500 w-[60px]" }, "其他渠道"),
          e("select", {
            value: newPlatform, onChange: (ev: any) => setNewPlatform(ev.target.value),
            className: "px-2 py-1 rounded bg-white/[0.02] border border-white/[0.06] text-white text-[11px] outline-none"
          },
            e("option", { value: "ig_dm" }, "IG DM"),
            e("option", { value: "tt_dm" }, "TikTok DM"),
            e("option", { value: "yt_about" }, "YT About 页"),
            e("option", { value: "discord" }, "Discord"),
            e("option", { value: "x_dm" }, "X DM"),
            e("option", { value: "wechat" }, "WeChat"),
          ),
          e("input", {
            value: newHandle, onChange: (ev: any) => setNewHandle(ev.target.value),
            placeholder: "ID / 链接 / handle",
            className: "flex-1 px-2 py-1 rounded bg-white/[0.02] border border-white/[0.06] text-white text-[11px] outline-none focus:border-purple-500/40 placeholder-slate-600"
          })
        ),
        item.profile_url && e("div", { className: "flex items-center gap-2 pt-2 mt-2 border-t border-white/[0.04]" },
          e("span", { className: "text-[10px] text-slate-500" }, "或直接前往:"),
          e("a", {
            href: item.profile_url, target: "_blank", rel: "noreferrer",
            className: "flex items-center gap-1 text-[11px] text-cyan-300 hover:text-cyan-200"
          }, e(ExternalLink, { size: 10 }), item.profile_url.replace("https://", ""))
        ),
        e("div", { className: "flex items-center gap-2 pt-2" },
          e("button", {
            disabled: true,
            title: "待接入: 需要联系人写入接口和权限校验",
            className: "flex-1 flex cursor-not-allowed items-center justify-center gap-1.5 rounded-md bg-purple-600/40 px-3 py-2 text-[11px] font-medium text-purple-100/70"
          }, e(Check, { size: 11 }), "保存联系方式 · 待接入"),
          e("button", {
            onClick: onClose,
            className: "rounded-md border border-white/[0.1] px-3 py-2 text-[11px] text-slate-400 hover:bg-white/[0.04]"
          }, "取消")
        )
      )
    )
  );
}
