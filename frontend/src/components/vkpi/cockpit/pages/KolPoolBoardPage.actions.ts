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
import { apiFetch } from "../../../../services/http";
import { addKolsToProject } from "../../../../services/vkpi/projects-api";
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
// 红线:纯读 + 既有写端点(收藏/入队/建档/入项目),绝不写 viltrox fit 分 / 不触 rule_v0;
//   网络出口 = domains/kol · services/vkpi/kolPool-api · projects-api.addKolsToProject
//   与既有 GET /api/admin/vkpi/projects 列表(EventsBoardPage/EditGroupModal 同款),
//   零新造端点。

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
    // 国家筛选 = 创作者所在国(it.country);geo_distribution 自波 C 起是受众地理(评论硬信号分层,
    // 多数 KOL 样本不足为空),只作补充命中,不再当唯一判据(否则筛选几乎全空)。
    if (
      f.country &&
      normalizeCountryCode(it.country) !== f.country &&
      !it.geo_distribution?.some((g: any) => normalizeCountryCode(g.country) === f.country)
    ) return false;
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

const DRAWER_PRODUCT_SCOPE_KEYS = [
  "product_context",
  "product_sku",
  "productSku",
  "product_identity",
  "productIdentity",
  "product_family",
  "productFamily",
  "product_family_name",
  "productFamilyName",
  "product_name",
  "productName",
] as const;

function validProductScopeValue(key: typeof DRAWER_PRODUCT_SCOPE_KEYS[number], value: unknown): boolean {
  if (key === "product_context") {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const context = value as Row;
    return [context.kind, context.identity, context.sku, context.label]
      .some((part) => typeof part === "string" ? Boolean(part.trim()) : part != null);
  }
  if (typeof value === "string") return Boolean(value.trim());
  return value != null;
}

function firstProductScopeValue(target: Row, ...keys: string[]) {
  for (const key of keys) {
    const value = target?.[key];
    if (typeof value === "string") {
      if (value.trim()) return value.trim();
    } else if (value != null) return value;
  }
  return undefined;
}

function hasProductScope(row: Row): boolean {
  const context = validProductScopeValue("product_context", row?.product_context)
    ? row.product_context as Row
    : {};
  const contextKind = firstProductScopeValue(context, "kind");
  const contextSku = firstProductScopeValue(context, "sku");
  const contextIdentity = firstProductScopeValue(context, "identity");
  const scalarSku = firstProductScopeValue(row, "product_sku", "productSku");
  const scalarIdentity = firstProductScopeValue(row, "product_identity", "productIdentity");
  const scalarFamilyIdentity = firstProductScopeValue(row, "product_family", "productFamily");

  // A name/label only describes an already-proven product identity. It must
  // never displace an exact fallback SKU. A scalar family field is itself an
  // explicitly typed family identity; a generic identity needs context.kind.
  return Boolean(
    contextSku
    || scalarSku
    || scalarFamilyIdentity
    || (contextKind && (contextIdentity || scalarIdentity)),
  );
}

function replaceWithCanonicalProductScope(row: Row, productSeed: Row): Row {
  if (!hasProductScope(productSeed || {})) return row;
  const scope = recallProductScopeForDrawer(productSeed || {});
  const next: Row = { ...row };
  DRAWER_PRODUCT_SCOPE_KEYS.forEach((key) => { delete next[key]; });
  return Object.assign(next, scope);
}

/**
 * 合并详情服务端投影时，只让本轮搜索 seed 的产品范围拥有最高优先级。
 * 其他详情字段仍由服务端更新；没有产品 seed 的普通入口保持原合并语义。
 */
export function mergePoolDrawerDetailRow(current: Row, server: Row, productSeed: Row = current): Row {
  const merged: Row = { ...(current || {}), ...(server || {}) };
  return replaceWithCanonicalProductScope(merged, productSeed || {});
}

export function usePoolDrawer(
  apiToken: string,
  avatarForItem: (item: any) => string,
  mergeAvatarSeed: (item: any, avatar: any) => any,
  accountKey?: string | number | null,
) {
  const [selectedItem, setSelectedItem] = React.useState<any>(null);
  const [selectedDetailBundle, setSelectedDetailBundle] = React.useState<any>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState("");
  const selectedItemIdRef = React.useRef<any>(null);
  const normalizedAccountKey = accountKey == null || String(accountKey).trim() === ""
    ? "account-unresolved"
    : String(accountKey);
  const drawerIdentityRef = React.useRef({ token: apiToken, accountKey: normalizedAccountKey, generation: 0 });
  if (drawerIdentityRef.current.token !== apiToken
    || drawerIdentityRef.current.accountKey !== normalizedAccountKey) {
    drawerIdentityRef.current = {
      token: apiToken,
      accountKey: normalizedAccountKey,
      generation: drawerIdentityRef.current.generation + 1,
    };
  }
  const drawerIdentity = drawerIdentityRef.current;
  const previousDrawerIdentityRef = React.useRef(drawerIdentity);
  const drawerRequestGenerationRef = React.useRef(0);
  const selectedItemIdentityRef = React.useRef<typeof drawerIdentity | null>(null);
  const selectedProductSeedRef = React.useRef<Row | null>(null);
  selectedItemIdRef.current = selectedItem?.id ?? null;

  React.useEffect(() => {
    if (previousDrawerIdentityRef.current === drawerIdentity) return;
    previousDrawerIdentityRef.current = drawerIdentity;
    drawerRequestGenerationRef.current += 1;
    selectedItemIdRef.current = null;
    selectedItemIdentityRef.current = null;
    selectedProductSeedRef.current = null;
    setSelectedItem(null);
    setSelectedDetailBundle(null);
    setDetailLoading(false);
    setDetailError("");
  }, [drawerIdentity]);

  const openItem = React.useCallback(async (item: any) => {
    const requestIdentity = drawerIdentity;
    if (drawerIdentityRef.current !== requestIdentity) return;
    const requestGeneration = ++drawerRequestGenerationRef.current;
    const isCurrentRequest = () => drawerIdentityRef.current === requestIdentity
      && drawerRequestGenerationRef.current === requestGeneration;
    const seedAvatar = avatarForItem(item);
    const seedItem = mergeAvatarSeed(item, seedAvatar);
    selectedProductSeedRef.current = seedItem;
    selectedItemIdentityRef.current = requestIdentity;
    setSelectedItem(seedItem);
    setSelectedDetailBundle(null);
    setDetailError("");
    setDetailLoading(Boolean(apiToken && seedItem?.id));
    if (!apiToken || !seedItem?.id) return;
    try {
      const bundle = await getKolPoolDetailBundle(apiToken, seedItem.id);
      if (!isCurrentRequest()) return;
      const normalized = toCockpitKolPoolRows([bundle.item || seedItem])[0];
      const detailItem = mergePoolDrawerDetailRow(
        seedItem,
        { ...(bundle.item || {}), ...normalized },
        seedItem,
      );
      setSelectedDetailBundle({ ...bundle, item: detailItem });
      // Full contact fields live only on this selected detail item. Do not add
      // them to the bulk normalizer/global KOL pool rows.
      setSelectedItem(mergeAvatarSeed(detailItem, seedAvatar));
    } catch (err) {
      if (!isCurrentRequest()) return;
      try {
        const detail = await getKolPoolItem(apiToken, seedItem.id, false);
        if (!isCurrentRequest()) return;
        const normalized = toCockpitKolPoolRows([detail.item || seedItem])[0];
        const detailItem = mergePoolDrawerDetailRow(
          seedItem,
          { ...(detail.item || {}), ...normalized, freshness: detail.freshness, refresh: detail.refresh },
          seedItem,
        );
        setSelectedItem(mergeAvatarSeed(detailItem, seedAvatar));
      } catch (fallbackErr) {
        if (!isCurrentRequest()) return;
        const msg = (fallbackErr as any)?.message || (fallbackErr as any)?.detail || (err as any)?.message || (err as any)?.detail || "详情接口读取失败";
        setDetailError(String(msg).slice(0, 120));
        setSelectedItem(seedItem);
      }
    } finally {
      if (isCurrentRequest()) setDetailLoading(false);
    }
  }, [apiToken, avatarForItem, drawerIdentity, mergeAvatarSeed]);

  // 受众画像刷新完成后重拉 detail_bundle(不重置抽屉,静默换水;失败保留旧详情)。
  const reloadDetail = React.useCallback(async () => {
    const id = selectedItem?.id;
    const requestIdentity = drawerIdentity;
    if (!apiToken || !id
      || drawerIdentityRef.current !== requestIdentity
      || selectedItemIdentityRef.current !== requestIdentity) return;
    const requestGeneration = ++drawerRequestGenerationRef.current;
    try {
      const bundle = await getKolPoolDetailBundle(apiToken, id);
      if (!bundle || !bundle.item) return;
      if (drawerIdentityRef.current !== requestIdentity
        || drawerRequestGenerationRef.current !== requestGeneration
        || selectedItemIdRef.current !== id) return;
      const normalized = toCockpitKolPoolRows([bundle.item])[0];
      const productSeed = selectedProductSeedRef.current || selectedItem || { id };
      const bundleItem = mergePoolDrawerDetailRow(
        productSeed,
        { ...bundle.item, ...normalized, id },
        productSeed,
      );
      setSelectedDetailBundle({ ...bundle, item: bundleItem });
      setSelectedItem((prev: any) => (prev && prev.id === id
        ? mergePoolDrawerDetailRow(prev, { ...bundle.item, ...normalized, id }, productSeed)
        : prev));
    } catch { /* 静默:重拉失败保留旧详情 */ }
  }, [apiToken, drawerIdentity, selectedItem?.id]);

  const closeDrawer = React.useCallback(() => {
    drawerRequestGenerationRef.current += 1;
    selectedItemIdentityRef.current = null;
    selectedProductSeedRef.current = null;
    setSelectedItem(null);
    setSelectedDetailBundle(null);
    setDetailLoading(false);
    setDetailError("");
  }, []);

  const setSelectedItemForCurrentAccount = React.useCallback((value: React.SetStateAction<any>) => {
    if (drawerIdentityRef.current !== drawerIdentity) return;
    selectedItemIdentityRef.current = drawerIdentity;
    setSelectedItem(value);
  }, [drawerIdentity]);

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
  }, [openItem]);

  const ownsSelectedItem = selectedItemIdentityRef.current === drawerIdentity;
  const visibleSelectedItem = ownsSelectedItem ? selectedItem : null;
  const visibleDetailBundle = ownsSelectedItem ? selectedDetailBundle : null;
  return { selectedItem: visibleSelectedItem, setSelectedItem: setSelectedItemForCurrentAccount, selectedDetailBundle: visibleDetailBundle, detailLoading: visibleSelectedItem ? detailLoading : false, detailError: visibleSelectedItem ? detailError : "", openItem, reloadDetail, closeDrawer };
}

/* ============ 收藏管线(C3 接线 + 候选 materialize 先建档拿真 id,旧页逻辑原样) ============ */

export function usePoolFavorites(
  apiToken: string,
  accountKey: string | number | null | undefined,
  selectedItem: any,
  setSelectedItem: React.Dispatch<any>,
  avatarForItem: (item: any) => string,
) {
  const [myList, setMyList] = React.useState<Set<unknown>>(() => new Set());
  const myListRef = React.useRef<Set<unknown>>(new Set());
  const normalizedAccountKey = accountKey == null || String(accountKey).trim() === ""
    ? "account-unresolved"
    : String(accountKey);
  const favoritesIdentityRef = React.useRef({ token: apiToken, accountKey: normalizedAccountKey, generation: 0 });
  if (favoritesIdentityRef.current.token !== apiToken
    || favoritesIdentityRef.current.accountKey !== normalizedAccountKey) {
    favoritesIdentityRef.current = {
      token: apiToken,
      accountKey: normalizedAccountKey,
      generation: favoritesIdentityRef.current.generation + 1,
    };
  }
  const favoritesIdentity = favoritesIdentityRef.current;
  const myListIdentityRef = React.useRef<typeof favoritesIdentity>(favoritesIdentity);
  const syncStateIdentityRef = React.useRef<typeof favoritesIdentity>(favoritesIdentity);
  const inFlightRef = React.useRef<Set<string>>(new Set());
  const syncGenerationRef = React.useRef(0);
  // 收藏写请求也必须绑定发起时身份。仅保护 GET 回读不够：旧账号的
  // then/catch/finally 若在切换身份后落地，会覆盖新身份星标、错误态，甚至
  // 删除新身份同一 KOL 的 in-flight 锁。generation 处理 A→B→A，token
  // snapshot 让 token/accountKey 在 render 当刻即失效，不等 effect cleanup。
  React.useEffect(() => () => {
    const current = favoritesIdentityRef.current;
    favoritesIdentityRef.current = { ...current, generation: current.generation + 1 };
  }, []);
  const [syncState, setSyncState] = React.useState<"idle" | "loading" | "synced" | "error">("idle");
  const [syncError, setSyncError] = React.useState("");

  const updateMyList = React.useCallback((
    identity: typeof favoritesIdentity,
    updater: (current: Set<unknown>) => Set<unknown>,
  ) => {
    if (favoritesIdentityRef.current !== identity) return;
    setMyList((current) => {
      if (favoritesIdentityRef.current !== identity) return current;
      const base = myListIdentityRef.current === identity ? current : new Set<unknown>();
      const next = updater(base);
      myListIdentityRef.current = identity;
      myListRef.current = next;
      return next;
    });
  }, []);

  const publishFavoritesChanged = React.useCallback(() => {
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("vkpi:favorites-changed"));
  }, []);

  // token/身份变化先清空旧身份星标；空数组也必须覆盖旧集合。读取失败显式标错，
  // 不再把陈旧内存态伪装成服务端收藏。
  React.useEffect(() => {
    const syncIdentity = favoritesIdentity;
    if (favoritesIdentityRef.current !== syncIdentity) return undefined;
    myListRef.current = new Set();
    myListIdentityRef.current = syncIdentity;
    syncStateIdentityRef.current = syncIdentity;
    setMyList(new Set());
    inFlightRef.current.clear();
    setSyncError("");
    if (!apiToken) {
      setSyncState("idle");
      return;
    }
    let cancelled = false;
    const syncFavorites = () => {
      const generation = ++syncGenerationRef.current;
      setSyncState("loading");
      setSyncError("");
      // 与同页 SmartKolInputPanel 使用同一完整上限，让服务层合并同身份的
      // 并发 GET；两个消费方仍各自保留 identity/generation 落地保护。
      void listKolPoolFavorites(apiToken, 5000, normalizedAccountKey)
        .then((resp) => {
          if (cancelled
            || favoritesIdentityRef.current !== syncIdentity
            || generation !== syncGenerationRef.current) return;
          const ids = (resp.items || []).map((it: any) => it.kol_pool_id).filter(Boolean);
          const next = new Set<unknown>(ids);
          myListRef.current = next;
          setMyList(next);
          setSyncState("synced");
        })
        .catch((err: any) => {
          if (cancelled
            || favoritesIdentityRef.current !== syncIdentity
            || generation !== syncGenerationRef.current) return;
          setSyncState("error");
          setSyncError(String(err?.message || err?.detail || "收藏状态读取失败"));
        });
    };
    syncFavorites();
    // 找达人批量收藏、入项目自动收藏及其他同页动作都通过这一事件回读服务端，
    // 页面星标不再停留在动作前的旧快照。
    window.addEventListener("vkpi:favorites-changed", syncFavorites);
    return () => {
      cancelled = true;
      syncGenerationRef.current += 1;
      window.removeEventListener("vkpi:favorites-changed", syncFavorites);
    };
  }, [apiToken, favoritesIdentity]);

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
    if (!apiToken) {
      setSyncState("error");
      setSyncError("未登录，收藏未写入");
      return;
    }
    const mutationIdentity = favoritesIdentity;
    if (favoritesIdentityRef.current !== mutationIdentity) return;
    const isCurrentMutation = () => favoritesIdentityRef.current === mutationIdentity;
    const opKey = `${mutationIdentity.generation}:${String(id)}`;
    if (inFlightRef.current.has(opKey)) return;
    inFlightRef.current.add(opKey);
    const wasIn = myListIdentityRef.current === mutationIdentity && myListRef.current.has(id);
    syncStateIdentityRef.current = mutationIdentity;
    updateMyList(mutationIdentity, (prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    setSyncState("loading");
    setSyncError("");
    const rollback = (err?: any) => {
      if (!isCurrentMutation()) return;
      updateMyList(mutationIdentity, (prev) => {
        const next = new Set(prev);
        if (wasIn) next.add(id); else next.delete(id);
        setSyncState("error");
        setSyncError(String(err?.message || err?.detail || "收藏写入失败"));
        return next;
      });
    };
    const finish = () => {
      if (!isCurrentMutation()) return;
      inFlightRef.current.delete(opKey);
    };
    const confirm = () => {
      if (!isCurrentMutation()) return;
      setSyncState("synced");
      publishFavoritesChanged();
    };
    const poolId = realPoolId(id);
    if (wasIn) {
      // 取消收藏:必须有真 id 才有可取消的持久行(候选本就没持久化)。
      if (!poolId) {
        rollback(new Error("缺少真实 KOL ID，未取消收藏"));
        finish();
        return;
      }
      unfavoriteKolPool(apiToken, poolId).then(confirm).catch(rollback).finally(finish);
      return;
    }
    if (poolId) {
      favoriteKolPool(apiToken, poolId).then(confirm).catch(rollback).finally(finish);
      return;
    }
    void (async () => {
      try {
        const newId = await materializeCandidate();
        if (!isCurrentMutation()) return;
        if (!newId) throw new Error("materialize-failed");
        await favoriteKolPool(apiToken, newId);
        if (!isCurrentMutation()) return;
        // 用真 id 替换乐观 Set 里的占位;并把 selectedItem.id 升级为真 id。
        updateMyList(mutationIdentity, (prev) => {
          const next = new Set(prev);
          next.delete(id);
          next.add(newId);
          return next;
        });
        setSelectedItem((prev: any) => (prev ? { ...prev, id: newId, kol_pool_id: newId } : prev));
        confirm();
      } catch (err) {
        rollback(err);
      } finally {
        finish();
      }
    })();
  }, [apiToken, favoritesIdentity, materializeCandidate, publishFavoritesChanged, setSelectedItem, updateMyList]);

  const ownsFavoriteState = myListIdentityRef.current === favoritesIdentity;
  const ownsSyncState = syncStateIdentityRef.current === favoritesIdentity;
  return {
    myList: ownsFavoriteState ? myList : new Set<unknown>(),
    toggleMyList,
    syncState: ownsSyncState ? syncState : "idle" as const,
    syncError: ownsSyncState ? syncError : "",
  };
}

/* ============ 召回/账号结果 → 抽屉(openRecallItem / openProfileItem,旧页逻辑原样) ============ */

/** 把搜索产品上下文带进详情 seed；家族身份单独保留，不能塞进 product_sku 冒充精确型号。 */
export function recallProductScopeForDrawer(primary: Row, fallback: Row = {}): Row {
  // 产品范围是一个身份整体，不能按字段在 primary/fallback 间拼接。
  // primary 只要有任一有效范围字段，fallback 的历史 SKU/家族/上下文就全部失效。
  const source = hasProductScope(primary || {})
    ? primary
    : hasProductScope(fallback || {})
      ? fallback
      : {};
  const context = validProductScopeValue("product_context", source.product_context)
    ? source.product_context as Row
    : null;
  const contextKind = firstProductScopeValue(context as Row, "kind");
  const scalarSku = firstProductScopeValue(source, "product_sku", "productSku");
  const scalarFamily = firstProductScopeValue(source, "product_family", "productFamily");
  const scalarFamilyName = firstProductScopeValue(source, "product_family_name", "productFamilyName");
  const familyScoped = contextKind === "product_family"
    || (!contextKind && !scalarSku && Boolean(scalarFamily || scalarFamilyName));
  const exactScoped = contextKind === "catalog_sku" || (!contextKind && Boolean(scalarSku));
  const sku = exactScoped
    ? firstProductScopeValue(context as Row, "sku") ?? scalarSku
    : undefined;
  const scalarIdentity = firstProductScopeValue(source, "product_identity", "productIdentity");
  const contextIdentity = firstProductScopeValue(context as Row, "identity");
  const identity = contextIdentity ?? scalarIdentity ?? sku ?? scalarFamily;
  const family = familyScoped ? contextIdentity ?? scalarFamily ?? scalarIdentity : undefined;
  const familyName = familyScoped
    ? firstProductScopeValue(context as Row, "label") ?? scalarFamilyName
    : undefined;
  const productName = identity
    ? firstProductScopeValue(context as Row, "label")
      ?? firstProductScopeValue(source, "product_name", "productName")
    : undefined;
  return {
    ...(context ? { product_context: context } : {}),
    ...(sku ? { product_sku: sku } : {}),
    ...(identity ? { product_identity: identity } : {}),
    ...(family ? { product_family: family } : {}),
    ...(familyName ? { product_family_name: familyName } : {}),
    ...(productName ? { product_name: productName } : {}),
  };
}

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
      const productScope = recallProductScopeForDrawer(recallItem, matched);
      const seed = replaceWithCanonicalProductScope({
        ...matched,
        id,
        kol_pool_id: id,
        handle: recallItem.handle || matched.handle,
        display_name: recallItem.display_name || matched.display_name || recallItem.handle,
        platform: recallItem.platform || matched.platform,
        profile_type: recallItem.profile_type || matched.profile_type,
        followers: recallItem.followers ?? matched.followers,
        ...productScope,
        candidate_kind: matched.candidate_kind || "existing",
      }, productScope);
      void openItem(mergeAvatarSeed(seed, avatar));
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
        ...recallProductScopeForDrawer(recallItem),
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
              ...recallProductScopeForDrawer(recallItem),
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

/* ============ 池 summary 消费(kol-pool/summary 单次拉取两键:low_reach_hidden_count
   → KPI 第四卡;discovery_funnel_30d → 发现转化漏斗模块。键缺席=诚实 pending/缺席,
   绝不编 0;原 useLowReachHidden 升级为本 hook,同端点零多余请求)。 ============ */

/** kol-pool/summary.discovery_funnel_30d 形状(四段各自可缺席=该段计数暂不可用) */
export interface PoolDiscoveryFunnel {
  window_days?: number;
  discovered?: number;
  enrolled?: number;
  deep_analyzed?: number;
  favorited?: number;
}

export function usePoolSummary(apiToken: string) {
  const [lowReachHidden, setLowReachHidden] = React.useState<number | null>(null);
  const [funnel30d, setFunnel30d] = React.useState<PoolDiscoveryFunnel | null>(null);
  const [summaryLoading, setSummaryLoading] = React.useState(false);
  React.useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    setSummaryLoading(true);
    getKolPoolSummary(apiToken)
      .then((resp: Row) => {
        if (cancelled) return;
        const n = Number(resp?.low_reach_hidden_count);
        setLowReachHidden(Number.isFinite(n) ? n : null);
        const f = resp?.discovery_funnel_30d;
        setFunnel30d(f && typeof f === "object" ? (f as PoolDiscoveryFunnel) : null);
      })
      .catch(() => { /* 读取失败 → 保持 null,KPI 卡/漏斗诚实 pending,绝不编 0 */ })
      .finally(() => { if (!cancelled) setSummaryLoading(false); });
    return () => { cancelled = true; };
  }, [apiToken]);
  return { lowReachHidden, funnel30d, summaryLoading };
}

/* ============ 快捷入项目(卡片流行内动作,不用先开抽屉):项目列表懒取一次
   (既有 GET /api/admin/vkpi/projects,EventsBoardPage 同款)+ 既有
   POST /projects/{id}/kols(addKolsToProject,后端幂等)。真实返回才回执,
   失败如实报错;候选无真 id → 动作不可达(卡面按钮已按 id 隐藏)。 ============ */

export interface QuickProjectMsg {
  text: string;
  tone: "ok" | "error" | "info";
}

export function useQuickAddToProject(apiToken: string) {
  const [target, setTarget] = React.useState<Row | null>(null);
  /** null = 未取/读取中;[] 可能是真空列表(诚实空态在弹窗里区分 error) */
  const [projects, setProjects] = React.useState<Row[] | null>(null);
  const [projectsError, setProjectsError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState<QuickProjectMsg | null>(null);

  const openFor = React.useCallback((item: Row) => {
    setTarget(item);
    setMsg(null);
    if (!apiToken) return;
    setProjects(null);
    setProjectsError("");
    apiFetch<{ projects?: Row[]; items?: Row[] }>("/api/admin/vkpi/projects?limit=200", { timeoutMs: 6000 }, apiToken)
      .then((resp) => {
        const rows = Array.isArray(resp?.projects) ? resp.projects : Array.isArray(resp?.items) ? resp.items : [];
        setProjects(rows);
      })
      .catch(() => {
        setProjects([]);
        setProjectsError("项目列表读取失败——关闭重开可重试");
      });
  }, [apiToken]);

  const close = React.useCallback(() => {
    setTarget(null);
    setMsg(null);
  }, []);

  const confirm = React.useCallback(async (projectId: string) => {
    const poolId = realPoolId(target?.id ?? target?.kol_pool_id);
    if (!apiToken || busy || !projectId || !poolId) return;
    setBusy(true);
    setMsg({ text: "入项目请求中…", tone: "info" });
    try {
      const result = await addKolsToProject(apiToken, projectId, [String(poolId)]);
      const inserted = Number(result.inserted || 0);
      const skipped = Number(result.skipped_existing || 0);
      const missing = Array.isArray(result.missing_kol_pool_ids) ? result.missing_kol_pool_ids.length : 0;
      if (inserted > 0) {
        setMsg({ text: `已新增 ${inserted} 人到项目——推进状态看「项目」板块。`, tone: "ok" });
        window.dispatchEvent(new CustomEvent("vkpi:projects-changed"));
        window.dispatchEvent(new CustomEvent("vkpi:favorites-changed"));
      } else if (skipped > 0 && missing === 0) {
        // assignment 已存在时后端仍会幂等执行“自动收藏”；回读收藏真值，
        // 避免 shared-only / 他人先加项目的 KOL 继续显示空星标。
        window.dispatchEvent(new CustomEvent("vkpi:favorites-changed"));
        setMsg({ text: "该 KOL 已在目标项目中，本次没有重复写入。", tone: "info" });
      } else {
        setMsg({ text: `未写入项目${missing ? `：${missing} 个 KOL 已不存在` : "，请刷新后重试"}`, tone: "error" });
      }
    } catch (err: any) {
      setMsg({ text: `入项目失败:${String(err?.message || err?.detail || err || "请重试").slice(0, 100)}`, tone: "error" });
    } finally {
      setBusy(false);
    }
  }, [apiToken, busy, target]);

  return { target, projects, projectsError, busy, msg, openFor, close, confirm };
}
