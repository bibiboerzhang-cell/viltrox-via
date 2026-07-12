import React from "react";
import {
  createPrdReferral,
  enqueueReplyQueueComment,
  listPrdReferrals,
  updatePrdReferralStatus,
  type PrdReferralCreateBody,
  type PrdReferralItem,
  type PrdReferralUpdatableStatus,
} from "../../../../services/vkpi/marketVoice-api";

// 市场之声 · 闭环动作 hooks(自 MarketVoicePage 拆出,行数纪律 ≤700/文件)。
//   useReplyQueueAction  V0e「转回复队列」:POST /reply-queue/enqueue-comment(幂等);
//   usePrdReferral       V1.1「转产品部」真闭环:POST /market/prd-referrals →
//                        vkpi_market_prd_referrals 落一行(迁移 234,幂等);
//   usePrdStatusBoard    「PRD 状态流转」模块数据 + 动作:GET /market/prd-referrals
//                        列表(最近 50 条)+ PATCH /{id}/status(referred → accepted /
//                        rejected,幂等)—— 药丸只在端点真实返回后按服务器回传行变色。
//   hooks 的共同纪律:状态只在端点真实返回后落地(ok / already_*),绝不点击即置绿;
//   失败原因原样进 error(不吞、不假绿);version 只在真实成功后自增,供调用方
//   重拉窗口计数(KPI 真数),绝不本地 +1 编数。
// 红线:不触 viltrox_fit_score / rule_v0;唯一网络出口 = services/vkpi/marketVoice-api。

type Row = Record<string, any>;

export function useReplyQueueAction(apiToken: string) {
  // queuedIds = 本会话真实入队成功(含 already_queued=幂等命中已有行)的评论 id
  const [queuedIds, setQueuedIds] = React.useState<Set<number>>(() => new Set());
  const [busyId, setBusyId] = React.useState<number | null>(null);
  const [error, setError] = React.useState("");

  const enqueue = React.useCallback(
    (commentId: number) => {
      if (!apiToken || busyId != null) return;
      setBusyId(commentId);
      setError("");
      enqueueReplyQueueComment(apiToken, commentId)
        .then((res) => {
          if (res && res.ok) {
            setQueuedIds((prev) => new Set(prev).add(commentId));
          } else {
            setError(String((res as Row)?.reason || "端点返回非 ok"));
          }
        })
        .catch((err: any) => {
          setError(String(err?.detail || err?.message || "入队失败"));
        })
        .finally(() => {
          setBusyId((cur) => (cur === commentId ? null : cur));
        });
    },
    [apiToken, busyId],
  );

  const clearError = React.useCallback(() => setError(""), []);
  return { queuedIds, busyId, error, enqueue, clearError };
}

/** 转交去重 key:`${source_table}:${source_id}`(与后端 UNIQUE 幂等锚同构)。 */
export const prdKey = (sourceTable: string, sourceId: string | number) => `${sourceTable}:${sourceId}`;

/**
 * 规则建议(lexicon_reco)稳定 source_id:kind + 标题主干。
 * 标题里括号段带窗口计数(如「窗口内 3 条抱怨」)随窗口漂移,截去后跨窗口稳定,
 * 同一建议在不同月份只转交一次(与后端 UNIQUE(source_table,source_id) 幂等锚配合)。
 */
export function recoStableKey(kind: string, title: string): string {
  const stem = String(title || "").split("(")[0].split("(")[0].trim();
  return `${String(kind || "reco")}:${stem}`.slice(0, 200);
}

export function usePrdReferral(apiToken: string) {
  // referredKeys = 本会话端点真实返回成功(含 already_referred)的 prdKey 集合
  const [referredKeys, setReferredKeys] = React.useState<Set<string>>(() => new Set());
  const [busyKey, setBusyKey] = React.useState<string | null>(null);
  const [error, setError] = React.useState("");
  // 真实新增转交后自增:调用方以此重拉 voice-report-ext,让「已转产品部」KPI 读服务器真数
  const [version, setVersion] = React.useState(0);

  const refer = React.useCallback(
    (body: PrdReferralCreateBody) => {
      const key = prdKey(body.source_table, body.source_id);
      if (!apiToken || busyKey != null || !body.source_id) return;
      setBusyKey(key);
      setError("");
      createPrdReferral(apiToken, body)
        .then((res) => {
          if (res && res.ok) {
            setReferredKeys((prev) => new Set(prev).add(key));
            if (!res.already_referred) setVersion((v) => v + 1);
          } else {
            setError(String((res as Row)?.reason || "端点返回非 ok"));
          }
        })
        .catch((err: any) => {
          setError(String(err?.detail || err?.message || "转交失败"));
        })
        .finally(() => {
          setBusyKey((cur) => (cur === key ? null : cur));
        });
    },
    [apiToken, busyKey],
  );

  const clearError = React.useCallback(() => setError(""), []);
  return { referredKeys, busyKey, error, version, refer, clearError };
}

/**
 * 「PRD 状态流转」模块(prdStatus):转交账本列表 + 行内 ✓采纳/✕拒绝。
 * - 列表 = GET /market/prd-referrals(最近 50 条,按转交时间倒序);version(新转交
 *   真实成功后自增)变化时重拉,让新转交的行即刻进账本;
 * - setStatus = PATCH /{id}/status:药丸只在端点真实返回后以服务器回传行覆盖本地行
 *   (绝不点击即变色);失败原因原样进 actionError(不吞、不假绿)。
 */
export function usePrdStatusBoard(apiToken: string, version: number) {
  const [items, setItems] = React.useState<PrdReferralItem[] | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  // listStatus:后端诚实态透传("ready" / "absent"=表未建,迁移 234 未 apply)
  const [listStatus, setListStatus] = React.useState("");
  const [reason, setReason] = React.useState("");
  const [busyId, setBusyId] = React.useState<number | null>(null);
  const [actionError, setActionError] = React.useState("");

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    listPrdReferrals(apiToken, { limit: 50 })
      .then((res) => {
        if (!alive) return;
        setItems(Array.isArray(res?.items) ? res.items : []);
        setListStatus(String(res?.status || ""));
        setReason(String(res?.reason || ""));
      })
      .catch((err: any) => {
        if (!alive) return;
        setError(String(err?.detail || err?.message || "加载失败"));
        setItems(null);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, version]);

  const setStatus = React.useCallback(
    (referralId: number, status: PrdReferralUpdatableStatus) => {
      if (!apiToken || busyId != null || !referralId) return;
      setBusyId(referralId);
      setActionError("");
      updatePrdReferralStatus(apiToken, referralId, status)
        .then((res) => {
          if (res && res.ok && res.item) {
            // 端点真实返回才变色:以服务器回传行覆盖本地行(already_set 同样是真行)
            setItems((prev) => (prev ? prev.map((it) => (it.id === res.item.id ? res.item : it)) : prev));
          } else {
            setActionError(String((res as Record<string, any>)?.reason || "端点返回非 ok"));
          }
        })
        .catch((err: any) => {
          setActionError(String(err?.detail || err?.message || "状态更新失败"));
        })
        .finally(() => {
          setBusyId((cur) => (cur === referralId ? null : cur));
        });
    },
    [apiToken, busyId],
  );

  const clearActionError = React.useCallback(() => setActionError(""), []);
  return { items, loading, error, listStatus, reason, busyId, actionError, setStatus, clearActionError };
}
