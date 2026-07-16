import React from "react";
import { Share2, Sparkles } from "lucide-react";

import type { SkillRunResult } from "../../../../services/vkpi/skills-api";

const e = React.createElement;

export const DRAWER_TABS: Array<{ key: string; label: string }> = [
  { key: "overview", label: "概览" },
  { key: "deep", label: "深度分析" },
  { key: "audience", label: "受众" },
  { key: "coop", label: "合作" },
];

const DRAWER_TAB_STORAGE_KEY = "vkpi:drawer-active-tab";

export function readStoredDrawerTab(): string {
  try {
    const raw = window.localStorage.getItem(DRAWER_TAB_STORAGE_KEY);
    if (raw && DRAWER_TABS.some((tab) => tab.key === raw)) return raw;
  } catch {
    // 隐私模式读失败按默认「概览」。
  }
  return "overview";
}

export function storeDrawerTab(key: string): void {
  window.localStorage.setItem(DRAWER_TAB_STORAGE_KEY, key);
}

export function KOLDrawerViewerContextBar({
  viewerCtx,
  releaseBusy,
  releaseMsg,
  onRelease,
}: {
  viewerCtx: any;
  releaseBusy: boolean;
  releaseMsg: string;
  onRelease: () => void;
}) {
  if (!viewerCtx?.share_origin && !viewerCtx?.claim && !releaseMsg) return null;

  return e("div", {
    className: "flex flex-wrap items-center gap-1.5 border-b border-white/[0.06] px-5 py-1.5",
  },
    viewerCtx?.share_origin && e("span", {
      className: "rounded border border-purple-400/30 bg-purple-400/[0.08] px-1.5 py-0.5 text-[9px] font-medium text-purple-200",
      title: "该 KOL 经共享池(P-GROUP-7)共享给你 · 只读可见,非本人收藏"
        + (viewerCtx.share_origin.created_at ? " · " + String(viewerCtx.share_origin.created_at).slice(0, 10) : ""),
    }, "来自 " + (viewerCtx.share_origin.shared_by_name || "未知成员") + " 的共享"),
    viewerCtx?.claim && e("span", {
      className: "rounded border border-amber-400/25 bg-amber-400/[0.06] px-1.5 py-0.5 text-[9px] text-amber-200",
      title: "active 认领(vkpi_kol_claims)"
        + (viewerCtx.claim.expires_at ? " · 到期 " + String(viewerCtx.claim.expires_at).slice(0, 10) : ""),
    }, "已认领 · " + (viewerCtx.claim.staff_name || ("成员 " + (viewerCtx.claim.staff_id ?? "—")))),
    viewerCtx?.claim?.can_release && e("button", {
      type: "button",
      disabled: releaseBusy,
      onClick: onRelease,
      className: "rounded border border-amber-400/40 px-1.5 py-0.5 text-[9px] font-medium text-amber-300 transition-colors hover:bg-amber-400/[0.10] disabled:opacity-50",
      title: "释放认领:取消认领回池,他人可再认领(认领人本人或管理层可操作)",
    }, releaseBusy ? "释放中…" : "释放"),
    releaseMsg && e("span", {
      className: "text-[9px] " + (releaseMsg.startsWith("释放失败") ? "text-rose-300" : "text-emerald-300"),
    }, releaseMsg),
  );
}

export function KOLDrawerCoopActions({
  apiToken,
  item,
  onOpenShare,
}: {
  apiToken: string;
  item: any;
  onOpenShare: () => void;
}) {
  return e(React.Fragment, null,
    apiToken && item?.id && e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
      e("button", {
        type: "button",
        onClick: onOpenShare,
        className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-purple-400/25 bg-purple-400/[0.06] px-3 py-2 text-[11px] font-medium text-purple-200 transition-colors hover:bg-purple-400/[0.12]",
      },
        e(Share2, { size: 12 }),
        "共享给成员",
      ),
    ),
    item?.id && e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
      e("button", {
        type: "button",
        onClick: () => {
          window.sessionStorage.setItem("vkpi:kol-profile-id", String(item.id));
          window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile"));
        },
        className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-cyan-400/25 bg-cyan-400/[0.06] px-3 py-2 text-[11px] font-medium text-cyan-200 transition-colors hover:bg-cyan-400/[0.12]",
      },
        e(Sparkles, { size: 12 }),
        "查看完整档案",
      ),
    ),
  );
}

export function KOLDrawerBriefSkill({ result, busy, error, onRun }: {
  result: SkillRunResult | null;
  busy: boolean;
  error: string;
  onRun: () => void;
}) {
  const output = (result && typeof result.output === "object" ? result.output : null) as Record<string, unknown> | null;
  const brief = (output && typeof output.brief === "object" ? output.brief : null) as Record<string, unknown> | null;
  const list = (value: unknown): string[] => (Array.isArray(value) ? value.map((x) => String(x)).filter(Boolean) : []);
  const hook = brief && typeof brief.hook === "string" ? brief.hook : "";
  const talkingPoints = brief ? list(brief.talking_points) : [];
  const dos = brief ? list(brief.do) : [];
  const donts = brief ? list(brief.dont) : [];
  const deliverables = brief ? list(brief.deliverables) : [];
  const okFalse = output ? output.ok === false : false;
  const showResult = Boolean(result) && Boolean(brief) && !okFalse;

  const renderListBlock = (label: string, items: string[], color: string) =>
    items.length > 0 && e("div", { key: label },
      e("div", { className: "text-[9px] mb-1", style: { color } }, label),
      e("ul", { className: "space-y-0.5" },
        items.map((it, i) => e("li", { key: i, className: "text-[10px] text-slate-300 leading-snug" }, "· " + it)),
      ),
    );

  return e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center justify-between gap-2 mb-1.5" },
      e("div", { className: "text-[11px] font-semibold text-white" }, "合作 Brief 草案 · Skill"),
      result?.skill_run_id != null && e("span", {
        className: "text-[9px] px-1.5 py-0.5 rounded bg-white/[0.06] text-slate-400",
      }, "run #" + result.skill_run_id),
    ),
    e("button", {
      type: "button",
      disabled: busy,
      onClick: onRun,
      className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-emerald-400/25 bg-emerald-400/[0.06] px-3 py-2 text-[11px] font-medium text-emerald-200 transition-colors hover:bg-emerald-400/[0.12] disabled:opacity-50",
    },
      e(Sparkles, { size: 12 }),
      busy ? "跑 Skill 中…" : (showResult ? "重新生成 Brief" : "跑 Skill·生成合作 Brief"),
    ),
    error && e("div", { className: "mt-1.5 text-[9.5px] leading-relaxed text-rose-300" }, error),
    showResult && e("div", { className: "mt-2 rounded-md border border-white/[0.05] bg-black/20 p-2.5 space-y-2" },
      hook && e("div", null,
        e("div", { className: "text-[9px] text-emerald-300 mb-0.5" }, "开场钩子"),
        e("div", { className: "text-[10.5px] text-slate-200 leading-relaxed" }, hook),
      ),
      renderListBlock("内容要点", talkingPoints, "#06b6d4"),
      renderListBlock("建议做", dos, "#10b981"),
      renderListBlock("避免", donts, "#fb7185"),
      renderListBlock("交付物", deliverables, "#a855f7"),
      output && typeof output.model_used === "string" && e("div", {
        className: "text-[9px] text-slate-500 pt-1",
      }, "模型 " + output.model_used + " · 草案仅供人审后编辑"),
    ),
  );
}
