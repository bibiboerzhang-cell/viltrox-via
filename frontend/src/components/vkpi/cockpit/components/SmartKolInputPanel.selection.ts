import { useEffect, useRef, useState } from "react";

import {
  approveKolSearchSession,
  createProjectDraftFromSession,
  favoriteKolPool,
  generateKolSearchSessionOutreach,
  resolveKolPool,
} from "../../../../services/vkpi/kolPool-api";
import { cleanText } from "./SmartKolInputPanel.helpers";
import { type SearchRequestEpoch, useSessionScopedSelection } from "./SmartKolInputPanel.sessionEpoch";


type SelectionParams = {
  apiToken: string;
  displayedSearchSessionId: number | null;
  canApprove: boolean;
  currentSearchRequest: () => SearchRequestEpoch;
  isCurrentSearchRequest: (epoch: SearchRequestEpoch) => boolean;
};


export function useSmartKolSelection({
  apiToken,
  displayedSearchSessionId,
  canApprove,
  currentSearchRequest,
  isCurrentSearchRequest,
}: SelectionParams) {
  const { pickedIds, setPickedIds, clearPickedIds, togglePick } = useSessionScopedSelection(displayedSearchSessionId);
  const [addingFav, setAddingFav] = useState(false);
  const [favNote, setFavNote] = useState("");
  const [resolvedPids, setResolvedPids] = useState<Map<string, number>>(() => new Map());
  const [resolvingKeys, setResolvingKeys] = useState<Set<string>>(() => new Set());
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftNote, setDraftNote] = useState("");
  const [outreachBusy, setOutreachBusy] = useState(false);
  const [outreachNote, setOutreachNote] = useState("");
  const [outreachResult, setOutreachResult] = useState<Record<string, any> | null>(null);
  const previousSessionId = useRef<number | null>(displayedSearchSessionId);

  useEffect(() => {
    if (previousSessionId.current === displayedSearchSessionId) return;
    previousSessionId.current = displayedSearchSessionId;
    setAddingFav(false);
    setFavNote("");
    setResolvedPids(new Map());
    setResolvingKeys(new Set());
    setDraftBusy(false);
    setDraftNote("");
    setOutreachBusy(false);
    setOutreachNote("");
    setOutreachResult(null);
  }, [displayedSearchSessionId]);

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
    if (!apiToken || !pickedIds.size) return;
    setAddingFav(true);
    setFavNote("");
    const requestEpoch = currentSearchRequest();
    const ids = [...pickedIds];
    const results = await Promise.allSettled(ids.map((id) => favoriteKolPool(apiToken, id)));
    const succeeded = results.filter((result) => result.status === "fulfilled").length;
    if (isCurrentSearchRequest(requestEpoch)) {
      setFavNote(succeeded === ids.length ? `已加入我的 MY KOL · ${succeeded} 人` : `加入 ${succeeded}/${ids.length}(其余失败,可重试)`);
      clearPickedIds();
    }
    setAddingFav(false);
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
      const source = response?.llm_used ? "LLM" : "确定性模板(LLM 未启用/预算关)";
      setOutreachNote(`已生成 ${count} 封话术草案 · ${source}${response?.truncated ? " · 已截断至上限" : ""}`);
    } catch (error: any) {
      if (isCurrentSearchRequest(requestEpoch)) setOutreachNote(`生成话术失败 · ${error?.message || "请重试"}`);
    } finally {
      setOutreachBusy(false);
    }
  }

  return {
    pickedIds, setPickedIds, clearPickedIds, togglePick, addingFav, favNote, resolvedPids, resolvingKeys,
    draftBusy, draftNote, outreachBusy, outreachNote, outreachResult,
    discoveryKey, pickDiscovery, addPickedToMyKol, approveAndCreateDraft, generateOutreachForPicked,
  };
}
