// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { AlertTriangle, Check, Copy, ExternalLink, Loader2, Sparkles, Wand2, X } from "lucide-react";
import { KPAvatar } from "../KPAvatar";
import { genEmailBody, genEmailSubject } from "../../lib/email";
import { kolHumanDisplayName, kolHumanIdentitySubtitle, kolHumanProfileLinkLabel } from "../../lib/kolIdentity";
import { useT } from "../../lib/i18n";
import type { ContactState, KolContactChannel, KolContactTier } from "../../lib/kolContacts";
import { useKolContactState } from "../../lib/useKolContactState";
import { apiFetch } from "../../../../../services/http";
import { OverlayPortal } from "./OverlayPortal";

const e = React.createElement;

// d6:关窗丢稿修复——模块级内存草稿(按 KOL id),重开同一 KOL 自动恢复;
// 刷新页面即清(内存级,后端联系人写端点落地前的最低保障)。
const CONTACT_DRAFT_TTL_MS = 30 * 60 * 1000;
const CONTACT_DRAFT_MAX_ENTRIES = 20;
const CONTACT_DRAFTS = new Map<string, { savedAt: number; value: Record<string, unknown> }>();

function readContactDraft(key: string) {
  const entry = CONTACT_DRAFTS.get(key);
  if (!entry) return null;
  if (Date.now() - entry.savedAt > CONTACT_DRAFT_TTL_MS) {
    CONTACT_DRAFTS.delete(key);
    return null;
  }
  return entry.value;
}

function writeContactDraft(key: string, value: Record<string, unknown>) {
  CONTACT_DRAFTS.delete(key);
  CONTACT_DRAFTS.set(key, { savedAt: Date.now(), value });
  while (CONTACT_DRAFTS.size > CONTACT_DRAFT_MAX_ENTRIES) {
    const oldestKey = CONTACT_DRAFTS.keys().next().value;
    if (oldestKey === undefined) break;
    CONTACT_DRAFTS.delete(oldestKey);
  }
}

// 揭示后两档徽标:已核验(公开商务信息+证据) / 观测到·未核验(扫描/声明来源,尚未核验)。
// 门面只出这两档中文,不出内部来源码。
function ContactTierBadge({ tier, t }: { tier?: KolContactTier; t: (source: string) => string }) {
  if (tier === "verified") {
    return e("span", {
      "data-contact-tier": "verified",
      title: t("来源已核验为公开商务联系方式"),
      className: "inline-flex shrink-0 items-center rounded border border-emerald-400/30 bg-emerald-500/[0.08] px-1 py-0.5 text-[9px] leading-none text-emerald-300",
    }, t("已核验"));
  }
  if (tier === "observed") {
    return e("span", {
      "data-contact-tier": "observed",
      title: t("由公开资料扫描或人工录入获得,尚未核验;联系前请自行确认"),
      className: "inline-flex shrink-0 items-center rounded border border-amber-400/30 bg-amber-500/[0.08] px-1 py-0.5 text-[9px] leading-none text-amber-200",
    }, t("观测到 · 未核验"));
  }
  return null;
}

export function ContactModal({ item, onClose, apiToken, currentUser, sessionGeneration = 0, initialContactState = null }: any) {
  if (!item) return null;
  const { t } = useT();
  const {
    state: contactState,
    retry: retryContact,
    clear: clearContact,
  } = useKolContactState({
    apiToken,
    kolPoolId: item.id,
    purpose: "compose_outreach",
    initialState: initialContactState as ContactState | null,
  });
  const contactChannels: KolContactChannel[] = contactState.status === "full" ? contactState.contacts : [];
  const emailContact = contactChannels.find((contact) => contact.type === "email");
  const contactEmail = emailContact?.value || "";
  const hasEmail = Boolean(contactEmail);
  const hasAnyContact = contactChannels.length > 0;
  const outreachName = kolHumanDisplayName(item);
  const outreachSubtitle = kolHumanIdentitySubtitle(item);
  const profileLinkLabel = kolHumanProfileLinkLabel(item);
  const currentUserEmail = String(currentUser?.email || "").trim();
  const currentUserName = String(currentUser?.name || "").trim();
  const hasResolvedSender = Boolean(Number(currentUser?.id) > 0 || currentUserEmail);
  const sender = {
    name: hasResolvedSender ? currentUserName : "",
    email: hasResolvedSender ? currentUserEmail : "",
  };
  const recommended = item.recommended_product_lines || [];
  const defaultProduct = recommended[0] || "Viltrox 镜头";
  const draftKey = `${sessionGeneration}:${currentUser?.id || sender.email || "anonymous"}:${item.id}`;
  // Freeze the pre-existing draft for this mounted modal. Writes below must
  // not turn an initial automatic "add" state into an explicit user choice.
  const initialDraftRef = React.useRef<{ key: string; value: any }>({
    key: draftKey,
    value: readContactDraft(draftKey),
  });
  if (initialDraftRef.current.key !== draftKey) {
    initialDraftRef.current = { key: draftKey, value: readContactDraft(draftKey) };
  }
  const draft: any = initialDraftRef.current.value;
  const [tab, setTab] = useState(draft?.tab ?? (hasEmail ? "email" : "add"));
  const [selectedProduct, setSelectedProduct] = useState(draft?.selectedProduct ?? defaultProduct);
  const [customProduct, setCustomProduct] = useState(draft?.customProduct ?? "");
  const [showCustom, setShowCustom] = useState(draft?.showCustom ?? false);
  const [templateApplying, setTemplateApplying] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveErr, setSaveErr] = useState("");
  const [subject, setSubject] = useState(draft?.subject ?? (hasEmail ? genEmailSubject(defaultProduct, item) : ""));
  const [body, setBody] = useState(draft?.body ?? (hasEmail ? genEmailBody(defaultProduct, item, sender) : ""));
  const [newEmail, setNewEmail] = useState(draft?.newEmail ?? "");
  const [newPlatform, setNewPlatform] = useState(draft?.newPlatform ?? "ig_dm");
  const [newHandle, setNewHandle] = useState(draft?.newHandle ?? "");
  const initializedEmailRef = React.useRef("");
  React.useEffect(() => {
    // Contact entry fields (newEmail/newHandle) are never retained. Composition
    // text gets a short, bounded in-memory recovery window only.
    writeContactDraft(draftKey, { tab, selectedProduct, customProduct, showCustom, subject, body, newPlatform });
  }, [draftKey, tab, selectedProduct, customProduct, showCustom, subject, body, newPlatform]);

  React.useEffect(() => {
    if (!contactEmail || initializedEmailRef.current === contactEmail) return;
    initializedEmailRef.current = contactEmail;
    if (!draft?.tab) setTab("email");
    setSubject((current: string) => current || genEmailSubject(defaultProduct, item));
    setBody((current: string) => current || genEmailBody(defaultProduct, item, sender));
  // `draft` and `sender` are stable inputs for this mounted KOL modal; contactEmail is the transition trigger.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contactEmail]);

  const closeModal = React.useCallback(() => {
    // Explicitly abort/drop plaintext before asking the parent to unmount.
    clearContact();
    onClose && onClose();
  }, [clearContact, onClose]);

  const otherContactChannels = contactChannels.filter((contact) => contact.type !== "email");
  const renderOtherContacts = () => otherContactChannels.length > 0 && e("div", {
    className: "rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-2",
  },
    e("div", { className: "mb-1 text-[10px] text-slate-500" }, "其他已收集联系方式"),
    otherContactChannels.map((contact) => e("div", {
      key: `${contact.type}:${contact.value}`,
      className: "border-b border-white/[0.04] py-1.5 text-[10.5px] last:border-b-0",
    },
      e("div", { className: "flex min-w-0 items-center gap-2" },
        e("span", { className: "w-[72px] shrink-0 text-slate-500" }, contact.label),
        e("span", { className: "min-w-0 flex-1 truncate text-slate-300" }, contact.value),
        contact.href && e("a", {
          href: contact.href,
          target: contact.href.startsWith("http") ? "_blank" : undefined,
          rel: "noreferrer",
          className: "shrink-0 text-cyan-300 hover:text-cyan-200",
        }, contact.actionLabel),
      ),
      (contact.tier || contact.lastVerifiedAt) && e("div", {
        className: "mt-1 flex items-center gap-1.5 pl-20 text-[9px] text-slate-500",
      },
        e(ContactTierBadge, { tier: contact.tier, t }),
        contact.tier === "verified" && contact.lastVerifiedAt && e("span", null, `${t("核验于")} ${contact.lastVerifiedAt}`),
      ),
    )),
  );

  const renderContactStatus = () => {
    if (contactState.status === "loading") {
      return e("div", { role: "status", className: "inline-flex items-center gap-1.5 text-[10.5px] text-slate-400" },
        e(Loader2, { size: 10, className: "animate-spin" }),
        "正在授权读取联系方式…",
      );
    }
    if (contactState.status === "restricted") {
      return e("div", {
        role: "alert",
        className: "rounded-md border border-amber-500/30 bg-amber-500/[0.06] px-2.5 py-1.5 text-[10.5px] text-amber-200",
      }, contactState.message || "联系方式已受保护，当前账号无法读取明文");
    }
    if (contactState.status === "error") {
      return e("div", {
        role: "alert",
        className: "rounded-md border border-amber-500/30 bg-amber-500/[0.06] px-2.5 py-1.5 text-[10.5px] text-amber-200",
      },
        e("span", null, contactState.message || "完整联系方式读取失败，请稍后重试"),
        e("button", {
          type: "button",
          onClick: retryContact,
          className: "ml-2 underline underline-offset-2 hover:text-amber-100",
        }, "重试"),
      );
    }
    if (contactState.status === "empty") {
      return e("div", { className: "rounded-md border border-amber-500/30 bg-amber-500/[0.06] p-2.5 text-[11px] text-amber-200 flex items-start gap-2" },
        e(AlertTriangle, { size: 12, className: "shrink-0 mt-0.5" }),
        e("div", null,
          e("div", { className: "font-medium mb-0.5" }, "此 KOL 暂无已验证联系方式"),
          e("div", { className: "text-amber-300/80" }, "可前往官方主页联系，或在下方录入有来源的渠道。"),
        ),
      );
    }
    return null;
  };
  
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
      setBody(genEmailBody(p, item, sender));
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
        { method: "POST", body: JSON.stringify({ kol_pool_id: item.id, kol_name: outreachName, product: prod, subject, body }) },
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
  // 保存联系方式:写后端合规留痕(source=manual/consent=manual_entry/记操作人)+ 展示快照。
  // 成功后关窗;KOL 列表下次刷新即显示。纯人工录入、零外调。
  const saveContact = async () => {
    if (saving) return;
    const em = (newEmail || "").trim();
    const hd = (newHandle || "").trim();
    if (!em && !hd) { setSaveErr("请至少填写邮箱或一个渠道 handle"); return; }
    if (!apiToken) { setSaveErr("缺少登录令牌,无法保存"); return; }
    setSaving(true); setSaveErr("");
    try {
      const res: any = await apiFetch(
        `/api/admin/vkpi/kol-pool/${item.id}/contacts`,
        { method: "POST", body: JSON.stringify({ email: em, platform: newPlatform, handle: hd }) },
        apiToken,
      );
      if (res && res.status === "saved") {
        setSaved(true);
        try { CONTACT_DRAFTS.delete(draftKey); } catch (_e) { /* ignore */ }
        setTimeout(closeModal, 800);
      } else {
        setSaveErr(res && res.reason ? String(res.reason) : "保存失败,请重试");
      }
    } catch (e: any) {
      setSaveErr(String(e && e.message ? e.message : e));
    } finally { setSaving(false); }
  };

  // 员工反馈 #2:弹层挂 body(OverlayPortal)+ 高度按视口 + 头部/Tab 常驻、表单区单一滚动。
  return e(OverlayPortal, { stage: "kol-pool" },
  e("div", {
    className: "cockpit-modal fixed inset-0 z-[60] flex items-center justify-center p-4",
    style: { background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" },
    onClick: closeModal
  },
    e("div", {
      role: "dialog",
      "aria-modal": "true",
      className: "flex max-h-[calc(100dvh-2rem)] w-full max-w-[520px] flex-col overflow-hidden rounded-xl border border-white/[0.08] bg-[#0a1020] shadow-2xl",
      onClick: (ev: any) => ev.stopPropagation()
    },
      // Header
      e("div", { className: "flex flex-none items-center gap-3 border-b border-white/[0.06] px-5 py-3" },
        e(KPAvatar, { name: outreachName, color: item.avatar_color, size: 36 }),
        e("div", { className: "flex-1 min-w-0" },
          e("h3", { className: "text-[13px] font-semibold text-white" }, hasEmail ? "发起合作邀请" : hasAnyContact ? "联系 KOL" : "联系人与合作邀请"),
          e("p", { className: "text-[10px] text-slate-500" }, outreachSubtitle)
        ),
        e("button", { onClick: closeModal, "aria-label": "关闭合作邀请", className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" },
          e(X, { size: 13 })
        )
      ),
      // Tab(只有 has email 才显示)
      hasEmail && e("div", { className: "flex flex-none items-center gap-1 border-b border-white/[0.04] px-5 pt-3" },
        ["email", "add"].map(t => e("button", {
          key: t,
          onClick: () => setTab(t),
          className: "px-3 py-1.5 text-[11px] border-b-2 transition-colors",
          style: tab === t 
            ? { borderColor: "#a855f7", color: "#fff" }
            : { borderColor: "transparent", color: "#94a3b8" }
        }, t === "email" ? "邮件邀请" : "添加额外渠道"))
      ),
      // 单一滚动区:两个 tab 的表单体都在这层里滚,头部/Tab 条常驻
      e("div", { className: "min-h-0 flex-1 overflow-y-auto overscroll-contain", "data-vkpi-modal-scroll": "content" },
      // Email tab
      tab === "email" && hasEmail && e("div", { className: "px-5 py-4 space-y-3" },
        e("div", { className: "flex items-center gap-2 text-[11px]" },
          e("span", { className: "text-slate-500 w-[40px]" }, "To"),
          e("span", { className: "text-cyan-300 flex-1 px-2 py-1 rounded bg-cyan-500/[0.05] border border-cyan-500/20" },
            contactEmail,
          ),
          e(ContactTierBadge, { tier: emailContact?.tier, t }),
        ),
        renderOtherContacts(),
        e("div", { className: "flex items-center gap-2 text-[11px]" },
          e("span", { className: "text-slate-500 w-[40px]" }, "From"),
          e("span", { className: "text-slate-300 flex-1 px-2 py-1 rounded bg-white/[0.02] border border-white/[0.06]" },
            sender.email || "未配置发件邮箱 · 复制后在邮箱客户端选择发件人",
          ),
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
            onClick: closeModal,
            className: "rounded-md border border-white/[0.1] px-3 py-2 text-[11px] text-slate-400 hover:bg-white/[0.04]"
          }, "取消")
        )
      ),
      // Add contact tab(没邮箱时默认显示)
      (tab === "add" || !hasEmail) && e("div", { className: "px-5 py-4 space-y-3" },
        renderContactStatus(),
        renderOtherContacts(),
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
            title: profileLinkLabel,
            className: "flex items-center gap-1 text-[11px] text-cyan-300 hover:text-cyan-200"
          }, e(ExternalLink, { size: 10 }), profileLinkLabel)
        ),
        saveErr && e("div", { className: "rounded-md border border-rose-500/30 bg-rose-500/10 px-2.5 py-1.5 text-[10.5px] text-rose-200" }, saveErr),
        e("div", { className: "flex items-center gap-2 pt-2" },
          e("button", {
            disabled: saving || saved || (!newEmail.trim() && !newHandle.trim()),
            onClick: saveContact,
            title: saved ? "已保存" : "保存到 KOL 联系方式(合规留痕 · 不外发)",
            className: `flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-[11px] font-medium ${saving || saved || (!newEmail.trim() && !newHandle.trim()) ? "cursor-not-allowed bg-purple-600/40 text-purple-100/70" : "bg-purple-600 text-white hover:bg-purple-500"}`
          }, e(Check, { size: 11 }), saved ? "已保存 ✓" : saving ? "保存中…" : "保存联系方式"),
          e("button", {
            onClick: closeModal,
            className: "rounded-md border border-white/[0.1] px-3 py-2 text-[11px] text-slate-400 hover:bg-white/[0.04]"
          }, "取消")
        )
      )
      )
    )
  ));
}
