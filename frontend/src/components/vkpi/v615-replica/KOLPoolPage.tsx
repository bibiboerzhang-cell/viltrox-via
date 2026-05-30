// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useMemo, useState } from "react";
import { AnimatePresence } from "framer-motion";
import { Info, Search, Star, X } from "lucide-react";
import { FilterBar } from "./components/FilterBar";
import { KOLDetailDrawer } from "./components/KOLDetailDrawer";
import { KOLTable } from "./components/KOLTable";
import { KPIBar } from "./components/KPIBar";
import { MarketCoverageCard } from "./components/MarketCoverageCard";
import { SearchProgressBar } from "./components/SearchProgressBar";
import { TrendPulseBar } from "./components/TrendPulseBar";
import { ContactModal } from "./components/modals/ContactModal";
import { getKolPoolItem } from "../../../domains/kol";
import { toV615KolPoolRows } from "./kolPoolRuntime";
import { candidateKindGroup } from "./lib/candidateKind";
import { CANDIDATE_KIND_INFO } from "./data/candidateKindInfo";
import { normalizeCountryCode } from "./data/countryInfo";

const e = React.createElement;

export function KOLPoolPage({ items: sourceItems = [], loading = false, error = "", apiToken = "" } = {}) {
  const [search, setSearch] = useState("");
  const [country, setCountry] = useState("");
  const [audienceType, setAudienceType] = useState("");
  const [trendLevel, setTrendLevel] = useState("");
  const [sortBy, setSortBy] = useState("v6_fit");
  const [hasViltrox, setHasViltrox] = useState(false);
  const [hasCompetitor, setHasCompetitor] = useState(false);
  const [selectedItem, setSelectedItem] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [myList, setMyList] = useState(new Set());
  const [contactItem, setContactItem] = useState(null);
  // Search v2 state
  const [searchMode, setSearchMode] = useState("balanced"); // balanced | precision | discovery
  const [kindFilter, setKindFilter] = useState("");          // "" | "existing" | "new" | specific kind
  const [myListFilter, setMyListFilter] = useState(false);   // toggle: only show items in myList
  const poolItems = Array.isArray(sourceItems) ? sourceItems : [];
  
  const toggleMyList = (id) => {
    setMyList(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const openItem = async (item) => {
    setSelectedItem(item);
    setDetailError("");
    setDetailLoading(Boolean(apiToken && item?.id));
    if (!apiToken || !item?.id) return;
    try {
      const detail = await getKolPoolItem(apiToken, item.id, false);
      const normalized = toV615KolPoolRows([detail.item || item])[0];
      setSelectedItem({ ...item, ...normalized, freshness: detail.freshness, refresh: detail.refresh });
    } catch (err) {
      const msg = err?.message || err?.detail || "详情接口读取失败";
      setDetailError(String(msg).slice(0, 120));
      setSelectedItem(item);
    } finally {
      setDetailLoading(false);
    }
  };
  
  // Base filter (no kind filter applied) — used for kindCounts so chips always show full pool counts.
  const filteredBase = useMemo(() => {
    return poolItems.filter(it => {
      if (search) {
        const q = search.toLowerCase();
        const hay = [it.handle, it.display_name, it.bio, it.country, it.devices?.camera_body, ...(it.devices?.lenses || []).map(l => l.model || "")].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (country && !it.geo_distribution?.some(g => normalizeCountryCode(g.country) === country)) return false;
      if (audienceType && it.audience_type !== audienceType) return false;
      const trend = typeof it.trend_resonance === "number" ? it.trend_resonance : null;
      if (trendLevel && trend == null) return false;
      if (trendLevel === "high"   && trend < 0.6) return false;
      if (trendLevel === "medium" && (trend < 0.3 || trend >= 0.6)) return false;
      if (trendLevel === "low"    && trend >= 0.3) return false;
      if (hasViltrox    && !it.devices?.has_viltrox) return false;
      if (hasCompetitor && !(it.devices?.competitor_brands && it.devices.competitor_brands.length > 0)) return false;
      return true;
    });
  }, [poolItems, search, country, audienceType, trendLevel, hasViltrox, hasCompetitor]);

  const kindCounts = useMemo(() => {
    return {
      total:           filteredBase.length,
      existing:        filteredBase.filter(it => candidateKindGroup(it.candidate_kind) === "existing").length,
      lowConfidence:   filteredBase.filter(it => it.candidate_kind === "existing_low_confidence").length,
      new:             filteredBase.filter(it => candidateKindGroup(it.candidate_kind) === "new").length,
      newPromoted:     filteredBase.filter(it => it.candidate_kind === "new_promoted").length,
      newDiscovered:   filteredBase.filter(it => it.candidate_kind === "new_discovered").length,
    };
  }, [filteredBase]);
  
  const items = useMemo(() => {
    let arr = filteredBase.filter(it => {
      if (myListFilter && !myList.has(it.id)) return false;
      if (!kindFilter) return true;
      if (kindFilter === "existing") return candidateKindGroup(it.candidate_kind) === "existing";
      if (kindFilter === "new")      return candidateKindGroup(it.candidate_kind) === "new";
      return it.candidate_kind === kindFilter;
    });
    // Search v2 ordering: bring "new" candidates up so the mode quotas are visible.
    // In prod, this is computed by select_balanced_top30() on the backend; here we
    // just demonstrate the visual effect of interleaving.
    arr.sort((a, b) => {
      if (sortBy === "v6_fit")    return (b.v6_fit || 0) - (a.v6_fit || 0);
      if (sortBy === "real_er")   return (b.real_er_pct || 0) - (a.real_er_pct || 0);
      if (sortBy === "loyalty")   return (b.loyalty_score || 0) - (a.loyalty_score || 0);
      if (sortBy === "trend")     return (b.trend_resonance ?? -1) - (a.trend_resonance ?? -1);
      if (sortBy === "upgrade")   return (b.upgrade_factor || 0) - (a.upgrade_factor || 0);
      if (sortBy === "followers") return (b.followers || 0) - (a.followers || 0);
      return 0;
    });
    return arr;
  }, [filteredBase, kindFilter, sortBy, myListFilter, myList]);
  
  return e(React.Fragment, null,
    e("div", { className: "p-5" },
        // ─── Page meta (V6 算法 subtitle + 数据时效) ───
        e("div", { className: "flex items-center justify-between mb-4 pb-3 border-b border-white/[0.04]" },
          e("div", { className: "flex items-baseline gap-2 min-w-0" },
            e("span", { className: "text-[12px] text-slate-500" }, "V6 算法"),
            e("span", { className: "text-slate-700" }, "·"),
            e("span", { className: "text-[11.5px] text-slate-400 truncate" }, "Real ER + 海外 Geo + Loyalty + Trend + 设备分析")
          ),
          e("div", { className: "flex items-center gap-1.5 text-[10.5px] text-slate-500 shrink-0" },
            e("span", { className: "h-1.5 w-1.5 rounded-full bg-emerald-400 inline-block" }),
            e("span", null, loading ? "正在读取真实 API" : error ? "真实 API 无信号" : "真实 API")
          )
        ),
        e(KPIBar, {
          items: poolItems,
          onCardClick: (k) => setKindFilter(kindFilter === k ? "" : k),
          activeKindFilter: kindFilter,
        }),
        e(TrendPulseBar),
        e(MarketCoverageCard, { items: poolItems }),
        e(FilterBar, {
          search, setSearch, country, setCountry, audienceType, setAudienceType,
          trendLevel, setTrendLevel, sortBy, setSortBy,
          hasViltrox, setHasViltrox, hasCompetitor, setHasCompetitor,
          searchMode, setSearchMode, kindFilter, setKindFilter, kindCounts,
          myListFilter, setMyListFilter, myListCount: myList.size,
        }),
        e(SearchProgressBar, { items: filteredBase, searchActive: !!search }),
        e("div", { className: "flex items-center justify-between mb-3" },
          e("div", { className: "text-[11px] text-slate-400 flex items-center gap-2 flex-wrap" },
            e("span", null,
              "显示 ", e("span", { className: "text-white font-medium" }, items.length),
              " / " + poolItems.length + " 个 KOL"
            ),
            e("span", { className: "text-slate-600" }, "·"),
            e("span", { className: "text-slate-500" },
              "模式 ", e("span", { className: "text-purple-300" }, searchMode === "balanced" ? "平衡 (15+15)" : searchMode === "precision" ? "精准 (20+10)" : "探索 (10+20)")
            ),
            kindFilter && e("span", { className: "flex items-center gap-1 px-1.5 py-0 rounded text-[10px] border border-purple-500/30 bg-purple-500/[0.08] text-purple-200" },
              "筛选: " + (kindFilter === "existing" ? "已有库" : kindFilter === "new" ? "新发现" : (CANDIDATE_KIND_INFO[kindFilter]?.short || kindFilter)),
              e("button", { onClick: () => setKindFilter(""), className: "hover:text-white" }, e(X, { size: 9 }))
            ),
            myListFilter && e("span", { className: "flex items-center gap-1 px-1.5 py-0 rounded text-[10px] border border-amber-500/30 bg-amber-500/[0.08] text-amber-300" },
              e(Star, { size: 9, style: { fill: "#fbbf24" } }), "仅显示我的列表",
              e("button", { onClick: () => setMyListFilter(false), className: "hover:text-white" }, e(X, { size: 9 }))
            ),
          ),
          e("div", { className: "flex items-center gap-3 text-[10px] text-slate-500" },
            e("span", { className: "flex items-center gap-1" }, e(Info, { size: 10 }), "点击行查看完整 V6 breakdown"),
          )
        ),
        e(KOLTable, {
          items,
          onRowClick: openItem,
          selectedItemId: selectedItem?.id,
          myList,
        })
        ,
        (loading || error || poolItems.length === 0) && e("div", {
          className: "mt-3 rounded-xl border border-white/[0.08] bg-white/[0.025] px-4 py-3 text-[11px] text-slate-400"
        }, loading ? "正在读取真实 KOL Pool API" : error ? "真实 KOL Pool API 无信号: " + error : "暂无真实 KOL Pool 数据")
    ),
    e(AnimatePresence, null,
      selectedItem && e(KOLDetailDrawer, {
        key: `kol-detail-${selectedItem.id || selectedItem.handle || "selected"}`,
        item: selectedItem,
        detailLoading,
        detailError,
        onClose: () => {
          setSelectedItem(null);
          setDetailLoading(false);
          setDetailError("");
        },
        inMyList: myList.has(selectedItem.id),
        onToggleMyList: toggleMyList,
        onContact: (it) => setContactItem(it),
      }),
      contactItem && e(ContactModal, {
        key: `kol-contact-${contactItem.id || contactItem.handle || "selected"}`,
        item: contactItem,
        onClose: () => setContactItem(null),
      })
    )
  );
}
