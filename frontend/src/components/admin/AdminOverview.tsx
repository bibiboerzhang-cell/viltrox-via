import { FormEvent, Suspense, lazy, useDeferredValue, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";

import type { AdminSubmission, AuthUser } from "../../lib/api";
import {
  approveAdminSubmission,
  backfillAdminOrders,
  createManualAdminSubmission,
  createStudentBatch,
  createStudentSchool,
  createAdminReward,
  correctAdminSubmissionProduct,
  deleteAdminSubmission,
  fetchAdminAnalyticsSnapshot,
  fetchAdminBrandSnapshot,
  fetchAdminCommerceSnapshot,
  fetchAdminCreatorsSnapshot,
  fetchAdminDashboard,
  fetchAdminMarketSnapshot,
  fetchAdminOperationsSnapshot,
  fetchAdminProductsSnapshot,
  fetchAdminRuntimeSnapshot,
  fetchAdminStudentSnapshot,
  fetchAdminSystemSnapshot,
  fetchAdminViaSnapshot,
  reanalyzeAdminSubmission,
  rejectAdminSubmission,
  runAdminSocialAction,
  runAdminUserAction,
  runAdminVerificationAction,
  runAdminRewardAction,
  updateAdminRedemption,
  updateAdminReward,
  unwrapAdminSnapshotPayload,
  adjustAdminPoints,
  grantAdminPoints,
  runViaEvaluation,
  runViaPolicyAction,
  runViaProposalAction,
  type AdminAnalyticsSnapshot,
  type AdminBrandSnapshot,
  type AdminCommerceSnapshot,
  type AdminCreatorsSnapshot,
  type AdminDashboardSnapshot,
  type AdminMarketSnapshot,
  type AdminOperationsSnapshot,
  type AdminProductsSnapshot,
  type AdminRuntimeSnapshot,
  type AdminStudentSnapshot,
  type AdminSystemSnapshot,
  type AdminViaSnapshot,
} from "../../services/admin.service";
import { EmptyState, MetricStrip, Panel, StatusPill } from "../ui";

const CommandTab = lazy(() =>
  import("./tabs/CommandTab").then((module) => ({ default: module.CommandTab })),
);
const OperationsTab = lazy(() =>
  import("./tabs/OperationsTab").then((module) => ({ default: module.OperationsTab })),
);
const CreatorsTab = lazy(() =>
  import("./tabs/CreatorsTab").then((module) => ({ default: module.CreatorsTab })),
);
const ProductsTab = lazy(() =>
  import("./tabs/ProductsTab").then((module) => ({ default: module.ProductsTab })),
);
const StudentTab = lazy(() =>
  import("./tabs/StudentTab").then((module) => ({ default: module.StudentTab })),
);
const AnalyticsTab = lazy(() =>
  import("./tabs/AnalyticsTab").then((module) => ({ default: module.AnalyticsTab })),
);
const ViaTab = lazy(() =>
  import("./tabs/ViaTab").then((module) => ({ default: module.ViaTab })),
);
const RuntimeTab = lazy(() =>
  import("./tabs/RuntimeTab").then((module) => ({ default: module.RuntimeTab })),
);

type WorkspaceTab =
  | "command"
  | "operations"
  | "creators"
  | "products"
  | "student"
  | "analytics"
  | "via"
  | "runtime"
  | "commerce"
  | "market"
  | "brand"
  | "system";
type AdminPageId =
  | "home"
  | "content"
  | "creators"
  | "students"
  | "rewards"
  | "orders"
  | "attribution"
  | "payouts"
  | "market"
  | "brand"
  | "analytics"
  | "policies"
  | "proposals"
  | "evaluations"
  | "conversations"
  | "personas"
  | "integrations"
  | "runtime"
  | "trust"
  | "staff";

interface AdminPageDefinition {
  id: AdminPageId;
  label: string;
  batch: string;
  description: string;
  subtabs: string[];
  workspace: WorkspaceTab | "placeholder";
  status: "live" | "partial" | "planned";
}

const ADMIN_PAGE_SIZE = 8;

const ADMIN_PAGE_GROUPS: Array<{ key: string; label: string; pages: AdminPageId[] }> = [
  { key: "batch1", label: "Batch 1", pages: ["home", "content", "creators", "students", "rewards"] },
  { key: "batch2", label: "Batch 2", pages: ["orders", "attribution", "payouts"] },
  { key: "batch3", label: "Batch 3", pages: ["market", "brand", "analytics"] },
  { key: "batch4", label: "Batch 4", pages: ["policies", "proposals", "evaluations", "conversations", "personas"] },
  { key: "batch5", label: "Batch 5", pages: ["integrations", "runtime", "trust", "staff"] },
];

const ADMIN_PAGE_DEFINITIONS: AdminPageDefinition[] = [
  {
    id: "home",
    label: "Home",
    batch: "Batch 1",
    description: "Top-level command center for review load, queue health, and reward activity.",
    subtabs: ["Overview", "Review queue", "Rewards", "Runtime pulse"],
    workspace: "command",
    status: "live",
  },
  {
    id: "content",
    label: "Content",
    batch: "Batch 1",
    description: "Submission review, approvals, manual add, user approvals, and verification handling.",
    subtabs: ["Review queue", "Manual add", "Users", "Verifications"],
    workspace: "operations",
    status: "live",
  },
  {
    id: "creators",
    label: "Creators",
    batch: "Batch 1",
    description: "Creator roster, growth lens, points log, and detail inspection.",
    subtabs: ["All", "Pending approval", "Pro tier", "Student lane", "Internal", "Blocked"],
    workspace: "creators",
    status: "live",
  },
  {
    id: "students",
    label: "Students",
    batch: "Batch 1",
    description: "School setup, batch issuance, activation funnel, and student lane operations.",
    subtabs: ["Overview", "Schools", "Batches", "Students", "Activation funnel"],
    workspace: "student",
    status: "live",
  },
  {
    id: "rewards",
    label: "Rewards",
    batch: "Batch 1",
    description: "Reward catalog management is wired; redemptions and drafts are still being split into dedicated views.",
    subtabs: ["Catalog", "Redemptions", "Drafts"],
    workspace: "command",
    status: "partial",
  },
  {
    id: "orders",
    label: "Orders",
    batch: "Batch 2",
    description: "Commerce order table, webhook history, and attribution overrides.",
    subtabs: ["All orders", "Unattributed", "Student lane", "Refunded", "Webhook events"],
    workspace: "commerce",
    status: "live",
  },
  {
    id: "attribution",
    label: "Attribution",
    batch: "Batch 2",
    description: "GMV attribution breakdown, funnel, creator mix, and UTM source analysis.",
    subtabs: ["Overview", "Funnel", "By creator", "By platform", "UTM sources"],
    workspace: "commerce",
    status: "live",
  },
  {
    id: "payouts",
    label: "Payouts",
    batch: "Batch 2",
    description: "Cycle management, approvals, holds, processing, and dispute resolution.",
    subtabs: ["Payout cycles", "Current cycle", "Payment history", "Disputes"],
    workspace: "commerce",
    status: "live",
  },
  {
    id: "market",
    label: "Market",
    batch: "Batch 3",
    description: "Competitor pricing, product series signals, recent product activity, and gap insights.",
    subtabs: ["B&H competitors", "Market observations", "Genre benchmarks", "Product gaps"],
    workspace: "market",
    status: "live",
  },
  {
    id: "brand",
    label: "Brand",
    batch: "Batch 3",
    description: "Official matrix, post performance, AI brand analysis, and voice system.",
    subtabs: ["Official matrix", "Recent posts", "AI brand analysis", "Brand voice"],
    workspace: "brand",
    status: "live",
  },
  {
    id: "analytics",
    label: "Analytics",
    batch: "Batch 3",
    description: "Leaderboards, benchmarks, learning corrections, and cross-platform trend reporting.",
    subtabs: ["Overview", "Content funnel", "Creator performance", "Product series", "Cohorts"],
    workspace: "analytics",
    status: "live",
  },
  {
    id: "policies",
    label: "Policies",
    batch: "Batch 4",
    description: "VIA live and shadow policy management, routing, rollout health, and version history.",
    subtabs: ["Live & shadow", "Version history", "Provider routing", "Rollout health"],
    workspace: "via",
    status: "live",
  },
  {
    id: "proposals",
    label: "Proposals",
    batch: "Batch 4",
    description: "Routing and prompt proposals generated from the learner and evaluation pipelines.",
    subtabs: ["Pending", "Staged", "Approved", "Rejected"],
    workspace: "via",
    status: "live",
  },
  {
    id: "evaluations",
    label: "Evaluations",
    batch: "Batch 4",
    description: "Offline evals, memory retention, retrieval precision, and routing learner outputs.",
    subtabs: ["Overview", "Eval runs", "Memory retention", "Retrieval evidence", "Routing learner"],
    workspace: "via",
    status: "live",
  },
  {
    id: "conversations",
    label: "Conversations",
    batch: "Batch 4",
    description: "Session review, transcript inspection, and operational replay entry points for VIA.",
    subtabs: ["Active", "All sessions", "Abandoned", "Flagged"],
    workspace: "via",
    status: "live",
  },
  {
    id: "personas",
    label: "Personas",
    batch: "Batch 4",
    description: "Persona presets, model posture, and sample session performance.",
    subtabs: ["Active", "Experimental", "Archived"],
    workspace: "via",
    status: "live",
  },
  {
    id: "integrations",
    label: "Integrations",
    batch: "Batch 5",
    description: "Connected services, health checks, latency, error rate, and environment posture.",
    subtabs: ["AI providers", "Commerce", "Data", "Email & platforms", "Not connected"],
    workspace: "system",
    status: "live",
  },
  {
    id: "runtime",
    label: "Runtime",
    batch: "Batch 5",
    description: "Current runtime metrics, cache state, rate limits, and system health.",
    subtabs: ["Overview", "Workers", "Scheduler", "Rate limits", "Cache"],
    workspace: "runtime",
    status: "live",
  },
  {
    id: "trust",
    label: "Trust",
    batch: "Batch 5",
    description: "Trust event stream, flagged users, blocked users, and threshold rule management.",
    subtabs: ["Event stream", "Flagged users", "Blocked", "Trust rules"],
    workspace: "system",
    status: "live",
  },
  {
    id: "staff",
    label: "Staff",
    batch: "Batch 5",
    description: "Internal access control, role matrix, audit logs, and API token management.",
    subtabs: ["Members", "Roles & permissions", "Audit log", "API tokens"],
    workspace: "system",
    status: "live",
  },
];

const ADMIN_PAGE_LOOKUP = Object.fromEntries(
  ADMIN_PAGE_DEFINITIONS.map((page) => [page.id, page]),
) as Record<AdminPageId, AdminPageDefinition>;

const ADMIN_PAGE_ZH: Record<AdminPageId, { label: string; description: string; subtabs: string[] }> = {
  home: {
    label: "首页",
    description: "总控中心，覆盖审核负载、队列健康和奖励活动。",
    subtabs: ["总览", "审核队列", "奖励", "运行脉冲"],
  },
  content: {
    label: "内容",
    description: "投稿审核、通过、手工补录、用户审批和验证处理。",
    subtabs: ["审核队列", "手工补录", "用户", "验证"],
  },
  creators: {
    label: "创作者",
    description: "创作者名册、增长视图、积分日志和详情检查。",
    subtabs: ["全部", "待审批", "Pro", "学生", "内部", "封禁"],
  },
  students: {
    label: "学生",
    description: "学校配置、批次发放、激活漏斗和学生通道运营。",
    subtabs: ["总览", "学校", "批次", "学生", "激活漏斗"],
  },
  rewards: {
    label: "奖励",
    description: "奖励目录管理已接通；兑换和草稿仍在拆分独立视图。",
    subtabs: ["目录", "兑换", "草稿"],
  },
  orders: {
    label: "订单",
    description: "Commerce 订单表、webhook 历史和归因覆盖。",
    subtabs: ["全部订单", "未归因", "学生通道", "已退款", "Webhook 事件"],
  },
  attribution: {
    label: "归因",
    description: "GMV 归因拆分、漏斗、创作者结构和 UTM 源分析。",
    subtabs: ["总览", "漏斗", "按创作者", "按平台", "UTM 来源"],
  },
  payouts: {
    label: "派息",
    description: "周期管理、审批、hold、处理和争议解决。",
    subtabs: ["派息周期", "当前周期", "支付历史", "争议"],
  },
  market: {
    label: "市场",
    description: "竞品价格、产品系列信号、近期动态和缺口洞察。",
    subtabs: ["B&H 竞品", "市场观察", "题材基准", "产品缺口"],
  },
  brand: {
    label: "品牌",
    description: "官方矩阵、帖子表现、AI 品牌分析和品牌语气。",
    subtabs: ["官方矩阵", "最近帖子", "AI 品牌分析", "品牌语气"],
  },
  analytics: {
    label: "分析",
    description: "排行榜、基准、学习纠错和跨平台趋势报告。",
    subtabs: ["总览", "内容漏斗", "创作者表现", "产品系列", "分群"],
  },
  policies: {
    label: "策略",
    description: "VIA live/shadow 策略管理、路由、灰度健康和版本历史。",
    subtabs: ["Live & Shadow", "版本历史", "模型路由", "灰度健康"],
  },
  proposals: {
    label: "提案",
    description: "由 learner 和评估流程生成的路由与提示词提案。",
    subtabs: ["待处理", "已 Stage", "已通过", "已拒绝"],
  },
  evaluations: {
    label: "评估",
    description: "离线评估、记忆保留、检索精度和路由 learner 输出。",
    subtabs: ["总览", "评估运行", "记忆保留", "检索证据", "路由 learner"],
  },
  conversations: {
    label: "会话",
    description: "会话审查、完整转录检查和 VIA 运维回放入口。",
    subtabs: ["活跃", "全部会话", "放弃", "已标记"],
  },
  personas: {
    label: "人格",
    description: "人格预设、模型姿态和样本会话表现。",
    subtabs: ["启用中", "实验中", "已归档"],
  },
  integrations: {
    label: "集成",
    description: "外部服务、健康检查、延迟、错误率和环境状态。",
    subtabs: ["AI 提供商", "Commerce", "数据", "邮件与平台", "未接通"],
  },
  runtime: {
    label: "运行态",
    description: "当前运行指标、缓存状态、限流和系统健康。",
    subtabs: ["总览", "Workers", "调度器", "限流", "缓存"],
  },
  trust: {
    label: "风控",
    description: "信任事件流、异常用户、封禁用户和阈值规则。",
    subtabs: ["事件流", "已标记用户", "已封禁", "规则"],
  },
  staff: {
    label: "员工",
    description: "内部访问控制、角色矩阵、审计日志和 API Token 管理。",
    subtabs: ["成员", "角色与权限", "审计日志", "API Token"],
  },
};

function adminPageHref(pageId: AdminPageId): string {
  return pageId === "home" ? "/admin" : `/admin/${pageId}`;
}

function localeText(isZh: boolean, en: string, zh: string): string {
  return isZh ? zh : en;
}

function localizedBatchLabel(batch: string, isZh: boolean): string {
  if (!isZh) {
    return batch;
  }
  return batch.replace(/^Batch\s+(\d+)/i, "第 $1 批次");
}

function localizedPage(page: AdminPageDefinition, isZh: boolean): AdminPageDefinition {
  if (!isZh) {
    return page;
  }
  const zh = ADMIN_PAGE_ZH[page.id];
  if (!zh) {
    return { ...page, batch: localizedBatchLabel(page.batch, true) };
  }
  return {
    ...page,
    batch: localizedBatchLabel(page.batch, true),
    label: zh.label,
    description: zh.description,
    subtabs: zh.subtabs,
  };
}

function resolveAdminPage(pathname: string): AdminPageId {
  const trimmed = pathname.replace(/^\/admin\/?/, "").split("/")[0]?.trim();
  if (!trimmed) {
    return "home";
  }
  return (ADMIN_PAGE_LOOKUP as Record<string, AdminPageDefinition | undefined>)[trimmed]?.id || "home";
}

function buildWorkspaceTabs(t: (key: string) => string): Array<{ key: WorkspaceTab; label: string; note: string }> {
  return [
    { key: "command", label: t("admin.tabs.command.label"), note: t("admin.tabs.command.note") },
    { key: "operations", label: t("admin.tabs.operations.label"), note: t("admin.tabs.operations.note") },
    { key: "creators", label: t("admin.tabs.creators.label"), note: t("admin.tabs.creators.note") },
    { key: "products", label: t("admin.tabs.products.label"), note: t("admin.tabs.products.note") },
    { key: "student", label: t("admin.tabs.student.label"), note: t("admin.tabs.student.note") },
    { key: "analytics", label: t("admin.tabs.analytics.label"), note: t("admin.tabs.analytics.note") },
    { key: "via", label: t("admin.tabs.via.label"), note: t("admin.tabs.via.note") },
    { key: "runtime", label: t("admin.tabs.runtime.label"), note: t("admin.tabs.runtime.note") },
  ];
}

function toNumber(value: unknown, fallback = 0): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function compactNumber(value: unknown): string {
  return toNumber(value).toLocaleString();
}

function percentLabel(value: unknown): string {
  return `${Math.round(toNumber(value) * 100)}%`;
}

function formatDate(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) {
    return "—";
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return raw;
  }
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function formatDateTime(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) {
    return "—";
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return raw;
  }
  return `${formatDate(raw)} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function titleCase(value: string) {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((item) => item.charAt(0).toUpperCase() + item.slice(1))
    .join(" ");
}

function parseRecord(value: unknown): Record<string, unknown> {
  if (!value) {
    return {};
  }
  if (typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      return {};
    }
  }
  return {};
}

function toneForStatus(status: string): "neutral" | "success" | "warning" | "danger" {
  const normalized = String(status || "").toLowerCase();
  if (normalized.includes("success") || normalized.includes("approved") || normalized.includes("active") || normalized.includes("healthy") || normalized.includes("live")) {
    return "success";
  }
  if (normalized.includes("hold") || normalized.includes("pending") || normalized.includes("review") || normalized.includes("staged")) {
    return "warning";
  }
  if (normalized.includes("reject") || normalized.includes("fail") || normalized.includes("revoke") || normalized.includes("rollback") || normalized.includes("error")) {
    return "danger";
  }
  return "neutral";
}

function JsonInfoList({ payload }: { payload: Record<string, unknown> | null | undefined }) {
  const entries = Object.entries(payload || {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!entries.length) {
    return <EmptyState title="No runtime fields yet" body="This block will hydrate once the backend returns a richer snapshot." />;
  }
  return (
    <div className="info-list">
      {entries.map(([key, value]) => (
        <div key={key}>
          <strong>{titleCase(key)}</strong>
          <span>{typeof value === "object" ? JSON.stringify(value) : String(value)}</span>
        </div>
      ))}
    </div>
  );
}

function DataTable({
  columns,
  rows,
  empty,
}: {
  columns: string[];
  rows: Array<Array<React.ReactNode>>;
  empty: string;
}) {
  return rows.length ? (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${columns[0]}-${index}`}>
              {row.map((cell, cellIndex) => (
                <td key={cellIndex}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <EmptyState title="No rows yet" body={empty} />
  );
}

function TablePager({
  page,
  totalPages,
  totalItems,
  label,
  onChange,
}: {
  page: number;
  totalPages: number;
  totalItems: number;
  label: string;
  onChange: (next: number) => void;
}) {
  if (totalPages <= 1) {
    return (
      <div className="table-pager table-pager--static">
        <span className="table-pager__meta">{label} · {totalItems} rows</span>
      </div>
    );
  }
  return (
    <div className="table-pager">
      <span className="table-pager__meta">
        {label} · page {page} / {totalPages} · {totalItems} rows
      </span>
      <div className="table-actions">
        <button className="outline-btn" type="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          Previous
        </button>
        <button className="outline-btn" type="button" disabled={page >= totalPages} onClick={() => onChange(page + 1)}>
          Next
        </button>
      </div>
    </div>
  );
}

function TabLoader({ label }: { label: string }) {
  const { t } = useTranslation();
  return (
    <div className="admin-workspace-grid">
      <Panel title={t("admin.loader", { label })} kicker="Lazy module">
        <EmptyState
          title={t("admin.loader", { label })}
          body="This tab now loads on demand so the admin shell can open faster."
        />
      </Panel>
    </div>
  );
}

function pageTone(status: AdminPageDefinition["status"]): "success" | "warning" | "danger" {
  if (status === "live") {
    return "success";
  }
  if (status === "partial") {
    return "warning";
  }
  return "danger";
}

function statusLabel(status: AdminPageDefinition["status"], isZh = false) {
  if (status === "live") {
    return isZh ? "已接通" : "Live";
  }
  if (status === "partial") {
    return isZh ? "部分完成" : "Partial";
  }
  return isZh ? "计划中" : "Planned";
}

function AdminPageHero({
  page,
  activeSubtabIndex,
  onSubtabChange,
}: {
  page: AdminPageDefinition;
  activeSubtabIndex: number;
  onSubtabChange: (nextIndex: number) => void;
}) {
  const { i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage?.toLowerCase().startsWith("zh");
  return (
    <section className="admin-v5-hero">
      <div>
        <div className="admin-v5-breadcrumb">V-OS / {page.batch} / {page.label}</div>
        <h2 className="admin-v5-title">{page.label}</h2>
        <p className="admin-v5-description">{page.description}</p>
      </div>
      <div className="admin-v5-hero__meta">
        <StatusPill label={statusLabel(page.status, Boolean(isZh))} tone={pageTone(page.status)} />
      </div>
      <div className="admin-v5-subtabs">
        {page.subtabs.map((tab, index) => (
          <button
            key={`${page.id}-${tab}`}
            type="button"
            className={`admin-v5-subtab${index === activeSubtabIndex ? " is-active" : ""}`}
            onClick={() => onSubtabChange(index)}
          >
            {tab}
          </button>
        ))}
      </div>
    </section>
  );
}

function AdminHomePage({
  command,
  metrics,
}: {
  command: AdminDashboardSnapshot | null;
  metrics: Array<{ label: string; value: string; note?: string }>;
}) {
  const { i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage?.toLowerCase().startsWith("zh");
  return (
    <div className="admin-workspace-grid">
      <MetricStrip items={metrics} columns={4} />
      <Panel title={localeText(Boolean(isZh), "Command pulse", "总控脉冲")} kicker={localeText(Boolean(isZh), "Batch 1", "第 1 批次")}>
        <div className="admin-v5-card-grid">
          {(command?.rewards || []).slice(0, 3).map((reward) => (
            <article key={reward.id} className="admin-mini-card">
              <strong>{reward.title}</strong>
              <p>{reward.description || reward.category || localeText(Boolean(isZh), "Reward catalog item", "奖励目录项目")}</p>
              <span>{compactNumber(reward.points_cost || 0)} pts</span>
            </article>
          ))}
          {!command?.rewards?.length ? (
            <EmptyState
              title={localeText(Boolean(isZh), "Reward catalog pending", "奖励目录待加载")}
              body={localeText(Boolean(isZh), "No reward catalog rows are loaded into the admin command snapshot yet.", "管理员总控快照里还没有加载到奖励目录数据。")}
            />
          ) : null}
        </div>
      </Panel>
      <Panel title={localeText(Boolean(isZh), "Review queue", "审核队列")} kicker={localeText(Boolean(isZh), "Recent submissions", "最近投稿")}>
        <DataTable
          columns={[
            localeText(Boolean(isZh), "Submission", "投稿"),
            localeText(Boolean(isZh), "Status", "状态"),
            localeText(Boolean(isZh), "Score", "分数"),
            localeText(Boolean(isZh), "Points", "积分"),
          ]}
          rows={(command?.submissions || []).slice(0, 8).map((item) => [
            <div key={`${item.id}-title`}>
              <div className="table-primary">{item.title || `Submission #${item.id}`}</div>
              <small>
                {item.platform || localeText(Boolean(isZh), "Platform pending", "平台待定")} ·{" "}
                {item.extracted_handle || item.creator_code || localeText(Boolean(isZh), "Handle pending", "Handle 待定")}
              </small>
            </div>,
            <StatusPill key={`${item.id}-status`} label={String(item.detection_status || "pending")} tone={toneForStatus(String(item.detection_status || ""))} />,
            compactNumber(item.overall_score || item.final_score || 0),
            compactNumber(item.points_awarded || 0),
          ])}
          empty={localeText(Boolean(isZh), "No submissions in the command snapshot yet.", "总控快照里还没有投稿数据。")}
        />
      </Panel>
    </div>
  );
}

function AdminPlaceholderPage({
  page,
  body,
}: {
  page: AdminPageDefinition;
  body: string;
}) {
  const { i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage?.toLowerCase().startsWith("zh");
  return (
    <div className="admin-workspace-grid">
      <Panel title={localeText(Boolean(isZh), `${page.label} structure ready`, `${page.label} 结构已就绪`)} kicker={page.batch}>
        <EmptyState title={localeText(Boolean(isZh), `${page.label} page scaffolded`, `${page.label} 页面骨架已接入`)} body={body} />
      </Panel>
      <Panel title={localeText(Boolean(isZh), "Target subtabs", "目标子标签")} kicker="v5 parity map">
        <div className="admin-v5-chip-grid">
          {page.subtabs.map((tab) => (
            <span key={`${page.id}-chip-${tab}`} className="admin-v5-subtab">
              {tab}
            </span>
          ))}
        </div>
      </Panel>
    </div>
  );
}

function AdminCommercePage({
  page,
  commerce,
  activeSubtabIndex,
  onBackfillOrders,
}: {
  page: AdminPageDefinition;
  commerce: AdminCommerceSnapshot | null;
  activeSubtabIndex: number;
  onBackfillOrders: () => void;
}) {
  const { i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage?.toLowerCase().startsWith("zh");
  if (!commerce) {
    return <AdminPlaceholderPage page={page} body={localeText(Boolean(isZh), "Commerce data is loading from the new admin endpoints.", "Commerce 数据正在从新的后台接口加载。")} />;
  }

  if (page.id === "orders") {
    const filteredOrders = commerce.orders.filter((row) => {
      if (activeSubtabIndex === 1) return String(row.attribution_type || "").toLowerCase() === "direct";
      if (activeSubtabIndex === 2) return String(row.attribution_type || "").toLowerCase() === "student";
      if (activeSubtabIndex === 3) return String(row.status || "").toLowerCase().includes("refund");
      return true;
    });
    return (
      <div className="admin-workspace-grid">
        <MetricStrip
          items={[
            { label: localeText(Boolean(isZh), "Orders", "订单"), value: compactNumber(commerce.ordersSummary?.total), note: localeText(Boolean(isZh), "Current query window", "当前查询窗口") },
            { label: localeText(Boolean(isZh), "Attributed", "已归因"), value: compactNumber(commerce.ordersSummary?.attributed_count), note: localeText(Boolean(isZh), "Creator or student lane", "创作者或学生通道") },
            { label: "GMV", value: compactNumber(commerce.ordersSummary?.gmv_cents), note: localeText(Boolean(isZh), "Cents", "分") },
            { label: localeText(Boolean(isZh), "Commission", "佣金"), value: compactNumber(commerce.ordersSummary?.commission_owed_cents), note: localeText(Boolean(isZh), "Cents owed", "应付分") },
          ]}
          columns={4}
        />
        {activeSubtabIndex !== 4 ? (
        <Panel
          title={
            activeSubtabIndex === 1
              ? localeText(Boolean(isZh), "Unattributed orders", "未归因订单")
              : activeSubtabIndex === 2
                ? localeText(Boolean(isZh), "Student-lane orders", "学生通道订单")
                : activeSubtabIndex === 3
                  ? localeText(Boolean(isZh), "Refunded orders", "退款订单")
                  : localeText(Boolean(isZh), "Orders", "订单")
          }
          kicker={localeText(Boolean(isZh), "Batch 2 commerce", "第 2 批次 Commerce")}
          aside={
            <button className="outline-btn" type="button" onClick={onBackfillOrders}>
              {localeText(Boolean(isZh), "Backfill orders", "回流订单")}
            </button>
          }
        >
          <DataTable
            columns={[
              localeText(Boolean(isZh), "Order", "订单"),
              localeText(Boolean(isZh), "Country", "国家"),
              localeText(Boolean(isZh), "Amount", "金额"),
              localeText(Boolean(isZh), "Attribution", "归因"),
              localeText(Boolean(isZh), "Status", "状态"),
            ]}
            rows={filteredOrders.slice(0, 12).map((row) => [
              <div key={`order-${row.id}`}>
                <div className="table-primary">{String(row.external_order_id || `#${row.id || ""}`)}</div>
                <small>{String(row.customer_email || localeText(Boolean(isZh), "no customer email", "没有客户邮箱"))}</small>
              </div>,
              String(row.customer_country || "—"),
              compactNumber(row.subtotal_cents || 0),
              `${String(row.attribution_type || "direct")} · ${String(row.attribution_source || "—")}`,
              <StatusPill key={`status-${row.id}`} label={String(row.status || "unknown")} tone={toneForStatus(String(row.status || ""))} />,
            ])}
            empty={localeText(Boolean(isZh), "No normalized orders yet.", "还没有标准化订单。")}
          />
        </Panel>
        ) : null}
        <Panel title={localeText(Boolean(isZh), "Webhook events", "Webhook 事件")} kicker={localeText(Boolean(isZh), "Ingest stream", "采集流")}>
          <DataTable
            columns={[
              localeText(Boolean(isZh), "Source", "来源"),
              localeText(Boolean(isZh), "Event", "事件"),
              localeText(Boolean(isZh), "External ID", "外部 ID"),
              localeText(Boolean(isZh), "Status", "状态"),
              localeText(Boolean(isZh), "Occurred", "发生时间"),
            ]}
            rows={commerce.webhookEvents.slice(0, 10).map((row) => [
              String(row.source_platform || "—"),
              String(row.event_type || "—"),
              String(row.external_id || "—"),
              <StatusPill key={`wh-${row.id}`} label={String(row.ingest_status || "queued")} tone={toneForStatus(String(row.ingest_status || ""))} />,
              formatDateTime(row.occurred_at),
            ])}
            empty={localeText(Boolean(isZh), "No webhook events in the current window.", "当前窗口内没有 webhook 事件。")}
          />
        </Panel>
      </div>
    );
  }

  if (page.id === "attribution") {
    const breakdown = parseRecord(commerce.attributionOverview?.breakdown);
    const breakdownItems = Object.entries(breakdown).map(([key, value]) => {
      const item = parseRecord(value);
      return {
        label: titleCase(key),
        value: compactNumber(item.gmv || 0),
        note: `${compactNumber(item.orders || 0)} orders · ${item.pct || 0}%`,
      };
    });
    return (
      <div className="admin-workspace-grid">
        {activeSubtabIndex === 0 ? <MetricStrip items={breakdownItems} columns={3} /> : null}
        {activeSubtabIndex === 0 || activeSubtabIndex === 2 ? (
        <Panel title={localeText(Boolean(isZh), "Top creators", "头部创作者")} kicker={localeText(Boolean(isZh), "Attribution mix", "归因结构")}>
          <DataTable
            columns={[
              localeText(Boolean(isZh), "Handle", "Handle"),
              localeText(Boolean(isZh), "Type", "类型"),
              localeText(Boolean(isZh), "Orders", "订单"),
              "GMV",
              "CTR",
            ]}
            rows={commerce.attributionByCreator.slice(0, 10).map((row) => [
              String(row.handle || "—"),
              String(row.type || "—"),
              compactNumber(row.orders || 0),
              compactNumber(row.gmv || 0),
              `${row.ctr_pct || 0}%`,
            ])}
            empty={localeText(Boolean(isZh), "No attributed creator performance yet.", "还没有创作者归因表现数据。")}
          />
        </Panel>
        ) : null}
        {activeSubtabIndex === 0 || activeSubtabIndex === 3 ? (
        <Panel title={localeText(Boolean(isZh), "By platform", "按平台")} kicker={localeText(Boolean(isZh), "Channel conversion", "渠道转化")}>
          <DataTable
            columns={[
              localeText(Boolean(isZh), "Source", "来源"),
              localeText(Boolean(isZh), "Orders", "订单"),
              "GMV",
              localeText(Boolean(isZh), "Clicks", "点击"),
              localeText(Boolean(isZh), "Conversion", "转化率"),
            ]}
            rows={commerce.attributionByPlatform.slice(0, 10).map((row) => [
              String(row.source || "—"),
              compactNumber(row.orders || 0),
              compactNumber(row.gmv || 0),
              compactNumber(row.clicks || 0),
              `${row.conversion_pct || 0}%`,
            ])}
            empty={localeText(Boolean(isZh), "No platform attribution rows yet.", "还没有平台归因数据。")}
          />
        </Panel>
        ) : null}
        {activeSubtabIndex === 0 || activeSubtabIndex === 1 ? (
        <Panel title={localeText(Boolean(isZh), "Funnel", "漏斗")} kicker={localeText(Boolean(isZh), "Journey drop-off", "路径流失")}>
          <DataTable
            columns={[
              localeText(Boolean(isZh), "Stage", "阶段"),
              localeText(Boolean(isZh), "Count", "数量"),
              localeText(Boolean(isZh), "Rate", "比率"),
              localeText(Boolean(isZh), "Drop to next", "下一步流失"),
            ]}
            rows={((commerce.attributionFunnel?.stages as Array<Record<string, unknown>>) || []).map((row) => [
              String(row.label || row.name || "—"),
              compactNumber(row.count || 0),
              `${row.rate || 0}%`,
              row.drop_to_next === null || row.drop_to_next === undefined ? "—" : `${row.drop_to_next}%`,
            ])}
            empty={localeText(Boolean(isZh), "No funnel rows yet.", "还没有漏斗数据。")}
          />
        </Panel>
        ) : null}
        {activeSubtabIndex === 0 || activeSubtabIndex === 4 ? (
        <Panel title={localeText(Boolean(isZh), "UTM sources", "UTM 来源")} kicker={localeText(Boolean(isZh), "Traffic quality", "流量质量")}>
          <DataTable
            columns={[
              localeText(Boolean(isZh), "Source", "来源"),
              localeText(Boolean(isZh), "Medium", "Medium"),
              localeText(Boolean(isZh), "Sessions", "会话"),
              localeText(Boolean(isZh), "Orders", "订单"),
              localeText(Boolean(isZh), "Conversion", "转化率"),
            ]}
            rows={commerce.attributionUtmSources.slice(0, 10).map((row) => [
              String(row.source || "—"),
              String(row.medium || "—"),
              compactNumber(row.sessions || 0),
              compactNumber(row.orders || 0),
              `${row.conversion_pct || 0}%`,
            ])}
            empty={localeText(Boolean(isZh), "No UTM performance rows yet.", "还没有 UTM 表现数据。")}
          />
        </Panel>
        ) : null}
      </div>
    );
  }

  return (
    <div className="admin-workspace-grid">
      <MetricStrip
        items={[
          { label: "Cycles", value: compactNumber(commerce.payoutCycles.length), note: "Tracked payout periods" },
          { label: "Disputes", value: compactNumber(commerce.payoutDisputes.length), note: "Open and resolved" },
          {
            label: "Current cycle",
            value: String(parseRecord(commerce.payoutCurrentCycle?.cycle).label || "—"),
            note: String(parseRecord(commerce.payoutCurrentCycle?.cycle).status || "not ready"),
          },
        ]}
        columns={3}
      />
      {activeSubtabIndex === 0 || activeSubtabIndex === 2 ? (
      <Panel title={localeText(Boolean(isZh), "Payout cycles", "派息周期")} kicker={localeText(Boolean(isZh), "Batch 2 commerce", "第 2 批次 Commerce")}>
        <DataTable
          columns={[
            localeText(Boolean(isZh), "Cycle", "周期"),
            localeText(Boolean(isZh), "Status", "状态"),
            localeText(Boolean(isZh), "Approved", "已批准"),
            localeText(Boolean(isZh), "Paid", "已支付"),
            localeText(Boolean(isZh), "Creators", "创作者"),
          ]}
          rows={commerce.payoutCycles.slice(0, 10).map((row) => [
            String(row.label || row.id || "—"),
            <StatusPill key={`cycle-${row.id}`} label={String(row.status || "unknown")} tone={toneForStatus(String(row.status || ""))} />,
            compactNumber(row.approved_cents || 0),
            compactNumber(row.paid_cents || 0),
            compactNumber(row.creator_count || 0),
          ])}
          empty={localeText(Boolean(isZh), "No payout cycles created yet.", "还没有派息周期。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 1 ? (
      <Panel title={localeText(Boolean(isZh), "Current cycle", "当前周期")} kicker={localeText(Boolean(isZh), "Pending payouts", "待处理派息")}>
        <DataTable
          columns={[
            localeText(Boolean(isZh), "Creator", "创作者"),
            localeText(Boolean(isZh), "Method", "方式"),
            localeText(Boolean(isZh), "Amount", "金额"),
            localeText(Boolean(isZh), "Status", "状态"),
          ]}
          rows={(((commerce.payoutCurrentCycle?.payouts as Array<Record<string, unknown>>) || []).slice(0, 10)).map((row) => [
            String(row.user_handle || row.user_email || "—"),
            String(row.method || "—"),
            compactNumber(row.amount_cents || 0),
            <StatusPill key={`payout-${row.id}`} label={String(row.status || "pending")} tone={toneForStatus(String(row.status || ""))} />,
          ])}
          empty={localeText(Boolean(isZh), "No payouts in the selected cycle.", "当前周期还没有派息记录。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 3 ? (
      <Panel title={localeText(Boolean(isZh), "Disputes", "争议")} kicker={localeText(Boolean(isZh), "Manual review", "人工审核")}>
        <DataTable
          columns={[
            localeText(Boolean(isZh), "Payout", "派息"),
            localeText(Boolean(isZh), "Reason", "原因"),
            localeText(Boolean(isZh), "Status", "状态"),
            localeText(Boolean(isZh), "Created", "创建时间"),
          ]}
          rows={commerce.payoutDisputes.slice(0, 10).map((row) => [
            String(row.payout_id || "—"),
            String(row.reason || "—"),
            <StatusPill key={`dispute-${row.id}`} label={String(row.status || "open")} tone={toneForStatus(String(row.status || ""))} />,
            formatDateTime(row.created_at),
          ])}
          empty={localeText(Boolean(isZh), "No payout disputes yet.", "还没有派息争议。")}
        />
      </Panel>
      ) : null}
    </div>
  );
}

function AdminMarketPage({ market, activeSubtabIndex }: { market: AdminMarketSnapshot | null; activeSubtabIndex: number }) {
  const { i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage?.toLowerCase().startsWith("zh");
  return (
    <div className="admin-workspace-grid">
      {activeSubtabIndex === 0 ? (
      <Panel title={localeText(Boolean(isZh), "Heatmap", "热力图")} kicker={localeText(Boolean(isZh), "Competitive segments", "竞品段位")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "Focal", "焦段"), "Viltrox", localeText(Boolean(isZh), "Competitors", "竞品"), localeText(Boolean(isZh), "Status", "状态")]}
          rows={(market?.heatmap || []).slice(0, 12).map((row) => [
            String(row.focal || "—"),
            compactNumber(row.viltrox_count || 0),
            compactNumber(
              Object.values(parseRecord(row.competitor_counts)).reduce<number>(
                (sum, value) => sum + toNumber(value),
                0,
              ),
            ),
            <StatusPill key={`heat-${row.focal}`} label={String(row.status || "mid")} tone={toneForStatus(String(row.status || ""))} />,
          ])}
          empty={localeText(Boolean(isZh), "No B&H comparison rows yet.", "还没有 B&H 对比数据。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 1 ? (
      <Panel title={localeText(Boolean(isZh), "Observations", "市场观察")} kicker={localeText(Boolean(isZh), "Market stream", "市场动态流")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "Kind", "类型"), localeText(Boolean(isZh), "Title", "标题"), localeText(Boolean(isZh), "Impact", "影响"), localeText(Boolean(isZh), "Observed", "时间")]}
          rows={(market?.observations || []).slice(0, 10).map((row) => [
            String(row.event_kind || "—"),
            String(row.event_title || row.notes || "—"),
            String(row.impact || "neutral"),
            formatDateTime(row.observed_at),
          ])}
          empty={localeText(Boolean(isZh), "No market observations yet.", "还没有市场观察数据。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 2 ? (
      <Panel title={localeText(Boolean(isZh), "Benchmarks", "题材基准")} kicker={localeText(Boolean(isZh), "Genre scoring", "题材评分")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "Genre", "题材"), localeText(Boolean(isZh), "Avg score", "平均分"), localeText(Boolean(isZh), "Pass rate", "通过率"), localeText(Boolean(isZh), "Samples", "样本")]}
          rows={(market?.benchmarks || []).slice(0, 10).map((row) => [
            String(row.name || "—"),
            compactNumber(row.avg_score || 0),
            String(row.pass_rate || "0"),
            compactNumber(row.sample_count || 0),
          ])}
          empty={localeText(Boolean(isZh), "No benchmark rows yet.", "还没有基准数据。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 3 ? (
      <Panel title={localeText(Boolean(isZh), "Product gaps", "产品缺口")} kicker={localeText(Boolean(isZh), "AI insights", "AI 洞察")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "Type", "类型"), localeText(Boolean(isZh), "Title", "标题"), localeText(Boolean(isZh), "Severity", "严重度"), localeText(Boolean(isZh), "Generated", "生成时间")]}
          rows={(market?.gaps || []).slice(0, 10).map((row) => [
            String(row.type || "—"),
            String(row.title || "—"),
            String(row.severity || "info"),
            formatDateTime(row.generated_at),
          ])}
          empty={localeText(Boolean(isZh), "No gap insights yet.", "还没有缺口洞察。")}
        />
      </Panel>
      ) : null}
    </div>
  );
}

function AdminBrandPage({ brand, activeSubtabIndex }: { brand: AdminBrandSnapshot | null; activeSubtabIndex: number }) {
  const { i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage?.toLowerCase().startsWith("zh");
  return (
    <div className="admin-workspace-grid">
      {activeSubtabIndex === 0 ? (
      <Panel title={localeText(Boolean(isZh), "Official matrix", "官方矩阵")} kicker={localeText(Boolean(isZh), "Brand accounts", "品牌账号")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "Platform", "平台"), "Handle", localeText(Boolean(isZh), "Posts", "帖子"), localeText(Boolean(isZh), "Views", "播放"), localeText(Boolean(isZh), "Engagement", "互动率")]}
          rows={(brand?.matrix || []).slice(0, 12).map((row) => [
            String(row.platform || "—"),
            String(row.handle || "—"),
            compactNumber(row.posts || 0),
            compactNumber(row.views || 0),
            `${row.engagement || 0}%`,
          ])}
          empty={localeText(Boolean(isZh), "No official matrix rows yet.", "还没有官方矩阵数据。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 1 ? (
      <Panel title={localeText(Boolean(isZh), "Recent posts", "最近帖子")} kicker={localeText(Boolean(isZh), "Latest scan", "最近扫描")}>
        <DataTable
          columns={["Handle", localeText(Boolean(isZh), "Title", "标题"), localeText(Boolean(isZh), "Views", "播放"), localeText(Boolean(isZh), "Engagement", "互动率"), localeText(Boolean(isZh), "Posted", "发布时间")]}
          rows={(brand?.posts || []).slice(0, 10).map((row) => [
            String(row.account_handle || "—"),
            String(row.title || "—"),
            compactNumber(row.views || 0),
            `${row.engagement_pct || 0}%`,
            formatDateTime(row.posted_at),
          ])}
          empty={localeText(Boolean(isZh), "No recent posts loaded yet.", "还没有最近帖子数据。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 2 ? (
      <Panel title={localeText(Boolean(isZh), "AI brand analysis", "AI 品牌分析")} kicker={localeText(Boolean(isZh), "Insight cache", "洞察缓存")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "Category", "分类"), localeText(Boolean(isZh), "Title", "标题"), localeText(Boolean(isZh), "Severity", "严重度"), localeText(Boolean(isZh), "Generated", "生成时间")]}
          rows={(brand?.insights || []).slice(0, 10).map((row) => [
            String(row.category || "—"),
            String(row.title || "—"),
            String(row.severity || "info"),
            formatDateTime(row.generated_at),
          ])}
          empty={localeText(Boolean(isZh), "No brand insights yet.", "还没有品牌洞察。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 3 ? (
      <Panel title={localeText(Boolean(isZh), "Voice", "品牌语气")} kicker={localeText(Boolean(isZh), "Guidelines", "规则")}>
        <JsonInfoList payload={brand?.voice || {}} />
      </Panel>
      ) : null}
    </div>
  );
}

function AdminIntegrationsPage({ system, activeSubtabIndex }: { system: AdminSystemSnapshot | null; activeSubtabIndex: number }) {
  const { i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage?.toLowerCase().startsWith("zh");
  const rows = Object.entries(system?.integrationsByCategory || {}).flatMap(([category, items]) =>
    items.map((item) => ({ category, ...(item as Record<string, unknown>) })),
  );
  const filteredRows = rows.filter((row) => {
    if (activeSubtabIndex === 0) return row.category === "ai";
    if (activeSubtabIndex === 1) return row.category === "commerce";
    if (activeSubtabIndex === 2) return row.category === "data";
    if (activeSubtabIndex === 3) return row.category === "email";
    if (activeSubtabIndex === 4) return String(("status" in row ? row.status : "") || "").includes("not_configured") || String(("status" in row ? row.status : "") || "") === "off";
    return true;
  });
  return (
    <div className="admin-workspace-grid">
      <Panel title={localeText(Boolean(isZh), "Integrations", "集成")} kicker={localeText(Boolean(isZh), "System health", "系统健康")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "Category", "分类"), localeText(Boolean(isZh), "Service", "服务"), localeText(Boolean(isZh), "Status", "状态"), localeText(Boolean(isZh), "Requests 24h", "24 小时请求"), "P95"]}
          rows={filteredRows.map((row) => [
            String(row.category || "—"),
            String(("service_name" in row ? row.service_name : undefined) || "—"),
            <StatusPill
              key={`svc-${String(("id" in row ? row.id : row.category) || "unknown")}`}
              label={String(("status" in row ? row.status : undefined) || "unknown")}
              tone={toneForStatus(String(("status" in row ? row.status : undefined) || ""))}
            />,
            compactNumber(("requests_24h" in row ? row.requests_24h : 0) || 0),
            compactNumber(("p95_ms" in row ? row.p95_ms : 0) || 0),
          ])}
          empty={localeText(Boolean(isZh), "No integration registry rows yet.", "还没有集成注册表数据。")}
        />
      </Panel>
    </div>
  );
}

function AdminTrustPage({ system, activeSubtabIndex }: { system: AdminSystemSnapshot | null; activeSubtabIndex: number }) {
  const { i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage?.toLowerCase().startsWith("zh");
  const filteredUsers = (system?.trustUsers || []).filter((row) => {
    if (activeSubtabIndex === 1) return String(row.trust_status || "").toLowerCase() === "flagged";
    if (activeSubtabIndex === 2) return String(row.trust_status || "").toLowerCase() === "blocked";
    return true;
  });
  return (
    <div className="admin-workspace-grid">
      {activeSubtabIndex !== 3 ? (
      <Panel title={localeText(Boolean(isZh), "Trust users", "风控用户")} kicker={localeText(Boolean(isZh), "Moderation surface", "风控面")}>
        <DataTable
          columns={["Handle", localeText(Boolean(isZh), "Score", "分数"), localeText(Boolean(isZh), "Status", "状态"), localeText(Boolean(isZh), "Violations", "违规次数")]}
          rows={filteredUsers.slice(0, 12).map((row) => [
            String(row.handle || row.email || "—"),
            compactNumber(row.trust_score || 0),
            <StatusPill key={`trust-${row.id}`} label={String(row.trust_status || "normal")} tone={toneForStatus(String(row.trust_status || ""))} />,
            compactNumber(row.violation_count || 0),
          ])}
          empty={localeText(Boolean(isZh), "No trust rows yet.", "还没有风控用户数据。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 0 ? (
      <Panel title={localeText(Boolean(isZh), "Trust events", "风控事件")} kicker={localeText(Boolean(isZh), "Recent changes", "最近变化")}>
        <DataTable
          columns={["Handle", localeText(Boolean(isZh), "Kind", "类型"), localeText(Boolean(isZh), "Delta", "变化"), localeText(Boolean(isZh), "Occurred", "时间")]}
          rows={(system?.trustEvents || []).slice(0, 12).map((row) => [
            String(row.user_handle || "—"),
            String(row.kind || "—"),
            compactNumber(row.delta || 0),
            formatDateTime(row.occurred_at),
          ])}
          empty={localeText(Boolean(isZh), "No trust events yet.", "还没有风控事件。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 3 ? (
      <Panel title={localeText(Boolean(isZh), "Rules", "规则")} kicker={localeText(Boolean(isZh), "Threshold config", "阈值配置")}>
        <JsonInfoList payload={system?.trustRules || {}} />
      </Panel>
      ) : null}
    </div>
  );
}

function AdminStaffPage({ system, activeSubtabIndex }: { system: AdminSystemSnapshot | null; activeSubtabIndex: number }) {
  const { i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage?.toLowerCase().startsWith("zh");
  return (
    <div className="admin-workspace-grid">
      {activeSubtabIndex === 0 ? (
      <Panel title={localeText(Boolean(isZh), "Members", "成员")} kicker={localeText(Boolean(isZh), "Admin access", "后台访问")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "User", "用户"), localeText(Boolean(isZh), "Role", "角色"), "MFA", localeText(Boolean(isZh), "Active", "激活")]}
          rows={(system?.staffMembers || []).slice(0, 12).map((row) => [
            String(row.user_email || row.user_name || "—"),
            String(row.role || "—"),
            String(row.mfa_enabled ? localeText(Boolean(isZh), "On", "开") : localeText(Boolean(isZh), "Off", "关")),
            <StatusPill key={`staff-${row.id}`} label={String(row.active ? "active" : "inactive")} tone={toneForStatus(String(row.active ? "active" : "inactive"))} />,
          ])}
          empty={localeText(Boolean(isZh), "No staff rows yet.", "还没有员工数据。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 1 ? (
      <Panel title={localeText(Boolean(isZh), "Roles", "角色")} kicker={localeText(Boolean(isZh), "Permission matrix", "权限矩阵")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "Role", "角色"), localeText(Boolean(isZh), "Description", "描述")]}
          rows={(system?.staffRoles || []).slice(0, 10).map((row) => [
            String(row.label || row.key || "—"),
            String(row.description || "—"),
          ])}
          empty={localeText(Boolean(isZh), "No role definitions yet.", "还没有角色定义。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 2 ? (
      <Panel title={localeText(Boolean(isZh), "Audit log", "审计日志")} kicker={localeText(Boolean(isZh), "Recent privileged actions", "最近高权限操作")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "Actor", "执行者"), localeText(Boolean(isZh), "Action", "动作"), localeText(Boolean(isZh), "Target", "目标"), localeText(Boolean(isZh), "Occurred", "时间")]}
          rows={(system?.auditLog || []).slice(0, 10).map((row) => [
            String(row.actor_name || "—"),
            String(row.action || "—"),
            `${String(row.target_type || "—")} · ${String(row.target_id || "—")}`,
            formatDateTime(row.occurred_at),
          ])}
          empty={localeText(Boolean(isZh), "No admin audit rows yet.", "还没有后台审计日志。")}
        />
      </Panel>
      ) : null}
      {activeSubtabIndex === 3 ? (
      <Panel title={localeText(Boolean(isZh), "API tokens", "API Token")} kicker={localeText(Boolean(isZh), "Active credentials", "当前凭证")}>
        <DataTable
          columns={[localeText(Boolean(isZh), "Name", "名称"), localeText(Boolean(isZh), "Scope", "范围"), localeText(Boolean(isZh), "Last used", "最近使用"), localeText(Boolean(isZh), "Status", "状态")]}
          rows={(system?.apiTokens || []).slice(0, 10).map((row) => [
            String(row.name || "—"),
            String(row.scope || "—"),
            formatDateTime(row.last_used_at),
            <StatusPill key={`token-${row.id}`} label={String(row.active ? "active" : "revoked")} tone={toneForStatus(String(row.active ? "active" : "revoked"))} />,
          ])}
          empty={localeText(Boolean(isZh), "No API tokens yet.", "还没有 API Token。")}
        />
      </Panel>
      ) : null}
    </div>
  );
}

export function AdminOverview({ token, user }: { token: string; user: AuthUser }) {
  const { t, i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage?.toLowerCase().startsWith("zh");
  const navigate = useNavigate();
  const location = useLocation();
  const [command, setCommand] = useState<AdminDashboardSnapshot | null>(null);
  const [operations, setOperations] = useState<AdminOperationsSnapshot | null>(null);
  const [creators, setCreators] = useState<AdminCreatorsSnapshot | null>(null);
  const [products, setProducts] = useState<AdminProductsSnapshot | null>(null);
  const [student, setStudent] = useState<AdminStudentSnapshot | null>(null);
  const [analytics, setAnalytics] = useState<AdminAnalyticsSnapshot | null>(null);
  const [via, setVia] = useState<AdminViaSnapshot | null>(null);
  const [runtime, setRuntime] = useState<AdminRuntimeSnapshot | null>(null);
  const [commerce, setCommerce] = useState<AdminCommerceSnapshot | null>(null);
  const [market, setMarket] = useState<AdminMarketSnapshot | null>(null);
  const [brand, setBrand] = useState<AdminBrandSnapshot | null>(null);
  const [system, setSystem] = useState<AdminSystemSnapshot | null>(null);
  const [selectedCreatorHandle, setSelectedCreatorHandle] = useState("");
  const [creatorSearch, setCreatorSearch] = useState("");
  const [productSearch, setProductSearch] = useState("");
  const [studentSearch, setStudentSearch] = useState("");
  const [viaSearch, setViaSearch] = useState("");
  const [operationsSearch, setOperationsSearch] = useState("");
  const [submissionStatusFilter, setSubmissionStatusFilter] = useState("all");
  const [verificationStatusFilter, setVerificationStatusFilter] = useState("all");
  const [selectedProductKey, setSelectedProductKey] = useState("");
  const [selectedStudentId, setSelectedStudentId] = useState("");
  const [selectedSubmissionId, setSelectedSubmissionId] = useState("");
  const [selectedUserId, setSelectedUserId] = useState("");
  const [selectedPolicyKey, setSelectedPolicyKey] = useState("");
  const [reviewPage, setReviewPage] = useState(1);
  const [pendingUserPage, setPendingUserPage] = useState(1);
  const [verificationPage, setVerificationPage] = useState(1);
  const [creatorPage, setCreatorPage] = useState(1);
  const [productPage, setProductPage] = useState(1);
  const [studentPage, setStudentPage] = useState(1);
  const [viaPage, setViaPage] = useState(1);
  const [pageSubtabs, setPageSubtabs] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState<string>("");
  const [message, setMessage] = useState<{ tone: "success" | "warning" | "danger"; body: string } | null>(null);
  const [rolloutNote, setRolloutNote] = useState("");
  const [manualSubmissionOpen, setManualSubmissionOpen] = useState(false);
  const [editingRewardId, setEditingRewardId] = useState<number | null>(null);
  const [rewardForm, setRewardForm] = useState({
    title: "",
    description: "",
    category: "coupon",
    points_cost: "500",
    meta_label: "",
    image_url: "",
    stock: "0",
    sort_order: "100",
    status: "draft",
  });
  const [pointsForm, setPointsForm] = useState({
    user_id: "",
    mode: "grant",
    amount: "50",
    reason: "Admin adjustment",
  });
  const [correctionForm, setCorrectionForm] = useState({
    submission_id: "",
    correct_series: "",
    correct_label: "",
    note: "",
  });
  const [manualSubmissionForm, setManualSubmissionForm] = useState({
    platform: "Instagram",
    extracted_handle: "",
    url: "",
    title: "",
    detection_status: "confirmed",
    product_series: "",
    product_label: "",
    final_score: "260",
    creator_score: "80",
    overall_score: "206",
    views: "0",
    likes: "0",
    comments: "0",
    shares: "0",
    recommendation: "Manually added by admin",
    memo: "",
  });
  const [schoolForm, setSchoolForm] = useState({
    school_id: "",
    school_code: "",
    school_name: "",
    region: "",
    country: "USA",
    partnership_status: "pilot",
    primary_color: "#0A2463",
    accent_color: "#D62828",
  });
  const [batchForm, setBatchForm] = useState({
    school_id: "",
    batch_name: "",
    count: "20",
    roster_csv: "",
  });
  const deferredCreatorSearch = useDeferredValue(creatorSearch);
  const deferredProductSearch = useDeferredValue(productSearch);
  const deferredStudentSearch = useDeferredValue(studentSearch);
  const deferredViaSearch = useDeferredValue(viaSearch);
  const deferredOperationsSearch = useDeferredValue(operationsSearch);
  const activePage = useMemo(() => resolveAdminPage(location.pathname), [location.pathname]);
  const activePageDef = ADMIN_PAGE_LOOKUP[activePage];
  const localizedActivePageDef = useMemo(() => localizedPage(activePageDef, Boolean(isZh)), [activePageDef, isZh]);
  const activeWorkspace = activePageDef.workspace;
  const activeSubtabIndex = pageSubtabs[activePage] ?? 0;

  async function loadCommand() {
    const next = await fetchAdminDashboard(token);
    setCommand(unwrapAdminSnapshotPayload(next));
  }

  async function loadOperations() {
    const next = await fetchAdminOperationsSnapshot(token);
    setOperations(next);
  }

  async function loadCreators(handle = selectedCreatorHandle) {
    const next = await fetchAdminCreatorsSnapshot(token, handle);
    setCreators(next);
    if (!handle) {
      const fallbackHandle =
        String(next.dashboard?.creators?.[0]?.handle || "").trim() ||
        String(next.roster?.[0]?.handle || next.roster?.[0]?.extracted_handle || "").trim();
      if (fallbackHandle) {
        setSelectedCreatorHandle(fallbackHandle);
        const enriched = await fetchAdminCreatorsSnapshot(token, fallbackHandle);
        setCreators(enriched);
      }
    }
  }

  async function loadProducts() {
    const next = await fetchAdminProductsSnapshot(token);
    setProducts(unwrapAdminSnapshotPayload(next));
  }

  async function loadStudent() {
    const next = await fetchAdminStudentSnapshot(token);
    setStudent(next);
    if (!batchForm.school_id && next.schools[0]?.school_id) {
      setBatchForm((current) => ({ ...current, school_id: next.schools[0].school_id || "" }));
    }
  }

  async function loadVia() {
    const next = await fetchAdminViaSnapshot(token);
    setVia(next);
  }

  async function loadRuntime() {
    const next = await fetchAdminRuntimeSnapshot(token);
    setRuntime(next);
  }

  async function loadAnalytics() {
    const next = await fetchAdminAnalyticsSnapshot(token);
    setAnalytics(next);
  }

  async function loadCommerce() {
    const next = await fetchAdminCommerceSnapshot(token);
    setCommerce(next);
  }

  async function loadMarket() {
    const next = await fetchAdminMarketSnapshot(token);
    setMarket(next);
  }

  async function loadBrand() {
    const next = await fetchAdminBrandSnapshot(token);
    setBrand(next);
  }

  async function loadSystem() {
    const next = await fetchAdminSystemSnapshot(token);
    setSystem(next);
  }

  useEffect(() => {
    void loadCommand();
  }, [token]);

  useEffect(() => {
    if (activeWorkspace === "operations" && !operations) {
      void loadOperations();
    }
    if (activeWorkspace === "creators" && !creators) {
      void loadCreators();
    }
    if (activeWorkspace === "products" && !products) {
      void loadProducts();
    }
    if (activeWorkspace === "student" && !student) {
      void loadStudent();
    }
    if (activeWorkspace === "analytics" && !analytics) {
      void loadAnalytics();
    }
    if (activeWorkspace === "commerce" && !commerce) {
      void loadCommerce();
    }
    if (activeWorkspace === "market" && !market) {
      void loadMarket();
    }
    if (activeWorkspace === "brand" && !brand) {
      void loadBrand();
    }
    if (activeWorkspace === "system" && !system) {
      void loadSystem();
    }
    if (activeWorkspace === "via" && !via) {
      void loadVia();
    }
    if (activeWorkspace === "runtime" && !runtime) {
      void loadRuntime();
    }
  }, [activeWorkspace, analytics, brand, commerce, creators, market, operations, products, runtime, student, system, via]);

  const topMetrics = useMemo(() => {
    const queueDepth = toNumber((command?.health || {})["queue_depth"]);
    const submissions = command?.submissions.length || 0;
    const rewards = command?.rewards.length || 0;
    return [
      { label: "Users", value: compactNumber(command?.stats?.total_users), note: "Registered creators" },
      { label: "Review Queue", value: compactNumber(command?.stats?.pending_submissions), note: `${submissions} recent rows` },
      { label: "Points Awarded", value: compactNumber(command?.stats?.total_points_awarded), note: `${rewards} rewards in catalog` },
      { label: "Queue Depth", value: compactNumber(queueDepth), note: String((command?.health || {})["queue_backend"] || "runtime queue") },
    ];
  }, [command]);

  const submissionRows = useMemo<AdminSubmission[]>(
    () => {
      const rows = operations?.reviewQueue?.length ? operations.reviewQueue : command?.submissions || [];
      return rows;
    },
    [command, operations],
  );

  const filteredSubmissionRows = useMemo(() => {
    const query = deferredOperationsSearch.trim().toLowerCase();
    return submissionRows.filter((item) => {
      const status = String(item.detection_status || "").toLowerCase();
      const matchesStatus = submissionStatusFilter === "all" || status === submissionStatusFilter;
      if (!matchesStatus) {
        return false;
      }
      if (!query) {
        return true;
      }
      const haystack = [
        item.title,
        item.platform,
        item.creator_code,
        item.display_name,
        item.extracted_handle,
        item.product_series,
        item.product_label,
      ]
        .map((value) => String(value || "").toLowerCase())
        .join(" ");
      return haystack.includes(query);
    });
  }, [deferredOperationsSearch, submissionRows, submissionStatusFilter]);

  const reviewTotalPages = Math.max(1, Math.ceil(filteredSubmissionRows.length / ADMIN_PAGE_SIZE));
  const reviewRowsPaged = useMemo(() => {
    const start = (reviewPage - 1) * ADMIN_PAGE_SIZE;
    return filteredSubmissionRows.slice(start, start + ADMIN_PAGE_SIZE);
  }, [filteredSubmissionRows, reviewPage]);

  const pendingUsers = useMemo(() => {
    const rows = operations?.users || [];
    const query = deferredOperationsSearch.trim().toLowerCase();
    return rows.filter((item) => {
      const status = String(item.status || "").toLowerCase();
      const matchesStatus = submissionStatusFilter === "all" || status === submissionStatusFilter;
      if (!matchesStatus) {
        return false;
      }
      if (!query) {
        return true;
      }
      const haystack = [item.name, item.email, item.creator_code, item.role, item.status]
        .map((value) => String(value || "").toLowerCase())
        .join(" ");
      return haystack.includes(query);
    });
  }, [deferredOperationsSearch, operations, submissionStatusFilter]);

  const pendingUserTotalPages = Math.max(1, Math.ceil(pendingUsers.length / ADMIN_PAGE_SIZE));
  const pendingUsersPaged = useMemo(() => {
    const start = (pendingUserPage - 1) * ADMIN_PAGE_SIZE;
    return pendingUsers.slice(start, start + ADMIN_PAGE_SIZE);
  }, [pendingUserPage, pendingUsers]);

  const selectedUserRow = useMemo(() => {
    const rows = operations?.users || [];
    return rows.find((item) => String(item.id) === String(selectedUserId)) || pendingUsers[0] || rows[0] || null;
  }, [operations, pendingUsers, selectedUserId]);

  const selectedUserSocialRows = useMemo(() => {
    if (!selectedUserRow) {
      return [];
    }
    return (operations?.socials || []).filter((item) => Number(item.user_id || 0) === Number(selectedUserRow.id));
  }, [operations, selectedUserRow]);

  const selectedUserRedemptions = useMemo(() => {
    if (!selectedUserRow) {
      return [];
    }
    return (operations?.redemptions || []).filter((item) => Number(item.user_id || 0) === Number(selectedUserRow.id));
  }, [operations, selectedUserRow]);

  const selectedUserPointRows = useMemo(() => {
    if (!selectedUserRow) {
      return [];
    }
    return (operations?.pointsLog || []).filter((item) => Number(item.user_id || 0) === Number(selectedUserRow.id));
  }, [operations, selectedUserRow]);

  const correctionTargetRows = useMemo(
    () => submissionRows.slice(0, 40),
    [submissionRows],
  );

  const selectedSubmissionRow = useMemo(
    () => submissionRows.find((item) => String(item.id) === String(selectedSubmissionId)) || submissionRows[0] || null,
    [selectedSubmissionId, submissionRows],
  );

  const selectedSubmissionAnalysis = useMemo(
    () => parseRecord(selectedSubmissionRow?.video_analysis),
    [selectedSubmissionRow],
  );

  const creatorRosterFiltered = useMemo(() => {
    const query = deferredCreatorSearch.trim().toLowerCase();
    if (!query) {
      return creators?.roster || [];
    }
    return (creators?.roster || []).filter((item) =>
      [item.creator_code, item.handle, item.extracted_handle, item.name, item.display_name, item.email]
        .map((value) => String(value || "").toLowerCase())
        .some((value) => value.includes(query)),
    );
  }, [creators, deferredCreatorSearch]);

  const creatorTotalPages = Math.max(1, Math.ceil(creatorRosterFiltered.length / ADMIN_PAGE_SIZE));
  const creatorRowsPaged = useMemo(() => {
    const start = (creatorPage - 1) * ADMIN_PAGE_SIZE;
    return creatorRosterFiltered.slice(start, start + ADMIN_PAGE_SIZE);
  }, [creatorPage, creatorRosterFiltered]);

  const productRowsFiltered = useMemo(() => {
    const query = deferredProductSearch.trim().toLowerCase();
    const rows = products?.dashboard?.products || [];
    if (!query) {
      return rows;
    }
    return rows.filter((item) =>
      [item.series]
        .map((value) => String(value || "").toLowerCase())
        .some((value) => value.includes(query)),
    );
  }, [deferredProductSearch, products]);

  const productTotalPages = Math.max(1, Math.ceil(productRowsFiltered.length / ADMIN_PAGE_SIZE));
  const productRowsPaged = useMemo(() => {
    const start = (productPage - 1) * ADMIN_PAGE_SIZE;
    return productRowsFiltered.slice(start, start + ADMIN_PAGE_SIZE);
  }, [productPage, productRowsFiltered]);

  const verificationRows = useMemo(() => {
    const rows = (operations?.verifyQueue || operations?.verifications) || [];
    const query = deferredOperationsSearch.trim().toLowerCase();
    return rows.filter((item) => {
      const status = String(item.status || "").toLowerCase();
      const matchesStatus = verificationStatusFilter === "all" || status === verificationStatusFilter;
      if (!matchesStatus) {
        return false;
      }
      if (!query) {
        return true;
      }
      const haystack = [item.platform, item.handle, item.status, item.generated_comment]
        .map((value) => String(value || "").toLowerCase())
        .join(" ");
      return haystack.includes(query);
    });
  }, [deferredOperationsSearch, operations, verificationStatusFilter]);

  const verificationTotalPages = Math.max(1, Math.ceil(verificationRows.length / ADMIN_PAGE_SIZE));
  const verificationRowsPaged = useMemo(() => {
    const start = (verificationPage - 1) * ADMIN_PAGE_SIZE;
    return verificationRows.slice(start, start + ADMIN_PAGE_SIZE);
  }, [verificationPage, verificationRows]);

  const selectedProductSummary = useMemo(() => {
    const rows = products?.dashboard?.products || [];
    return rows.find((item) => String(item.series || "") === selectedProductKey) || rows[0] || null;
  }, [products, selectedProductKey]);

  const selectedProductCatalogRows = useMemo(() => {
    const series = String(selectedProductSummary?.series || "").toLowerCase();
    if (!series) {
      return (products?.catalog || []).slice(0, 8);
    }
    return (products?.catalog || []).filter((item) => {
      const haystack = `${item.series || ""} ${item.label || ""}`.toLowerCase();
      return haystack.includes(series);
    });
  }, [products, selectedProductSummary]);

  const selectedProductRecentRows = useMemo(() => {
    const series = String(selectedProductSummary?.series || "").toLowerCase();
    if (!series) {
      return (products?.dashboard?.recent || []).slice(0, 8);
    }
    const rows = (products?.dashboard?.recent || []).filter((item) =>
      `${item.product || ""} ${item.product_series || ""} ${item.title || ""}`.toLowerCase().includes(series),
    );
    return rows.slice(0, 8);
  }, [products, selectedProductSummary]);

  const selectedStudentRow = useMemo(() => {
    const rows = student?.overview?.students || [];
    return rows.find((item) => String(item.user_id) === String(selectedStudentId)) || rows[0] || null;
  }, [selectedStudentId, student]);

  const studentRowsFiltered = useMemo(() => {
    const query = deferredStudentSearch.trim().toLowerCase();
    const rows = student?.overview?.students || [];
    if (!query) {
      return rows;
    }
    return rows.filter((item) =>
      [item.name, item.email, item.school_name, item.school_id, item.student_id_code, item.creator_code, item.status]
        .map((value) => String(value || "").toLowerCase())
        .some((value) => value.includes(query)),
    );
  }, [deferredStudentSearch, student]);

  const studentTotalPages = Math.max(1, Math.ceil(studentRowsFiltered.length / ADMIN_PAGE_SIZE));
  const studentRowsPaged = useMemo(() => {
    const start = (studentPage - 1) * ADMIN_PAGE_SIZE;
    return studentRowsFiltered.slice(start, start + ADMIN_PAGE_SIZE);
  }, [studentPage, studentRowsFiltered]);

  const selectedStudentOps = useMemo(() => {
    if (!selectedStudentRow) {
      return [];
    }
    const schoolId = String(selectedStudentRow.school_id || "");
    const creatorCode = String(selectedStudentRow.creator_code || "");
    const studentIdCode = String(selectedStudentRow.student_id_code || "");
    return [
      ...((student?.overview?.recent_events || []).filter((item) => {
        const haystack = `${item.school_id || ""} ${item.creator_code || ""} ${item.student_id_code || ""} ${item.qr_id || ""}`;
        return haystack.includes(schoolId) || haystack.includes(creatorCode) || haystack.includes(studentIdCode);
      })),
      ...((student?.overview?.recent_audit || []).filter((item) => {
        const haystack = `${item.school_id || ""} ${item.creator_code || ""} ${item.student_id_code || ""} ${item.qr_id || ""}`;
        return haystack.includes(schoolId) || haystack.includes(creatorCode) || haystack.includes(studentIdCode);
      })),
    ].slice(0, 8);
  }, [selectedStudentRow, student]);

  const viaProposalRows = useMemo(() => {
    const query = deferredViaSearch.trim().toLowerCase();
    const rows = via?.proposals || [];
    if (!query) {
      return rows;
    }
    return rows.filter((proposal) =>
      [proposal.policy_key, proposal.proposal_key, proposal.status, proposal.target, proposal.audit_actor]
        .map((value) => String(value || "").toLowerCase())
        .some((value) => value.includes(query)),
    );
  }, [deferredViaSearch, via]);

  const viaTotalPages = Math.max(1, Math.ceil(viaProposalRows.length / ADMIN_PAGE_SIZE));
  const viaRowsPaged = useMemo(() => {
    const start = (viaPage - 1) * ADMIN_PAGE_SIZE;
    return viaProposalRows.slice(start, start + ADMIN_PAGE_SIZE);
  }, [viaPage, viaProposalRows]);

  const selectedViaLivePolicy = useMemo(() => {
    const live = via?.livePolicies || [];
    const proposals = via?.proposals || [];
    return (
      live.find((item) => String(item.version_key || item.policy_key || "") === selectedPolicyKey || String(item.policy_key || "") === selectedPolicyKey) ||
      live[0] ||
      null
    );
  }, [selectedPolicyKey, via]);

  const selectedViaHistoryRows = useMemo(() => {
    const key = String(selectedViaLivePolicy?.policy_key || selectedPolicyKey || "").trim();
    const rows = via?.policyHistory || [];
    if (!key) {
      return rows.slice(0, 8);
    }
    const filtered = rows.filter((item) =>
      [item.policy_key, item.version_key, item.version_label]
        .map((value) => String(value || ""))
        .some((value) => value.includes(key)),
    );
    return filtered.slice(0, 8);
  }, [selectedPolicyKey, selectedViaLivePolicy, via]);

  const selectedViaAlerts = useMemo(() => {
    const key = String(selectedViaLivePolicy?.policy_key || selectedPolicyKey || "").trim();
    const rows = via?.rolloutAlerts || [];
    if (!key) {
      return rows.slice(0, 4);
    }
    return rows.filter((item) => String(item.policy_key || "").includes(key)).slice(0, 4);
  }, [selectedPolicyKey, selectedViaLivePolicy, via]);

  const selectedViaMemory = useMemo(() => {
    const key = String(selectedViaLivePolicy?.policy_key || selectedPolicyKey || "").trim();
    const rows = via?.memoryRetention || [];
    if (!key) {
      return rows.slice(0, 6);
    }
    return rows.filter((item) => String(item.policy_key || item.bucket_key || "").includes(key)).slice(0, 6);
  }, [selectedPolicyKey, selectedViaLivePolicy, via]);

  useEffect(() => {
    if (!selectedProductKey && products?.dashboard?.products?.[0]?.series) {
      setSelectedProductKey(String(products.dashboard.products[0].series || ""));
    }
  }, [products, selectedProductKey]);

  useEffect(() => {
    if (!selectedStudentId && student?.overview?.students?.[0]?.user_id) {
      setSelectedStudentId(String(student.overview.students[0].user_id));
    }
  }, [selectedStudentId, student]);

  useEffect(() => {
    if (!selectedPolicyKey && (via?.livePolicies?.[0]?.policy_key || via?.livePolicies?.[0]?.version_key || via?.proposals?.[0]?.policy_key)) {
      setSelectedPolicyKey(
        String(
          via?.livePolicies?.[0]?.policy_key ||
            via?.livePolicies?.[0]?.version_key ||
            via?.proposals?.[0]?.policy_key ||
            via?.proposals?.[0]?.proposal_key ||
            "",
        ),
      );
    }
  }, [selectedPolicyKey, via]);

  useEffect(() => {
    if (!selectedSubmissionId && submissionRows[0]?.id) {
      setSelectedSubmissionId(String(submissionRows[0].id));
    }
  }, [selectedSubmissionId, submissionRows]);

  useEffect(() => {
    if (!selectedUserId && pendingUsers[0]?.id) {
      setSelectedUserId(String(pendingUsers[0].id));
    }
  }, [pendingUsers, selectedUserId]);

  useEffect(() => {
    setReviewPage(1);
    setPendingUserPage(1);
    setVerificationPage(1);
  }, [deferredOperationsSearch, submissionStatusFilter, verificationStatusFilter]);

  useEffect(() => {
    setCreatorPage(1);
  }, [deferredCreatorSearch]);

  useEffect(() => {
    setProductPage(1);
  }, [deferredProductSearch]);

  useEffect(() => {
    setStudentPage(1);
  }, [deferredStudentSearch]);

  useEffect(() => {
    setViaPage(1);
  }, [deferredViaSearch]);

  async function refreshActiveTab() {
    if (activeWorkspace === "placeholder") {
      setMessage({
        tone: "warning",
        body: localeText(Boolean(isZh), `${localizedActivePageDef.label} route is live, but its dedicated data pipeline is not wired yet.`, `${localizedActivePageDef.label} 路由已经上线，但专属数据管线还没有完全接通。`),
      });
      return;
    }
    setBusy(`refresh:${activeWorkspace}`);
    setMessage(null);
    try {
      if (activeWorkspace === "command") {
        await loadCommand();
      } else if (activeWorkspace === "operations") {
        await loadOperations();
      } else if (activeWorkspace === "creators") {
        await loadCreators();
      } else if (activeWorkspace === "products") {
        await loadProducts();
      } else if (activeWorkspace === "student") {
        await loadStudent();
      } else if (activeWorkspace === "analytics") {
        await loadAnalytics();
      } else if (activeWorkspace === "commerce") {
        await loadCommerce();
      } else if (activeWorkspace === "market") {
        await loadMarket();
      } else if (activeWorkspace === "brand") {
        await loadBrand();
      } else if (activeWorkspace === "system") {
        await loadSystem();
      } else if (activeWorkspace === "via") {
        await loadVia();
      } else if (activeWorkspace === "runtime") {
        await loadRuntime();
      }
      setMessage({ tone: "success", body: localeText(Boolean(isZh), `${localizedActivePageDef.label} refreshed.`, `${localizedActivePageDef.label} 已刷新。`) });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : localeText(Boolean(isZh), "Could not refresh workspace", "无法刷新当前工作区") });
    } finally {
      setBusy("");
    }
  }

  async function handleBackfillOrders() {
    setBusy("commerce:backfill");
    setMessage(null);
    try {
      const result = await backfillAdminOrders(token);
      await loadCommerce();
      setMessage({
        tone: "success",
        body: localeText(
          Boolean(isZh),
          `Order backfill finished. Scanned ${compactNumber(result.scanned || 0)} events.`,
          `订单回流完成，已扫描 ${compactNumber(result.scanned || 0)} 条事件。`,
        ),
      });
    } catch (error) {
      setMessage({
        tone: "danger",
        body: error instanceof Error ? error.message : localeText(Boolean(isZh), "Could not backfill orders", "无法回流订单"),
      });
    } finally {
      setBusy("");
    }
  }

  async function submitSchool(event: FormEvent) {
    event.preventDefault();
    setBusy("student:school");
    setMessage(null);
    try {
      await createStudentSchool(token, schoolForm);
      await loadStudent();
      setBatchForm((current) => ({ ...current, school_id: schoolForm.school_id }));
      setMessage({ tone: "success", body: `School ${schoolForm.school_name} is now live in the student ops workspace.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not save school" });
    } finally {
      setBusy("");
    }
  }

  async function submitBatch(event: FormEvent) {
    event.preventDefault();
    setBusy("student:batch");
    setMessage(null);
    try {
      await createStudentBatch(token, {
        school_id: batchForm.school_id,
        batch_name: batchForm.batch_name,
        count: Number(batchForm.count || 0),
        roster_csv: batchForm.roster_csv,
      });
      await loadStudent();
      setMessage({ tone: "success", body: `Batch ${batchForm.batch_name} generated on the 2.0 runtime.` });
      setBatchForm((current) => ({ ...current, batch_name: "", roster_csv: "" }));
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not generate student batch" });
    } finally {
      setBusy("");
    }
  }

  async function userAction(userId: number, action: "approve" | "reject") {
    setBusy(`operations:user:${userId}:${action}`);
    setMessage(null);
    try {
      await runAdminUserAction(token, userId, action, `2.0 admin ${action}`);
      await loadOperations();
      await loadCommand();
      setMessage({ tone: "success", body: `User #${userId} ${action}d.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : `Could not ${action} user` });
    } finally {
      setBusy("");
    }
  }

  async function socialAction(accountId: number, action: "verify" | "reject") {
    setBusy(`operations:social:${accountId}:${action}`);
    setMessage(null);
    try {
      await runAdminSocialAction(token, accountId, action);
      await loadOperations();
      setMessage({
        tone: "success",
        body: action === "verify" ? `Social account #${accountId} verified.` : `Social account #${accountId} removed.`,
      });
    } catch (error) {
      setMessage({
        tone: "danger",
        body: error instanceof Error ? error.message : `Could not ${action} social account`,
      });
    } finally {
      setBusy("");
    }
  }

  async function verificationAction(verificationId: number, action: "approve" | "reject") {
    setBusy(`operations:verification:${verificationId}:${action}`);
    setMessage(null);
    try {
      await runAdminVerificationAction(token, verificationId, action, action === "reject" ? "Rejected from 2.0 admin" : "");
      await loadOperations();
      setMessage({
        tone: "success",
        body: action === "approve" ? `Verification #${verificationId} approved.` : `Verification #${verificationId} rejected.`,
      });
    } catch (error) {
      setMessage({
        tone: "danger",
        body: error instanceof Error ? error.message : `Could not ${action} verification`,
      });
    } finally {
      setBusy("");
    }
  }

  async function redemptionAction(redemptionId: number, status: string) {
    setBusy(`operations:redemption:${redemptionId}:${status}`);
    setMessage(null);
    try {
      await updateAdminRedemption(token, redemptionId, {
        status,
        admin_note: `Updated from 2.0 admin to ${status}`,
      });
      await loadOperations();
      setMessage({ tone: "success", body: `Redemption #${redemptionId} updated to ${status}.` });
    } catch (error) {
      setMessage({
        tone: "danger",
        body: error instanceof Error ? error.message : "Could not update redemption",
      });
    } finally {
      setBusy("");
    }
  }

  async function submitReward(event: FormEvent) {
    event.preventDefault();
    setBusy("command:reward");
    setMessage(null);
    const payload = {
      title: rewardForm.title.trim(),
      description: rewardForm.description.trim(),
      category: rewardForm.category.trim(),
      points_cost: Number(rewardForm.points_cost || 0),
      meta_label: rewardForm.meta_label.trim(),
      image_url: rewardForm.image_url.trim(),
      stock: Number(rewardForm.stock || 0),
      sort_order: Number(rewardForm.sort_order || 0),
      status: rewardForm.status.trim() || "draft",
    };
    try {
      if (editingRewardId) {
        await updateAdminReward(token, editingRewardId, payload);
      } else {
        await createAdminReward(token, payload);
      }
      await loadCommand();
      setMessage({ tone: "success", body: editingRewardId ? `Reward #${editingRewardId} updated.` : "Reward created." });
      if (!editingRewardId) {
        setRewardForm({
          title: "",
          description: "",
          category: "coupon",
          points_cost: "500",
          meta_label: "",
          image_url: "",
          stock: "0",
          sort_order: "100",
          status: "draft",
        });
      }
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not save reward" });
    } finally {
      setBusy("");
    }
  }

  async function rewardAction(rewardId: number, action: "publish" | "archive" | "delete") {
    setBusy(`command:reward:${rewardId}:${action}`);
    setMessage(null);
    try {
      await runAdminRewardAction(token, rewardId, action);
      await loadCommand();
      setMessage({ tone: "success", body: `Reward #${rewardId} ${action} complete.` });
      if (editingRewardId === rewardId && action === "delete") {
        setEditingRewardId(null);
      }
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : `Could not ${action} reward` });
    } finally {
      setBusy("");
    }
  }

  async function submitPoints(event: FormEvent) {
    event.preventDefault();
    const userId = Number(pointsForm.user_id || 0);
    const amount = Number(pointsForm.amount || 0);
    if (!userId || !amount) {
      setMessage({ tone: "warning", body: "Pick a user and a non-zero amount first." });
      return;
    }
    setBusy("operations:points");
    setMessage(null);
    try {
      if (pointsForm.mode === "grant") {
        await grantAdminPoints(token, userId, { points: amount, reason: pointsForm.reason.trim() || "Admin grant" });
      } else {
        await adjustAdminPoints(token, userId, {
          delta: pointsForm.mode === "deduct" ? -Math.abs(amount) : Math.abs(amount),
          reason: pointsForm.reason.trim() || "Admin adjustment",
        });
      }
      await loadOperations();
      setMessage({ tone: "success", body: `Points operation completed for user #${userId}.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not update points" });
    } finally {
      setBusy("");
    }
  }

  async function submitCorrection(event: FormEvent, overrideSubmissionId?: number) {
    event.preventDefault();
    const submissionId = Number(overrideSubmissionId || correctionForm.submission_id || 0);
    if (!submissionId || !correctionForm.correct_series.trim() || !correctionForm.correct_label.trim()) {
      setMessage({ tone: "warning", body: "Choose a submission, series, and label before saving correction." });
      return;
    }
    setBusy("products:correction");
    setMessage(null);
    try {
      await correctAdminSubmissionProduct(token, submissionId, {
        correct_series: correctionForm.correct_series.trim(),
        correct_label: correctionForm.correct_label.trim(),
        note: correctionForm.note.trim(),
      });
      await loadCommand();
      await loadProducts();
      setMessage({ tone: "success", body: `Submission #${submissionId} corrected and learning updated.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not save correction" });
    } finally {
      setBusy("");
    }
  }

  async function submitManualSubmission(event: FormEvent) {
    event.preventDefault();
    setBusy("operations:manual-submission");
    setMessage(null);
    try {
      const created = await createManualAdminSubmission(token, {
        platform: manualSubmissionForm.platform,
        extracted_handle: manualSubmissionForm.extracted_handle.trim(),
        url: manualSubmissionForm.url.trim(),
        title: manualSubmissionForm.title.trim(),
        detection_status: manualSubmissionForm.detection_status,
        product_series: manualSubmissionForm.product_series.trim(),
        product_label: manualSubmissionForm.product_label.trim(),
        final_score: Number(manualSubmissionForm.final_score || 0),
        creator_score: Number(manualSubmissionForm.creator_score || 0),
        overall_score: Number(manualSubmissionForm.overall_score || 0),
        views: Number(manualSubmissionForm.views || 0),
        likes: Number(manualSubmissionForm.likes || 0),
        comments: Number(manualSubmissionForm.comments || 0),
        shares: Number(manualSubmissionForm.shares || 0),
        recommendation: manualSubmissionForm.recommendation.trim() || "Manually added by admin",
        memo: manualSubmissionForm.memo.trim(),
      });
      await loadOperations();
      await loadCommand();
      setManualSubmissionOpen(false);
      setManualSubmissionForm({
        platform: "Instagram",
        extracted_handle: "",
        url: "",
        title: "",
        detection_status: "confirmed",
        product_series: "",
        product_label: "",
        final_score: "260",
        creator_score: "80",
        overall_score: "206",
        views: "0",
        likes: "0",
        comments: "0",
        shares: "0",
        recommendation: "Manually added by admin",
        memo: "",
      });
      if (created?.id) {
        setSelectedSubmissionId(String(created.id));
        setCorrectionForm((current) => ({ ...current, submission_id: String(created.id) }));
      }
      setMessage({ tone: "success", body: `Manual submission${created?.id ? ` #${created.id}` : ""} created.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not create manual submission" });
    } finally {
      setBusy("");
    }
  }

  async function approveSubmission(submissionId: number) {
    const source = submissionRows.find((item) => item.id === submissionId);
    setBusy(`operations:submission:${submissionId}:approve`);
    setMessage(null);
    try {
      await approveAdminSubmission(token, submissionId, {
        campaign_score: source?.final_score !== undefined ? Number(source.final_score) : undefined,
        creator_score: source?.creator_score !== undefined ? Number(source.creator_score) : undefined,
        overall_score: source?.overall_score !== undefined ? Number(source.overall_score) : undefined,
        product_series: source?.product_series ? String(source.product_series) : undefined,
        product_label: source?.product_label ? String(source.product_label) : undefined,
        memo_append: "Approved from Admin 2.0 parity view",
      });
      await loadOperations();
      await loadCommand();
      setMessage({ tone: "success", body: `Submission #${submissionId} approved and points synced.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not approve submission" });
    } finally {
      setBusy("");
    }
  }

  async function rejectSubmission(submissionId: number) {
    setBusy(`operations:submission:${submissionId}:reject`);
    setMessage(null);
    try {
      await rejectAdminSubmission(token, submissionId, "Rejected from Admin 2.0 parity view");
      await loadOperations();
      await loadCommand();
      setMessage({ tone: "success", body: `Submission #${submissionId} rejected.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not reject submission" });
    } finally {
      setBusy("");
    }
  }

  async function reanalyzeSubmission(submissionId: number) {
    setBusy(`operations:submission:${submissionId}:reanalyze`);
    setMessage(null);
    try {
      await reanalyzeAdminSubmission(token, submissionId);
      await loadOperations();
      await loadCommand();
      setMessage({ tone: "success", body: `Submission #${submissionId} queued for reanalysis.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not queue reanalysis" });
    } finally {
      setBusy("");
    }
  }

  async function removeSubmission(submissionId: number) {
    if (typeof window !== "undefined" && !window.confirm(`Delete submission #${submissionId}? This cannot be undone.`)) {
      return;
    }
    setBusy(`operations:submission:${submissionId}:delete`);
    setMessage(null);
    try {
      await deleteAdminSubmission(token, submissionId);
      await loadOperations();
      await loadCommand();
      if (selectedSubmissionId === String(submissionId)) {
        setSelectedSubmissionId("");
      }
      setMessage({ tone: "success", body: `Submission #${submissionId} deleted.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not delete submission" });
    } finally {
      setBusy("");
    }
  }

  async function evaluateViaNow() {
    setBusy("via:evaluate");
    setMessage(null);
    try {
      await runViaEvaluation(token);
      await loadVia();
      setMessage({ tone: "success", body: "Via offline evaluator ran against the new runtime and refreshed the policy workspace." });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not run Via evaluator" });
    } finally {
      setBusy("");
    }
  }

  async function proposalAction(proposalKey: string, action: "approve" | "reject" | "apply" | "stage") {
    setBusy(`via:proposal:${proposalKey}:${action}`);
    setMessage(null);
    try {
      await runViaProposalAction(token, proposalKey, action, rolloutNote);
      await loadVia();
      setMessage({ tone: "success", body: `Proposal ${proposalKey} ${action.replace("_", " ")} complete.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not update proposal" });
    } finally {
      setBusy("");
    }
  }

  async function policyAction(versionKey: string, action: "promote" | "rollback" | "advance-rollout") {
    setBusy(`via:policy:${versionKey}:${action}`);
    setMessage(null);
    try {
      await runViaPolicyAction(token, versionKey, action, rolloutNote);
      await loadVia();
      setMessage({ tone: "success", body: `Policy ${versionKey} ${action.replace("-", " ")} complete.` });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : "Could not update policy version" });
    } finally {
      setBusy("");
    }
  }

  const commandWorkspace = (
    <Suspense fallback={<TabLoader label={t("admin.tabs.command.label")} />}>
      <CommandTab
        command={command}
        busy={busy}
        editingRewardId={editingRewardId}
        setEditingRewardId={setEditingRewardId}
        rewardForm={rewardForm}
        setRewardForm={setRewardForm}
        rewardAction={rewardAction}
        submitReward={submitReward}
      />
    </Suspense>
  );

  const operationsWorkspace = (
    <Suspense fallback={<TabLoader label={t("admin.tabs.operations.label")} />}>
      <OperationsTab
        command={command}
        operations={operations}
        products={products}
        operationsSearch={operationsSearch}
        setOperationsSearch={setOperationsSearch}
        submissionStatusFilter={submissionStatusFilter}
        setSubmissionStatusFilter={setSubmissionStatusFilter}
        verificationStatusFilter={verificationStatusFilter}
        setVerificationStatusFilter={setVerificationStatusFilter}
        filteredSubmissionRows={filteredSubmissionRows}
        reviewRowsPaged={reviewRowsPaged}
        reviewPage={reviewPage}
        reviewTotalPages={reviewTotalPages}
        setReviewPage={setReviewPage}
        manualSubmissionOpen={manualSubmissionOpen}
        setManualSubmissionOpen={setManualSubmissionOpen}
        manualSubmissionForm={manualSubmissionForm}
        setManualSubmissionForm={setManualSubmissionForm}
        submitManualSubmission={submitManualSubmission}
        selectedSubmissionRow={selectedSubmissionRow}
        selectedSubmissionAnalysis={selectedSubmissionAnalysis}
        correctionForm={correctionForm}
        setCorrectionForm={setCorrectionForm}
        loadProducts={loadProducts}
        busy={busy}
        approveSubmission={approveSubmission}
        rejectSubmission={rejectSubmission}
        reanalyzeSubmission={reanalyzeSubmission}
        removeSubmission={removeSubmission}
        setSelectedSubmissionId={setSelectedSubmissionId}
        submitCorrection={submitCorrection}
        pendingUsers={pendingUsers}
        pendingUsersPaged={pendingUsersPaged}
        pendingUserPage={pendingUserPage}
        pendingUserTotalPages={pendingUserTotalPages}
        setPendingUserPage={setPendingUserPage}
        selectedUserRow={selectedUserRow}
        selectedUserSocialRows={selectedUserSocialRows}
        selectedUserRedemptions={selectedUserRedemptions}
        selectedUserPointRows={selectedUserPointRows}
        setSelectedUserId={setSelectedUserId}
        userAction={userAction}
        verificationRows={verificationRows}
        verificationRowsPaged={verificationRowsPaged}
        verificationPage={verificationPage}
        verificationTotalPages={verificationTotalPages}
        setVerificationPage={setVerificationPage}
        verificationAction={verificationAction}
        socialAction={socialAction}
        redemptionAction={redemptionAction}
        pointsForm={pointsForm}
        setPointsForm={setPointsForm}
        submitPoints={submitPoints}
      />
    </Suspense>
  );

  const creatorsWorkspace = (
    <Suspense fallback={<TabLoader label={t("admin.tabs.creators.label")} />}>
      <CreatorsTab
        creators={creators}
        creatorSearch={creatorSearch}
        setCreatorSearch={setCreatorSearch}
        selectedCreatorHandle={selectedCreatorHandle}
        setSelectedCreatorHandle={setSelectedCreatorHandle}
        creatorPage={creatorPage}
        creatorTotalPages={creatorTotalPages}
        creatorRosterFiltered={creatorRosterFiltered}
        creatorRowsPaged={creatorRowsPaged}
        setCreatorPage={setCreatorPage}
        busy={busy}
        loadCreators={loadCreators}
        setBusy={setBusy}
        setMessage={setMessage}
      />
    </Suspense>
  );

  const productsWorkspace = (
    <Suspense fallback={<TabLoader label={t("admin.tabs.products.label")} />}>
      <ProductsTab
        products={products}
        productSearch={productSearch}
        setProductSearch={setProductSearch}
        productPage={productPage}
        productTotalPages={productTotalPages}
        productRowsFiltered={productRowsFiltered}
        productRowsPaged={productRowsPaged}
        setProductPage={setProductPage}
        selectedProductKey={selectedProductKey}
        setSelectedProductKey={setSelectedProductKey}
        selectedProductSummary={selectedProductSummary}
        selectedProductCatalogRows={selectedProductCatalogRows}
        selectedProductRecentRows={selectedProductRecentRows}
        correctionTargetRows={correctionTargetRows}
        correctionForm={correctionForm}
        setCorrectionForm={setCorrectionForm}
        submitCorrection={submitCorrection}
        busy={busy}
      />
    </Suspense>
  );

  const studentWorkspace = (
    <Suspense fallback={<TabLoader label={t("admin.tabs.student.label")} />}>
      <StudentTab
        student={student}
        schoolForm={schoolForm}
        setSchoolForm={setSchoolForm}
        batchForm={batchForm}
        setBatchForm={setBatchForm}
        submitSchool={submitSchool}
        submitBatch={submitBatch}
        busy={busy}
        studentSearch={studentSearch}
        setStudentSearch={setStudentSearch}
        studentPage={studentPage}
        studentTotalPages={studentTotalPages}
        studentRowsFiltered={studentRowsFiltered}
        studentRowsPaged={studentRowsPaged}
        setStudentPage={setStudentPage}
        selectedStudentRow={selectedStudentRow}
        setSelectedStudentId={setSelectedStudentId}
        selectedStudentOps={selectedStudentOps}
      />
    </Suspense>
  );

  const analyticsWorkspace = (
    <Suspense fallback={<TabLoader label={t("admin.tabs.analytics.label")} />}>
      <AnalyticsTab analytics={analytics} />
    </Suspense>
  );

  const viaWorkspace = (
    <Suspense fallback={<TabLoader label={t("admin.tabs.via.label")} />}>
      <ViaTab
        via={via}
        rolloutNote={rolloutNote}
        setRolloutNote={setRolloutNote}
        busy={busy}
        evaluateViaNow={evaluateViaNow}
        viaSearch={viaSearch}
        setViaSearch={setViaSearch}
        viaPage={viaPage}
        viaTotalPages={viaTotalPages}
        viaProposalRows={viaProposalRows}
        viaRowsPaged={viaRowsPaged}
        setViaPage={setViaPage}
        proposalAction={proposalAction}
        selectedPolicyKey={selectedPolicyKey}
        setSelectedPolicyKey={setSelectedPolicyKey}
        selectedViaLivePolicy={selectedViaLivePolicy}
        selectedViaHistoryRows={selectedViaHistoryRows}
        selectedViaAlerts={selectedViaAlerts}
        selectedViaMemory={selectedViaMemory}
        policyAction={policyAction}
      />
    </Suspense>
  );

  const runtimeWorkspace = (
    <Suspense fallback={<TabLoader label={t("admin.tabs.runtime.label")} />}>
      <RuntimeTab runtime={runtime} />
    </Suspense>
  );

  const activePageContent = (() => {
    switch (activePage) {
      case "home":
        return <AdminHomePage command={command} metrics={topMetrics} />;
      case "content":
        return (
          <>
            <Panel title="Batch 1 content review" kicker="Current React module reuse">
              <p className="admin-v5-copy">
                This page now owns the review queue route. The existing Operations workspace is mounted below while the
                v5 split finishes separating content, users, and verification flows.
              </p>
            </Panel>
            {operationsWorkspace}
          </>
        );
      case "creators":
        return creatorsWorkspace;
      case "students":
        return studentWorkspace;
      case "rewards":
        return (
          <>
            <Panel title="Rewards workspace" kicker="Batch 1">
              <p className="admin-v5-copy">
                Reward catalog management is live below. Redemptions and drafts still share the same command snapshot
                until the dedicated commerce views land.
              </p>
            </Panel>
            {commandWorkspace}
          </>
        );
      case "orders":
      case "attribution":
      case "payouts":
        return <AdminCommercePage page={localizedActivePageDef} commerce={commerce} activeSubtabIndex={activeSubtabIndex} onBackfillOrders={() => void handleBackfillOrders()} />;
      case "market":
        return <AdminMarketPage market={market} activeSubtabIndex={activeSubtabIndex} />;
      case "brand":
        return <AdminBrandPage brand={brand} activeSubtabIndex={activeSubtabIndex} />;
      case "integrations":
        return <AdminIntegrationsPage system={system} activeSubtabIndex={activeSubtabIndex} />;
      case "trust":
        return <AdminTrustPage system={system} activeSubtabIndex={activeSubtabIndex} />;
      case "staff":
        return <AdminStaffPage system={system} activeSubtabIndex={activeSubtabIndex} />;
      case "analytics":
        return analyticsWorkspace;
      case "policies":
      case "proposals":
      case "evaluations":
      case "conversations":
      case "personas":
        return (
          <>
            <Panel title="VIA Brain workspace" kicker={activePageDef.batch}>
              <p className="admin-v5-copy">
                {activePageDef.label} now resolves to its own admin route. The shared VIA control module is mounted
                below while the page-specific split into five dedicated React surfaces is completed.
              </p>
            </Panel>
            {viaWorkspace}
          </>
        );
      case "runtime":
        return runtimeWorkspace;
      default:
        return commandWorkspace;
    }
  })();

  return (
    <div className="page-stack">
      <div className="admin-v5-shell">
        <aside className="admin-v5-sidebar">
          {ADMIN_PAGE_GROUPS.map((group) => (
            <section key={group.key} className="admin-v5-sidebar__group">
              <div className="admin-v5-sidebar__label">{localizedBatchLabel(group.label, Boolean(isZh))}</div>
              <div className="admin-v5-sidebar__links">
                {group.pages.map((pageId) => {
                  const page = localizedPage(ADMIN_PAGE_LOOKUP[pageId], Boolean(isZh));
                  return (
                    <button
                      key={page.id}
                      type="button"
                      className={`admin-v5-sidebar__link${activePage === page.id ? " is-active" : ""}`}
                      onClick={() => navigate(adminPageHref(page.id))}
                    >
                      <span>{page.label}</span>
                      <small>{statusLabel(page.status, Boolean(isZh))}</small>
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </aside>

        <section className="admin-v5-page">
          <AdminPageHero
            page={localizedActivePageDef}
            activeSubtabIndex={activeSubtabIndex}
            onSubtabChange={(nextIndex) =>
              setPageSubtabs((current) => ({ ...current, [activePage]: nextIndex }))
            }
          />

          <div className="admin-v5-page__actions">
            <button className="ghost-button admin-refresh-button" type="button" disabled={Boolean(busy)} onClick={() => void refreshActiveTab()}>
              {busy === `refresh:${activeWorkspace}`
                ? localeText(Boolean(isZh), "Refreshing...", "刷新中...")
                : localeText(Boolean(isZh), `Refresh ${localizedActivePageDef.label}`, `刷新 ${localizedActivePageDef.label}`)}
            </button>
          </div>

          {message ? <div className={`inline-message inline-message--${message.tone}`}>{message.body}</div> : null}

          {activePageContent}
        </section>
      </div>
    </div>
  );
}
