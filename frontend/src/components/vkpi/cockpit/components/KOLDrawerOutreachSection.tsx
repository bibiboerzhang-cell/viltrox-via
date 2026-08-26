// C4 外联区(合作 tab):一键生成外联包 = 产品契合 brief + 中英双语邮件草稿 + 邮箱状态。
// - brief 复用后端已有 why-fit/内容契合深析产物(零新分析);邮件草稿走 llm_gateway 预算闸,
//   LLM 不可用回落双语模板(personalized=false 时诚实标注)。
// - 邮箱缺失时后端自动复用既有富化管线补抓;生成期间前端显示「补抓中」。
// 红线:纯展示 + 复制,绝不自动外发;不渲染任何 viltrox/v6_fit 数值。
import React from "react";
import { Copy, Send, Sparkles } from "lucide-react";
import {
  generateKolOutreachPack,
  getKolOutreachPack,
  type VkpiOutreachPackResponse,
} from "../../../../services/vkpi/kolOutreach-api";

const e = React.createElement;

// 复制按钮(自带「已复制」瞬时反馈;剪贴板不可用时静默降级为无反馈)。
function CopyBlockButton({ text, label = "复制" }: { text: string; label?: string }) {
  const [copied, setCopied] = React.useState(false);
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  React.useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);
  const onCopy = () => {
    if (!text) return;
    void navigator.clipboard?.writeText(text)
      .then(() => {
        setCopied(true);
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => setCopied(false));
  };
  return e("button", {
    type: "button",
    onClick: onCopy,
    className: "flex items-center gap-1 rounded border border-white/[0.08] bg-white/[0.03] px-1.5 py-0.5 text-[9px] text-slate-300 transition-colors hover:bg-white/[0.08]",
  },
    e(Copy, { size: 9 }),
    copied ? "已复制" : label,
  );
}

// 邮箱状态角标:有邮箱(绿)/ 补抓中(琥珀)/ 无邮箱(灰)。
function EmailStatusChip({ email, enriching }: { email: VkpiOutreachPackResponse["email"]; enriching: boolean }) {
  const address = String(email?.email || "");
  const chip = (label: string, color: string) => e("span", {
    className: "rounded px-1.5 py-0.5 text-[9px]",
    style: { background: color + "1f", color },
  }, label);
  if (address) return chip("邮箱 " + address, "#34d399");
  if (enriching) return chip("邮箱补抓中…", "#fbbf24");
  const enrichStatus = String(email?.enrich?.status || "");
  if (enrichStatus === "no_public_contact") return chip("无公开邮箱(已补抓)", "#94a3b8");
  if (enrichStatus === "disabled") return chip("无邮箱 · 富化开关关闭", "#94a3b8");
  if (enrichStatus === "budget_blocked") return chip("无邮箱 · 预算闸拦截", "#94a3b8");
  return chip("无邮箱", "#94a3b8");
}

export function KOLDrawerOutreachSection({ apiToken, kolPoolId }: any) {
  const [resp, setResp] = React.useState<VkpiOutreachPackResponse | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");

  // 开抽屉/换 KOL:只读已有缓存(零 LLM);失败静默(外联包是增益,非阻塞)。
  React.useEffect(() => {
    setResp(null); setErr(""); setBusy(false);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    void getKolOutreachPack(apiToken, kolPoolId)
      .then((payload) => { if (!cancelled) setResp(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setResp(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  if (!apiToken || !kolPoolId) return null;

  const ready = resp?.state === "ready" && resp?.pack;
  const pack = ready ? resp?.pack : null;
  const brief = pack?.brief || {};
  const draft = pack?.email_draft || {};
  const provenance = pack?.provenance || {};
  const emailMissing = String(resp?.email?.state || "") !== "present";

  const handleGenerate = (force: boolean) => {
    if (busy) return;
    setBusy(true); setErr("");
    void generateKolOutreachPack(apiToken, kolPoolId, force)
      .then((payload) => {
        if (payload && payload.state === "ready") {
          setResp(payload);
        } else {
          setErr(String(payload?.reason || "生成未产出结果,请稍后重试"));
        }
      })
      .catch((ex: any) => {
        const status = ex && typeof ex.status === "number" ? ex.status : 0;
        const raw = ex?.message ? String(ex.message) : "";
        // 人话化:线上后端可能还没这条路由(本地分支功能)→ 如实告知,不甩原始报错。
        setErr(status === 404 || /404|not found/i.test(raw)
          ? "外联包后端未上线(本地分支功能),暂不可用。"
          : (raw || "生成失败,请稍后重试。"));
      })
      .finally(() => setBusy(false));
  };

  const whyFit = Array.isArray(brief.why_fit) ? brief.why_fit : [];
  const watchouts = Array.isArray(brief.watchouts) ? brief.watchouts : [];
  const talkingPoints = Array.isArray(draft.talking_points) ? draft.talking_points : [];
  const briefCopyText = [
    brief.product_focus ? "拟推产品: " + brief.product_focus : "",
    brief.creator_type ? "创作者类型: " + brief.creator_type : "",
    brief.content_highlights ? "内容亮点: " + brief.content_highlights : "",
    whyFit.length ? "契合点:\n" + whyFit.map((x) => "- " + x).join("\n") : "",
    watchouts.length ? "注意点:\n" + watchouts.map((x) => "- " + x).join("\n") : "",
  ].filter(Boolean).join("\n\n");

  const renderBullets = (label: string, items: string[], color: string) =>
    items.length > 0 && e("div", { key: label },
      e("div", { className: "mb-0.5 text-[9px]", style: { color } }, label),
      e("ul", { className: "space-y-0.5" },
        items.map((it, i) => e("li", { key: i, className: "text-[10px] leading-snug text-slate-300" }, "· " + it)),
      ),
    );

  const renderEmailBlock = (label: string, body: string) =>
    Boolean(body) && e("div", null,
      e("div", { className: "mb-0.5 flex items-center justify-between" },
        e("div", { className: "text-[9px] text-cyan-300" }, label),
        e(CopyBlockButton, { text: body }),
      ),
      e("div", { className: "whitespace-pre-wrap rounded border border-white/[0.05] bg-black/25 px-2 py-1.5 text-[10px] leading-relaxed text-slate-300" }, body),
    );

  return e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
    // 标题行 + 邮箱状态角标
    e("div", { className: "mb-1.5 flex items-center justify-between gap-2" },
      e("div", { className: "flex items-center gap-1.5" },
        e(Send, { size: 11, className: "text-sky-300" }),
        e("div", { className: "text-[11px] font-semibold text-white" }, "外联包 · 一键 Brief + 邮件草稿"),
      ),
      e(EmailStatusChip, { email: resp?.email, enriching: busy && emailMissing }),
    ),
    // 生成按钮(已有当日缓存 → 「重新生成」走 force)
    e("button", {
      type: "button",
      disabled: busy,
      onClick: () => handleGenerate(Boolean(ready)),
      className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-sky-400/25 bg-sky-400/[0.06] px-3 py-2 text-[11px] font-medium text-sky-200 transition-colors hover:bg-sky-400/[0.12] disabled:opacity-50",
    },
      e(Sparkles, { size: 12 }),
      busy ? (emailMissing ? "生成中(顺带补抓邮箱)…" : "生成中…") : (ready ? "重新生成外联包" : "生成外联包"),
    ),
    err && e("div", { className: "mt-1.5 text-[9.5px] leading-relaxed text-rose-300" }, err),
    // 草稿预览
    ready && e("div", { className: "mt-2 space-y-2.5" },
      // ── ① 产品契合 brief ──
      e("div", { className: "rounded-md border border-white/[0.05] bg-black/20 p-2.5 space-y-1.5" },
        e("div", { className: "flex items-center justify-between" },
          e("div", { className: "text-[9px] uppercase tracking-wider text-slate-500" }, "产品契合 Brief"),
          e(CopyBlockButton, { text: briefCopyText, label: "复制全部" }),
        ),
        brief.product_focus && e("div", { className: "text-[10.5px] text-slate-200" },
          e("span", { className: "text-emerald-300" }, "拟推产品 "), brief.product_focus,
        ),
        brief.creator_type && e("div", { className: "text-[10px] leading-snug text-slate-300" }, brief.creator_type),
        brief.content_highlights && e("div", { className: "text-[10px] leading-relaxed text-slate-400" }, brief.content_highlights),
        renderBullets("为什么契合", whyFit, "#10b981"),
        renderBullets("注意点", watchouts, "#fb7185"),
        !brief.sources?.content_fit_v1 && e("div", { className: "text-[9px] text-slate-500" },
          "提示:该 KOL 暂无内容契合深析缓存,brief 依据较薄 — 可先在「深度分析」tab 跑内容契合。"),
      ),
      // ── ② 邮件草稿(中英双语) ──
      e("div", { className: "rounded-md border border-white/[0.05] bg-black/20 p-2.5 space-y-2" },
        e("div", { className: "flex items-center justify-between" },
          e("div", { className: "text-[9px] uppercase tracking-wider text-slate-500" }, "邮件草稿 · 人审后手动外发"),
          draft.personalized === false && e("span", { className: "rounded bg-amber-300/[0.12] px-1.5 py-0.5 text-[9px] text-amber-200" }, "模板兜底 · 建议人工润色"),
        ),
        Boolean(draft.subject) && e("div", { className: "flex items-center justify-between gap-2" },
          e("div", { className: "min-w-0 flex-1 truncate text-[10.5px] text-slate-200" },
            e("span", { className: "text-cyan-300" }, "主题 "), draft.subject,
          ),
          e(CopyBlockButton, { text: String(draft.subject || "") }),
        ),
        renderEmailBlock("English", String(draft.email_en || "")),
        renderEmailBlock("中文", String(draft.email_zh || "")),
        renderBullets("后续沟通要点", talkingPoints, "#06b6d4"),
      ),
      // 出处行:生成方式/是否缓存;未走智能生成时如实展示原因。
      // 红线2:具体模型/厂商标识只进 title 溯源,不上门面。
      e("div", {
        className: "text-[9px] text-slate-500",
        title: provenance.llm_used
          ? String(provenance.model || provenance.provider || "")
          : undefined,
      },
        (provenance.llm_used
          ? "智能生成"
          : "确定性模板(" + (provenance.reason || "智能生成暂不可用") + ")")
        + (resp?.cached ? " · 今日缓存" : "")
        + (provenance.generated_at ? " · " + String(provenance.generated_at).slice(0, 16).replace("T", " ") + " UTC" : ""),
      ),
    ),
  );
}
