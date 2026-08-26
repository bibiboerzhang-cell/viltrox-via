import { useEffect, useRef, useState } from "react";

import {
  approveKolSearchSession,
  createProjectDraftFromSession,
  favoriteKolPool,
  generateKolSearchSessionOutreach,
  listKolPoolFavorites,
  resolveKolPool,
} from "../../../../services/vkpi/kolPool-api";
import { cleanText } from "./SmartKolInputPanel.helpers";
import { type SearchRequestEpoch, useSessionScopedSelection } from "./SmartKolInputPanel.sessionEpoch";


type SelectionParams = {
  apiToken: string;
  displayedSearchSessionId: number | null;
  canApprove: boolean;
  canFavorite: boolean;
  currentSearchRequest: () => SearchRequestEpoch;
  isCurrentSearchRequest: (epoch: SearchRequestEpoch) => boolean;
};

type FavoriteActionScope = {
  apiToken: string;
  requestEpoch: SearchRequestEpoch;
  sessionId: number | null;
};


export function useSmartKolSelection({
  apiToken,
  displayedSearchSessionId,
  canApprove,
  canFavorite,
  currentSearchRequest,
  isCurrentSearchRequest,
}: SelectionParams) {
  const { pickedIds, setPickedIds, clearPickedIds, togglePick } = useSessionScopedSelection(displayedSearchSessionId);
  const [addingFav, setAddingFav] = useState(false);
  const [favNote, setFavNote] = useState("");
  const [favoriteIds, setFavoriteIds] = useState<Set<number>>(() => new Set());
  const [favoriteBusyIds, setFavoriteBusyIds] = useState<Set<number>>(() => new Set());
  const [favoriteResults, setFavoriteResults] = useState<Map<number, string>>(() => new Map());
  const [favoriteErrors, setFavoriteErrors] = useState<Map<number, string>>(() => new Map());
  const [favoritesSyncing, setFavoritesSyncing] = useState(false);
  const [favoritesLoadError, setFavoritesLoadError] = useState("");
  const [resolvedPids, setResolvedPids] = useState<Map<string, number>>(() => new Map());
  const [resolvingKeys, setResolvingKeys] = useState<Set<string>>(() => new Set());
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftNote, setDraftNote] = useState("");
  const [outreachBusy, setOutreachBusy] = useState(false);
  const [outreachNote, setOutreachNote] = useState("");
  const [outreachResult, setOutreachResult] = useState<Record<string, any> | null>(null);
  const previousSessionId = useRef<number | null>(displayedSearchSessionId);
  const displayedSessionIdRef = useRef<number | null>(displayedSearchSessionId);
  const apiTokenRef = useRef(apiToken);
  const canFavoriteRef = useRef(canFavorite);
  const favoriteOperationSequence = useRef(0);
  const favoriteOperationById = useRef<Map<number, number>>(new Map());
  const activeBulkFavoriteOperation = useRef(0);
  displayedSessionIdRef.current = displayedSearchSessionId;
  apiTokenRef.current = apiToken;
  canFavoriteRef.current = canFavorite;

  useEffect(() => {
    if (previousSessionId.current === displayedSearchSessionId) return;
    previousSessionId.current = displayedSearchSessionId;
    favoriteOperationById.current.clear();
    activeBulkFavoriteOperation.current = 0;
    setAddingFav(false);
    setFavNote("");
    setFavoriteBusyIds(new Set());
    setFavoriteResults(new Map());
    setFavoriteErrors(new Map());
    setResolvedPids(new Map());
    setResolvingKeys(new Set());
    setDraftBusy(false);
    setDraftNote("");
    setOutreachBusy(false);
    setOutreachNote("");
    setOutreachResult(null);
  }, [displayedSearchSessionId]);

  useEffect(() => {
    if (!apiToken) {
      setFavoriteIds(new Set());
      setFavoriteBusyIds(new Set());
      setFavoritesSyncing(false);
      setFavoritesLoadError("");
      return undefined;
    }
    let active = true;
    favoriteOperationById.current.clear();
    activeBulkFavoriteOperation.current = 0;
    setFavoriteIds(new Set());
    setFavoriteBusyIds(new Set());
    setFavoritesSyncing(true);
    setFavoritesLoadError("");
    listKolPoolFavorites(apiToken, 5000)
      .then((response) => {
        if (!active) return;
        const ids = new Set<number>();
        for (const item of Array.isArray(response?.items) ? response.items : []) {
          const poolId = Number(item?.kol_pool_id);
          if (Number.isInteger(poolId) && poolId > 0) ids.add(poolId);
        }
        // Merge instead of replace: a user may click “关注” while the initial list is still
        // in flight. The mutation is authoritative and must not disappear when this read lands.
        setFavoriteIds((current) => new Set([...current, ...ids]));
      })
      .catch(() => {
        if (active) setFavoritesLoadError("MY KOL 关注状态暂时无法同步，可直接关注；服务端会幂等处理");
      })
      .finally(() => {
        if (active) setFavoritesSyncing(false);
      });
    return () => {
      active = false;
    };
  }, [apiToken]);

  function markFavoriteBusy(ids: number[], busy: boolean) {
    setFavoriteBusyIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => (busy ? next.add(id) : next.delete(id)));
      return next;
    });
  }

  function setFavoriteMessage(poolId: number, message: string, failed = false) {
    const setTarget = failed ? setFavoriteErrors : setFavoriteResults;
    const clearTarget = failed ? setFavoriteResults : setFavoriteErrors;
    setTarget((current) => new Map(current).set(poolId, message));
    clearTarget((current) => {
      const next = new Map(current);
      next.delete(poolId);
      return next;
    });
  }

  function favoriteActionScope(): FavoriteActionScope {
    return { apiToken, requestEpoch: currentSearchRequest(), sessionId: displayedSearchSessionId };
  }

  function isFavoriteActionCurrent(scope: FavoriteActionScope): boolean {
    return apiTokenRef.current === scope.apiToken
      && displayedSessionIdRef.current === scope.sessionId
      && canFavoriteRef.current
      && isCurrentSearchRequest(scope.requestEpoch);
  }

  async function favoriteOne(poolId: number) {
    if (!apiToken || !canFavorite || !Number.isInteger(poolId) || poolId <= 0 || favoriteBusyIds.has(poolId)) return;
    if (favoriteIds.has(poolId)) {
      setFavoriteMessage(poolId, "已在 MY KOL");
      return;
    }
    const scope = favoriteActionScope();
    const operationId = favoriteOperationSequence.current + 1;
    favoriteOperationSequence.current = operationId;
    favoriteOperationById.current.set(poolId, operationId);
    markFavoriteBusy([poolId], true);
    try {
      const response = await favoriteKolPool(apiToken, poolId);
      if (!isFavoriteActionCurrent(scope) || favoriteOperationById.current.get(poolId) !== operationId) return;
      const status = String(response?.status || "");
      if (!["favorited", "already_favorited"].includes(status)) {
        setFavoriteMessage(poolId, "关注结果未确认，请重试", true);
        setFavNote("KOL 关注结果未确认；当前行已保留，可直接重试");
        return;
      }
      const already = status === "already_favorited";
      setFavoriteIds((current) => new Set(current).add(poolId));
      setFavoriteMessage(poolId, already ? "已在 MY KOL" : "已加入 MY KOL");
      setFavNote(already ? "该 KOL 已在你的 MY KOL 中" : "已关注 1 人，可前往 MY KOL 继续跟进");
    } catch {
      if (!isFavoriteActionCurrent(scope) || favoriteOperationById.current.get(poolId) !== operationId) return;
      setFavoriteMessage(poolId, "关注失败，请重试", true);
      setFavNote("有 KOL 关注失败；未写入的行可直接重试");
    } finally {
      if (favoriteOperationById.current.get(poolId) === operationId) {
        favoriteOperationById.current.delete(poolId);
        markFavoriteBusy([poolId], false);
      }
    }
  }

  function discoveryKey(item: any): string {
    return `${cleanText(item?.platform).toLowerCase()}:${cleanText(item?.handle).toLowerCase().replace(/^@/, "")}`;
  }

  async function pickDiscovery(item: any) {
    const direct = Number(item?.kol_pool_id) || 0;
    if (direct > 0) { togglePick(direct); return; }
    const key = discoveryKey(item);
    const cached = resolvedPids.get(key);
    if (cached) { togglePick(cached); return; }
    if (resolvingKeys.has(key) || !apiToken) return;
    const handle = cleanText(item?.handle).replace(/^@/, "");
    if (!handle) { setFavNote("该新发现缺 handle,无法定位入库记录"); return; }
    const requestEpoch = currentSearchRequest();
    setResolvingKeys((current) => new Set(current).add(key));
    try {
      const response: any = await resolveKolPool(apiToken, handle, cleanText(item?.platform));
      if (!isCurrentSearchRequest(requestEpoch)) return;
      const poolId = Number(response?.kol_pool_id || response?.matched_kol_pool_id) || 0;
      if (poolId > 0) {
        setResolvedPids((current) => new Map(current).set(key, poolId));
        togglePick(poolId);
      } else {
        setFavNote(`「${handle}」尚未入库,请稍后重试或刷新发现列表`);
      }
    } catch {
      if (isCurrentSearchRequest(requestEpoch)) setFavNote(`「${handle}」定位失败,请重试`);
    } finally {
      setResolvingKeys((current) => {
        const next = new Set(current);
        next.delete(key);
        return next;
      });
    }
  }

  async function addPickedToMyKol() {
    if (!apiToken || !canFavorite || !pickedIds.size || activeBulkFavoriteOperation.current) return;
    const scope = favoriteActionScope();
    const operationId = favoriteOperationSequence.current + 1;
    favoriteOperationSequence.current = operationId;
    activeBulkFavoriteOperation.current = operationId;
    setAddingFav(true);
    setFavNote("");
    const ids = [...pickedIds];
    const alreadyIds = ids.filter((id) => favoriteIds.has(id));
    const pendingIds = ids.filter((id) => !favoriteIds.has(id));
    pendingIds.forEach((id) => favoriteOperationById.current.set(id, operationId));
    markFavoriteBusy(pendingIds, true);
    try {
      const results = await Promise.allSettled(pendingIds.map((id) => favoriteKolPool(apiToken, id)));
      if (!isFavoriteActionCurrent(scope) || activeBulkFavoriteOperation.current !== operationId) return;
      const createdIds: number[] = [];
      const serverAlreadyIds: number[] = [];
      const failedIds: number[] = [];
      results.forEach((result, index) => {
        const poolId = pendingIds[index];
        if (result.status === "rejected") {
          failedIds.push(poolId);
          return;
        }
        const status = String(result.value?.status || "");
        if (status === "favorited") createdIds.push(poolId);
        else if (status === "already_favorited") serverAlreadyIds.push(poolId);
        else failedIds.push(poolId);
      });
      const confirmedIds = [...createdIds, ...serverAlreadyIds];
      if (confirmedIds.length) {
        setFavoriteIds((current) => {
          const next = new Set(current);
          confirmedIds.forEach((id) => next.add(id));
          return next;
        });
      }
      createdIds.forEach((id) => setFavoriteMessage(id, "已加入 MY KOL"));
      [...alreadyIds, ...serverAlreadyIds].forEach((id) => setFavoriteMessage(id, "已在 MY KOL"));
      failedIds.forEach((id) => setFavoriteMessage(id, "关注失败或结果未确认，请重试", true));
      const alreadyCount = alreadyIds.length + serverAlreadyIds.length;
      const parts = [
        createdIds.length ? `新增 ${createdIds.length} 人` : "",
        alreadyCount ? `已关注 ${alreadyCount} 人` : "",
        failedIds.length ? `失败或未确认 ${failedIds.length} 人（已保留选择，可重试）` : "",
      ].filter(Boolean);
      setFavNote(`MY KOL 处理完成 · ${parts.join(" · ")} · 可继续分组、认领和跟进`);
      if (failedIds.length) setPickedIds(new Set(failedIds));
      else clearPickedIds();
    } finally {
      const ownedIds = pendingIds.filter((id) => favoriteOperationById.current.get(id) === operationId);
      ownedIds.forEach((id) => favoriteOperationById.current.delete(id));
      if (ownedIds.length) markFavoriteBusy(ownedIds, false);
      if (activeBulkFavoriteOperation.current === operationId) {
        activeBulkFavoriteOperation.current = 0;
        setAddingFav(false);
      }
    }
  }

  async function approveAndCreateDraft() {
    if (!apiToken || !pickedIds.size || !displayedSearchSessionId || !canApprove) return;
    setDraftBusy(true);
    setDraftNote("");
    const requestEpoch = currentSearchRequest();
    const ids = [...pickedIds];
    try {
      await approveKolSearchSession(apiToken, displayedSearchSessionId, ids);
      if (!isCurrentSearchRequest(requestEpoch)) return;
      const draft: any = await createProjectDraftFromSession(apiToken, displayedSearchSessionId);
      if (!isCurrentSearchRequest(requestEpoch)) return;
      const total = draft?.cost_estimate?.total_cents || {};
      const lowUsd = Math.round((total.low || 0) / 100);
      const highUsd = Math.round((total.high || 0) / 100);
      const risk = draft?.cost_estimate?.risk?.level || "—";
      const budget = total.low || total.high
        ? ` · 预算 ~$${lowUsd.toLocaleString()}–$${highUsd.toLocaleString()} · 风险 ${risk}`
        : "";
      const warning = draft?.kol_attach_warning ? ` · ⚠ ${String(draft.kol_attach_warning).slice(0, 60)}` : "";
      setDraftNote(`已建草案 ${draft?.project_uid || ""}(挂 ${draft?.attached_kol_count ?? 0}/${ids.length} 人)${budget}${warning}`);
    } catch (error: any) {
      if (isCurrentSearchRequest(requestEpoch)) setDraftNote(`建草案失败 · ${error?.message || "请重试"}`);
    } finally {
      setDraftBusy(false);
    }
  }

  async function generateOutreachForPicked() {
    if (!apiToken || !pickedIds.size || !displayedSearchSessionId || !canApprove) return;
    setOutreachBusy(true);
    setOutreachNote("");
    setOutreachResult(null);
    const requestEpoch = currentSearchRequest();
    const ids = [...pickedIds];
    try {
      await approveKolSearchSession(apiToken, displayedSearchSessionId, ids);
      if (!isCurrentSearchRequest(requestEpoch)) return;
      const response: any = await generateKolSearchSessionOutreach(apiToken, displayedSearchSessionId);
      if (!isCurrentSearchRequest(requestEpoch)) return;
      setOutreachResult(response || null);
      const count = Array.isArray(response?.messages) ? response.messages.length : 0;
      const source = response?.llm_used ? "智能生成" : "确定性模板(智能生成未启用或预算已关)";
      setOutreachNote(`已生成 ${count} 封话术草案 · ${source}${response?.truncated ? " · 已截断至上限" : ""}`);
    } catch (error: any) {
      if (isCurrentSearchRequest(requestEpoch)) setOutreachNote(`生成话术失败 · ${error?.message || "请重试"}`);
    } finally {
      setOutreachBusy(false);
    }
  }

  return {
    pickedIds, setPickedIds, clearPickedIds, togglePick, addingFav, favNote, resolvedPids, resolvingKeys,
    favoriteIds, favoriteBusyIds, favoriteResults, favoriteErrors, favoritesSyncing, favoritesLoadError,
    draftBusy, draftNote, outreachBusy, outreachNote, outreachResult,
    discoveryKey, pickDiscovery, favoriteOne, addPickedToMyKol, approveAndCreateDraft, generateOutreachForPicked,
  };
}
