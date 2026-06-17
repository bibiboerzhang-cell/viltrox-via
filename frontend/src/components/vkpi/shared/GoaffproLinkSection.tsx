// 共享:KOL「生成追踪链(GOAFFPRO)」区块。
// 从 v615-replica/components/KOLDetailDrawer.tsx 原样抽出(逻辑零改),
// 供 KOL 详情抽屉 + MY KOL 详情(PoolEvidenceContent)两处复用。
import React from "react";
import { AlertTriangle, Check, Link2 } from "lucide-react";
import { generateKolGoaffproLink, getKolGoaffproLink, type GoaffproKolLink } from "../../../services/vkpi/goaffpro-api";

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

// D2:追踪链 + 优惠码卡片(只读输入框 + 复制 + 「发给 KOL」提示)。
function GoaffproLinkCard({ link }: { link: GoaffproKolLink }) {
  const trackingUrl = String(link.tracking_url || "");
  const coupon = String(link.coupon || "");
  return e("div", { className: "mt-2 rounded-md border border-teal-400/20 bg-teal-400/[0.04] p-2.5 space-y-2" },
    e("div", { className: "flex items-center gap-1.5 text-[9px] uppercase tracking-wider text-teal-200/80" },
      e(Link2, { size: 10 }),
      "GOAFFPRO 追踪链已就绪",
      link.already_linked && e("span", { className: "rounded bg-teal-500/15 px-1 py-0.5 text-[8px] text-teal-200" }, "已存在")
    ),
    // 追踪链(只读输入框 + 复制)
    e("div", null,
      e("div", { className: "mb-1 text-[9px] text-slate-500" }, "追踪链(发给 KOL,产生的销售自动归到该 KOL)"),
      trackingUrl
        ? e("div", { className: "flex items-center gap-1.5" },
            e("input", {
              type: "text",
              readOnly: true,
              value: trackingUrl,
              onFocus: (ev: any) => ev.target.select(),
              className: "flex-1 min-w-0 rounded-md border border-white/[0.08] bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none",
            }),
            e(CopyValueButton, { value: trackingUrl, label: "复制" })
          )
        : e("div", { className: "text-[10px] text-slate-500" }, "—（GOAFFPRO 未回追踪链,待真 key 校准)")
    ),
    // 优惠码(复制)
    coupon && e("div", null,
      e("div", { className: "mb-1 text-[9px] text-slate-500" }, "优惠码"),
      e("div", { className: "flex items-center gap-1.5" },
        e("code", { className: "flex-1 min-w-0 truncate rounded-md border border-white/[0.08] bg-black/30 px-2 py-1.5 text-[11px] font-semibold tabular-nums text-emerald-200" }, coupon),
        e(CopyValueButton, { value: coupon, label: "复制码" })
      )
    ),
    e("div", { className: "text-[9px] leading-relaxed text-teal-200/70" },
      "📨 把追踪链" + (coupon ? " + 优惠码" : "") + "发给 KOL,KOL 零注册;销售经 GOAFFPRO 归因回这条 KOL。"
    )
  );
}

// D2:KOL 详情「生成追踪链(GOAFFPRO)」区块。
// 开抽屉先 GET 已有映射(有则直接显示卡,不重复生成);否则给生成按钮,点击 POST 建链。
// 出错(ok:false)显示 error + 「联系管理员校准」,raw 折叠可见便于调试。
export function GoaffproLinkSection({ apiToken, kolPoolId }: any) {
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
    void getKolGoaffproLink(apiToken, kolPoolId)
      .then((payload) => {
        if (cancelled) return;
        if (payload && payload.linked && payload.tracking_url) {
          setLink(payload);
        } else {
          setLink(null);
        }
      })
      .catch(() => { if (!cancelled) setLink(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  const handleGenerate = () => {
    if (!apiToken || !kolPoolId || generating) return;
    setGenerating(true);
    setError("");
    setErrorRaw(null);
    void generateKolGoaffproLink(apiToken, kolPoolId)
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

  return e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
    !link && e("button", {
      type: "button",
      disabled: loading || generating,
      onClick: handleGenerate,
      className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-teal-400/25 bg-teal-400/[0.06] px-3 py-2 text-[11px] font-medium text-teal-200 transition-colors hover:bg-teal-400/[0.12] disabled:opacity-50",
    },
      e(Link2, { size: 12 }),
      loading ? "读取追踪链…" : generating ? "生成中…" : "🔗 生成追踪链(GOAFFPRO)"
    ),
    link && e(GoaffproLinkCard, { link }),
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
