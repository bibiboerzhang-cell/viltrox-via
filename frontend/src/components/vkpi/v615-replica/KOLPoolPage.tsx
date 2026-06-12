// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useCallback, useMemo, useState } from "react";
import { AnimatePresence } from "framer-motion";
import { ChevronDown, Info, Search, Star, X } from "lucide-react";
import { FilterBar } from "./components/FilterBar";
import { KOLDetailDrawer } from "./components/KOLDetailDrawer";
import { KOLTable } from "./components/KOLTable";
import { KPIBar } from "./components/KPIBar";
import { MarketCoverageCard } from "./components/MarketCoverageCard";
import { SearchProgressBar } from "./components/SearchProgressBar";
import { SmartKolInputPanel } from "./components/SmartKolInputPanel";
import { ContactModal } from "./components/modals/ContactModal";
import { KolPoolAllModal } from "./components/modals/KolPoolAllModal";
import { getKolPoolDetailBundle, getKolPoolItem } from "../../../domains/kol";
import { toV615KolPoolRows } from "./kolPoolRuntime";
import { candidateKindGroup } from "./lib/candidateKind";
import { CANDIDATE_KIND_INFO } from "./data/candidateKindInfo";
import { normalizeCountryCode } from "./data/countryInfo";

const e = React.createElement;

function avatarCandidate(item) {
  if (!item || typeof item !== "object") return "";
  return String(
    item.avatar_url ||
    item.avatarUrl ||
    item.avatar_image_url ||
    item.profile_image_url ||
    item.profileImageUrl ||
    item.source_fields?.avatar_url ||
    item.source_fields?.profile_image_url ||
    ""
  ).trim();
}

function kolIdFrom(item) {
  const raw = item?.id ?? item?.kol_pool_id ?? item?.kolPoolId;
  const id = Number(raw);
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function KOLPoolPage({ items: sourceItems = [], loading = false, error = "", apiToken = "" } = {}) {
  const [search, setSearch] = useState("");
  const [country, setCountry] = useState("");
  const [audienceType, setAudienceType] = useState("");
  const [trendLevel, setTrendLevel] = useState("");
  const [sortBy, setSortBy] = useState("v6_fit");
  const [hasViltrox, setHasViltrox] = useState(false);
  const [hasCompetitor, setHasCompetitor] = useState(false);
  const [selectedItem, setSelectedItem] = useState(null);
  const [selectedDetailBundle, setSelectedDetailBundle] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [myList, setMyList] = useState(new Set());
  const [contactItem, setContactItem] = useState(null);
  const [poolModalOpen, setPoolModalOpen] = useState(false);
  const [inlineListOpen, setInlineListOpen] = useState(false);
  // Search v2 state
  const [searchMode, setSearchMode] = useState("balanced"); // balanced | precision | discovery
  const [kindFilter, setKindFilter] = useState("");          // "" | "existing" | "new" | specific kind
  const [myListFilter, setMyListFilter] = useState(false);   // toggle: only show items in myList
  const [recallAvatarIndex, setRecallAvatarIndex] = useState(new Map());
  const poolItems = Array.isArray(sourceItems) ? sourceItems : [];
  // d14:as-of 戳 = 全池 max(last_seen_at),YYYY-MM-DD。
  const dataAsOf = useMemo(() => {
    let max = "";
    for (const it of poolItems || []) {
      const v = String(it?.last_seen_at || "");
      if (v && v > max) max = v;
    }
    return max ? max.slice(0, 10) : "";
  }, [poolItems]);


  const rememberRecallItems = useCallback((recallItems = []) => {
    if (!Array.isArray(recallItems) || recallItems.length === 0) return;
    setRecallAvatarIndex((prev) => {
      const next = new Map(prev);
      recallItems.forEach((item) => {
        const id = kolIdFrom(item);
        const avatar = avatarCandidate(item);
        if (id && avatar) next.set(id, avatar);
      });
      return next;
    });
  }, []);

  const avatarForItem = (item) => {
    const direct = avatarCandidate(item);
    if (direct) return direct;
    const id = kolIdFrom(item);
    return id ? recallAvatarIndex.get(id) || "" : "";
  };

  const mergeAvatarSeed = (item, avatar) => {
    if (!avatar) return item;
    return {
      ...item,
      avatar_url: item?.avatar_url || avatar,
      avatar_image_url: item?.avatar_image_url || avatar,
      profile_image_url: item?.profile_image_url || avatar,
    };
  };
  
  const toggleMyList = (id) => {
    setMyList(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const openItem = async (item) => {
    const seedAvatar = avatarForItem(item);
    const seedItem = mergeAvatarSeed(item, seedAvatar);
    setSelectedItem(seedItem);
    setSelectedDetailBundle(null);
    setDetailError("");
    setDetailLoading(Boolean(apiToken && seedItem?.id));
    if (!apiToken || !seedItem?.id) return;
    try {
      const bundle = await getKolPoolDetailBundle(apiToken, seedItem.id);
      const normalized = toV615KolPoolRows([bundle.item || seedItem])[0];
      setSelectedDetailBundle(bundle);
      setSelectedItem(mergeAvatarSeed({ ...seedItem, ...normalized }, seedAvatar));
    } catch (err) {
      try {
        const detail = await getKolPoolItem(apiToken, seedItem.id, false);
        const normalized = toV615KolPoolRows([detail.item || seedItem])[0];
        setSelectedItem(mergeAvatarSeed({ ...seedItem, ...normalized, freshness: detail.freshness, refresh: detail.refresh }, seedAvatar));
      } catch (fallbackErr) {
        const msg = fallbackErr?.message || fallbackErr?.detail || err?.message || err?.detail || "详情接口读取失败";
        setDetailError(String(msg).slice(0, 120));
        setSelectedItem(seedItem);
      }
    } finally {
      setDetailLoading(false);
    }
  };

  const openRecallItem = useCallback((recallItem) => {
    const id = kolIdFrom(recallItem);
    if (!id) return;
    const matched = poolItems.find((it) => kolIdFrom(it) === id) || {};
    const avatar = avatarCandidate(recallItem) || avatarCandidate(matched);
    void openItem(mergeAvatarSeed({
      ...matched,
      id,
      kol_pool_id: id,
      handle: recallItem.handle || matched.handle,
      display_name: recallItem.display_name || matched.display_name || recallItem.handle,
      platform: recallItem.platform || matched.platform,
      profile_type: recallItem.profile_type || matched.profile_type,
      followers: recallItem.followers ?? matched.followers,
      candidate_kind: matched.candidate_kind || "existing",
    }, avatar));
  }, [poolItems, recallAvatarIndex, apiToken]);
  
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
    e("div", { className: "p-4 sm:p-5" },
        e("section", {
          className: "mb-4 rounded-2xl border border-white/[0.065] bg-[linear-gradient(135deg,rgba(124,58,237,0.055),rgba(20,184,166,0.035)_42%,rgba(2,6,23,0.35))] p-3.5 shadow-[0_24px_72px_rgba(0,0,0,0.20)]"
        },
          e("div", { className: "mb-3 flex flex-col gap-2 border-b border-white/[0.045] pb-2.5 lg:flex-row lg:items-center lg:justify-between" },
            e("div", { className: "min-w-0" },
              e("div", { className: "flex flex-wrap items-center gap-2" },
                e("h1", { className: "text-[14px] font-semibold text-white" }, "KOL Pool · Command Center"),
                e("span", { className: "rounded-full border border-cyan-300/15 bg-cyan-400/[0.06] px-2 py-0.5 text-[9.5px] text-cyan-100" }, "URL / 召回 / 列表")
              ),
              e("div", { className: "mt-0.5 max-w-3xl text-[10px] leading-relaxed text-slate-500" },
                "一个入口处理 URL、建档、视频分析与语义召回；高级工具折叠保留。"
              )
            ),
            e("div", { className: "flex shrink-0 flex-wrap items-center gap-2 text-[10px] text-slate-500" },
              e("span", { className: "inline-flex items-center gap-1.5 rounded-full border border-emerald-300/15 bg-emerald-400/[0.06] px-2 py-1 text-emerald-100" },
                e("span", { className: "h-1.5 w-1.5 rounded-full bg-emerald-400" }),
                loading ? "正在读取真实 API" : error ? "真实 API 无信号" : "真实 API"
              ),
              e("span", { className: "rounded-full border border-white/[0.07] px-2 py-1" }, "V6 Fit 只读展示"),
              dataAsOf && e("span", {
                className: "rounded-full border border-amber-300/20 bg-amber-400/[0.06] px-2 py-1 text-amber-200/90",
                title: "全池最近一次抓取时间(max last_seen_at)。日刷新恢复前数据停更,诚实陈旧优于假新鲜。",
              }, `数据截至 ${dataAsOf}`)
            )
          ),
          e(KPIBar, {
            items: poolItems,
            onCardClick: (k) => {
              setKindFilter(kindFilter === k ? "" : k);
              setInlineListOpen(true);
            },
            activeKindFilter: kindFilter,
            onTotalClick: () => setPoolModalOpen(true),
          }),
          e("div", { className: "mt-2.5 space-y-2" },
            e(SmartKolInputPanel, { apiToken, onRecallItems: rememberRecallItems, onOpenRecallItem: openRecallItem })
          ),
          e("div", { className: "mt-2.5" },
            e(MarketCoverageCard, { items: poolItems })
          )
        ),
        e("section", { className: "mb-4 rounded-lg border border-white/[0.055] bg-white/[0.014]" },
          e("button", {
            type: "button",
            onClick: () => setInlineListOpen((open) => !open),
            className: "flex w-full items-center justify-between gap-3 px-3.5 py-2.5 text-left"
          },
            e("span", { className: "min-w-0" },
              e("span", { className: "block text-[11px] font-medium text-slate-300" }, "表格视图"),
              e("span", { className: "block truncate text-[10px] text-slate-600" },
                "默认收起；Pool 总数大窗已覆盖全量查看、搜索和打开详情。"
              )
            ),
            e("span", { className: "flex shrink-0 items-center gap-2 text-[10px] text-slate-500" },
              e("span", null, inlineListOpen ? "收起" : "展开"),
              e(ChevronDown, { size: 13, className: "transition-transform " + (inlineListOpen ? "rotate-180" : "") })
            )
          ),
          inlineListOpen && e("div", { className: "border-t border-white/[0.045] px-3 pb-3 pt-3" },
            e(FilterBar, {
              search, setSearch, country, setCountry, audienceType, setAudienceType,
              trendLevel, setTrendLevel, sortBy, setSortBy,
              hasViltrox, setHasViltrox, hasCompetitor, setHasCompetitor,
              searchMode, setSearchMode, kindFilter, setKindFilter, kindCounts,
              myListFilter, setMyListFilter, myListCount: myList.size,
            }),
            (search || kindFilter || myListFilter) && e(SearchProgressBar, { items: filteredBase, searchActive: !!search }),
            e("div", { className: "mb-3 flex flex-col gap-2 rounded-lg border border-white/[0.055] bg-white/[0.015] px-3 py-2 lg:flex-row lg:items-center lg:justify-between" },
              e("div", { className: "flex flex-wrap items-center gap-2 text-[10.5px] text-slate-500" },
                e("span", { className: "inline-flex items-center gap-1.5 text-slate-300" },
                  e(Search, { size: 10, className: "text-purple-300" }),
                  e("span", null, "结果"),
                  e("span", { className: "text-white font-medium tabular-nums" }, items.length),
                  e("span", { className: "text-slate-600" }, "/" + poolItems.length)
                ),
                e("span", { className: "rounded border border-white/[0.06] px-1.5 py-0.5 text-[9.5px] text-slate-500" },
                  searchMode === "balanced" ? "平衡 15+15" : searchMode === "precision" ? "精准 20+10" : "探索 10+20"
                ),
                kindFilter && e("span", { className: "inline-flex items-center gap-1 rounded border border-purple-500/25 bg-purple-500/[0.07] px-1.5 py-0.5 text-[9.5px] text-purple-200" },
                  kindFilter === "existing" ? "已有库" : kindFilter === "new" ? "新发现" : (CANDIDATE_KIND_INFO[kindFilter]?.short || kindFilter),
                  e("button", { onClick: () => setKindFilter(""), className: "hover:text-white" }, e(X, { size: 9 }))
                ),
                myListFilter && e("span", { className: "inline-flex items-center gap-1 rounded border border-amber-500/25 bg-amber-500/[0.07] px-1.5 py-0.5 text-[9.5px] text-amber-300" },
                  e(Star, { size: 9, style: { fill: "#fbbf24" } }), "我的列表",
                  e("button", { onClick: () => setMyListFilter(false), className: "hover:text-white" }, e(X, { size: 9 }))
                )
              ),
              e("div", { className: "flex items-center gap-1.5 text-[9.5px] text-slate-600" },
                e(Info, { size: 10 }),
                e("span", null, "点行打开详情抽屉")
              )
            ),
            e(KOLTable, {
              items,
              onRowClick: openItem,
              selectedItemId: selectedItem?.id,
              myList,
            })
          )
        ),
        (loading || error || poolItems.length === 0) && e("div", {
          className: "mt-3 rounded-xl border border-white/[0.08] bg-white/[0.025] px-4 py-3 text-[11px] text-slate-400"
        }, loading ? "正在读取真实 KOL Pool API" : error ? "真实 KOL Pool API 无信号: " + error : "暂无真实 KOL Pool 数据")
    ),
    e(AnimatePresence, null,
      poolModalOpen && e(KolPoolAllModal, {
        key: "kol-pool-all-modal",
        items: poolItems,
        myList,
        selectedItemId: selectedItem?.id,
        onClose: () => setPoolModalOpen(false),
        onRowClick: (item) => {
          void openItem(item);
          window.setTimeout(() => setPoolModalOpen(false), 0);
        },
      }),
      selectedItem && e(KOLDetailDrawer, {
        key: `kol-detail-${selectedItem.id || selectedItem.handle || "selected"}`,
        item: selectedItem,
        detailBundle: selectedDetailBundle,
        apiToken,
        detailLoading,
        detailError,
        onClose: () => {
          setSelectedItem(null);
          setSelectedDetailBundle(null);
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
