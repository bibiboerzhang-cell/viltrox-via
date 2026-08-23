// Action Inbox 标签/元信息表(从 ActionInboxPanel 抽出以守千行卫兵;行为不变)。
// 所有中文标签在面板里经 t() 显示,英文词条住 data/i18nEn.ts。
import {
  Brain,
  CalendarClock,
  ClipboardList,
  Eye,
  FileCheck,
  Gavel,
  PackageOpen,
  RefreshCw,
  Share2,
  Sparkles,
  Target,
  UserPlus,
} from "lucide-react";

// execute outcome 的 reason 码 → 友好中文(后端 executors/validators 的诚实回因)。
export const EXEC_REASON: Record<string, string> = {
  not_approved: "未审批",
  reminder_only_no_executor: "提醒类 · 无需执行,可忽略",
  no_executor: "暂无执行器",
  unknown_category: "未知动作类别",
  entity_missing: "关联实体已不存在",
  budget_hard_stop: "超出单次预算上限",
  touches_v6_fit_violation: "命中 fit 红线,已拦截",
  validation_failed: "执行前校验未通过",
  execution_finalize_failed: "执行结果未能安全落账,必须人工核对",
  execution_claim_lost: "执行状态发生变化,必须人工核对",
  deep_missing_no_profile_url: "缺主页 URL,无法深析",
  kol_profile_no_profile_url: "缺主页 URL,无法补全",
  failed_retry_not_in_failed_state: "任务非失败态,无需重试",
  content_candidate_no_post_id: "缺内容贴 ID",
};

// 9 类 → 中文标签 + 图标 + 强调色
export const CATEGORY_META = {
  kol_profile: { label: "补全资料", Icon: UserPlus, color: "text-amber-300" },
  deep_missing: { label: "深析待跑", Icon: Brain, color: "text-violet-300" },
  failed_retry: { label: "失败重试", Icon: RefreshCw, color: "text-red-300" },
  project_observation: { label: "开观察窗", Icon: Eye, color: "text-cyan-300" },
  content_candidate: { label: "内容确认", Icon: FileCheck, color: "text-emerald-300" },
  retrospective: { label: "项目复盘", Icon: ClipboardList, color: "text-sky-300" },
  event_followup: { label: "活动收尾", Icon: CalendarClock, color: "text-orange-300" },
  inventory_low: { label: "库存预警", Icon: PackageOpen, color: "text-yellow-300" },
  // W4 produce,meta 在此补全:项目共享给你(sky 色)。
  project_shared_to_you: { label: "项目共享", Icon: Share2, color: "text-sky-300" },
  // 闭环波 L4:到期强制裁决任务(L2 produce)。不可跳过,内嵌裁决一屏。
  gtm_verdict: { label: "裁决对答案", Icon: Gavel, color: "text-fuchsia-300" },
  // GTM-Loop L1:GTM Plan materialize 落库的押注(无自动执行体,人做业务动作后标记已执行)。
  gtm_bet: { label: "GTM押注", Icon: Target, color: "text-sky-300" },
  // 波 C·S 车道:Skill「创作者匹配」跑完后的建议条(marketing_brain/skill_license_gate INBOX_CATEGORY)。
  skill_creator_match: { label: "创作者匹配建议", Icon: Sparkles, color: "text-emerald-300" },
};

export const PRIORITY_META = {
  high: { label: "高", cls: "bg-red-500/15 text-red-300 border-red-500/25" },
  medium: { label: "中", cls: "bg-amber-500/15 text-amber-300 border-amber-500/25" },
  low: { label: "低", cls: "bg-slate-500/15 text-slate-300 border-slate-500/25" },
};
// 路线0:风险等级徽标(执行该动作的风险,独立于优先级)。
export const RISK_META: Record<string, { label: string; cls: string }> = {
  high: { label: "风险高", cls: "bg-rose-500/15 text-rose-300 border-rose-500/25" },
  medium: { label: "风险中", cls: "bg-orange-500/15 text-orange-300 border-orange-500/25" },
  low: { label: "风险低", cls: "bg-emerald-500/12 text-emerald-300/80 border-emerald-500/20" },
};
