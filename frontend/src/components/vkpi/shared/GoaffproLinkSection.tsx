// 共享:KOL「生成追踪链(GOAFFPRO)」区块。
// 从 v615-replica/components/KOLDetailDrawer.tsx 原样抽出(逻辑零改),
// 供 KOL 详情抽屉 + MY KOL 详情(PoolEvidenceContent)两处复用。
import React from "react";
import { AlertTriangle, Check, Link2 } from "lucide-react";
import { generateKolGoaffproLink, getKolGoaffproLink, updateKolCommission, type GoaffproKolLink } from "../../../services/vkpi/goaffpro-api";

const e = React.createElement;

// D2:复制按钮(复用 navigator.clipboard,带 1.6s「已复制」反馈),供追踪链/优惠码两处复用。
function CopyValueButton({ value, label = "复制" }: any) {
  const [copied, setCopied] = React.useState(false);
  return e("button", {
    type: "button",
    className: "shrink-0 inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] font-medium transition-colors " +
      (copied
        ? "border-emerald-400/30 bg-emerald-400/[0.1] text-emerald-200"
        : "border-white/[0.1] bg-white/[0.04] text-slate-300 hover:bg-white/[0.08] hover:text-white"),
    title: copied ? "已复制" : label,
    onClick: () => {
      void navigator.clipboard?.writeText(String(value || "")).then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      });
    },
  },
    e(copied ? Check : Link2, { size: 10 }),
    copied ? "已复制" : label
  );
}

// 判定 tracking_url 是否真带**非空**追踪码(?ref=xxx / /ref/xxx);?ref= 空值或光店铺链 = 未追踪。
export function hasRefParam(url: any): boolean {
  return /[?&]ref=[^&\s]+|\/ref\/[^/\s]+/i.test(String(url || ""));
}

// D2:追踪链 + 优惠码卡片(只读输入框 + 复制 + 「发给 KOL」提示)。
// onRegenerate:卡片右上角「重新生成」入口(始终可点,解决「链有了就不能重生」)。
function LinkRow({ label, url }: { label: string; url: string }) {
  return e("div", null,
    e("div", { className: "mb-1 text-[9px] text-slate-500" }, label),
    e("div", { className: "flex items-center gap-1.5" },
      e("input", {
        type: "text",
        readOnly: true,
        value: url,
        onFocus: (ev: any) => ev.target.select(),
        className: "flex-1 min-w-0 rounded-md border border-white/[0.08] bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none",
      }),
      e(CopyValueButton, { value: url, label: "复制" })
    )
  );
}

function GoaffproLinkCard({ link, onRegenerate, regenerating, apiToken, kolPoolId }: any) {
  const trackingUrl = String(link.tracking_url || "");
  const productUrl = String(link.product_url || "");
  const productName = String(link.product_name || "");
  const coupon = String(link.coupon || "");
  const statusLabel = statusBadge((link as any).status);
  const [commissionRate, setCommissionRate] = React.useState(String((link as any).commission_rate || ""));
  const [editing, setEditing] = React.useState(false);
  const [editVal, setEditVal] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [saveErr, setSaveErr] = React.useState("");
  React.useEffect(() => { setCommissionRate(String((link as any).commission_rate || "")); }, [(link as any).commission_rate]);
  const canEdit = !!(apiToken && kolPoolId);
  const saveCommission = () => {
    const n = parseFloat(editVal);
    if (!canEdit || isNaN(n) || n < 0) { setSaveErr("请输入有效数字(≥0)"); return; }
    setSaving(true); setSaveErr("");
    void updateKolCommission(apiToken, kolPoolId, Math.round(n))
      .then((res: any) => {
        if (res && res.ok) { setCommissionRate(String(res.commission_rate || Math.round(n) + "%")); setEditing(false); }
        else setSaveErr(String((res && res.error) || "保存失败"));
      })
      .catch((e2: any) => setSaveErr(e2 && e2.message ? String(e2.message) : "保存失败"))
      .finally(() => setSaving(false));
  };
  return e("div", { className: "mt-2 rounded-md border border-teal-400/20 bg-teal-400/[0.04] p-2.5 space-y-2" },
    e("div", { className: "flex items-center gap-1.5 text-[9px] uppercase tracking-wider text-teal-200/80" },
      e(Link2, { size: 10 }),
      "GOAFFPRO 追踪链已就绪",
      link.already_linked && e("span", { className: "rounded bg-teal-500/15 px-1 py-0.5 text-[8px] text-teal-200" }, "已存在"),
      onRegenerate && e("button", {
        type: "button",
        disabled: regenerating,
        onClick: onRegenerate,
        className: "ml-auto rounded px-1 py-0.5 text-[8px] normal-case text-slate-400 hover:text-teal-200 disabled:opacity-50",
        title: "重新拉取/重建该 KOL 的追踪链",
      }, regenerating ? "刷新中…" : "↻ 重新生成")
    ),
    // 产品页追踪链(项目/活动绑产品时;落具体产品页,转化更高,归因照样算该 KOL)
    productUrl
      ? e(LinkRow, { label: "🎯 产品页追踪链(落「" + (productName || "产品页") + "」· 推荐发给 KOL)", url: productUrl })
      : null,
    // 通用追踪链(落首页)
    trackingUrl
      ? e(LinkRow, { label: productUrl ? "通用追踪链(落首页)" : "追踪链(发给 KOL,产生的销售自动归到该 KOL)", url: trackingUrl })
      : e("div", { className: "text-[10px] text-slate-500" }, "—（GOAFFPRO 未回追踪链）"),
    // 优惠码(复制)
    coupon && e("div", null,
      e("div", { className: "mb-1 text-[9px] text-slate-500" }, "优惠码"),
      e("div", { className: "flex items-center gap-1.5" },
        e("code", { className: "flex-1 min-w-0 truncate rounded-md border border-white/[0.08] bg-black/30 px-2 py-1.5 text-[11px] font-semibold tabular-nums text-emerald-200" }, coupon),
        e(CopyValueButton, { value: coupon, label: "复制码" })
      )
    ),
    // 佣金比例(可调,改完 PATCH 推回 GOAFFPRO 总台)+ 审批状态
    e("div", { className: "flex flex-wrap items-center gap-1.5" },
      editing
        ? e("span", { className: "inline-flex items-center gap-1 rounded-md border border-amber-400/30 bg-amber-400/[0.08] px-2 py-1 text-[10px] text-amber-200" },
            "💰 佣金比例",
            e("input", {
              type: "number", min: 0, autoFocus: true, value: editVal,
              onChange: (ev: any) => setEditVal(ev.target.value),
              onKeyDown: (ev: any) => { if (ev.key === "Enter") saveCommission(); if (ev.key === "Escape") setEditing(false); },
              className: "w-12 rounded border border-white/[0.15] bg-black/30 px-1 py-0.5 text-[10px] text-white outline-none tabular-nums",
            }),
            "%",
            e("button", { type: "button", disabled: saving, onClick: saveCommission, className: "rounded bg-amber-500/80 px-1.5 py-0.5 text-[9px] font-medium text-white hover:bg-amber-500 disabled:opacity-50" }, saving ? "保存中" : "保存并推回总台"),
            e("button", { type: "button", onClick: () => { setEditing(false); setSaveErr(""); }, className: "text-[9px] text-slate-400 hover:text-white" }, "取消")
          )
        : e("span", { className: "inline-flex items-center gap-1 rounded-md border border-amber-400/25 bg-amber-400/[0.08] px-2 py-1 text-[10px] text-amber-200" },
            "💰 佣金比例 ", e("b", { className: "tabular-nums" }, commissionRate || "—"),
            canEdit && e("button", {
              type: "button",
              onClick: () => { setEditing(true); setEditVal(commissionRate.replace(/[^0-9.]/g, "")); setSaveErr(""); },
              className: "ml-1 rounded px-1 text-[9px] text-amber-300/80 hover:text-amber-100 underline decoration-dotted",
              title: "调整佣金比例(改完推回 GOAFFPRO 总台)",
            }, "调整")
          ),
      statusLabel && e("span", { className: "rounded-md border px-2 py-1 text-[10px] " + statusLabel.cls }, statusLabel.text),
      saveErr && e("span", { className: "text-[9px] text-rose-300" }, saveErr)
    ),
    e("div", { className: "text-[9px] leading-relaxed text-teal-200/70" },
      "📨 把追踪链" + (coupon ? " + 优惠码" : "") + "发给 KOL,KOL 零注册;销售经 GOAFFPRO 归因回这条 KOL。"
    )
  );
}

// 审批状态→人话 + 配色(approved=归因+结算都生效;pending=能追踪但佣金待审批)。
function statusBadge(status: any): { text: string; cls: string } | null {
  const s = String(status || "").toLowerCase();
  if (!s) return null;
  if (s === "approved" || s === "active" || s === "1") return { text: "✅ 已审批 · 归因生效", cls: "border-emerald-400/25 bg-emerald-400/[0.08] text-emerald-200" };
  if (s === "pending") return { text: "⏳ 待审批 · 能追踪,佣金待批", cls: "border-amber-400/25 bg-amber-400/[0.08] text-amber-200" };
  if (s === "rejected") return { text: "⛔ 已拒绝", cls: "border-rose-400/25 bg-rose-400/[0.08] text-rose-200" };
  return { text: s, cls: "border-white/[0.1] bg-white/[0.04] text-slate-300" };
}

// D2:KOL 详情「生成追踪链(GOAFFPRO)」区块。
// 开抽屉先 GET 已有映射(有则直接显示卡,不重复生成);否则给生成按钮,点击 POST 建链。
// 出错(ok:false)显示 error + 「联系管理员校准」,raw 折叠可见便于调试。
export function GoaffproLinkSection({ apiToken, kolPoolId, product }: any) {
  const [link, setLink] = React.useState<GoaffproKolLink | null>(null);
  const [loading, setLoading] = React.useState(false); // GET 已有映射
  const [generating, setGenerating] = React.useState(false); // POST 建链
  const [error, setError] = React.useState("");
  const [errorRaw, setErrorRaw] = React.useState<unknown>(null);
  const [rawOpen, setRawOpen] = React.useState(false);

  React.useEffect(() => {
    setLink(null);
    setError("");
    setErrorRaw(null);
    setRawOpen(false);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    setLoading(true);
    void getKolGoaffproLink(apiToken, kolPoolId, product)
      .then((payload) => {
        if (cancelled) return;
        // 保留 linked 的 payload(即使是光链/需重生)——这样能显「重新生成」而非把按钮藏掉。
        if (payload && payload.linked) {
          setLink(payload);
        } else {
          setLink(null);
        }
      })
      .catch(() => { if (!cancelled) setLink(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId, product]);

  const handleGenerate = () => {
    if (!apiToken || !kolPoolId || generating) return;
    setGenerating(true);
    setError("");
    setErrorRaw(null);
    void generateKolGoaffproLink(apiToken, kolPoolId, product)
      .then((payload) => {
        if (payload && payload.ok && payload.tracking_url) {
          setLink(payload);
        } else if (payload && payload.ok) {
          // ok 但无追踪链(GOAFFPRO 字段待校准):仍显示卡(空链占位)。
          setLink(payload);
        } else {
          setError(String(payload?.error || payload?.reason || "生成追踪链失败"));
          setErrorRaw(payload?.raw ?? payload ?? null);
        }
      })
      .catch((err: any) => {
        setError(err?.message ? String(err.message) : "生成追踪链请求失败");
        setErrorRaw(null);
      })
      .finally(() => setGenerating(false));
  };

  if (!apiToken || !kolPoolId) return null;

  // 光链(无 ?ref=)或后端标 needs_regenerate = 当前未真追踪 → 显「重新生成」而非藏按钮。
  const trackingUrl = String(link?.tracking_url || "");
  const hasRef = hasRefParam(trackingUrl);
  const needsRegen = !!(link && (!hasRef || (link as any).needs_regenerate));
  const showCard = !!(link && hasRef);
  const showButton = !link || needsRegen;

  return e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
    showButton && e("button", {
      type: "button",
      disabled: loading || generating,
      onClick: handleGenerate,
      className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-teal-400/25 bg-teal-400/[0.06] px-3 py-2 text-[11px] font-medium text-teal-200 transition-colors hover:bg-teal-400/[0.12] disabled:opacity-50",
    },
      e(Link2, { size: 12 }),
      loading ? "读取追踪链…" : generating ? "生成中…" : (needsRegen ? "🔗 重新生成追踪链(当前未带追踪码)" : "🔗 生成追踪链(GOAFFPRO)")
    ),
    showCard && e(GoaffproLinkCard, { link: link as GoaffproKolLink, onRegenerate: handleGenerate, regenerating: generating, apiToken, kolPoolId }),
    error && e("div", { className: "mt-2 rounded-md border border-rose-400/20 bg-rose-400/[0.05] p-2.5" },
      e("div", { className: "flex items-start gap-1.5" },
        e(AlertTriangle, { size: 11, className: "mt-0.5 shrink-0 text-rose-300" }),
        e("div", { className: "min-w-0" },
          e("div", { className: "text-[10.5px] leading-relaxed text-rose-200" }, error),
          e("div", { className: "mt-1 text-[9.5px] text-rose-300/70" }, "请联系管理员校准 GOAFFPRO 接入(creds / 字段映射)。")
        )
      ),
      errorRaw != null && e("div", { className: "mt-1.5" },
        e("button", {
          type: "button",
          onClick: () => setRawOpen((v: boolean) => !v),
          className: "text-[9px] text-slate-500 hover:text-slate-300",
        }, rawOpen ? "收起原始响应 ▲" : "展开原始响应(调试)▼"),
        rawOpen && e("pre", {
          className: "mt-1 max-h-40 overflow-auto rounded border border-white/[0.06] bg-black/30 p-2 text-[9px] leading-relaxed text-slate-400 whitespace-pre-wrap break-all",
        }, (() => { try { return JSON.stringify(errorRaw, null, 2); } catch { return String(errorRaw); } })())
      ),
      e("button", {
        type: "button",
        disabled: generating,
        onClick: handleGenerate,
        className: "mt-2 text-[10px] text-teal-300 hover:text-teal-200 disabled:opacity-50",
      }, generating ? "重试中…" : "重试生成")
    )
  );
}
