import React from "react";
import {
  favoriteKolPool,
  getKolPoolDetailBundle,
  getKolPoolItem,
  listKolPoolFavorites,
  unfavoriteKolPool,
  type VkpiKolRecallItem,
  type VkpiKolUrlDeepCrawlResponse,
} from "../../../../domains/kol";
import { toCockpitKolPoolRows } from "../kolPoolRuntime";
import {
  enqueueVideoAnalysisBatch,
  getKolPoolSummary,
  importKolPool,
  listKolsNeedingAnalysis,
  resolveKolPool,
} from "../../../../services/vkpi/kolPool-api";
import { candidateKindGroup } from "../lib/candidateKind";
import { normalizeCountryCode } from "../data/countryInfo";

// KOL 池 · 板块页动作/派生 hooks(KolPoolBoardPage 专用,页内拆件不入公共桶;
//   金样板 = MarketVoicePage.actions / LaunchPadBoardPage.actions 同构)。
//   全部逻辑 = 旧 KOLPoolPage.tsx 页级逻辑 1:1 平移(零丢失零改行为):
//   收藏管线(候选先建档拿真 id 再收藏)/ 详情抽屉(detail-bundle 优先、单项兜底)/
//   跨页事件消费(vkpi:open-kol-pool-item / vkpi:open-kol-pool-search)/
//   召回·账号结果开抽屉(openRecallItem / openProfileItem,含 resolve 反查)/
//   待深析清单 + 批量入队 / 筛选·分类计数·排序纯函数。
// 红线:纯读 + 既有写端点(收藏/入队/建档),绝不写 viltrox fit 分 / 不触 rule_v0;
//   唯一网络出口 = domains/kol 与 services/vkpi/kolPool-api 既有函数,零新造端点。

export type Row = Record<string, any>;

/* ============ 纯工具(旧页同名函数原样平移) ============ */

export function avatarCandidate(item: any): string {
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

export function kolIdFrom(item: any): number | null {
  const raw = item?.id ?? item?.kol_pool_id ?? item?.kolPoolId;
  const id = Number(raw);
  return Number.isFinite(id) && id > 0 ? id : null;
}

export const realPoolId = (id: any): number | null => {
  const n = Number(id);
  return Number.isFinite(n) && n > 0 ? n : null;
};

/** d14 口径:as-of 戳 = 全池 max(last_seen_at),YYYY-MM-DD(诚实陈旧优于假新鲜)。 */
export function dataAsOfFrom(poolItems: Row[]): string {
  let max = "";
  for (const it of poolItems || []) {
    const v = String(it?.last_seen_at || "");
    if (v && v > max) max = v;
  }
  return max ? max.slice(0, 10) : "";
}

/** KPI「本周新发现」:created_at 落在近 7 天(UTC 存,毫秒差比较)。
 *  null = 全部行都没带 created_at(本机 SWR 缓存还是旧投影)→ KPI 卡诚实 pending,
 *  绝不把「字段缺席」当 0 报(刷新落地新投影后自动出真值)。 */
export function weekNewCount(poolItems: Row[]): number | null {
  const weekAgo = Date.now() - 7 * 24 * 3600 * 1000;
  let n = 0;
  let withField = 0;
  for (const it of poolItems || []) {
    const raw = String(it?.created_at || "");
    if (!raw) continue;
    withField += 1;
    const t = Date.parse(raw);
    if (Number.isFinite(t) && t >= weekAgo) n += 1;
  }
  if ((poolItems || []).length > 0 && withField === 0) return null;
  return n;
}

/** KPI「已深析」:llm_deep_analysis_count>0 的 KOL 数 + 深析结果总条数(列表 payload 真计数)。 */
export function deepAnalyzedStats(poolItems: Row[]): { kols: number; rows: number } {
  let kols = 0;
  let rows = 0;
  for (const it of poolItems || []) {
    const c = Number(it?.llm_deep_analysis_count);
    if (Number.isFinite(c) && c > 0) {
      kols += 1;
      rows += c;
    }
  }
  return { kols, rows };
}

/* ============ 筛选/分类/排序(旧页 filteredBase/kindCounts/items 纯函数化,行为不变) ============ */

export interface PoolFilters {
  search: string;
  country: string;
  audienceType: string;
  trendLevel: string;
  hasViltrox: boolean;
  hasCompetitor: boolean;
}

export function filterPoolItems(poolItems: Row[], f: PoolFilters): Row[] {
  return (poolItems || []).filter((it: any) => {
    if (f.search) {
      const q = f.search.toLowerCase();
      const hay = [it.handle, it.display_name, it.bio, it.country, it.devices?.camera_body, ...(it.devices?.lenses || []).map((l: any) => l.model || "")].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (f.country && !it.geo_distribution?.some((g: any) => normalizeCountryCode(g.country) === f.country)) return false;
    if (f.audienceType && it.audience_type !== f.audienceType) return false;
    const trend = typeof it.trend_resonance === "number" ? it.trend_resonance : null;
    if (f.trendLevel && trend == null) return false;
    if (f.trendLevel === "high" && (trend as number) < 0.6) return false;
    if (f.trendLevel === "medium" && ((trend as number) < 0.3 || (trend as number) >= 0.6)) return false;
    if (f.trendLevel === "low" && (trend as number) >= 0.3) return false;
    if (f.hasViltrox && !it.devices?.has_viltrox) return false;
    if (f.hasCompetitor && !(it.devices?.competitor_brands && it.devices.competitor_brands.length > 0)) return false;
    return true;
  });
}

export function computeKindCounts(filteredBase: Row[]) {
  return {
    total: filteredBase.length,
    existing: filteredBase.filter((it: any) => candidateKindGroup(it.candidate_kind) === "existing").length,
    lowConfidence: filteredBase.filter((it: any) => it.candidate_kind === "existing_low_confidence").length,
    new: filteredBase.filter((it: any) => candidateKindGroup(it.candidate_kind) === "new").length,
    newPromoted: filteredBase.filter((it: any) => it.candidate_kind === "new_promoted").length,
    newDiscovered: filteredBase.filter((it: any) => it.candidate_kind === "new_discovered").length,
  };
}

export function sortedPoolItems(
  filteredBase: Row[],
  opts: { kindFilter: string; myListFilter: boolean; myList: Set<unknown>; sortBy: string },
): Row[] {
  const arr = filteredBase.filter((it: any) => {
    if (opts.myListFilter && !opts.myList.has(it.id)) return false;
    if (!opts.kindFilter) return true;
    if (opts.kindFilter === "existing") return candidateKindGroup(it.candidate_kind) === "existing";
    if (opts.kindFilter === "new") return candidateKindGroup(it.candidate_kind) === "new";
    return it.candidate_kind === opts.kindFilter;
  });
  arr.sort((a: any, b: any) => {
    if (opts.sortBy === "v6_fit") return (b.v6_fit || 0) - (a.v6_fit || 0);
    if (opts.sortBy === "real_er") return (b.real_er_pct || 0) - (a.real_er_pct || 0);
    if (opts.sortBy === "loyalty") return (b.loyalty_score || 0) - (a.loyalty_score || 0);
    if (opts.sortBy === "trend") return (b.trend_resonance ?? -1) - (a.trend_resonance ?? -1);
    if (opts.sortBy === "upgrade") return (b.upgrade_factor || 0) - (a.upgrade_factor || 0);
    if (opts.sortBy === "followers") return (b.followers || 0) - (a.followers || 0);
    return 0;
  });
  return arr;
}

/* ============ 召回头像索引(旧页 recallAvatarIndex 平移) ============ */

export function useRecallAvatars() {
  const [recallAvatarIndex, setRecallAvatarIndex] = React.useState<Map<number, string>>(() => new Map());
  const rememberRecallItems = React.useCallback((recallItems: any[] = []) => {
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
  const avatarForItem = React.useCallback((item: any) => {
    const direct = avatarCandidate(item);
    if (direct) return direct;
    const id = kolIdFrom(item);
    return id ? recallAvatarIndex.get(id) || "" : "";
  }, [recallAvatarIndex]);
  const mergeAvatarSeed = React.useCallback((item: any, avatar: any) => {
    if (!avatar) return item;
    return {
      ...item,
      avatar_url: item?.avatar_url || avatar,
      avatar_image_url: item?.avatar_image_url || avatar,
      profile_image_url: item?.profile_image_url || avatar,
    };
  }, []);
  return { rememberRecallItems, avatarForItem, mergeAvatarSeed };
}

/* ============ 详情抽屉(openItem detail-bundle 优先、单项兜底 + 受众刷新重拉 + 跨页事件) ============ */

export function usePoolDrawer(apiToken: string, avatarForItem: (item: any) => string, mergeAvatarSeed: (item: any, avatar: any) => any) {
  const [selectedItem, setSelectedItem] = React.useState<any>(null);
  const [selectedDetailBundle, setSelectedDetailBundle] = React.useState<any>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState("");

  const openItem = React.useCallback(async (item: any) => {
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
  }, [apiToken, avatarForItem, mergeAvatarSeed]);

  // 受众画像刷新完成后重拉 detail_bundle(不重置抽屉,静默换水;失败保留旧详情)。
  const reloadDetail = React.useCallback(async () => {
    const id = selectedItem?.id;
    if (!apiToken || !id) return;
    try {
      const bundle = await getKolPoolDetailBundle(apiToken, id);
      if (!bundle || !bundle.item) return;
      const normalized = toCockpitKolPoolRows([bundle.item])[0];
      setSelectedDetailBundle(bundle);
      setSelectedItem((prev: any) => (prev && prev.id === id ? { ...prev, ...normalized, id } : prev));
    } catch { /* 静默:重拉失败保留旧详情 */ }
  }, [apiToken, selectedItem?.id]);

  const closeDrawer = React.useCallback(() => {
    setSelectedItem(null);
    setSelectedDetailBundle(null);
    setDetailLoading(false);
    setDetailError("");
  }, []);

  // 任务板「打开」video/账号任务 → 消费 pending id,按 id 拉 detail 开抽屉(池内必有,
  // 不依赖 watchlist)。挂载即消费一次 + 监听事件(已在本页时点开也生效)。失败静默。
  React.useEffect(() => {
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

  return { selectedItem, setSelectedItem, selectedDetailBundle, detailLoading, detailError, openItem, reloadDetail, closeDrawer };
}

/* ============ 收藏管线(C3 接线 + 候选 materialize 先建档拿真 id,旧页逻辑原样) ============ */

export function usePoolFavorites(
  apiToken: string,
  selectedItem: any,
  setSelectedItem: React.Dispatch<any>,
  avatarForItem: (item: any) => string,
) {
  const [myList, setMyList] = React.useState<Set<unknown>>(() => new Set());

  // 挂载拉取本人收藏集(端点未活时静默回退纯内存 Set,行为同旧)。
  React.useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    listKolPoolFavorites(apiToken)
      .then((resp) => {
        if (cancelled) return;
        const ids = (resp.items || []).map((it: any) => it.kol_pool_id).filter(Boolean);
        if (ids.length) setMyList(new Set(ids));
      })
      .catch(() => { /* 端点未激活 → 保持本地 Set,星标仍可用 */ });
    return () => { cancelled = true; };
  }, [apiToken]);

  // 候选(new_discovered / 未入库,无真 kol_pool_id)先建档拿真 id 再收藏,
  // 否则收藏挂不到真 vkpi_kol_pool 行 → MY KOL 看不见。复用既有 importKolPool upsert。
  const materializeCandidate = React.useCallback(async (): Promise<number | null> => {
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
  }, [apiToken, selectedItem, avatarForItem]);

  const toggleMyList = React.useCallback((id: any) => {
    const wasIn = myList.has(id);
    setMyList((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    // 乐观更新 + 真持久化;失败(端点未活/网络)回滚本地态,不留假象。
    if (!apiToken) return;
    const rollback = () => setMyList((prev) => {
      const next = new Set(prev);
      if (wasIn) next.add(id); else next.delete(id);
      return next;
    });
    const poolId = realPoolId(id);
    if (wasIn) {
      // 取消收藏:必须有真 id 才有可取消的持久行(候选本就没持久化)。
      if (!poolId) return;
      unfavoriteKolPool(apiToken, poolId).catch(rollback);
      return;
    }
    if (poolId) {
      favoriteKolPool(apiToken, poolId).catch(rollback);
      return;
    }
    void (async () => {
      try {
        const newId = await materializeCandidate();
        if (!newId) throw new Error("materialize-failed");
        await favoriteKolPool(apiToken, newId);
        // 用真 id 替换乐观 Set 里的占位;并把 selectedItem.id 升级为真 id。
        setMyList((prev) => {
          const next = new Set(prev);
          next.delete(id);
          next.add(newId);
          return next;
        });
        setSelectedItem((prev: any) => (prev ? { ...prev, id: newId, kol_pool_id: newId } : prev));
      } catch {
        rollback();
      }
    })();
  }, [apiToken, myList, materializeCandidate, setSelectedItem]);

  return { myList, toggleMyList };
}

/* ============ 召回/账号结果 → 抽屉(openRecallItem / openProfileItem,旧页逻辑原样) ============ */

export function useSmartOpeners(
  apiToken: string,
  poolItems: Row[],
  openItem: (item: any) => Promise<void>,
  mergeAvatarSeed: (item: any, avatar: any) => any,
) {
  const openRecallItem = React.useCallback((recallItem: VkpiKolRecallItem | any) => {
    if (!recallItem || typeof recallItem !== "object") return;
    const id = kolIdFrom(recallItem);
    const matched: any = id ? (poolItems.find((it: any) => kolIdFrom(it) === id) || {}) : {};
    const avatar = avatarCandidate(recallItem) || avatarCandidate(matched);
    // ① 库内项(有 kol_pool_id)→ openItem 拉 detail_bundle 打开抽屉看全部信息。
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
    // ② 全网发现项(new_creator,无 kol_pool_id):先按 handle+platform 反查真池行,
    //    命中按库内项打开;查不到/失败退回轻量 seed 只读展示(绝不让点击无响应)。
    const src = (recallItem.source_fields && typeof recallItem.source_fields === "object") ? recallItem.source_fields : {};
    const handle = String(recallItem.handle || recallItem.display_name || src.handle || src.channel_name || "").trim();
    const why = String(recallItem.why_fit || recallItem.recall_reason || src.why_fit || src.sample_title || src.evidence || "").trim();
    const platform = String(recallItem.platform || src.platform || "").trim();
    const displayName = String(recallItem.display_name || src.display_name || src.channel_name || handle || "").trim();
    const openSeed = () => {
      void openItem(mergeAvatarSeed({
        id: null,
        kol_pool_id: null,
        handle: handle || "未入库候选",
        display_name: displayName,
        platform,
        profile_type: recallItem.profile_type || "creator",
        followers: recallItem.followers ?? src.followers ?? null,
        profile_url: String(recallItem.profile_url || src.profile_url || src.channel_url || src.source_url || "").trim(),
        bio: why,
        candidate_kind: "new_discovered",
      }, avatar));
    };
    if (handle && apiToken) {
      void (async () => {
        try {
          const resp: any = await resolveKolPool(apiToken, handle, platform);
          const pid = Number(resp?.kol_pool_id || resp?.matched_kol_pool_id) || 0;
          if (pid > 0) {
            void openItem(mergeAvatarSeed({
              id: pid,
              kol_pool_id: pid,
              handle,
              display_name: displayName,
              platform,
              profile_type: recallItem.profile_type || "creator",
              followers: recallItem.followers ?? src.followers ?? null,
              candidate_kind: "existing",
            }, avatar));
            return;
          }
        } catch { /* resolve 失败 → 轻量 seed 兜底 */ }
        openSeed();
      })();
      return;
    }
    openSeed();
  }, [apiToken, poolItems, openItem, mergeAvatarSeed]);

  // 找达人(账号 URL)结果卡点击 → 抽屉:从 deep-crawl 结果拼 item(镜像 openRecallItem)。
  // 有 kol_pool_id → openItem 拉 detail_bundle 看全部;无 → 轻量 seed 只读展示。绝不编造粉丝数。
  const openProfileItem = React.useCallback((result: VkpiKolUrlDeepCrawlResponse | any) => {
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
  }, [poolItems, openItem, mergeAvatarSeed]);

  return { openRecallItem, openProfileItem };
}

/* ============ 待深析清单 + 批量入队(旧页「待分析」折叠区逻辑平移;模块挂载即拉) ============ */

export function useNeedsAnalysis(apiToken: string) {
  const [needsItems, setNeedsItems] = React.useState<any[]>([]);
  const [needsLoading, setNeedsLoading] = React.useState(false);
  const [needsBusy, setNeedsBusy] = React.useState(false);
  const [needsMsg, setNeedsMsg] = React.useState("");
  const loadNeeds = React.useCallback(() => {
    if (!apiToken) return;
    setNeedsLoading(true);
    listKolsNeedingAnalysis(apiToken, 50)
      .then((r: any) => setNeedsItems(Array.isArray(r.items) ? r.items : []))
      .catch(() => setNeedsItems([]))
      .finally(() => setNeedsLoading(false));
  }, [apiToken]);
  React.useEffect(() => {
    loadNeeds();
  }, [loadNeeds]);
  const runNeedsBatch = React.useCallback(() => {
    if (!apiToken || needsBusy) return;
    const items = needsItems.filter((it) => it.evidence_id).map((it) => ({ kol_pool_id: it.kol_pool_id, evidence_id: it.evidence_id }));
    if (!items.length) return;
    setNeedsBusy(true);
    setNeedsMsg("");
    enqueueVideoAnalysisBatch(apiToken, items)
      .then((r: any) => {
        setNeedsMsg(`已入队 ${r.queued || 0} 个(跳过 ${r.skipped || 0})· 后台逐条处理中`);
        loadNeeds();
      })
      .catch((err: any) => setNeedsMsg("入队失败:" + String(err && err.message ? err.message : err)))
      .finally(() => setNeedsBusy(false));
  }, [apiToken, needsItems, needsBusy, loadNeeds]);
  return { needsItems, needsLoading, needsBusy, needsMsg, loadNeeds, runNeedsBatch };
}

/* ============ 低触达隐藏计数(kol-pool/summary.low_reach_hidden_count;缺席=诚实 pending) ============ */

export function useLowReachHidden(apiToken: string) {
  const [count, setCount] = React.useState<number | null>(null);
  React.useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    getKolPoolSummary(apiToken)
      .then((resp: Row) => {
        if (cancelled) return;
        const n = Number(resp?.low_reach_hidden_count);
        setCount(Number.isFinite(n) ? n : null);
      })
      .catch(() => { /* 读取失败 → 保持 null,KPI 卡诚实 pending,绝不编 0 */ });
    return () => { cancelled = true; };
  }, [apiToken]);
  return count;
}
