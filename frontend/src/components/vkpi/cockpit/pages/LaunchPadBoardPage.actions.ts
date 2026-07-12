import React from "react";
import { approvePublish, remindPublishKol, reschedulePublish } from "../../../../services/vkpi/publish-api";
import { reviewContentPost, type Row } from "../../../../services/vkpi/launchBoard-api";

// 发射台 · 闭环动作 hooks(金样板 MarketVoicePage.actions 同构;行数纪律 ≤700/文件)。
//   usePublishActions  发布审批三动作:POST /publish/approve|reschedule|remind →
//                      vkpi_publish_approvals 按 (source_table,source_id) upsert(迁移 173)。
//   useContentReview   内容候选人工复核:PATCH /projects/content-posts/{id} →
//                      matched/rejected/needs_review(matched 时后端回填观察窗口)。
//   共同纪律:状态只在端点真实返回(status success/ok)后落地,绝不点击即置绿;失败原因
//   原样进 error(不吞、不假绿);version 只在真实成功后自增,供调用方重拉服务器真数
//   (审批列表 / KPI 计数),绝不本地 +1 编数。
// 红线:不触 viltrox_fit_score / rule_v0;唯一网络出口 = services(publish-api / launchBoard-api)。

/** 审批 upsert 幂等锚(与后端 UNIQUE(source_table,source_id) 同构)。 */
export const pubKey = (sourceTable: string, sourceId: string | number) => `${sourceTable}:${sourceId}`;

export interface PublishActionState {
  approved?: boolean;
  scheduledAt?: string;
  reminded?: boolean;
}

export interface PublishMeta {
  platform?: string;
  account_handle?: string;
  title?: string;
}

export function usePublishActions(apiToken: string) {
  // states = 本会话端点真实返回成功的动作留痕(key → 已通过/已重排/已提醒)
  const [states, setStates] = React.useState<Record<string, PublishActionState>>({});
  const [busyKey, setBusyKey] = React.useState<string | null>(null);
  const [error, setError] = React.useState("");
  const [version, setVersion] = React.useState(0);

  const run = React.useCallback(
    (
      key: string,
      call: () => Promise<Row>,
      onOk: (res: Row) => PublishActionState,
    ) => {
      if (!apiToken || busyKey != null) return;
      setBusyKey(key);
      setError("");
      call()
        .then((res) => {
          if (res && String(res.status) === "success") {
            setStates((prev) => ({ ...prev, [key]: { ...prev[key], ...onOk(res) } }));
            setVersion((v) => v + 1);
          } else {
            setError(String((res as Row)?.detail || (res as Row)?.reason || "端点返回非 success"));
          }
        })
        .catch((err: any) => {
          setError(String(err?.detail || err?.message || "操作失败"));
        })
        .finally(() => {
          setBusyKey((cur) => (cur === key ? null : cur));
        });
    },
    [apiToken, busyKey],
  );

  const approve = React.useCallback(
    (sourceTable: string, sourceId: string | number, meta: PublishMeta = {}) => {
      const key = pubKey(sourceTable, sourceId);
      run(key, () => approvePublish(apiToken, sourceTable, String(sourceId), meta), () => ({ approved: true }));
    },
    [apiToken, run],
  );

  const reschedule = React.useCallback(
    (sourceTable: string, sourceId: string | number, whenIso: string, meta: PublishMeta = {}) => {
      const key = pubKey(sourceTable, sourceId);
      run(key, () => reschedulePublish(apiToken, sourceTable, String(sourceId), whenIso, meta), (res) => ({
        scheduledAt: String(res.scheduled_publish_at || whenIso),
      }));
    },
    [apiToken, run],
  );

  const remind = React.useCallback(
    (sourceTable: string, sourceId: string | number, meta: PublishMeta = {}) => {
      const key = pubKey(sourceTable, sourceId);
      run(key, () => remindPublishKol(apiToken, sourceTable, String(sourceId), meta), () => ({ reminded: true }));
    },
    [apiToken, run],
  );

  const clearError = React.useCallback(() => setError(""), []);
  return { states, busyKey, error, version, approve, reschedule, remind, clearError };
}

export type ContentReviewAction = "matched" | "rejected" | "needs_review";

export function useContentReview(apiToken: string) {
  // reviewed = 本会话端点真实返回 ok 的复核结果(post_id → 新 status),渲染层覆盖显示
  const [reviewed, setReviewed] = React.useState<Record<number, string>>({});
  const [busyId, setBusyId] = React.useState<number | null>(null);
  const [error, setError] = React.useState("");
  const [version, setVersion] = React.useState(0);

  const review = React.useCallback(
    (postId: number, action: ContentReviewAction, note = "") => {
      if (!apiToken || busyId != null || !postId) return;
      setBusyId(postId);
      setError("");
      reviewContentPost(apiToken, postId, action, note)
        .then((res) => {
          if (res && String(res.status) === "ok") {
            setReviewed((prev) => ({ ...prev, [postId]: String(res.post?.status || action) }));
            setVersion((v) => v + 1);
          } else {
            setError(String((res as Row)?.error || "端点返回非 ok"));
          }
        })
        .catch((err: any) => {
          setError(String(err?.detail || err?.message || "复核失败"));
        })
        .finally(() => {
          setBusyId((cur) => (cur === postId ? null : cur));
        });
    },
    [apiToken, busyId],
  );

  const clearError = React.useCallback(() => setError(""), []);
  return { reviewed, busyId, error, version, review, clearError };
}
