// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { AlertTriangle, Check, ChevronRight, Globe2, Loader2, MapPin, Search, Star, Target, Upload } from "lucide-react";
import { COUNTRY_INFO } from "../data/countryInfo";

const e = React.createElement;

export function FilterBar({ search, setSearch, country, setCountry, audienceType, setAudienceType, trendLevel, setTrendLevel, sortBy, setSortBy, hasViltrox, setHasViltrox, hasCompetitor, setHasCompetitor, searchMode, setSearchMode, kindFilter, setKindFilter, kindCounts, myListFilter, setMyListFilter, myListCount }) {
  const [localApplying, setLocalApplying] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [examplesOpen, setExamplesOpen] = useState(false);
  const advancedActive = Boolean(country || audienceType || trendLevel || hasViltrox || hasCompetitor || sortBy !== "v6_fit");
  const exampleChips = [
    "5 月上传 35mm 评测的 YouTube 摄影师",
    "友商用户 · Geo A 级 · 未联系过",
    "Real ER ≥ 5% · 升级窗口 high",
    "命中本周 #side-by-side 的全域 KOL",
  ];
  const applyLocalSearch = (q) => {
    if (q !== undefined) setSearch(q);
    setLocalApplying(true);
    setTimeout(() => setLocalApplying(false), 300);
  };
  
  const modeChips = [
    { key: "balanced",  label: "平衡",  hint: "老 15 · 新 15",  color: "rgba(168,85,247,0.30)" },
    { key: "precision", label: "精准",  hint: "老 20 · 新 10",  color: "rgba(34,197,94,0.30)" },
    { key: "discovery", label: "探索",  hint: "老 10 · 新 20",  color: "rgba(6,182,212,0.30)" },
  ];
  
  const kindChips = [
    { key: "",                        label: "全部",      n: kindCounts.total },
    { key: "existing",                label: "已有库",    n: kindCounts.existing,         dotColor: "#86efac" },
    { key: "existing_low_confidence", label: "待补全",    n: kindCounts.lowConfidence,    dotColor: "#fdba74" },
    { key: "new",                     label: "新发现",    n: kindCounts.new,              dotColor: "#c4b5fd" },
    { key: "new_promoted",            label: "高潜",      n: kindCounts.newPromoted,      dotColor: "#c4b5fd" },
    { key: "new_discovered",          label: "校验中",    n: kindCounts.newDiscovered,    dotColor: "#cbd5e1" },
  ];
  
  return e("div", { className: "mb-3 space-y-2 rounded-lg border border-white/[0.065] bg-white/[0.018] p-2.5" },
    // ── 本地搜索行 ──
    e("div", { className: "space-y-2" },
      e("div", { className: "flex items-center gap-2" },
        e("div", { 
          className: "relative flex-1 transition-all duration-200",
          style: localApplying 
            ? { boxShadow: "0 0 0 1px rgba(168,85,247,0.5), 0 0 16px rgba(168,85,247,0.25)" }
            : {}
        },
          e(Search, { 
            size: 13, 
            className: "absolute left-3 top-1/2 -translate-y-1/2 transition-colors", 
            style: { color: localApplying ? "#c4b5fd" : "rgba(168,85,247,0.7)" } 
          }),
          e("input", {
            type: "text", value: search, onChange: ev => setSearch(ev.target.value),
            onKeyDown: ev => { if (ev.key === "Enter") applyLocalSearch(); },
            placeholder: "输入关键词、@handle 或 URL,在本地 KOL Pool 内筛选...",
            className: "w-full rounded-md border border-white/[0.075] bg-white/[0.018] py-1.5 pl-9 pr-11 text-[12px] text-white outline-none placeholder-slate-500 focus:border-purple-500/40"
          }),
          e("button", {
            onClick: () => applyLocalSearch(),
            disabled: localApplying,
            className: "absolute right-2 top-1/2 -translate-y-1/2 flex items-center justify-center h-6 w-6 rounded-md bg-purple-600 hover:bg-purple-500 text-white disabled:opacity-60",
            title: "本地筛选(Enter)"
          }, localApplying 
            ? e(Loader2, { size: 12, className: "animate-spin" })
            : e(ChevronRight, { size: 13 })
          )
        ),
        // ── 搜索模式选择(老/新比例) ──
          e("div", { className: "flex shrink-0 items-center overflow-hidden rounded-md border border-white/[0.075] bg-white/[0.018]" },
            modeChips.map(m => e("button", {
              key: m.key,
              onClick: () => setSearchMode(m.key),
              className: "px-2.5 py-1.5 text-[10px] transition-colors",
              title: m.hint,
              style: searchMode === m.key
                ? { background: m.color, color: "#fff" }
                : { color: "rgba(148,163,184,0.7)" }
          }, e("span", { className: "font-medium" }, m.label)))
        ),
        e("button", {
          disabled: true,
          title: "待接入: 需要导入预览 API，不执行批量导入",
          className: "hidden cursor-not-allowed items-center gap-1.5 rounded-md border border-white/[0.075] px-2.5 py-1.5 text-[10.5px] text-slate-500 opacity-70 shrink-0 xl:flex"
        }, e(Upload, { size: 11 }), "一键导入 · 待接入")
      ),
      e("div", { className: "flex items-center gap-1.5 flex-wrap pl-1" },
        e("button", {
          type: "button",
          onClick: () => setExamplesOpen((open) => !open),
          className: "inline-flex items-center gap-1 rounded text-[10px] text-slate-500 hover:text-purple-200"
        },
          examplesOpen ? "收起试例" : "展开试例",
          e(ChevronRight, { size: 9, className: "transition-transform " + (examplesOpen ? "rotate-90" : "") })
        ),
        examplesOpen && exampleChips.map((c, i) => e("button", {
          key: i,
          onClick: () => applyLocalSearch(c),
          className: "px-2 py-0.5 rounded text-[10px] border border-white/[0.06] bg-white/[0.015] text-slate-400 hover:bg-purple-500/[0.08] hover:border-purple-500/30 hover:text-purple-200 transition-colors"
        }, c))
      )
    ),
    // ── 候选类型 chip 过滤行 ──
    e("div", { className: "flex flex-wrap items-center gap-1.5 border-t border-white/[0.04] pt-2" },
      e("span", { className: "text-[10px] text-slate-500 uppercase tracking-wider mr-1" }, "候选类型"),
      kindChips.map(c => e("button", {
        key: c.key || "all",
        onClick: () => setKindFilter(c.key),
        className: "flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[10px] transition-colors",
        style: kindFilter === c.key
          ? { background: "rgba(168,85,247,0.14)", borderColor: "rgba(168,85,247,0.35)", color: "#fff" }
          : { borderColor: "rgba(255,255,255,0.08)", color: "rgba(148,163,184,0.8)", background: "rgba(255,255,255,0.015)" }
      },
        c.dotColor && e("span", { style: { width: 5, height: 5, borderRadius: "50%", background: c.dotColor } }),
        e("span", null, c.label),
        e("span", { className: "tabular-nums text-[9.5px] opacity-70" }, c.n)
      )),
      // spacer
      e("div", { className: "flex-1" }),
      // ── 我的列表 toggle ──
      e("button", {
        onClick: () => setMyListFilter(!myListFilter),
        className: "flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[10px] transition-colors shrink-0",
        title: myListFilter ? "显示全部" : "只显示我的列表",
        style: myListFilter
          ? { background: "rgba(251,191,36,0.14)", borderColor: "rgba(251,191,36,0.45)", color: "#fbbf24" }
          : { borderColor: "rgba(251,191,36,0.18)", color: "rgba(251,191,36,0.8)", background: "rgba(251,191,36,0.04)" }
      },
        e(Star, { size: 10, style: { fill: myListFilter ? "#fbbf24" : "transparent" } }),
        e("span", null, "我的列表"),
        e("span", { className: "tabular-nums text-[9.5px] opacity-80" }, myListCount)
      )
    ),
    e("div", { className: "flex items-center justify-between gap-2 border-t border-white/[0.04] pt-2" },
      e("span", { className: "text-[10px] text-slate-500" },
        advancedActive ? "高级筛选已启用" : "高级筛选未启用"
      ),
      e("button", {
        type: "button",
        onClick: () => setAdvancedOpen((open) => !open),
        className: "inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.018] px-2 py-1 text-[10px] text-slate-400 transition-colors hover:border-white/[0.14] hover:text-white",
      },
        advancedOpen ? "收起高级筛选" : "展开高级筛选",
        e(ChevronRight, { size: 10, className: "transition-transform " + (advancedOpen ? "rotate-90" : "") })
      )
    ),
    // ── 旧筛选行 ──
    advancedOpen && e("div", { className: "flex flex-wrap items-center gap-2 text-[10px] pt-2 border-t border-white/[0.04]" },
      // Country
      e("div", { className: "flex items-center gap-1.5" },
        e("span", { className: "text-slate-500 uppercase tracking-wider" }, "国家"),
        e("select", {
          value: country, onChange: ev => setCountry(ev.target.value),
          className: "rounded-md border border-white/[0.08] bg-white/[0.02] px-2 py-1 text-[10px] text-white outline-none"
        },
          e("option", { value: "" }, "全部"),
          Object.entries(COUNTRY_INFO).filter(([k]) => k !== "CN").map(([k, v]) => 
            e("option", { key: k, value: k }, v.flag + " " + k + " · " + v.tier)
          )
        )
      ),
      // Type
      e("div", { className: "flex items-center gap-1.5" },
        e("span", { className: "text-slate-500 uppercase tracking-wider" }, "类型"),
        e("div", { className: "flex items-center rounded-md border border-white/[0.08] bg-white/[0.02] overflow-hidden" },
          [
            { key: "", label: "全部", icon: null },
            { key: "Global", label: "全域", icon: Globe2 },
            { key: "Regional", label: "区域", icon: MapPin },
            { key: "Local", label: "Local", icon: Target },
          ].map(t =>
            e("button", {
              key: t.key, onClick: () => setAudienceType(t.key),
              className: "flex items-center gap-1 px-2 py-1 transition-colors",
              style: audienceType === t.key 
                ? { background: "rgba(168,85,247,0.15)", color: "#fff" }
                : { color: "rgba(148,163,184,0.7)" }
            }, t.icon && e(t.icon, { size: 9 }), t.label)
          )
        )
      ),
      // Trend
      e("div", { className: "flex items-center gap-1.5" },
        e("span", { className: "text-slate-500 uppercase tracking-wider" }, "Trend"),
        e("select", {
          value: trendLevel, onChange: ev => setTrendLevel(ev.target.value),
          className: "rounded-md border border-white/[0.08] bg-white/[0.02] px-2 py-1 text-[10px] text-white outline-none"
        },
          e("option", { value: "" }, "全部"),
          e("option", { value: "high" }, "高 ≥0.6"),
          e("option", { value: "medium" }, "中 0.3-0.6"),
          e("option", { value: "low" }, "低 <0.3"),
        )
      ),
      // Devices toggle
      e("button", {
        onClick: () => setHasViltrox(!hasViltrox),
        className: "flex items-center gap-1 px-2 py-1 rounded-md border text-[10px] transition-colors",
        style: hasViltrox
          ? { background: "rgba(168,85,247,0.15)", borderColor: "rgba(168,85,247,0.3)", color: "#c4b5fd" }
          : { borderColor: "rgba(255,255,255,0.08)", color: "rgba(148,163,184,0.7)" }
      }, e(Check, { size: 9 }), "已用 Viltrox"),
      e("button", {
        onClick: () => setHasCompetitor(!hasCompetitor),
        className: "flex items-center gap-1 px-2 py-1 rounded-md border text-[10px] transition-colors",
        style: hasCompetitor
          ? { background: "rgba(248,113,113,0.12)", borderColor: "rgba(248,113,113,0.3)", color: "#fca5a5" }
          : { borderColor: "rgba(255,255,255,0.08)", color: "rgba(148,163,184,0.7)" }
      }, e(AlertTriangle, { size: 9 }), "友商用户"),
      // Sort
      e("div", { className: "flex items-center gap-1.5 ml-auto" },
        e("span", { className: "text-slate-500 uppercase tracking-wider" }, "排序"),
        e("select", {
          value: sortBy, onChange: ev => setSortBy(ev.target.value),
          className: "rounded-md border border-white/[0.08] bg-white/[0.02] px-2 py-1 text-[10px] text-white outline-none"
        },
          e("option", { value: "v6_fit" }, "V6 Fit ↓"),
          e("option", { value: "real_er" }, "Real ER ↓"),
          e("option", { value: "loyalty" }, "Loyalty ↓"),
          e("option", { value: "trend" }, "Trend ↓"),
          e("option", { value: "upgrade" }, "Upgrade ↓"),
          e("option", { value: "followers" }, "粉丝 ↓"),
        )
      )
    )
  );
}
