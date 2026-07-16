import { useCallback, useEffect, useRef, useState } from "react";

import { VkpiDashboard, type VkpiDashboardData, type VkpiPageKey } from "../../vkpi";
import { getInitialVkpiPage } from "../../vkpi/layout/vkpiDashboardRouting";
import {
  createSalesAttribution,
  importAmazonAttributionRows,
  uploadAmazonAttributionReport,
} from "../../../domains/attribution";
import type { VkpiCostUpdatePayload, VkpiManualAuthorizationEvidence } from "../../../services/vkpi/cost-api";
import {
  addProjectCost,
  approveMarketingCost,
  updateMarketingCost,
  upsertProductCost,
  voidMarketingCost,
} from "../../../domains/attribution";
import {
  copyTextToClipboard,
  fetchVkpiDashboardData,
  runKpiRollup,
} from "../../../domains/dashboard";
import {
  VKPI_REPORT_SECTION_KEYS,
  createVkpiReportExport,
  downloadVkpiFile,
  generateVkpiReport,
  reportApiErrorMessage,
  type VkpiReportGenerateConfig,
} from "../../../services/vkpi/reports-api";
import { uploadMarketingEvidenceFile } from "../../../domains/evidence";
import {
  claimKol,
  lookupKol,
  scanKolAccount,
  updateMarketingKol,
} from "../../../domains/kol";
import {
  archiveMarketingLink,
  createMarketingLink,
  healthCheckMarketingLink,
  pauseMarketingLink,
} from "../../../domains/attribution";
import {
  addProjectContent,
  addProjectMessage,
  addProjectShipment,
  createProject,
  deleteProject,
  transitionProjectStage,
  updateProject,
  upsertProjectTerms,
} from "../../../domains/projects";
import { inviteMarketingStaff, updateStaffMarketingPermission } from "../../../domains/settings";
import type { VkpiProjectStage } from "../../vkpi";
import { uploadMyAvatar } from "../../../services/auth.service";
import { useAuth } from "../../../hooks/useAuth";

type VkpiRangeKey = "today" | "7d" | "30d" | "mtd" | "qtd";
const VKPI_DASHBOARD_CACHE_VERSION = "v4"; // 2026-07-03:空成员名单投毒事故,bump 一次全员自愈
const VKPI_DASHBOARD_CACHE_TTL_MS = 5 * 60 * 1000;

interface Props {
  token: string;
  onSignOut?: () => Promise<void> | void;
  user?: {
    name?: string;
    email?: string;
    role?: string;
    auth_role?: string;
    staff_role?: string;
    avatar_url?: string;
    avatar_required?: boolean;
    staff_id?: number;
    is_owner?: boolean;
    permissions?: Record<string, string>;
  } | null;
}

type CachedVkpiDashboard = {
  savedAt: number;
  data: VkpiDashboardData;
};

function vkpiCacheKey(range: VkpiRangeKey, scope: string, staffId?: number): string {
  return `vkpi:dashboard:${VKPI_DASHBOARD_CACHE_VERSION}:${scope}:${range}:${staffId || "all"}`;
}

function readVkpiDashboardCache(key: string): CachedVkpiDashboard | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const cached = JSON.parse(raw) as CachedVkpiDashboard;
    if (!cached?.data || !cached.savedAt) return null;
    if (Date.now() - cached.savedAt > VKPI_DASHBOARD_CACHE_TTL_MS) return null;
    return cached;
  } catch {
    return null;
  }
}

function writeVkpiDashboardCache(key: string, data: VkpiDashboardData): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify({ savedAt: Date.now(), data }));
  } catch {
    // Cache is an optimization only; storage quota should never block the page.
  }
}

function permissionLevel(user: Props["user"], key: string): string {
  return String(user?.permissions?.[key] || "none").toLowerCase();
}

function canUseManagerView(user: Props["user"]): boolean {
  const role = String(user?.staff_role || user?.role || "").toLowerCase();
  return Boolean(
    user?.is_owner ||
      ["admin", "manager", "lead", "marketing_lead", "marketing_manager"].includes(role) || // 2026-07-03:补 admin,此前主管全靠 is_owner 兜底,降级即跌员工视角
      
      permissionLevel(user, "vkpi") === "admin" ||
      permissionLevel(user, "system.members") === "admin",
  );
}

type WeeklyReportStatus = {
  state: "idle" | "loading" | "success" | "error";
  message: string;
  href?: string;
};

export function VkpiTab({ token, user, onSignOut }: Props) {
  const { refreshUser } = useAuth();
  const mountedRef = useRef(false);
  const activeLoadKeyRef = useRef("");
  const inFlightLoadRef = useRef<{ key: string; promise: Promise<void> } | null>(null);
  const [data, setData] = useState<VkpiDashboardData | undefined>();
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [actionLink, setActionLink] = useState<{ href: string; label: string } | null>(null);
  const [weeklyReportStatus, setWeeklyReportStatus] = useState<WeeklyReportStatus | null>(null);
  const [avatarUrl, setAvatarUrl] = useState(user?.avatar_url || "");
  const [viewMode, setViewMode] = useState<"manager" | "employee">("manager");
  const [pendingPageAfterViewSwitch, setPendingPageAfterViewSwitch] = useState<VkpiPageKey | null>(null);
  const [range, setRange] = useState<VkpiRangeKey>("7d");
  const [lastSyncedAt, setLastSyncedAt] = useState("");
  const isManager = canUseManagerView(user);
  const effectiveViewMode = isManager ? viewMode : "employee";
  const scope = effectiveViewMode === "manager" ? "all" : "self";
  const userRoleLabel = isManager
    ? effectiveViewMode === "manager" ? "管理层" : "成员视角"
    : "成员";
  const canRenderWithoutDashboardData = getInitialVkpiPage(effectiveViewMode) === "cockpit";

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      activeLoadKeyRef.current = "";
    };
  }, []);

  const load = useCallback((): Promise<void> => {
    const cacheKey = vkpiCacheKey(range, scope, user?.staff_id);
    // Keep the bearer token out of persistent storage, but include it in the
    // in-memory identity so a fast account switch can never join the previous
    // session's request. The cache key supplies scope/range/staff isolation.
    const loadKey = `${token}\u0000${cacheKey}`;
    activeLoadKeyRef.current = loadKey;
    const existing = inFlightLoadRef.current;
    if (existing?.key === loadKey) return existing.promise;

    if (mountedRef.current) {
      setLoading(true);
      setMessage("");
      setActionLink(null);
    }
    const cached = readVkpiDashboardCache(cacheKey);
    if (cached?.data && mountedRef.current && activeLoadKeyRef.current === loadKey) {
      setData(cached.data);
      setLastSyncedAt(cached.data.lastSyncedAt || new Date(cached.savedAt).toISOString());
    }

    const promise = (async () => {
      try {
        const nextData = await fetchVkpiDashboardData(token, {
          range,
          scope,
          staffId: user?.staff_id ? String(user.staff_id) : undefined,
        });
        // 2026-07-03 缓存防毒:成员名单切片是静默超时型(optionalFetch 回空),
        // 空结果不得覆盖缓存里已有的真名单 —— 否则一次超时把「账号授权/分组成员」钉死为空。
        if (!nextData.staffMembers?.length && cached?.data?.staffMembers?.length) {
          nextData.staffMembers = cached.data.staffMembers;
        }
        if (!mountedRef.current || activeLoadKeyRef.current !== loadKey) return;
        setData(nextData);
        setLastSyncedAt(nextData.lastSyncedAt || new Date().toISOString());
        writeVkpiDashboardCache(cacheKey, nextData);
      } catch (error) {
        if (!mountedRef.current || activeLoadKeyRef.current !== loadKey) return;
        if (cached?.data) {
          console.warn("V-KPI dashboard refresh failed; keeping local cache", error);
          return;
        }
        setMessage(error instanceof Error ? error.message : "加载 Viltrox Marketing 失败");
      } finally {
        if (inFlightLoadRef.current?.key === loadKey) {
          inFlightLoadRef.current = null;
        }
        if (mountedRef.current && activeLoadKeyRef.current === loadKey) {
          setLoading(false);
        }
      }
    })();
    inFlightLoadRef.current = { key: loadKey, promise };
    return promise;
  }, [range, scope, token, user?.staff_id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!pendingPageAfterViewSwitch) return;
    if (typeof window !== "undefined") {
      const nextHash = `#${pendingPageAfterViewSwitch}`;
      if (window.location.hash !== nextHash) {
        window.history.replaceState(null, "", nextHash);
      }
      const event = typeof HashChangeEvent === "function" ? new HashChangeEvent("hashchange") : new Event("hashchange");
      window.dispatchEvent(event);
    }
    setPendingPageAfterViewSwitch(null);
  }, [effectiveViewMode, pendingPageAfterViewSwitch]);

  const handleToggleView = useCallback((targetPage?: VkpiPageKey) => {
    if (targetPage) {
      setPendingPageAfterViewSwitch(targetPage);
      setViewMode("employee");
      return;
    }
    setViewMode((current) => current === "manager" ? "employee" : "manager");
  }, []);

  const currentReportConfig = useCallback((): VkpiReportGenerateConfig => ({
    period: range === "30d" || range === "mtd" || range === "qtd" ? "monthly" : "weekly",
    date: new Date().toISOString().slice(0, 10),
    language: "zh",
    sections: VKPI_REPORT_SECTION_KEYS,
    format: "visual",
    scope: scope === "all" ? "all" : "self",
    staffId: user?.staff_id,
  }), [range, scope, user?.staff_id]);

  const handleExportPDF = useCallback(async () => {
    setMessage("正在生成 PDF 导出...");
    setActionLink(null);
    try {
      const result = await createVkpiReportExport(token, "pdf", currentReportConfig());
      if (!result.downloadUrl) {
        setMessage("PDF 导出任务已提交，但接口没有返回下载链接。");
        return;
      }
      await downloadVkpiFile(token, result.downloadUrl, `vkpi-report-${new Date().toISOString().slice(0, 10)}.pdf`);
      setMessage("PDF 已就绪并下载。");
    } catch (error) {
      setMessage(reportApiErrorMessage(error, "PDF 导出失败"));
      setActionLink(null);
    }
  }, [currentReportConfig, token]);

  const handleExportCSV = useCallback(async () => {
    setMessage("正在生成 CSV 导出...");
    setActionLink(null);
    try {
      const result = await createVkpiReportExport(token, "csv", currentReportConfig());
      if (!result.downloadUrl) {
        setMessage("CSV 导出任务已提交，但接口没有返回下载链接。");
        return;
      }
      await downloadVkpiFile(token, result.downloadUrl, `vkpi-report-${new Date().toISOString().slice(0, 10)}.csv`);
      setMessage("CSV 已就绪并下载。");
    } catch (error) {
      setMessage(reportApiErrorMessage(error, "CSV 导出失败"));
      setActionLink(null);
    }
  }, [currentReportConfig, token]);

  const handleGenerateWeeklyReport = useCallback(async () => {
    setMessage("正在生成周报...");
    setActionLink(null);
    setWeeklyReportStatus({ state: "loading", message: "正在生成周报..." });
    try {
      const result = await generateVkpiReport(token, { ...currentReportConfig(), period: "weekly" });
      if (result.status.toLowerCase() !== "ready") {
        throw new Error(`报告接口返回状态：${result.status || "unknown"}`);
      }
      if (result.downloadUrl) {
        await downloadVkpiFile(token, result.downloadUrl, `vkpi-weekly-${new Date().toISOString().slice(0, 10)}.pdf`);
      }
      await load();
      const doneMessage = result.downloadUrl ? "周报已生成并下载。" : "周报记录已生成，但接口没有返回下载链接。";
      setMessage(doneMessage);
      setActionLink(null);
      setWeeklyReportStatus({ state: "success", message: doneMessage });
    } catch (error) {
      const errorMessage = reportApiErrorMessage(error, "周报生成失败");
      setMessage(errorMessage);
      setActionLink(null);
      setWeeklyReportStatus({ state: "error", message: errorMessage });
    }
  }, [currentReportConfig, load, token]);

  const handleUploadAvatar = useCallback(async (file: File) => {
    setMessage("正在上传真人头像...");
    try {
      const result = await uploadMyAvatar(token, file);
      if (result.status !== "success" || !result.user) {
        throw new Error(result.message || "头像上传失败");
      }
      setAvatarUrl(result.user.avatar_url || "");
      await refreshUser();
      setMessage("头像已更新。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "头像上传失败");
    }
  }, [refreshUser, token]);

  const handleLookupKol = useCallback(async (payload: Parameters<typeof lookupKol>[1]) => {
    setMessage(payload.scanAccount ? "正在查重并抓取红人账号..." : "正在查重红人...");
    const result = await lookupKol(token, payload);
    setMessage("红人查重完成。");
    await load();
    return result;
  }, [load, token]);

  const handleScanKolAccount = useCallback(async (kolId: string, maxPosts = 24) => {
    setMessage("正在抓取账号数据和生成评估报告...");
    const result = await scanKolAccount(token, kolId, maxPosts);
    setMessage("账号数据抓取完成。");
    await load();
    return result;
  }, [load, token]);

  const handleClaimKol = useCallback(async (kolId: string) => {
    setMessage("正在绑定红人...");
    await claimKol(token, kolId);
    setMessage("红人已绑定当前成员。");
    await load();
  }, [load, token]);

  const handleUpdateKol = useCallback(async (kolId: string, payload: Parameters<typeof updateMarketingKol>[2]) => {
    setMessage("正在保存红人补录资料...");
    await updateMarketingKol(token, kolId, payload);
    setMessage("红人资料已保存。");
    await load();
  }, [load, token]);

  const handleUploadEvidenceFile = useCallback(async (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => {
    setMessage("正在上传证据文件...");
    const result = await uploadMarketingEvidenceFile(token, file, payload);
    setMessage("证据文件已上传。");
    return result;
  }, [token]);

  const handleCreateProject = useCallback(async (payload: Parameters<typeof createProject>[1]) => {
    setMessage("正在创建项目...");
    await createProject(token, payload);
    setMessage("项目已创建。");
    await load();
  }, [load, token]);

  const handleUpdateProject = useCallback(async (projectId: string, payload: Parameters<typeof updateProject>[2]) => {
    setMessage("正在更新项目...");
    await updateProject(token, projectId, payload);
    setMessage("项目已更新。");
    await load();
  }, [load, token]);

  const handleMoveProjectStage = useCallback(async (projectId: string, toStage: VkpiProjectStage, note?: string, extras?: { trackingNumber?: string; sampleStatus?: string; sourceRefType?: string; sourceRefId?: string }) => {
    setMessage("正在更新项目阶段...");
    await transitionProjectStage(token, projectId, { toStage, note, ...extras });
    setMessage("项目阶段已更新。");
    await load();
  }, [load, token]);

  const handleDeleteProject = useCallback(async (projectId: string, reason?: string) => {
    setMessage("正在删除项目...");
    await deleteProject(token, projectId, reason || "用户在项目列表删除");
    setMessage("项目已删除，相关 live 短链已暂停，历史成本和归因保留。");
    await load();
  }, [load, token]);

  const handleAddProjectCost = useCallback(async (payload: Parameters<typeof addProjectCost>[1]) => {
    setMessage("正在登记项目成本...");
    await addProjectCost(token, payload);
    setMessage("成本已计入项目。");
    await load();
  }, [load, token]);

  const handleUpdateCost = useCallback(async (costId: string, payload: VkpiCostUpdatePayload) => {
    setMessage("正在更新成本记录...");
    await updateMarketingCost(token, costId, payload);
    setMessage("成本记录已更新。");
    await load();
  }, [load, token]);

  const handleApproveCost = useCallback(async (costId: string, note: string, authorizationEvidence: VkpiManualAuthorizationEvidence) => {
    setMessage("正在审核成本记录...");
    await approveMarketingCost(token, costId, note, authorizationEvidence);
    setMessage("成本记录已审核。");
    await load();
  }, [load, token]);

  const handleVoidCost = useCallback(async (costId: string, reason: string, authorizationEvidence: VkpiManualAuthorizationEvidence) => {
    setMessage("正在作废成本记录...");
    await voidMarketingCost(token, costId, reason, authorizationEvidence);
    setMessage("成本记录已作废，历史审计保留。");
    await load();
  }, [load, token]);

  const handleAddProjectMessage = useCallback(async (projectId: string, payload: Record<string, unknown>) => {
    setMessage("正在保存项目消息证据...");
    await addProjectMessage(token, projectId, payload);
    setMessage("项目消息证据已保存。");
    await load();
  }, [load, token]);

  const handleAddProjectContent = useCallback(async (projectId: string, payload: Record<string, unknown>) => {
    setMessage("正在保存发布内容证据...");
    await addProjectContent(token, projectId, payload);
    setMessage("发布内容证据已保存。");
    await load();
  }, [load, token]);

  const handleUpsertProjectTerms = useCallback(async (projectId: string, payload: Record<string, unknown>) => {
    setMessage("正在保存合作条款...");
    await upsertProjectTerms(token, projectId, payload);
    setMessage("合作条款已保存。");
    await load();
  }, [load, token]);

  const handleAddProjectShipment = useCallback(async (projectId: string, payload: Record<string, unknown>) => {
    setMessage("正在保存物流 / 样品证据...");
    await addProjectShipment(token, projectId, payload);
    setMessage("物流 / 样品证据已保存。");
    await load();
  }, [load, token]);

  const handleCreateLink = useCallback(async (payload: Parameters<typeof createMarketingLink>[1]) => {
    setMessage("正在创建短链...");
    await createMarketingLink(token, payload);
    setMessage("短链已创建。");
    await load();
  }, [load, token]);

  const handlePauseLink = useCallback(async (linkId: string) => {
    setMessage("正在暂停短链...");
    await pauseMarketingLink(token, linkId);
    setMessage("短链已暂停。");
    await load();
  }, [load, token]);

  const handleArchiveLink = useCallback(async (linkId: string) => {
    setMessage("正在归档短链...");
    await archiveMarketingLink(token, linkId);
    setMessage("短链已归档，历史点击和订单证据保留。");
    await load();
  }, [load, token]);

  const handleHealthCheckLink = useCallback(async (linkId: string) => {
    setMessage("正在检查短链...");
    await healthCheckMarketingLink(token, linkId);
    setMessage("短链健康检查已完成。");
    await load();
  }, [load, token]);

  const handleInviteStaff = useCallback(async (payload: Parameters<typeof inviteMarketingStaff>[1]) => {
    setMessage("正在授权成员账号...");
    await inviteMarketingStaff(token, payload);
    setMessage("成员账号已授权。");
    await load();
  }, [load, token]);

  const handleUpdateStaffPermission = useCallback(async (staffId: string, permission: "none" | "read" | "write") => {
    setMessage("正在更新成员权限...");
    await updateStaffMarketingPermission(token, staffId, permission);
    setMessage("成员权限已更新。");
    await load();
  }, [load, token]);

  const handleRunKpiRollup = useCallback(async (ledgerDate?: string) => {
    setMessage("正在计入成员 KPI 工作量...");
    await runKpiRollup(token, ledgerDate);
    setMessage("KPI 工作量已计入。");
    await load();
  }, [load, token]);

  const handleUpsertProductCost = useCallback(async (payload: Parameters<typeof upsertProductCost>[1]) => {
    setMessage("正在保存产品镜头成本...");
    await upsertProductCost(token, payload);
    setMessage("产品镜头成本已保存。");
    await load();
  }, [load, token]);

  const handleCreateAttribution = useCallback(async (payload: Parameters<typeof createSalesAttribution>[1]) => {
    setMessage("正在写入销售归因...");
    await createSalesAttribution(token, payload);
    setMessage("销售归因已写入。");
    await load();
  }, [load, token]);

  const handleImportAmazonRows = useCallback(async (payload: Parameters<typeof importAmazonAttributionRows>[1]) => {
    setMessage("正在导入 Amazon 归因 rows...");
    await importAmazonAttributionRows(token, payload);
    setMessage("Amazon 归因 rows 已导入。");
    await load();
  }, [load, token]);

  const handleUploadAmazonReport = useCallback(async (payload: Parameters<typeof uploadAmazonAttributionReport>[1]) => {
    setMessage("正在上传 Amazon 归因报表...");
    await uploadAmazonAttributionReport(token, payload);
    setMessage("Amazon 归因报表已导入。");
    await load();
  }, [load, token]);

  if (loading && !data && !canRenderWithoutDashboardData) {
    return <div style={{ padding: 24, color: "#667085" }}>正在加载 Viltrox Marketing...</div>;
  }

  if (!data && !canRenderWithoutDashboardData) {
    return (
      <div style={{ padding: 24, color: "#667085" }}>
        <strong style={{ display: "block", color: "#101828", marginBottom: 8 }}>Viltrox Marketing 暂不可用</strong>
        <div style={{ marginBottom: 12 }}>{message || "没有返回看板数据。"}</div>
        <button type="button" className="ax-btn ax-btn--primary" onClick={() => void load()}>重试</button>
      </div>
    );
  }

  return (
    <div style={{ position: "relative" }}>
      {message ? (
        <div style={{ position: "sticky", top: 0, zIndex: 20, padding: "10px 16px", background: "#101828", color: "#fff", fontSize: 12 }}>
          {message}
          {actionLink ? (
            <a
              href={actionLink.href}
              target="_blank"
              rel="noreferrer"
              style={{ color: "#fff", marginLeft: 12, fontWeight: 800, textDecoration: "underline" }}
            >
              {actionLink.label}
            </a>
          ) : null}
        </div>
      ) : null}
      <VkpiDashboard
        data={data}
        range={range}
        onRangeChange={setRange}
        onRefreshData={() => void load()}
        isRefreshing={loading}
        lastSyncedAt={lastSyncedAt}
        apiToken={token}
        userName={user?.name || user?.email || "Viltrox 成员"}
        userRole={userRoleLabel}
        userEmail={user?.email || ""}
        userAuthRole={String(user?.staff_role || user?.role || "")}
        userAvatar={avatarUrl || user?.avatar_url || ""}
        avatarRequired={Boolean(user?.avatar_required || !(avatarUrl || user?.avatar_url))}
        viewMode={effectiveViewMode}
        canSwitchView={isManager}
        onToggleView={handleToggleView}
        onExportPDF={handleExportPDF}
        onExportCSV={handleExportCSV}
        onGenerateWeeklyReport={handleGenerateWeeklyReport}
        weeklyReportStatus={weeklyReportStatus}
        onCopyShortLink={(slug) => void copyTextToClipboard(slug)}
        onUploadAvatar={handleUploadAvatar}
        onSignOut={onSignOut}
        onLookupKol={handleLookupKol}
        onScanKolAccount={handleScanKolAccount}
        onClaimKol={handleClaimKol}
        onUpdateKol={handleUpdateKol}
        onUploadEvidenceFile={handleUploadEvidenceFile}
        onCreateProject={handleCreateProject}
        onUpdateProject={handleUpdateProject}
        onMoveProjectStage={handleMoveProjectStage}
        onDeleteProject={handleDeleteProject}
        onAddProjectCost={handleAddProjectCost}
        onUpdateCost={handleUpdateCost}
        onApproveCost={handleApproveCost}
        onVoidCost={handleVoidCost}
        onAddProjectMessage={handleAddProjectMessage}
        onAddProjectContent={handleAddProjectContent}
        onUpsertProjectTerms={handleUpsertProjectTerms}
        onAddProjectShipment={handleAddProjectShipment}
        onCreateLink={handleCreateLink}
        onPauseLink={handlePauseLink}
        onArchiveLink={handleArchiveLink}
        onHealthCheckLink={handleHealthCheckLink}
        onInviteStaff={handleInviteStaff}
        onUpdateStaffPermission={handleUpdateStaffPermission}
        onRunKpiRollup={handleRunKpiRollup}
        onUpsertProductCost={handleUpsertProductCost}
        onCreateAttribution={handleCreateAttribution}
        onImportAmazonRows={handleImportAmazonRows}
        onUploadAmazonReport={handleUploadAmazonReport}
      />
    </div>
  );
}

export default VkpiTab;
