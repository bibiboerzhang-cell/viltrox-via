// 告警 rule_key → 门面中文标签(英文走 I18N_EN 同键;cockpit 侧用 t(alertRuleLabel(key)))。
// 字面与后端一致:domains/alerts/anomaly.py ALL_RULES(异常哨兵四路)+ 既有 comment_intelligence.* /
// team_feedback.open。未登记的 rule_key 诚实回落到原 key,不猜。

export const ALERT_RULE_LABELS: Record<string, string> = {
  "anomaly.video_metric_mad": "视频播放异常",
  "anomaly.channel_post_mad": "官号帖子异常",
  "anomaly.prediction_residual_psi": "预测漂移",
  "anomaly.pipeline_failure_cluster": "管道故障聚集",
  "team_feedback.open": "团队反馈",
};

const RULE_PREFIX_LABELS: Array<[string, string]> = [
  ["comment_intelligence", "评论风险"],
  ["anomaly.", "异常哨兵"],
];

/** rule_key → 中文标签;未知 key 原样返回(空 key 返回空串)。 */
export function alertRuleLabel(ruleKey: unknown): string {
  const key = String(ruleKey || "").trim();
  if (!key) return "";
  if (ALERT_RULE_LABELS[key]) return ALERT_RULE_LABELS[key];
  for (const [prefix, label] of RULE_PREFIX_LABELS) {
    if (key.startsWith(prefix)) return label;
  }
  return key;
}

/** 是否为已登记的可读规则(用于决定是否在标签旁附原始 key)。 */
export function isKnownAlertRule(ruleKey: unknown): boolean {
  return alertRuleLabel(ruleKey) !== String(ruleKey || "").trim();
}
