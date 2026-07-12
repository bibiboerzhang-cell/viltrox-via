import React from "react";
import {
  draftReply,
  fetchReplyQueue,
  fetchReplyQueueKpiSeries,
  markReply,
  screenReplyQueue,
  type ReplyQueueItem,
  type ReplyQueueKpiSeries,
  type ReplyQueueStatus,
} from "../../../../services/vkpi/replyQueue-api";

// 回复队列 · 板块页动作层(金样板 MarketVoicePage.actions / ProjectsBoardPage.actions 同构;
// 行数纪律 ≤700/文件)。
//   useQueueData    服务端分页拉取(MarketVoice feed 先例):首页 status='' + limit=500 +
//                   offset=0;>500 队列由弹窗「载入更多」逐页追加(loadMore);total=服务端
//                   COUNT 真分母。载入更多失败绝不吞已加载列表(moreError 行内透出);
//                   首拉/刷新失败 = error 原文;reload 回到第一页重拉服务器真数,绝不本地编数。
//   useKpiSeriesData KPI 时序(GET /reply-queue/kpi-series):四卡 sparkline 按日真序列 +
//                   环比 delta;端点失败/未 ready → kpi=null + error 原文,四卡诚实回落
//                   spempty 虚线零药丸(绝不编时序)。
//   useScreenAction 「扫描新意向」:POST /reply-queue/screen —— 回执只信端点真实返回
//                   (ok + scanned/matched/enqueued 真计数),成功后重拉列表。
//   useDraftAction  「生成草稿」:POST /reply-queue/{id}/draft —— 后端原子认领防并发双烧;
//                   409(claimed_by_other/already_finalized/status_conflict)诚实透出,
//                   非 ok / 未知形状 = gone 不写 ✓;成功以服务器返回的草稿/状态落地。
//   useMarkAction   「标记已回/忽略」:POST /reply-queue/{id}/mark + expected_status 乐观锁;
//                   status_conflict 时以服务器 current_status 纠正本地行(服务器真相),
//                   错误原样透出不吞。
//   useCopyDraft    一键复制草稿(v0 铁律:绝不自动发帖,复制后人工回)。
// 红线:不触 viltrox_fit_score / rule_v0;唯一网络出口 = services/vkpi/replyQueue-api。

export type Row = Record<string, any>;

// 后端 reason → 诚实人话(括注原始 reason,不吞诊断信息)
const REASON_TEXT: Record<string, string> = {
  claimed_by_other: "该条已被他人认领跟进,不重复起草",
  already_finalized: "该条已是终态(已回复/已忽略),不回炉",
  status_conflict: "状态已被他人改动",
  not_found: "队列行不存在(可能已被清理)",
  invalid_status: "状态值不合法",
  reply_queue_table_missing: "队列表未建",
  comment_not_found: "源评论不存在",
};

export function reasonText(reason: string): string {
  const key = String(reason || "").trim();
  const friendly = REASON_TEXT[key];
  return friendly ? `${friendly}(${key})` : key || "未知原因";
}

const errText = (err: any, fallback: string) => String(err?.detail || err?.message || fallback);

/** 单页条数(端点单次封顶 500;>500 队列由「载入更多」逐页追加)。 */
export const QUEUE_PAGE = 500;

/** 服务端分页拉取(MarketVoice feed 先例):首页 offset=0;loadMore 追加下一页;
 *  total=服务端过滤后总数(「已显 X/Y」真分母);载入更多失败不吞已加载列表。 */
export function useQueueData(apiToken: string) {
  const [items, setItems] = React.useState<ReplyQueueItem[] | null>(null);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(false);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [error, setError] = React.useState("");
  const [moreError, setMoreError] = React.useState("");
  const [version, setVersion] = React.useState(0);
  // 并发口径:单飞行闸(busyRef);首页/载入更多共用一条通道,晚到响应无从交错
  const busyRef = React.useRef(false);

  const fetchPage = React.useCallback(
    (offset: number) => {
      if (!apiToken || busyRef.current) return;
      busyRef.current = true;
      if (offset === 0) {
        setLoading(true);
        setError("");
      } else {
        setLoadingMore(true);
      }
      setMoreError(""); // 每次请求发起都清(MarketVoice 先例:瞬时分页失败不黏死)
      fetchReplyQueue(apiToken, { status: "", limit: QUEUE_PAGE, offset })
        .then((res) => {
          setTotal(res.total);
          setItems((prev) => (offset === 0 || !prev ? res.items : [...prev, ...res.items]));
        })
        .catch((err: any) => {
          // 首拉失败 items 仍为 null → 模块闸走诚实错误卡;刷新失败不清已有数据
          // (页头如实挂「刷新失败 · 列表为上次成功数据」);载入更多失败 → moreError
          // 行内透出,已加载列表照常渲染(失败不吞列表)
          if (offset === 0) setError(errText(err, "加载队列失败"));
          else setMoreError(errText(err, "载入更多失败"));
        })
        .finally(() => {
          busyRef.current = false;
          if (offset === 0) setLoading(false);
          else setLoadingMore(false);
        });
    },
    [apiToken],
  );

  React.useEffect(() => {
    if (apiToken) fetchPage(0);
  }, [apiToken, version, fetchPage]);

  const reload = React.useCallback(() => setVersion((v) => v + 1), []);
  /** 追加下一页(offset=已载入行数;拉齐后 hasMore=false 自然停)。 */
  const loadMore = React.useCallback(() => {
    if (!items || items.length >= total) return;
    fetchPage(items.length);
  }, [items, total, fetchPage]);
  /** 单行以服务器返回字段就地纠正(草稿/状态落地;绝不编造服务器没给的值)。 */
  const patchItem = React.useCallback((id: number, patch: Partial<ReplyQueueItem>) => {
    setItems((prev) => (prev ? prev.map((it) => (it.id === id ? { ...it, ...patch } : it)) : prev));
  }, []);

  return { items, total, loading, loadingMore, error, moreError, reload, loadMore, patchItem };
}

/** KPI 时序:四卡 sparkline/环比真数据;非 ready(empty/error)→ kpi=null + 原因,
 *  四卡回落 spempty 虚线零药丸(诚实无时序,绝不编序列)。 */
export function useKpiSeriesData(apiToken: string) {
  const [kpi, setKpi] = React.useState<ReplyQueueKpiSeries | null>(null);
  const [error, setError] = React.useState("");
  const [version, setVersion] = React.useState(0);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setError("");
    fetchReplyQueueKpiSeries(apiToken)
      .then((res) => {
        if (!alive) return;
        if (res && res.status === "ready") {
          setKpi(res);
        } else {
          setKpi(null);
          setError(String(res?.reason || "时序端点未就绪"));
        }
      })
      .catch((err: any) => {
        if (!alive) return;
        setKpi(null);
        setError(errText(err, "时序加载失败"));
      });
    return () => {
      alive = false;
    };
  }, [apiToken, version]);

  const reload = React.useCallback(() => setVersion((v) => v + 1), []);
  return { kpi, error, reload };
}

export interface ScreenReceipt {
  scanned: number;
  matched: number;
  enqueued: number;
}

/** 扫描新意向:端点真实返回 ok 才落回执(真计数),随后由调用方重拉列表。 */
export function useScreenAction(apiToken: string, onDone: () => void) {
  const [busy, setBusy] = React.useState(false);
  const [receipt, setReceipt] = React.useState<ScreenReceipt | null>(null);
  const [error, setError] = React.useState("");

  const run = React.useCallback(() => {
    if (!apiToken || busy) return;
    setBusy(true);
    setError("");
    setReceipt(null);
    screenReplyQueue(apiToken, { limit: 800 })
      .then((res) => {
        if (res && res.ok) {
          setReceipt({
            scanned: Number(res.scanned) || 0,
            matched: Number(res.matched) || 0,
            enqueued: Number(res.enqueued) || 0,
          });
          onDone();
        } else {
          setError(reasonText(String((res as Row)?.reason || "端点返回非 ok")));
        }
      })
      .catch((err: any) => setError(errText(err, "扫描失败")))
      .finally(() => setBusy(false));
  }, [apiToken, busy, onDone]);

  return { busy, receipt, error, run };
}

/** 本会话草稿回执(provider / retrieved_skus 真值,进详情溯源行;不上门面)。 */
export interface DraftReceipt {
  provider: string;
  retrievedSkus: string[];
}

/** 生成草稿:成功以服务器返回的 draft_reply/status 落地;非 ok = gone 不写 ✓。 */
export function useDraftAction(
  apiToken: string,
  patchItem: (id: number, patch: Partial<ReplyQueueItem>) => void,
) {
  const [busyId, setBusyId] = React.useState<number | null>(null);
  const [error, setError] = React.useState("");
  const [receipts, setReceipts] = React.useState<Record<number, DraftReceipt>>({});

  const run = React.useCallback(
    (id: number) => {
      if (!apiToken || busyId != null) return;
      setBusyId(id);
      setError("");
      draftReply(apiToken, id)
        .then((res) => {
          if (res && res.ok && typeof res.draft_reply === "string" && res.draft_reply) {
            patchItem(id, { draft_reply: res.draft_reply, status: String(res.status || "drafted") });
            setReceipts((prev) => ({
              ...prev,
              [id]: {
                provider: String(res.provider || ""),
                retrievedSkus: Array.isArray(res.retrieved_skus) ? res.retrieved_skus.map(String) : [],
              },
            }));
          } else {
            setError(reasonText(String((res as Row)?.reason || "端点返回非 ok")));
          }
        })
        .catch((err: any) => {
          // 409 等业务错误带 detail=reason → 人话映射;纯网络错误 → 原文透出
          const detail = String(err?.detail || "");
          setError(detail ? reasonText(detail) : errText(err, "生成草稿失败"));
        })
        .finally(() => setBusyId((cur) => (cur === id ? null : cur)));
    },
    [apiToken, busyId, patchItem],
  );

  const clearError = React.useCallback(() => setError(""), []);
  return { busyId, error, receipts, run, clearError };
}

/** 标记状态:expected_status 乐观锁;冲突时以服务器 current_status 纠正本地行。 */
export function useMarkAction(
  apiToken: string,
  patchItem: (id: number, patch: Partial<ReplyQueueItem>) => void,
) {
  const [busyId, setBusyId] = React.useState<number | null>(null);
  const [error, setError] = React.useState("");

  const run = React.useCallback(
    (id: number, status: ReplyQueueStatus, expectedStatus: string) => {
      if (!apiToken || busyId != null) return;
      setBusyId(id);
      setError("");
      markReply(apiToken, id, status, expectedStatus)
        .then((res) => {
          if (res && res.ok) {
            patchItem(id, { status: String(res.status || status) });
          } else {
            setError(reasonText(String((res as Row)?.reason || "端点返回非 ok")));
            const current = String((res as Row)?.current_status || "");
            if (current) patchItem(id, { status: current });
          }
        })
        .catch((err: any) => {
          // 409 status_conflict:router 只透 reason 字符串,拿不到 current_status →
          // 诚实报错,由用户「刷新」拉服务器真相(不猜、不本地编状态)。
          const detail = String(err?.detail || "");
          setError(detail ? reasonText(detail) : errText(err, "标记失败"));
        })
        .finally(() => setBusyId((cur) => (cur === id ? null : cur)));
    },
    [apiToken, busyId, patchItem],
  );

  const clearError = React.useCallback(() => setError(""), []);
  return { busyId, error, run, clearError };
}

/** 一键复制草稿(v0 铁律:不自动发帖;复制成功徽 1.5s 自清)。 */
export function useCopyDraft() {
  const [copiedId, setCopiedId] = React.useState<number | null>(null);

  const copy = React.useCallback((id: number, text: string) => {
    const doneFlag = () => {
      setCopiedId(id);
      globalThis.setTimeout(() => setCopiedId((cur) => (cur === id ? null : cur)), 1500);
    };
    try {
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(text).then(doneFlag).catch(() => doneFlag());
      } else {
        doneFlag();
      }
    } catch {
      doneFlag();
    }
  }, []);

  return { copiedId, copy };
}
