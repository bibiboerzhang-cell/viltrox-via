// TextResultSection 的 props 契约 + memo 比较器 + 回调稳定化(M2「治卡」②)。
//
// 背景:结果区是 68 个 props 的巨树。容器每重渲一次(历史条刷新、关注同步、筛选面板开合……),
// 它就跟着整棵重画。直接套 React.memo 是挡不住的——props 里有两类「每次都是新引用」的东西:
//   (a) 回调:controller 里 run / queueTextAdvance / retrySearchSession / resumeSearchPolling …
//       是普通函数声明,每次渲染重新创建。controller 不在本车道名下,故在这里用 latest-ref
//       包一层稳定壳:外壳身份恒定,内部永远调最新那个,不会读到过期闭包。
//   (b) 每渲染重算的派生对象:activeSessionCounts / sessionBanner / llmPlan(asRecord 空对象)
//       / reachFloorDisplay / sessionProgress —— 内容常常一模一样,只是换了地址。
//       对这几个 key 退化为「按内容比对」;其余一律引用比对。
//
// 比较器 fail-open:任何一项判不出相等就照常重渲染,绝不会因为比较器把真实更新吞掉。
import { useMemo, useRef } from "react";

import type { VkpiKolRecallItem, VkpiKolRecallResponse, VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import type { Row } from "./SmartKolInputPanel.helpers";
import type { SearchSessionProgress } from "./SmartKolInputPanel.derivers";
import type { SessionBanner } from "./SmartKolInputPanel.SearchProgress";
import { sameByContent } from "./SmartKolInputPanel.renderGuards";

export type TextResultSectionProps = {
  recallResult: VkpiKolRecallResponse;
  searchSession: VkpiKolSearchHistoryItem | null;
  llmPlan: Row;
  discoveryItems: any[];
  discoveryTotal?: number;
  discoveryAutoEnrolled?: number | null;
  /** 品牌官方账号排除数(诚实信息):>0 才渲染一行说明;旧后端无该键恒 0 静默。 */
  discoveryBrandExcluded?: number;
  /** 触达展示闸折叠计数(2026-07-12「分析后再 po」):lowReach=低触达不展示(已入库仅不推荐)、
   *  analyzing=档案补全中,达标后自动放出;旧后端/无隐藏 → null 不渲染。 */
  reachFloorDisplay?: {
    discovery: { lowReach: number; analyzing: number; pendingFollowers: number };
    recall: { lowReach: number; analyzing: number; pendingFollowers: number };
  } | null;
  input: string;
  apiToken: string;
  isBusy: boolean;
  state: string;
  plannerFellBack: boolean;
  personaEditing: boolean;
  personaDraft: string;
  setPersonaEditing: (v: boolean) => void;
  setPersonaDraft: (v: string) => void;
  setInput: (v: string) => void;
  run: (overrideQuery?: string, productSku?: string) => void;
  discoveryPlatforms: string[];
  setDiscoveryPlatforms: (updater: (cur: string[]) => string[]) => void;
  discoveryRegion: string;
  setDiscoveryRegion: (v: string) => void;
  contentLanguages: string[];
  setContentLanguages: (v: string[]) => void;
  kolProfileTypes: string[];
  setKolProfileTypes: (v: string[]) => void;
  excludeChinese: boolean;
  setExcludeChinese: (v: boolean) => void;
  queueTextAdvance: (overrideQuery?: string) => void;
  pickedIds: Set<number>;
  setPickedIds: (v: Set<number>) => void;
  favNote: string;
  favoriteIds: ReadonlySet<number>;
  favoriteBusyIds: ReadonlySet<number>;
  favoriteResults: ReadonlyMap<number, string>;
  favoriteErrors: ReadonlyMap<number, string>;
  favoritesSyncing: boolean;
  favoritesLoadError: string;
  draftNote: string;
  outreachNote: string;
  outreachResult: Record<string, any> | null;
  addingFav: boolean;
  draftBusy: boolean;
  outreachBusy: boolean;
  displayedSearchSessionId: number | null;
  isSessionPolling: boolean;
  isSessionPollPaused: boolean;
  resultsStale: boolean;
  approvalReady: boolean;
  favoriteOne: (kolPoolId: number) => void;
  addPickedToMyKol: () => void;
  approveAndCreateDraft: () => void;
  generateOutreachForPicked: () => void;
  discoveryKey: (item: any) => string;
  onOpenRecallItem?: (item: VkpiKolRecallItem) => void;
  sessionBanner: SessionBanner;
  sessionProgress: SearchSessionProgress;
  activeSessionCounts: Row;
  sessionPollNotice: string;
  retrySearchSession: () => void;
  resumeSearchPolling: () => void;
};

/** 这几个 props 由容器每渲染重算,内容常常没变、只是换了地址 → 退化为按内容比对。 */
export const CONTENT_COMPARED_PROP_KEYS = [
  "llmPlan",
  "reachFloorDisplay",
  "sessionBanner",
  "sessionProgress",
  "activeSessionCounts",
] as const satisfies readonly (keyof TextResultSectionProps)[];

const CONTENT_COMPARED = new Set<string>(CONTENT_COMPARED_PROP_KEYS);

/** React.memo 比较器:返回 true = 可以跳过重渲染。判不出相等就返回 false(照常重渲染)。 */
export function textResultPropsAreEqual(
  prev: TextResultSectionProps,
  next: TextResultSectionProps,
): boolean {
  const keys = Object.keys(next) as (keyof TextResultSectionProps)[];
  if (keys.length !== Object.keys(prev).length) return false;
  for (const key of keys) {
    if (Object.is(prev[key], next[key])) continue;
    if (CONTENT_COMPARED.has(key as string) && sameByContent(prev[key], next[key])) continue;
    return false;
  }
  return true;
}

export type TextResultCallbackProps = Pick<
  TextResultSectionProps,
  | "setPersonaEditing" | "setPersonaDraft" | "setInput" | "run"
  | "setDiscoveryPlatforms" | "setDiscoveryRegion" | "setContentLanguages" | "setKolProfileTypes"
  | "setExcludeChinese" | "queueTextAdvance" | "setPickedIds" | "favoriteOne"
  | "addPickedToMyKol" | "approveAndCreateDraft" | "generateOutreachForPicked"
  | "discoveryKey" | "onOpenRecallItem" | "retrySearchSession" | "resumeSearchPolling"
>;

/**
 * latest-ref 稳定壳:外壳身份跨渲染恒定(memo 的浅比较能过),调用时永远转发到最新的那个函数,
 * 因此不会出现「memo 挡住重渲 → 子树握着上一轮闭包」的过期回调问题。
 */
export function useStableTextResultCallbacks(props: TextResultSectionProps): TextResultCallbackProps {
  const latest = useRef(props);
  latest.current = props;
  return useMemo<TextResultCallbackProps>(() => ({
    setPersonaEditing: (v) => latest.current.setPersonaEditing(v),
    setPersonaDraft: (v) => latest.current.setPersonaDraft(v),
    setInput: (v) => latest.current.setInput(v),
    run: (overrideQuery, productSku) => latest.current.run(overrideQuery, productSku),
    setDiscoveryPlatforms: (updater) => latest.current.setDiscoveryPlatforms(updater),
    setDiscoveryRegion: (v) => latest.current.setDiscoveryRegion(v),
    setContentLanguages: (v) => latest.current.setContentLanguages(v),
    setKolProfileTypes: (v) => latest.current.setKolProfileTypes(v),
    setExcludeChinese: (v) => latest.current.setExcludeChinese(v),
    queueTextAdvance: (overrideQuery) => latest.current.queueTextAdvance(overrideQuery),
    setPickedIds: (v) => latest.current.setPickedIds(v),
    favoriteOne: (kolPoolId) => latest.current.favoriteOne(kolPoolId),
    addPickedToMyKol: () => latest.current.addPickedToMyKol(),
    approveAndCreateDraft: () => latest.current.approveAndCreateDraft(),
    generateOutreachForPicked: () => latest.current.generateOutreachForPicked(),
    discoveryKey: (item) => latest.current.discoveryKey(item),
    // 可选回调:容器没给就静默 no-op,与原来的 `onOpenRecallItem?.(...)` 行为一致。
    onOpenRecallItem: (item) => latest.current.onOpenRecallItem?.(item),
    retrySearchSession: () => latest.current.retrySearchSession(),
    resumeSearchPolling: () => latest.current.resumeSearchPolling(),
  }), []);
}
