// G1 外联信号卡(合作 tab 增益块,建议挂在 KOLDrawerOutreachSection 之后):
// ① 「敢给差评」信号:该 KOL 历史上公开批评过产品 = 可信度加分(词表法证据,附原文摘录);
// ② 外联三承诺:7 天内付款 / 送测留用不回收 / 书面承诺不干预差评(开关态如实展示)。
// 诚实态:未检出证据如实说「未检出(不代表没有)」;接口失败整卡安静缺席(非阻塞增益块)。
// 红线:纯展示,零写库;不渲染任何 viltrox/v6_fit 数值;草稿仅供人审后手动外发。
import React from "react";
import { HandCoins, MessageSquareQuote, ShieldCheck } from "lucide-react";
import {
  getKolCriticSignal,
  getOutreachThreePromises,
  type VkpiCriticSignal,
  type VkpiThreePromises,
} from "../../../../services/vkpi/outreachSignals-api";

const e = React.createElement;

const SOURCE_LABELS: Record<string, string> = {
  deep_analysis: "深析转述",
  creator_title: "创作者标题",
  creator_description: "创作者描述",
};

export function OutreachCriticSignalCard({ apiToken, kolPoolId }: any) {
  const [critic, setCritic] = React.useState<VkpiCriticSignal | null>(null);
  const [promises, setPromises] = React.useState<VkpiThreePromises | null>(null);

  // 开抽屉/换 KOL 只读拉取(后端词表法纯聚合,读得起);失败静默不渲染。
  React.useEffect(() => {
    setCritic(null);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    void getKolCriticSignal(apiToken, kolPoolId)
      .then((payload) => { if (!cancelled) setCritic(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setCritic(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  // 三承诺文案(全局常量,与 KOL 无关,只拉一次)。
  React.useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    void getOutreachThreePromises(apiToken)
      .then((payload) => { if (!cancelled) setPromises(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setPromises(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

  if (!apiToken || !kolPoolId) return null;
  if (!critic && !promises) return null; // 双读端都失败 → 整卡安静缺席

  const criticReady = critic?.status === "ready";
  const hasHistory = Boolean(criticReady && critic?.has_critic_history);
  const example = hasHistory ? critic?.example : null;
  const counts = critic?.counts || {};
  const items = Array.isArray(promises?.items) ? promises!.items! : [];

  return e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
    // ── ① 敢给差评信号 ──
    criticReady && e("div", { className: "rounded-md border border-white/[0.05] bg-black/20 p-2.5" },
      e("div", { className: "flex items-center justify-between gap-2" },
        e("div", { className: "flex items-center gap-1.5" },
          e(MessageSquareQuote, { size: 11, className: hasHistory ? "text-emerald-300" : "text-slate-500" }),
          e("span", { className: "text-[10.5px] font-semibold text-white" }, "敢给差评信号"),
        ),
        hasHistory
          ? e("span", { className: "rounded bg-emerald-400/[0.12] px-1.5 py-0.5 text-[9px] text-emerald-200" },
              "有历史批评证据 · 可信度加分")
          : e("span", { className: "rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] text-slate-400" },
              "未检出(不代表没有)"),
      ),
      // 证据摘录:词表命中原文(来源如实标注),绝不杜撰
      example && e("div", { className: "mt-1.5 rounded border border-emerald-300/10 bg-emerald-400/[0.04] px-2 py-1.5" },
        e("div", { className: "text-[9px] text-emerald-300/80" },
          (SOURCE_LABELS[String(example.source || "")] || String(example.source || "")) +
          (example.title ? " · " + String(example.title).slice(0, 60) : "")),
        e("div", { className: "mt-0.5 text-[10px] leading-relaxed text-slate-300" }, "「" + String(example.quote || "") + "」"),
      ),
      e("div", { className: "mt-1 text-[9px] leading-relaxed text-slate-500" },
        String(critic?.basis || ""),
        hasHistory && (counts.deep_analysis || 0) + (counts.creator_title || 0) + (counts.creator_description || 0) > 1
          ? " · 共 " + ((counts.deep_analysis || 0) + (counts.creator_title || 0) + (counts.creator_description || 0)) + " 处证据"
          : "",
      ),
    ),
    // ── ② 外联三承诺(打行业三大怨气;开关态如实展示)──
    promises && e("div", { className: "mt-2 rounded-md border border-white/[0.05] bg-black/20 p-2.5" },
      e("div", { className: "flex items-center justify-between gap-2" },
        e("div", { className: "flex items-center gap-1.5" },
          e(ShieldCheck, { size: 11, className: "text-sky-300" }),
          e("span", { className: "text-[10.5px] font-semibold text-white" }, "外联三承诺(嵌入草稿)"),
        ),
        promises.enabled === false
          ? e("span", { className: "rounded bg-amber-300/[0.12] px-1.5 py-0.5 text-[9px] text-amber-200" }, "开关已关闭")
          : e("span", { className: "rounded bg-sky-400/[0.10] px-1.5 py-0.5 text-[9px] text-sky-200" }, "已启用"),
      ),
      e("ul", { className: "mt-1.5 space-y-1" },
        items.map((it, i) => e("li", { key: it.key || i, className: "flex items-start gap-1.5" },
          e(HandCoins, { size: 10, className: "mt-0.5 shrink-0 text-sky-300/70" }),
          e("div", { className: "min-w-0" },
            e("div", { className: "text-[10px] leading-snug text-slate-300" }, String(it.zh || "")),
            it.pain_point_zh && e("div", { className: "text-[9px] text-slate-600" }, "对撞怨气:" + it.pain_point_zh),
          ),
        )),
      ),
      e("div", { className: "mt-1.5 text-[9px] leading-relaxed text-slate-600" },
        "生成外联包/联系草稿时自动逐字嵌入中英正文;草稿仅供人审后手动外发。"),
    ),
  );
}
