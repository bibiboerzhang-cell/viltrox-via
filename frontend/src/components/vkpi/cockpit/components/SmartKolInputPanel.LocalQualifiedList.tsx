import { CheckCircle2, Clock3, ShieldAlert } from "lucide-react";

import type { VkpiKolRecallItem, VkpiKolRecallResponse } from "../../../../domains/kol";

import { localQualifiedSummary, type LocalQualifiedRow } from "./SmartKolInputPanel.LocalQualified";

function compactNumber(value: number | null): string {
  if (value == null) return "待核验";
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function dateLabel(value: string): string {
  if (!value) return "待核验";
  // Activity qualification is day-based in UTC. Preserve an explicit ISO calendar date instead
  // of shifting midnight into the previous day in western browser time zones.
  const isoDay = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (isoDay) return `${isoDay[1]}/${isoDay[2]}/${isoDay[3]}`;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 16);
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(parsed);
}

function qualificationTone(row: LocalQualifiedRow): string {
  if (row.qualification === "qualified") return "border-emerald-300/25 bg-emerald-400/[0.10] text-emerald-100";
  if (row.qualification === "rejected") return "border-rose-300/20 bg-rose-400/[0.08] text-rose-100";
  return "border-amber-300/20 bg-amber-400/[0.08] text-amber-100";
}

function statusTone(value: string): string {
  if (["可联系", "已完成"].includes(value)) return "text-emerald-200";
  if (value.includes("中")) return "text-cyan-200";
  if (value.includes("失败")) return "text-rose-200";
  return "text-slate-400";
}

export function LocalQualifiedList({
  result,
  onOpen,
}: {
  result: VkpiKolRecallResponse;
  onOpen?: (item: VkpiKolRecallItem) => void;
}) {
  const summary = localQualifiedSummary(result);
  return (
    <div className="space-y-2" data-testid="local-qualified-list">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-violet-300/20 bg-black/20 px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-violet-100">
            <CheckCircle2 size={12} /> 本地合格 {summary.qualified}/{summary.target}
          </span>
          <span className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[9.5px] text-slate-400">
            服务端返回 {summary.serverReturned}
          </span>
          <span className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[9.5px] text-slate-400">
            过闸候选 {summary.serverQualified}
          </span>
          <span className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[9.5px] text-slate-400">
            合格唯一 {summary.uniqueQualified}
          </span>
          {summary.pending > 0 ? (
            <span className="inline-flex items-center gap-1 rounded border border-amber-300/15 bg-amber-400/[0.05] px-1.5 py-0.5 text-[9.5px] text-amber-100/80">
              <Clock3 size={9} /> 待验收 {summary.pending}（不计入）
            </span>
          ) : null}
        </div>
        <span className="text-[9.5px] text-slate-500">按服务端排名逐条出现 · 不在浏览器重算资格</span>
      </div>

      {summary.shortfall > 0 ? (
        <div className="flex items-start gap-1.5 rounded-md border border-amber-300/20 bg-amber-400/[0.06] px-2.5 py-1.5 text-[10px] text-amber-100/90">
          <ShieldAlert size={11} className="mt-0.5 shrink-0" />
          <span>
            还缺 {summary.shortfall} 人
            {summary.shortfallReasons.length ? ` · ${summary.shortfallReasons.join("；")}` : ""}
          </span>
        </div>
      ) : (
        <div className="rounded-md border border-emerald-300/20 bg-emerald-400/[0.06] px-2.5 py-1.5 text-[10px] text-emerald-100">
          本地 30 人硬闸已满足；未知或待核验候选未计入。
        </div>
      )}

      {summary.rows.length ? (
        <div className="overflow-x-auto rounded-lg border border-white/[0.07]">
          <table className="min-w-[1080px] w-full border-collapse text-left text-[10px]">
            <thead className="bg-white/[0.035] text-slate-500">
              <tr>
                <th className="w-10 px-2 py-2 font-medium">排名</th>
                <th className="min-w-36 px-2 py-2 font-medium">KOL</th>
                <th className="w-24 px-2 py-2 font-medium">平台</th>
                <th className="w-24 px-2 py-2 font-medium">粉丝</th>
                <th className="w-28 px-2 py-2 font-medium">最新视频</th>
                <th className="min-w-36 px-2 py-2 font-medium">市场证据</th>
                <th className="min-w-52 px-2 py-2 font-medium">为什么匹配</th>
                <th className="w-24 px-2 py-2 font-medium">联系方式</th>
                <th className="w-24 px-2 py-2 font-medium">分析</th>
                <th className="w-28 px-2 py-2 font-medium">硬闸</th>
              </tr>
            </thead>
            <tbody>
              {summary.rows.map((row) => (
                <tr
                  key={row.identity}
                  className="border-t border-white/[0.055] bg-black/10 text-slate-300 hover:bg-violet-400/[0.035]"
                >
                  <td className="px-2 py-2 tabular-nums text-slate-500">{row.rank}</td>
                  <td className="px-2 py-2">
                    <button
                      type="button"
                      onClick={() => onOpen?.(row.item)}
                      className="max-w-52 truncate text-left font-medium text-slate-100 hover:text-cyan-100"
                      title={row.name}
                    >
                      {row.name}
                    </button>
                  </td>
                  <td className="px-2 py-2 uppercase text-slate-400">{row.platform}</td>
                  <td className="px-2 py-2 tabular-nums">{compactNumber(row.followers)}</td>
                  <td className="px-2 py-2 tabular-nums">{dateLabel(row.latestVideoAt)}</td>
                  <td className="max-w-48 truncate px-2 py-2" title={row.marketEvidence || "待服务端提供市场证据"}>
                    {row.marketEvidence || "待核验"}
                  </td>
                  <td className="max-w-72 truncate px-2 py-2" title={row.whyFit || "待补充匹配依据"}>
                    {row.whyFit || "待补充"}
                  </td>
                  <td className={`px-2 py-2 ${statusTone(row.contactStatus)}`}>{row.contactStatus}</td>
                  <td className={`px-2 py-2 ${statusTone(row.analysisStatus)}`}>{row.analysisStatus}</td>
                  <td className="px-2 py-2">
                    <span className={`inline-flex rounded border px-1.5 py-0.5 text-[9px] ${qualificationTone(row)}`}>
                      {row.qualificationLabel}{row.qualification === "pending" ? "（不计数）" : ""}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-4 text-center text-[11px] text-slate-500">
          本地候选正在进入服务端排名流，首批到达后会立即显示。
        </div>
      )}
    </div>
  );
}
