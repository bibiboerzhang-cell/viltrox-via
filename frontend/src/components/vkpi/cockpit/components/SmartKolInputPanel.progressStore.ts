// 搜索进度外部存储(M2「治卡」①):把「轮询这一拍的进度文案」从 React 组件树上摘下来,
// 改由订阅它的那一行自订阅、自重渲。
//
// 为什么要有它:轮询原来每拍都 setSessionPollNotice(...) —— 一次 setState 打在
// SmartKolInputPanel 容器上,68 props 的结果巨树跟着整页重画。这行文案只占屏幕上一行。
//
// 为什么只放文案、不放阶段与计数:阶段卡与「已找到/已入库」读的是 controller 里
// mergeKolSearchSessionSnapshots 合并后的会话(keep-richer:后到的稀疏快照不许把已显示的字段刷掉)。
// 轮询手上只有未合并的原始快照,把它发布出去会让稀疏的一拍把数字往回走。文案本来就是按原始快照算的
// (改造前也是),放进来不改变口径;阶段与计数继续走 props,合并后的真值。
//
// 红线:这里只搬运轮询已经算好的诚实事实,不新增百分比、不推断进度、不编造数字。
// store 为空 = 展示层回落到 props,不猜。
import { useSyncExternalStore } from "react";

export type SearchProgressSnapshot = {
  /** 这份快照属于哪个搜索会话;null = 当前没有在跑的轮询。 */
  sessionId: number | null;
  /** 轮询这一拍的阶段文案(或同步失败提示);空串 = 无话可说,展示层回落到 props。 */
  notice: string;
};

const EMPTY_SNAPSHOT: SearchProgressSnapshot = { sessionId: null, notice: "" };

let snapshot: SearchProgressSnapshot = EMPTY_SNAPSHOT;
const listeners = new Set<() => void>();

export function subscribeSearchProgress(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** useSyncExternalStore 要求同一状态返回同一引用,故快照对象只在真正发布时替换。 */
export function getSearchProgressSnapshot(): SearchProgressSnapshot {
  return snapshot;
}

export function publishSearchProgressNotice(sessionId: number | null, notice: string): void {
  if (snapshot.sessionId === sessionId && snapshot.notice === notice) return;
  snapshot = { sessionId, notice };
  for (const listener of Array.from(listeners)) listener();
}

/** 轮询开始 / 结束时复位:回到「无实时文案」,展示层立刻回落到 props 上的终态文案。 */
export function resetSearchProgress(sessionId: number | null = null): void {
  publishSearchProgressNotice(sessionId, "");
}

/** 仅供测试:把 store 清回出厂状态。 */
export function __resetSearchProgressStoreForTests(): void {
  snapshot = EMPTY_SNAPSHOT;
  listeners.clear();
}

export function useSearchProgressSnapshot(): SearchProgressSnapshot {
  return useSyncExternalStore(
    subscribeSearchProgress,
    getSearchProgressSnapshot,
    getSearchProgressSnapshot,
  );
}
