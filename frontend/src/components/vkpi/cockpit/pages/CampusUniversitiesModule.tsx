/**
 * 校园大学目录模块(Events 板块「校园活动」入口)。
 *
 * 数据 = 打包人审快照(2026-07-17 官方 .edu 页面逐一核实:专业存在 + 系页可
 * 回跳;联系方式仅收机构级公开信息)。端点零联网零写库;本模块只读展示 +
 * 地区/名校筛选,发函前应回访系页复核 —— 卡面如实标注快照口径,绝不装实时。
 */
import React from "react";
import {
  getCampusUniversities,
  type VkpiCampusUniversities,
  type VkpiCampusUniversity,
} from "../../../../services/vkpi/events-api";

const REGION_TABS: Array<{ key: string; label: string }> = [
  { key: "", label: "全部地区" },
  { key: "northeast", label: "东北部" },
  { key: "south", label: "南部" },
  { key: "midwest", label: "中西部" },
  { key: "west", label: "西部" },
];

const TIER_BADGE: Record<string, { label: string; cls: string }> = {
  A: { label: "摄影名校", cls: "border-warn bg-warn-soft text-warn" },
  B: { label: "综合大学", cls: "border-good bg-good-soft text-good" },
};

export function CampusUniversitiesModule({ apiToken = "" }: { apiToken?: string }) {
  const [data, setData] = React.useState<VkpiCampusUniversities | null>(null);
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [region, setRegion] = React.useState("");
  const [tierAOnly, setTierAOnly] = React.useState(false);
  const [openIdx, setOpenIdx] = React.useState<number | null>(null);

  React.useEffect(() => {
    if (!apiToken) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void getCampusUniversities(apiToken)
      .then((resp) => {
        if (!cancelled) {
          setData(resp);
          setError("");
        }
      })
      .catch((err: any) => {
        if (!cancelled) setError(err?.message || "读取失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  const rows = React.useMemo(() => {
    const all = data?.universities ?? [];
    return all.filter(
      (u) => (!region || u.region === region) && (!tierAOnly || u.tier === "A"),
    );
  }, [data, region, tierAOnly]);

  if (!apiToken) return <div className="text-xs text-muted">登录后可见</div>;
  if (loading) return <div className="text-xs text-muted">读取校园目录…</div>;
  if (error) return <div className="text-xs text-crit">校园目录读取失败 · {error}</div>;
  if (!data || data.status !== "ready" || !data.universities.length) {
    return <div className="text-xs text-muted">目录暂无条目</div>;
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {/* 筛选条 + 快照口径(诚实标注,发函前回访系页) */}
      <div className="flex flex-wrap items-center gap-1.5">
        {REGION_TABS.map((tab) => (
          <button
            key={tab.key || "all"}
            type="button"
            onClick={() => setRegion(tab.key)}
            className={`rounded-md border px-2 py-0.5 text-[10px] transition-colors ${
              region === tab.key
                ? "border-accent bg-accent-soft text-accent"
                : "border-line bg-panel text-ink-2 hover:border-line-strong"
            }`}
          >
            {tab.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setTierAOnly((v) => !v)}
          className={`rounded-md border px-2 py-0.5 text-[10px] transition-colors ${
            tierAOnly
              ? "border-warn bg-warn-soft text-warn"
              : "border-line bg-panel text-ink-2 hover:border-line-strong"
          }`}
        >
          只看摄影名校
        </button>
        <span className="ml-auto text-[9.5px] text-muted">
          {rows.length} 所 · 人审快照 {String(data.reviewed_at || "")}(发函前回访系页复核)
        </span>
      </div>

      {/* 列表(点行展开:切入点备注 + 联系方式) */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.map((u, idx) => {
          const badge = TIER_BADGE[u.tier] || TIER_BADGE.B;
          const open = openIdx === idx;
          return (
            <div key={`${u.name}-${u.state}`} className="border-b border-line last:border-b-0">
              <button
                type="button"
                onClick={() => setOpenIdx(open ? null : idx)}
                className="flex w-full items-center gap-2 px-1 py-1.5 text-left transition-colors hover:bg-accent-soft"
              >
                <span className="min-w-0 flex-1 truncate text-xs text-ink">{u.name}</span>
                <span className={`flex-none rounded-[5px] border px-1 py-px text-[8px] font-bold ${badge.cls}`}>
                  {badge.label}
                </span>
                <span className="flex-none text-[10px] text-muted">
                  {[u.city, u.state].filter(Boolean).join(", ")}
                </span>
              </button>
              {open && (
                <div className="flex flex-col gap-1 px-1 pb-2 text-[11px] text-ink-2">
                  {u.program && <div>{u.program}{u.degrees ? ` · ${u.degrees}` : ""}</div>}
                  {u.notes && <div className="text-[10.5px] text-muted">{u.notes}</div>}
                  <div className="flex flex-wrap items-center gap-2 pt-0.5 text-[10.5px]">
                    {u.dept_url && (
                      <a href={u.dept_url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                        系页 ↗
                      </a>
                    )}
                    {u.contact_email && (
                      <a href={`mailto:${u.contact_email}`} className="text-accent hover:underline">
                        {u.contact_email}
                      </a>
                    )}
                    {u.contact_phone && <span className="tabular-nums text-muted">{u.contact_phone}</span>}
                    {!u.contact_email && !u.contact_phone && (
                      <span className="text-muted">联系走系页联系表</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {!rows.length && <div className="px-1 py-3 text-xs text-muted">该筛选下暂无条目</div>}
      </div>
    </div>
  );
}
