// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useCallback, useEffect, useMemo, useState } from "react";
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
import { favoriteKolPool, getKolPoolDetailBundle, getKolPoolItem, listKolPoolFavorites, unfavoriteKolPool } from "../../../domains/kol";
import { toCockpitKolPoolRows } from "./kolPoolRuntime";
import { listKolsNeedingAnalysis, enqueueVideoAnalysisBatch, importKolPool } from "../../../services/vkpi/kolPool-api";
import { candidateKindGroup } from "./lib/candidateKind";
import { CANDIDATE_KIND_INFO } from "./data/candidateKindInfo";
import { normalizeCountryCode } from "./data/countryInfo";

const e = React.createElement;

function avatarCandidate(item: any) {
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

function kolIdFrom(item: any) {
  const raw = item?.id ?? item?.kol_pool_id ?? item?.kolPoolId;
  const id = Number(raw);
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function KOLPoolPage({ items: sourceItems = [], loading = false, error = "", apiToken = "", staff = [] }: any = {}) {
  const [search, setSearch] = useState("");
  const [country, setCountry] = useState("");
  const [audienceType, setAudienceType] = useState("");
  const [trendLevel, setTrendLevel] = useState("");
  const [sortBy, setSortBy] = useState("v6_fit");
  const [hasViltrox, setHasViltrox] = useState(false);
  const [hasCompetitor, setHasCompetitor] = useState(false);
  const [selectedItem, setSelectedItem] = useState<any>(null);
  const [selectedDetailBundle, setSelectedDetailBundle] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [myList, setMyList] = useState(new Set());
  const [contactItem, setContactItem] = useState<any>(null);
  const [poolModalOpen, setPoolModalOpen] = useState(false);
  const [inlineListOpen, setInlineListOpen] = useState(false);
  // Search v2 state
  const [searchMode, setSearchMode] = useState("balanced"); // balanced | precision | discovery
  const [kindFilter, setKindFilter] = useState("");          // "" | "existing" | "new" | specific kind
  const [myListFilter, setMyListFilter] = useState(false);   // toggle: only show items in myList
  const [recallAvatarIndex, setRecallAvatarIndex] = useState(new Map());
  // 待分析(库内有视频证据但无 ready 深析)+ 批量入队
  const [needsItems, setNeedsItems] = useState<any[]>([]);
  const [needsOpen, setNeedsOpen] = useState(false);
  const [needsLoading, setNeedsLoading] = useState(false);
  const [needsBusy, setNeedsBusy] = useState(false);
  const [needsMsg, setNeedsMsg] = useState("");
  const loadNeeds = useCallback(() => {
    if (!apiToken) return;
    setNeedsLoading(true);
    listKolsNeedingAnalysis(apiToken, 50)
      .then((r: any) => setNeedsItems(Array.isArray(r.items) ? r.items : []))
      .catch(() => setNeedsItems([]))
      .finally(() => setNeedsLoading(false));
  }, [apiToken]);
  const runNeedsBatch = useCallback(() => {
    if (!apiToken || needsBusy) return;
    const items = needsItems.filter((it) => it.evidence_id).map((it) => ({ kol_pool_id: it.kol_pool_id, evidence_id: it.evidence_id }));
    if (!items.length) return;
    setNeedsBusy(true); setNeedsMsg("");
    enqueueVideoAnalysisBatch(apiToken, items)
      .then((r: any) => { setNeedsMsg(`已入队 ${r.queued || 0} 个(跳过 ${r.skipped || 0})· worker 串行处理中`); loadNeeds(); })
      .catch((e2: any) => setNeedsMsg("入队失败:" + String(e2 && e2.message ? e2.message : e2)))
      .finally(() => setNeedsBusy(false));
  }, [apiToken, needsItems, needsBusy, loadNeeds]);
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


  const rememberRecallItems = useCallback((recallItems: any[] = []) => {
    if (!Array.isArray(recallItems) || recallItems.length === 0) return;
    setRecallAvatarIndex((prev) => {
      const next = new Map(prev);
      recallItems.forEach((item: any) => {
        const id = kolIdFrom(item);
        const avatar = avatarCandidate(item);
        if (id && avatar) next.set(id, avatar);
      });
      return next;
    });
  }, []);

  const avatarForItem = (item: any) => {
    const direct = avatarCandidate(item);
    if (direct) return direct;
    const id = kolIdFrom(item);
    return id ? recallAvatarIndex.get(id) || "" : "";
  };

  const mergeAvatarSeed = (item: any, avatar: any) => {
    if (!avatar) return item;
    return {
      ...item,
      avatar_url: item?.avatar_url || avatar,
      avatar_image_url: item?.avatar_image_url || avatar,
      profile_image_url: item?.profile_image_url || avatar,
    };
  };
  
  // C3 收藏接线:挂载拉取本人收藏集(107 未 apply / 端点未活时静默回退纯内存 Set,行为同旧)。
  useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    listKolPoolFavorites(apiToken)
      .then((resp) => {
        if (cancelled) return;
        const ids = (resp.items || []).map((it) => it.kol_pool_id).filter(Boolean);
        if (ids.length) setMyList(new Set(ids));
      })
      .catch(() => { /* 端点未激活(107 未 apply)→ 保持本地 Set,星标仍可用 */ });
    return () => { cancelled = true; };
  }, [apiToken]);

  // 候选(new_discovered / 未入库,无真 kol_pool_id)先建档拿真 id 再收藏,
  // 否则收藏挂不到真 vkpi_kol_pool 行 → my_kol_aggregate._pool_favorites JOIN 不出 → MY KOL 看不见。
  // 复用既有 importKolPool(/kol-pool/import:ON CONFLICT(platform,handle) upsert,返回真 id),不新造端点。
  const realPoolId = (id: any) => { const n = Number(id); return Number.isFinite(n) && n > 0 ? n : null; };

  // 用打开的抽屉项(selectedItem)的 identity 字段把候选落进 vkpi_kol_pool,返回真 id。
  const materializeCandidate = async (): Promise<number | null> => {
    const it = selectedItem || {};
    const handle = String(it.handle || it.display_name || "").trim();
    const platform = String(it.platform || "").trim();
    if (!handle || !platform) return null;
    const resp = await importKolPool(apiToken, {
      items: [{
        handle,
        platform,
        display_name: it.display_name || handle,
        profile_url: it.profile_url || "",
        avatar_url: avatarForItem(it) || "",
        bio: it.bio || "",
        followers: it.followers ?? null,
      }],
      sourceType: "manual",
      sourceRef: "kol_pool_favorite_materialize",
      platform,
    });
    const row = (resp.items || [])[0] as any;
    return realPoolId(row?.id ?? row?.kol_pool_id);
  };

  const toggleMyList = (id: any) => {
    const wasIn = myList.has(id);
    setMyList(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    // 乐观更新 + 真持久化;失败(端点未活/网络)回滚本地态,不留假象。
    if (!apiToken) return;
    const rollback = () => setMyList(prev => {
      const next = new Set(prev);
      if (wasIn) next.add(id); else next.delete(id);
      return next;
    });
    const poolId = realPoolId(id);

    // 取消收藏:必须有真 id 才有可取消的持久行(候选本就没持久化)。
    if (wasIn) {
      if (!poolId) return;
      unfavoriteKolPool(apiToken, poolId).catch(rollback);
      return;
    }

    // 收藏:已有真 id → 直接收藏;否则(候选)先建档拿真 id 再收藏,并把真 id 并入本地态。
    if (poolId) {
      favoriteKolPool(apiToken, poolId).catch(rollback);
      return;
    }
    void (async () => {
      try {
        const newId = await materializeCandidate();
        if (!newId) throw new Error("materialize-failed");
        await favoriteKolPool(apiToken, newId);
        // 用真 id 替换乐观 Set 里的占位(候选 id 多为 null);并把 selectedItem.id 升级为真 id,
        // 让星标状态(inMyList=myList.has(selectedItem.id))与后续 toggle 都用真 id。
        setMyList(prev => {
          const next = new Set(prev);
          next.delete(id);
          next.add(newId);
          return next;
        });
        setSelectedItem((prev: any) => prev ? { ...prev, id: newId, kol_pool_id: newId } : prev);
      } catch {
        rollback();
      }
    })();
  };
  const openItem = async (item: any) => {
    const seedAvatar = avatarForItem(item);
    const seedItem = mergeAvatarSeed(item, seedAvatar);
    setSelectedItem(seedItem);
    setSelectedDetailBundle(null);
    setDetailError("");
    setDetailLoading(Boolean(apiToken && seedItem?.id));
    if (!apiToken || !seedItem?.id) return;
    try {
      const bundle = await getKolPoolDetailBundle(apiToken, seedItem.id);
      const normalized = toCockpitKolPoolRows([bundle.item || seedItem])[0];
      setSelectedDetailBundle(bundle);
      setSelectedItem(mergeAvatarSeed({ ...seedItem, ...normalized }, seedAvatar));
    } catch (err) {
      try {
        const detail = await getKolPoolItem(apiToken, seedItem.id, false);
        const normalized = toCockpitKolPoolRows([detail.item || seedItem])[0];
        setSelectedItem(mergeAvatarSeed({ ...seedItem, ...normalized, freshness: detail.freshness, refresh: detail.refresh }, seedAvatar));
      } catch (fallbackErr) {
        const msg = (fallbackErr as any)?.message || (fallbackErr as any)?.detail || (err as any)?.message || (err as any)?.detail || "详情接口读取失败";
        setDetailError(String(msg).slice(0, 120));
        setSelectedItem(seedItem);
      }
    } finally {
      setDetailLoading(false);
    }
  };

  // 受众画像刷新完成后重拉 detail_bundle(不重置抽屉,静默换水;失败保留旧详情)。
  const reloadDetail = async () => {
    const id = selectedItem?.id;
    if (!apiToken || !id) return;
    try {
      const bundle = await getKolPoolDetailBundle(apiToken, id);
      if (!bundle || !bundle.item) return;
      const normalized = toCockpitKolPoolRows([bundle.item])[0];
      setSelectedDetailBundle(bundle);
      setSelectedItem((prev: any) => (prev && prev.id === id ? { ...prev, ...normalized, id } : prev));
    } catch { /* 静默:重拉失败保留旧详情 */ }
  };

  // item1:任务板「打开」video/账号任务 → 消费 pending id,按 id 拉 detail 开抽屉(池内必有,
  // 不依赖 watchlist)。挂载即消费一次 + 监听事件(已在本页时点开也生效)。失败静默。
  useEffect(() => {
    const consume = () => {
      try {
        const pending = window.localStorage.getItem("vkpi:pending-kolpool-open-id");
        if (!pending) return;
        window.localStorage.removeItem("vkpi:pending-kolpool-open-id");
        const id = Number(pending);
        if (Number.isFinite(id) && id > 0) void openItem({ id });
      } catch { /* localStorage 不可用忽略 */ }
    };
    consume();
    window.addEventListener("vkpi:open-kol-pool-item", consume);
    return () => window.removeEventListener("vkpi:open-kol-pool-item", consume);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiToken]);

  // 顶栏全局搜索接真:消费 pending 关键词 → 填入本地筛选并展开表格视图
  // (vkpi:open-kol-pool-item 同款 localStorage+event 管道)。挂载即消费一次 + 监听事件。
  useEffect(() => {
    const consumeSearch = () => {
      try {
        const pending = window.localStorage.getItem("vkpi:pending-kolpool-search");
        if (!pending) return;
        window.localStorage.removeItem("vkpi:pending-kolpool-search");
        setSearch(pending);
        setInlineListOpen(true);
      } catch { /* localStorage 不可用忽略 */ }
    };
    consumeSearch();
    window.addEventListener("vkpi:open-kol-pool-search", consumeSearch);
    return () => window.removeEventListener("vkpi:open-kol-pool-search", consumeSearch);
  }, []);

  const openRecallItem = useCallback((recallItem: any) => {
    if (!recallItem || typeof recallItem !== "object") return;
    const id = kolIdFrom(recallItem);
    const matched: any = id ? (poolItems.find((it: any) => kolIdFrom(it) === id) || {}) : {};
    const avatar = avatarCandidate(recallItem) || avatarCandidate(matched);
    // ① 库内项(有 kol_pool_id)→ openItem 设 selectedItem 并拉 detail_bundle 打开抽屉看全部信息。
    if (id) {
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
      return;
    }
    // ② 全网发现项(new_creator,无 kol_pool_id,还没入库)→ 不再静默 return;
    //    用召回项自带 identity(头像/handle/平台/followers/profile_url/why_fit/sample)拼轻量 seed,
    //    直接打开抽屉只读展示。openItem 见 seed 无 id 会跳过 detail_bundle 拉取(纯前端、不触评分),
    //    把 why_fit / 召回理由映射进 bio 让用户看到「为什么推荐它」。
    const src = (recallItem.source_fields && typeof recallItem.source_fields === "object") ? recallItem.source_fields : {};
    const handle = String(recallItem.handle || recallItem.display_name || src.handle || src.channel_name || "").trim();
    const why = String(recallItem.why_fit || recallItem.recall_reason || src.why_fit || src.sample_title || src.evidence || "").trim();
    void openItem(mergeAvatarSeed({
      id: null,
      kol_pool_id: null,
      handle: handle || "未入库候选",
      display_name: String(recallItem.display_name || src.display_name || src.channel_name || handle || "").trim(),
      platform: String(recallItem.platform || src.platform || "").trim(),
      profile_type: recallItem.profile_type || "creator",
      followers: recallItem.followers ?? src.followers ?? null,
      profile_url: String(recallItem.profile_url || src.profile_url || src.channel_url || src.source_url || "").trim(),
      bio: why,
      candidate_kind: "new_discovered",
    }, avatar));
  }, [poolItems, recallAvatarIndex, apiToken]);

  // P7:找达人(账号 URL)结果卡点击 → 打开右侧 KOL 详情抽屉(镜像 openRecallItem)。
  // 从 deep-crawl 结果拼 item:kol_pool_id(matched_kol_pool_id / profile_flow.kol_pool_id)+ handle/platform +
  // 基础资料(头像/粉丝/简介/帖数,优先 profile_flow.profile_data,缺则 creator_identity / 顶层字段兜底)。
  // 有 kol_pool_id → openItem 拉 detail_bundle 看全部;无 → 轻量 seed 只读展示。绝不编造粉丝数。
  const openProfileItem = useCallback((result: any) => {
    if (!result || typeof result !== "object") return;
    const profileFlow = result.profile_flow && typeof result.profile_flow === "object" ? result.profile_flow : {};
    const profileData = profileFlow.profile_data && typeof profileFlow.profile_data === "object" ? profileFlow.profile_data : {};
    const creator = result.creator_identity && typeof result.creator_identity === "object" ? result.creator_identity : {};
    const id = kolIdFrom({ id: result.matched_kol_pool_id ?? profileFlow.kol_pool_id });
    const avatar = String(profileData.avatar_url || creator.avatar_url || "").trim();
    const handle = String(profileData.handle || creator.handle || result.handle || "").trim();
    const platform = String(profileData.platform || creator.platform || result.platform || "").trim();
    const followers = profileData.followers ?? creator.followers ?? creator.subscriber_count ?? null;
    const postsCount = profileData.posts_count ?? creator.posts_count ?? null;
    const bio = String(profileData.bio || creator.bio || creator.description || "").trim();
    const profileUrl = String(
      profileData.profile_url || creator.profile_url || creator.channel_url || result.url?.normalized || "",
    ).trim();
    const matched: any = id ? (poolItems.find((it: any) => kolIdFrom(it) === id) || {}) : {};
    void openItem(mergeAvatarSeed({
      ...matched,
      id: id || null,
      kol_pool_id: id || null,
      handle: handle || matched.handle || "未入库账号",
      display_name: matched.display_name || handle,
      platform: platform || matched.platform,
      profile_type: matched.profile_type || "creator",
      followers: followers ?? matched.followers ?? null,
      posts_count: postsCount ?? matched.posts_count ?? null,
      bio: bio || matched.bio,
      profile_url: profileUrl || matched.profile_url,
      candidate_kind: matched.candidate_kind || (id ? "existing" : "new_discovered"),
    }, avatar));
  }, [poolItems, recallAvatarIndex, apiToken]);
  
  // Base filter (no kind filter applied) — used for kindCounts so chips always show full pool counts.
  const filteredBase = useMemo(() => {
    return poolItems.filter((it: any) => {
      if (search) {
        const q = search.toLowerCase();
        const hay = [it.handle, it.display_name, it.bio, it.country, it.devices?.camera_body, ...(it.devices?.lenses || []).map((l: any) => l.model || "")].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (country && !it.geo_distribution?.some((g: any) => normalizeCountryCode(g.country) === country)) return false;
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
      existing:        filteredBase.filter((it: any) => candidateKindGroup(it.candidate_kind) === "existing").length,
      lowConfidence:   filteredBase.filter((it: any) => it.candidate_kind === "existing_low_confidence").length,
      new:             filteredBase.filter((it: any) => candidateKindGroup(it.candidate_kind) === "new").length,
      newPromoted:     filteredBase.filter((it: any) => it.candidate_kind === "new_promoted").length,
      newDiscovered:   filteredBase.filter((it: any) => it.candidate_kind === "new_discovered").length,
    };
  }, [filteredBase]);
  
  const items = useMemo(() => {
    let arr = filteredBase.filter((it: any) => {
      if (myListFilter && !myList.has(it.id)) return false;
      if (!kindFilter) return true;
      if (kindFilter === "existing") return candidateKindGroup(it.candidate_kind) === "existing";
      if (kindFilter === "new")      return candidateKindGroup(it.candidate_kind) === "new";
      return it.candidate_kind === kindFilter;
    });
    // Search v2 ordering: bring "new" candidates up so the mode quotas are visible.
    // In prod, this is computed by select_balanced_top30() on the backend; here we
    // just demonstrate the visual effect of interleaving.
    arr.sort((a: any, b: any) => {
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
            onCardClick: (k: any) => {
              setKindFilter(kindFilter === k ? "" : k);
              setInlineListOpen(true);
            },
            activeKindFilter: kindFilter,
            onTotalClick: () => setPoolModalOpen(true),
          }),
          e("div", { className: "mt-2.5 space-y-2" },
            // K1:searchMode 接通传递——三档(平衡/精准/探索)真实改变 smart 搜索请求参数(映射表见 SmartKolInputPanel)。
            e(SmartKolInputPanel, { apiToken, searchMode, onRecallItems: rememberRecallItems, onOpenRecallItem: openRecallItem, onOpenProfile: openProfileItem })
          ),
          // 待分析:库内有视频证据但还没深析的 KOL → 批量入队
          e("div", { className: "mt-2.5 rounded-lg border border-white/[0.06] bg-white/[0.015]" },
            e("button", {
              onClick: () => { const willOpen = !needsOpen; setNeedsOpen(willOpen); if (willOpen && !needsItems.length) loadNeeds(); },
              className: "flex w-full items-center gap-2 px-3 py-2 text-[11px] text-slate-300 hover:bg-white/[0.03]"
            },
              e(ChevronDown, { size: 13, className: `shrink-0 transition-transform ${needsOpen ? "" : "-rotate-90"}` }),
              e("span", { className: "flex-1 text-left font-medium" }, "待分析 · 库内有视频未深析"),
              needsItems.length ? e("span", { className: "rounded bg-amber-500/20 px-1.5 py-0.5 text-[9px] text-amber-200" }, `${needsItems.length}${needsItems.length >= 50 ? "+" : ""}`) : null
            ),
            needsOpen ? e("div", { className: "px-3 pb-3" },
              e("div", { className: "mb-2 flex items-center justify-between" },
                e("span", { className: "text-[10px] text-slate-500" }, needsLoading ? "加载中…" : `${needsItems.length} 个 KOL 有视频但未深析`),
                e("button", {
                  onClick: runNeedsBatch, disabled: needsBusy || !needsItems.length,
                  className: `rounded px-2.5 py-1 text-[10.5px] font-medium ${needsBusy || !needsItems.length ? "bg-white/[0.05] text-slate-600 cursor-not-allowed" : "bg-emerald-500/90 hover:bg-emerald-500 text-white"}`
                }, needsBusy ? "入队中…" : `批量分析全部 (${needsItems.length})`)
              ),
              needsMsg ? e("div", { className: "mb-2 text-[10px] text-emerald-300" }, needsMsg) : null,
              e("div", { className: "max-h-48 space-y-1 overflow-y-auto" },
                needsItems.slice(0, 50).map((it: any) => e("div", { key: it.kol_pool_id, className: "flex items-center gap-2 rounded px-1.5 py-1 text-[10.5px] text-slate-300 hover:bg-white/[0.02]" },
                  e("span", { className: "flex-1 truncate font-medium text-slate-200" }, it.handle || `#${it.kol_pool_id}`),
                  e("span", { className: "text-slate-500" }, it.platform),
                  e("span", { className: "w-12 text-right text-slate-600" }, `${it.evidence_count || 0} 视频`)
                ))
              )
            ) : null
          ),
          e("div", { className: "mt-2.5" },
            e(MarketCoverageCard, { items: poolItems, onGoDiscover: (c: string) => { setCountry(c); setSearchMode("discovery"); } })
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
                // K1:与真实映射表对齐(库内召回 创作者+测评 · 全网发现条数),不再显示旧占位数字。
                e("span", {
                  className: "rounded border border-white/[0.06] px-1.5 py-0.5 text-[9.5px] text-slate-500",
                  title: "当前搜索模式的真实配额:库内召回(创作者+测评)· 全网发现条数"
                },
                  searchMode === "balanced" ? "平衡 8+7·发现30" : searchMode === "precision" ? "精准 10+5·发现20" : "探索 5+5·发现40"
                ),
                kindFilter && e("span", { className: "inline-flex items-center gap-1 rounded border border-purple-500/25 bg-purple-500/[0.07] px-1.5 py-0.5 text-[9.5px] text-purple-200" },
                  kindFilter === "existing" ? "已有库" : kindFilter === "new" ? "新发现" : ((CANDIDATE_KIND_INFO as any)[kindFilter]?.short || kindFilter),
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
        onContact: (it: any) => setContactItem(it),
        staff,
        onReloadDetail: reloadDetail,
      }),
      contactItem && e(ContactModal, {
        key: `kol-contact-${contactItem.id || contactItem.handle || "selected"}`,
        item: contactItem,
        apiToken,
        onClose: () => setContactItem(null),
      })
    )
  );
}
