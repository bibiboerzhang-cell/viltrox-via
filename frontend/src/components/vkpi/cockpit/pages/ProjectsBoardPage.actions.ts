import React from "react";
import type { VkpiKolOption, VkpiProjectRow } from "../../vkpiTypes";
import {
  addKolsToProject,
  advanceProjectKol,
  getAvailableProjectKols,
  submitProjectKolActionStub,
  updateProjectFollowStatus,
  updateProjectKolShipping,
  updateProjectStar,
} from "../../../../domains/projects";
import { buildKolOptions } from "../../../../domains/kol";
import { advanceContentPostsToRetrospective } from "../../../../services/vkpi/fulfillment-api";
import {
  buildImportSearchTerms,
  findImportKolMatch,
  lookupImportKolPoolOption,
  type ImportKolRow,
} from "../../pages/ProjectsPage.helpers";
import type { ProjectsPageProps } from "../../pages/ProjectsPage.types";
import { kolHumanDisplayName } from "../lib/kolIdentity";

// Projects 板块页 · 动作层(金样板 LaunchPadBoardPage.actions 同构;行数纪律 ≤700/文件)。
//   useRetroAdvance        履约闭环末棒「推进复盘」:POST /projects/{id}/content-posts/
//                          advance-retrospective —— matched 帖批量置 retrospective_ready +
//                          enqueue 复盘聚合。回执只信端点真实返回(status==="ok" 且带
//                          advanced_count);轮询封顶/未知形状 = gone 不写 ✓(不装成功)。
//   useProjectsCrudActions 旧 ProjectsPage 全部写动作原样搬家(零语义改动):加 KOL /
//                          批量导入匹配 / KOL 级阶段推进 / 物流写库 / 证据 stub / 跟进
//                          暂停 / 重点星标 / 删除(取消)项目。
//   useEndpoint            只读端点装载(LaunchPad 同款:失败 = error 原文;version 自增
//                          = 重拉服务器真数,绝不本地编数)。
// 红线:不触 viltrox_fit_score / rule_v0;唯一网络出口 = services/domains 层。

export type Row = Record<string, any>;

// React 18 development StrictMode replays effects.  The read-only endpoint hook used to
// start a second request during that replay while merely marking the first callback dead.
// Reuse the still-running request for the same stable fetcher/token/version instead.  The
// WeakMap keeps this scoped to each caller's useCallback identity and releases it with the
// component; version changes (manual refresh/write-after-read) intentionally create a new
// request.
const endpointInflight = new WeakMap<Function, Map<string, Promise<unknown>>>();

function sharedEndpointRequest<T>(
  fetcher: (token: string) => Promise<T>,
  apiToken: string,
  version: number,
): Promise<T> {
  let requests = endpointInflight.get(fetcher);
  if (!requests) {
    requests = new Map<string, Promise<unknown>>();
    endpointInflight.set(fetcher, requests);
  }
  const key = `${apiToken}\u0000${version}`;
  const existing = requests.get(key);
  if (existing) return existing as Promise<T>;

  // Defer the actual call one microtask so StrictMode's setup-cleanup-setup pass can join
  // deterministically even when a test/mock fetcher resolves synchronously.
  let request: Promise<T>;
  request = Promise.resolve()
    .then(() => fetcher(apiToken))
    .finally(() => {
      if (requests?.get(key) === request) requests.delete(key);
    });
  requests.set(key, request);
  return request;
}

/** 只读端点装载(端点失败 = error 原文;version 自增 = 重拉服务器真数)。 */
export function useEndpoint<T>(apiToken: string, version: number, fetcher: (token: string) => Promise<T>) {
  const [result, setResult] = React.useState<{ token: string; data: T | null; error: string }>({
    token: apiToken,
    data: null,
    error: "",
  });
  // Never render data resolved for a previous authenticated principal.  The effect cleanup
  // prevents late writes, while this render-time scope check also closes the frame between a
  // token prop change and the next effect (and the indefinitely-stale case when the new read
  // fails).
  const scopedResult = result.token === apiToken
    ? result
    : { token: apiToken, data: null as T | null, error: "" };
  React.useEffect(() => {
    if (!apiToken) {
      setResult((current) => (
        current.token === "" && current.data == null && !current.error
          ? current
          : { token: "", data: null, error: "" }
      ));
      return;
    }
    let alive = true;
    setResult((current) => (
      current.token === apiToken
        ? { ...current, error: "" }
        : { token: apiToken, data: null, error: "" }
    ));
    sharedEndpointRequest(fetcher, apiToken, version)
      .then((res) => {
        if (alive) setResult({ token: apiToken, data: (res as T) ?? null, error: "" });
      })
      .catch((err: any) => {
        if (alive) {
          const message = String(err?.detail || err?.message || "读取失败");
          setResult((current) => (
            current.token === apiToken
              ? { ...current, error: message }
              : { token: apiToken, data: null, error: message }
          ));
        }
      });
    return () => {
      alive = false;
    };
  }, [apiToken, version, fetcher]);
  return { data: scopedResult.data, error: scopedResult.error };
}

export interface RetroReceipt {
  advancedCount: number;
  retrospectiveQueued: boolean;
}

/** 推进复盘(matched → retrospective_ready + enqueue 聚合)。
 *  纪律:端点真实返回 status==="ok" 才落 receipt;其余一律 error(gone 不写 ✓)。 */
export function useRetroAdvance(apiToken: string) {
  const [receipts, setReceipts] = React.useState<Record<string, RetroReceipt>>({});
  const [busyProjectId, setBusyProjectId] = React.useState<string | null>(null);
  const [error, setError] = React.useState("");
  const [version, setVersion] = React.useState(0);

  const advance = React.useCallback(
    (projectId: string | number) => {
      const key = String(projectId);
      if (!apiToken || !key || busyProjectId != null) return;
      setBusyProjectId(key);
      setError("");
      advanceContentPostsToRetrospective(apiToken, key)
        .then((res) => {
          const status = String((res as Row)?.status || "");
          const advancedCount = Number((res as Row)?.advanced_count);
          if (status === "ok" && Number.isFinite(advancedCount)) {
            setReceipts((prev) => ({
              ...prev,
              [key]: { advancedCount, retrospectiveQueued: Boolean((res as Row)?.retrospective) },
            }));
            setVersion((v) => v + 1);
          } else {
            // 未知形状/非 ok = 不写 ✓,原因原样透出
            setError(String((res as Row)?.error || (res as Row)?.reason || `端点返回非 ok(status=${status || "缺席"})`));
          }
        })
        .catch((err: any) => {
          setError(String(err?.detail || err?.message || "推进复盘失败"));
        })
        .finally(() => {
          setBusyProjectId((cur) => (cur === key ? null : cur));
        });
    },
    [apiToken, busyProjectId],
  );

  const clearError = React.useCallback(() => setError(""), []);
  return { receipts, busyProjectId, error, version, advance, clearError };
}

export interface CrudDeps {
  apiToken?: string;
  data: ProjectsPageProps["data"];
  onLookupKol?: ProjectsPageProps["onLookupKol"];
  onDeleteProject?: ProjectsPageProps["onDeleteProject"];
  onRefreshData?: ProjectsPageProps["onRefreshData"];
  setMessage: (message: string) => void;
  setBusy: (busy: boolean) => void;
}

/** 旧 ProjectsPage 写动作原样搬家(签名/口径零改动;旧页保留为回滚垫)。 */
export function useProjectsCrudActions({ apiToken, data, onLookupKol, onDeleteProject, onRefreshData, setMessage, setBusy }: CrudDeps) {
  const loadAvailableKols = async (targetProject: VkpiProjectRow, scope = "favorites") => {
    if (!apiToken) return data.kolOptions;
    // P0-2 裁决:默认 favorites 子集;scope='all' 是「从全池查找」逃生门。
    const response = await getAvailableProjectKols(apiToken, targetProject.id, "", scope);
    return buildKolOptions(response.kols || []);
  };

  const addKolsToCampaign = async (targetProject: VkpiProjectRow, kols: VkpiKolOption[]) => {
    if (!apiToken) throw new Error("缺少 API token，不能写入项目 KOL。");
    // 负责人=该 KOL 的 active claim owner(后端按 vkpi_kol_claims 决定),不强塞 assignedStaffId。
    const result = await addKolsToProject(apiToken, targetProject.id, kols.map((kol) => kol.id), undefined);
    setMessage(`已向「${targetProject.campaign}」写入 ${result.inserted || 0} 个 KOL，跳过 ${result.skipped_existing || 0} 个已有项。`);
    await onRefreshData?.();
  };

  const advanceProjectKolRow = async (projectId: string, kolRef: string, payload: Record<string, unknown>) => {
    if (!apiToken) throw new Error("缺少 API token，不能推进 KOL 阶段。");
    return advanceProjectKol(apiToken, projectId, kolRef, payload);
  };

  const updateProjectKolShippingRow = async (projectId: string, kolRef: string, payload: Record<string, unknown>) => {
    if (!apiToken) throw new Error("缺少 API token，不能写入物流。");
    return updateProjectKolShipping(apiToken, projectId, kolRef, payload);
  };

  const submitProjectKolStub = async (
    projectId: string,
    kolRef: string,
    actionKind: "screenshot" | "video" | "contract",
    payload: Record<string, unknown>,
  ) => {
    if (!apiToken) throw new Error("缺少 API token，不能提交该操作。");
    return submitProjectKolActionStub(apiToken, projectId, kolRef, actionKind, payload);
  };

  const setProjectFollowStatus = async (project: VkpiProjectRow, followStatus: "active" | "paused") => {
    if (!apiToken) throw new Error("缺少 API token，不能更新跟进状态。");
    await updateProjectFollowStatus(apiToken, project.id, followStatus);
    await onRefreshData?.();
  };

  const setProjectStar = async (project: VkpiProjectRow, starred: boolean) => {
    if (!apiToken) throw new Error("缺少 API token，不能更新重点标记。");
    await updateProjectStar(apiToken, project.id, starred);
    await onRefreshData?.();
  };

  const deleteProjectRow = async (project: VkpiProjectRow, reason?: string, actionLabel = "删除项目") => {
    if (!onDeleteProject) return;
    setBusy(true);
    try {
      await onDeleteProject(project.id, reason || `${actionLabel}：${project.campaign}`);
      setMessage(actionLabel === "取消推广" ? "推广已取消，历史记录已保留。" : "项目已删除，历史记录已保留。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `${actionLabel}失败`);
      throw error;
    } finally {
      setBusy(false);
    }
  };

  const importKolRows = async (targetProject: VkpiProjectRow, rows: ImportKolRow[]): Promise<boolean> => {
    if (!apiToken) throw new Error("缺少 API token，不能向项目批量追加 KOL。");
    setBusy(true);
    try {
      const matchedKols: VkpiKolOption[] = [];
      const unmatchedRows: ImportKolRow[] = [];
      const seenKolIds = new Set<string>();
      for (const row of rows) {
        const queryTerms = buildImportSearchTerms(row);
        const candidateMap = new Map<string, VkpiKolOption>();
        for (const term of queryTerms.length ? queryTerms : [row.handle]) {
          // 批量导入匹配走全池(scope=all):按导入 handle 在全库找人,而非仅本人收藏。
          const response = await getAvailableProjectKols(apiToken, targetProject.id, term, "all");
          buildKolOptions(response.kols || []).forEach((kol) => candidateMap.set(kol.id, kol));
        }
        let match = findImportKolMatch(row, Array.from(candidateMap.values()));
        if (!match && onLookupKol) {
          match = await lookupImportKolPoolOption(row, onLookupKol);
        }
        if (!match) {
          unmatchedRows.push(row);
          continue;
        }
        if (seenKolIds.has(match.id)) continue;
        seenKolIds.add(match.id);
        matchedKols.push(match);
      }
      if (!matchedKols.length) {
        throw new Error(`没有在 KOL Pool 里匹配到可追加的 KOL。未匹配：${unmatchedRows.slice(0, 5).map((row) => kolHumanDisplayName({ ...row })).join("、") || "全部"}`);
      }
      await addKolsToCampaign(targetProject, matchedKols);
      const unmatchedLabel = unmatchedRows.length
        ? `；未匹配 ${unmatchedRows.length} 行：${unmatchedRows.slice(0, 5).map((row) => kolHumanDisplayName({ ...row })).join("、")}${unmatchedRows.length > 5 ? "…" : ""}`
        : "";
      setMessage(`已向「${targetProject.campaign}」追加 ${matchedKols.length} 个 KOL${unmatchedLabel}。`);
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "KOL 名单导入失败");
      throw error;
    } finally {
      setBusy(false);
    }
  };

  return {
    loadAvailableKols,
    addKolsToCampaign,
    advanceProjectKolRow,
    updateProjectKolShippingRow,
    submitProjectKolStub,
    setProjectFollowStatus,
    setProjectStar,
    deleteProjectRow,
    importKolRows,
  };
}
