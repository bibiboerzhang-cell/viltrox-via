
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Brain, Clock3, FileText, Search, Zap } from "lucide-react";
import { buildApiUrl } from "../../../../services/http";
import { retryTask } from "../../../../services/vkpi/tasks-api";
import { useWorkflowRunsStream, type WorkflowRunsStream } from "../useWorkflowRunsStream";

const e = React.createElement;
const PENDING_SEARCH_SESSION_KEY = "vkpi:pendingKolSearchSessionId";
// 「只看我的」开关记忆键(2026-07-02):开=活跃/排队/最近完成只展示自己发起的任务。
const MINE_ONLY_STORAGE_KEY = "vkpi:taskboard-mine-only";

const LANES = [
  {
    key: "search",
    title: "搜索中",
    Icon: Search,
    color: "var(--ds-good)",
    titleColor: "var(--ds-good)",
    border: "color-mix(in srgb, var(--ds-good) 30%, transparent)",
    bg: "color-mix(in srgb, var(--ds-good) 8%, transparent)",
    showBar: false,
  },
  {
    key: "thinking",
    title: "思考中",
    Icon: Brain,
    color: "var(--ds-accent-2)",
    titleColor: "var(--ds-accent-2)",
    border: "color-mix(in srgb, var(--ds-accent-2) 35%, transparent)",
    bg: "color-mix(in srgb, var(--ds-accent-2) 10%, transparent)",
    showBar: true,
  },
  {
    key: "summarizing",
    title: "总结中",
    Icon: FileText,
    color: "var(--ds-warn)",
    titleColor: "var(--ds-warn)",
    border: "color-mix(in srgb, var(--ds-warn) 35%, transparent)",
    bg: "color-mix(in srgb, var(--ds-warn) 10%, transparent)",
    showBar: true,
  },
];

function asArray(value: any) {
  return Array.isArray(value) ? value : [];
}

function taskTargetText(task: any) {
  const target = task?.target && typeof task.target === "object" ? task.target : {};
  return String(
    target.label ||
    target.handle ||
    target.display_name ||
    target.target_id ||
    target.source_url ||
    task?.target_label ||
    ""
  ).trim();
}

function taskLabel(task: any) {
  return `${task.kind || "任务"} · ${taskTargetText(task) || "未命名"}`;
}

// 人性化 ETA:<60s 显示秒,≥60s 显示分钟。字段缺失/非法(旧任务、LLM 调用行)返回空串不显示。
// 诊断 P1-1 根治已落(迁移112 started_at → queue_view 用真处理时长 claim→done):ETA 可信;
// 无 started_at 样本时 queue_view 回退 300s 默认,不再是墙钟 7.8 天的谎言。
function etaHumanText(task: any) {
  const eta = Number(task?.eta_seconds);
  if (!Number.isFinite(eta) || eta <= 0) return "";
  if (eta < 60) return `约 ${Math.max(1, Math.round(eta))} 秒`;
  return `约 ${Math.round(eta / 60)} 分钟`;
}

// 排队行元信息:「第 X 位 · 约 Y」。queue_position 缺失退回 ahead_count(前方 N 个),都缺则不显示。
function queueMetaText(task: any) {
  const parts: string[] = [];
  const pos = Number(task?.queue_position);
  const ahead = Number(task?.ahead_count);
  if (Number.isFinite(pos) && pos > 0) parts.push(`第 ${pos} 位`);
  else if (Number.isFinite(ahead)) parts.push(ahead > 0 ? `前方 ${ahead} 个` : "下一个就是它");
  const eta = etaHumanText(task);
  if (eta) parts.push(eta);
  return parts.join(" · ");
}

function taskRetryText(task: any) {
  const category = String(task?.error_category || "").trim();
  const retryAt = String(task?.next_retry_at || "").trim();
  if (!category && !retryAt) return "";
  let timeText = "";
  if (retryAt) {
    const parsed = new Date(retryAt);
    if (Number.isFinite(parsed.getTime())) {
      timeText = parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
  }
  const categoryLabel = category === "provider_pressure" ? "provider压力" : category;
  if (categoryLabel && timeText) return `${categoryLabel} · 退避至 ${timeText}`;
  if (timeText) return `退避至 ${timeText}`;
  return categoryLabel;
}

// 失败桶分类(2026-06-15):把后端 _error_category(apify_jobs_worker.py:140-243)的细类
// 折叠成 5 个展示桶 + 其他。apify_jobs 任务带 error_category;vkpi_async_tasks 没有,
// 故 error_category 为空时回退按 task.error 文本关键词匹配(沿用后端同序优先级)。
const FAIL_BUCKETS = {
  download: { key: "download", label: "下载", tone: "#FAC775" },
  provider: { key: "provider", label: "服务方", tone: "#FAC775" },
  media: { key: "media", label: "媒体解析", tone: "rgba(255,255,255,0.40)" },
  content_restricted: { key: "content_restricted", label: "内容受限", tone: "#fb7185" },
  code_error: { key: "code_error", label: "代码错误", tone: "#fb7185" },
  other: { key: "other", label: "其他", tone: "rgba(255,255,255,0.40)" },
};

function taskFailBucket(task: any) {
  const category = String(task?.error_category || "").trim().toLowerCase();
  if (category) {
    if (category === "download") return FAIL_BUCKETS.download;
    if (category === "provider_pressure" || category === "timeout" || category === "stale_running") return FAIL_BUCKETS.provider;
    if (category === "media_resolve") return FAIL_BUCKETS.media;
    if (
      category === "content_restricted" ||
      category === "content_blocked" ||
      category === "content_unavailable" ||
      category === "permanent" ||
      category === "blocked"
    ) return FAIL_BUCKETS.content_restricted;
    if (category === "code_error") return FAIL_BUCKETS.code_error;
    return FAIL_BUCKETS.other;
  }
  const text = String(task?.error || "");
  if (!text) return FAIL_BUCKETS.other;
  if (/429|resource_exhausted|rate limit|quota exceeded|50[0-4]|5xx|internal server error|unavailable|high demand|overloaded|timeout/i.test(text)) return FAIL_BUCKETS.provider;
  if (/yt[-_]?dlp|download_failed|direct_video_download_failed/i.test(text)) return FAIL_BUCKETS.download;
  if (/media_resolve/i.test(text)) return FAIL_BUCKETS.media;
  if (/age.?restricted|private video|login required|sign in to confirm|members-only|subscriber-only|copyright|dmca|removed|terminated|not found|404|deleted|unsupported|invalid_video_url/i.test(text)) return FAIL_BUCKETS.content_restricted;
  if (/modulenotfounderror|importerror|nameerror|attributeerror|typeerror|keyerror|indexerror|traceback|no module named|cannot import name/i.test(text)) return FAIL_BUCKETS.code_error;
  return FAIL_BUCKETS.other;
}

function taskSearchSessionId(task: any) {
  const session = task?.search_session && typeof task.search_session === "object" ? task.search_session : {};
  const raw = session.session_id || session.id || task?.target?.target_id;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 && String(task?.target?.target_type || "").includes("search_session")
    ? parsed
    : Number.isFinite(Number(session.session_id || session.id)) && Number(session.session_id || session.id) > 0
      ? Number(session.session_id || session.id)
      : undefined;
}

function openSearchSessionFromTask(task: any) {
  const sessionId = taskSearchSessionId(task);
  if (!sessionId || typeof window === "undefined") return;
  window.localStorage.setItem(PENDING_SEARCH_SESSION_KEY, String(sessionId));
  window.dispatchEvent(new CustomEvent("vkpi:open-kol-search-session", { detail: { sessionId, task } }));
}

function taskMyKolPoolId(task: any) {
  const target = task?.target && typeof task.target === "object" ? task.target : {};
  const poolId = Number(target.target_id);
  return String(target.target_type || "") === "kol_profile" && Number.isFinite(poolId) && poolId > 0 ? poolId : undefined;
}

// item1:video / 账号档案等任务的 KOL 主体在 target.kol_pool_id(target_id 是 video/evidence id)。
// 点「打开」→ 直达 KOL Pool 抽屉(池里一定有,不受 watchlist 限制)。
function taskKolPoolId(task: any) {
  const target = task?.target && typeof task.target === "object" ? task.target : {};
  const poolId = Number(target.kol_pool_id);
  return Number.isFinite(poolId) && poolId > 0 ? poolId : undefined;
}

// 2026-06-12 波5 R5:target_type=project 的任务(合同润色等同族)可回到项目详情。
// 注意只认 target_type === 'project'(target_id 即 project_id);
// 'project_contract' 的 target_id 是合同 id,无法可靠映射项目,保持禁用(诚实降级)。
function taskProjectId(task: any) {
  const target = task?.target && typeof task.target === "object" ? task.target : {};
  const projectId = Number(target.target_id);
  return String(target.target_type || "") === "project" && Number.isFinite(projectId) && projectId > 0 ? projectId : undefined;
}

// 2026-07-02 裁令更新(推翻 06-12 的回 MY KOL):凡能定位到 KOL 的任务(评论采集/账号分析/video)
// 一律直达 KOL Pool 完整详情抽屉 —— 池内必有该 KOL,不受是否收藏(watchlist)限制;
// 之前 kol_profile 跳 MY KOL,未收藏的 KOL 会落空。project 任务仍回项目详情。
function openTaskOrigin(task: any) {
  const kolPoolId = taskKolPoolId(task) || taskMyKolPoolId(task);
  if (kolPoolId && typeof window !== "undefined") {
    window.localStorage.setItem("vkpi:pending-kolpool-open-id", String(kolPoolId));
    window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-item", { detail: { kolPoolId, task } }));
    return;
  }
  const projectId = taskProjectId(task);
  if (projectId && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("vkpi:open-project-task", { detail: { projectId: String(projectId), task } }));
    return;
  }
  openSearchSessionFromTask(task);
}

function taskCanOpen(task: any) {
  return Boolean(taskMyKolPoolId(task) || taskKolPoolId(task) || taskProjectId(task) || taskSearchSessionId(task));
}

// 当前用户身份(/api/auth/me 拉一次,只留比较用的两个 id;拿不到时 me=null → 全部回退「用户 N」)。
interface MeIdentity {
  userId: string;
  staffId: string;
}

// 归属判断:initiator_user_id 对 me.id、initiator_staff_id 对 me.staff_id(各对各的域,
// 不交叉比 —— user id 与别人的 staff id 撞号会误标「我」)。后端可能给 number/string,统一转字符串比。
function taskIsMine(task: any, me: MeIdentity | null) {
  if (!me) return false;
  const byUser = task?.initiator_user_id;
  if (byUser != null && byUser !== "" && me.userId && String(byUser) === me.userId) return true;
  const byStaff = task?.initiator_staff_id;
  if (byStaff != null && byStaff !== "" && me.staffId && String(byStaff) === me.staffId) return true;
  return false;
}

// 归属 chip:自己的任务显示「我」+ 金色高亮边;别人的保持「用户 N」;无发起人字段(系统任务)不显示。
function initiatorChipNode(task: any, me: MeIdentity | null) {
  const initiator = task?.initiator_user_id ?? task?.initiator_staff_id;
  if (initiator == null || initiator === "") return null;
  if (taskIsMine(task, me)) {
    return e("span", {
      className: "shrink-0 rounded border border-warn bg-warn-soft px-1 text-[9px] font-medium leading-4 text-warn",
    }, "我");
  }
  return e("span", { className: "shrink-0 text-[9px] text-muted" }, `用户 ${initiator}`);
}

// 我的任务置顶:稳定排序(现代 JS Array.sort 保证稳定),两组内部保持原有顺序。
function sortMineFirst(tasks: any[], me: MeIdentity | null) {
  if (!me) return tasks;
  return tasks.slice().sort((a: any, b: any) => Number(taskIsMine(b, me)) - Number(taskIsMine(a, me)));
}

function lightColor(light: any) {
  const tone = String(light?.tone || "").toLowerCase();
  if (tone === "red") return "var(--ds-crit)";
  if (tone === "amber") return "var(--ds-warn)";
  return "var(--ds-good)";
}

function TaskRow({ task, color, showBar, me }: any) {
  const rawProgress = task.progress_pct ?? task.progress;
  const hasProgress = Number.isFinite(Number(rawProgress));
  const progress = Math.max(6, Math.min(100, Number(rawProgress || 0)));
  const canOpen = taskCanOpen(task);
  const retryText = taskRetryText(task);
  return e("button", {
    type: "button",
    onClick: canOpen ? () => openTaskOrigin(task) : undefined,
    disabled: !canOpen,
    className: `block w-full min-w-0 text-left ${canOpen ? "cursor-pointer rounded-md transition-colors hover:bg-accent-soft" : "cursor-default"} disabled:cursor-default`,
    title: canOpen ? ((taskKolPoolId(task) || taskMyKolPoolId(task)) ? "打开 KOL Pool 该 KOL 完整详情" : "打开这次查找记录") : "",
  },
    e("div", { className: "flex items-center gap-1.5 min-w-0" },
      e("span", {
        className: `h-[5px] w-[5px] shrink-0 rounded-full ${showBar ? "animate-pulse" : ""}`,
        style: { background: color }
      }),
      e("span", { className: "truncate text-[11px] leading-4 text-ink-2" }, taskLabel(task)),
      initiatorChipNode(task, me)
    ),
    retryText && e("div", { className: "ml-[11px] truncate text-[10px] leading-4 text-muted" }, retryText),
    showBar && (
      hasProgress
        ? e("div", { className: "ml-[11px] mt-1 h-[3px] overflow-hidden rounded-full bg-panel" },
          e("div", {
            className: "h-full rounded-full transition-[width] duration-500",
            style: { width: `${progress}%`, background: color }
          })
        )
        : e("div", { className: "ml-[11px] mt-1 h-[3px] overflow-hidden rounded-full bg-panel" },
          e("div", {
            className: "h-full w-1/2 rounded-full opacity-60",
            style: { background: `linear-gradient(90deg, transparent, ${color}, transparent)` }
          })
        )
    )
  );
}

function TaskLane({ lane, tasks, me }: any) {
  return e("section", {
    className: "rounded-lg border px-[9px] py-2",
    style: { borderColor: lane.border, background: lane.bg }
  },
    e("div", { className: "mb-1.5 flex items-center justify-between gap-2" },
      e("div", { className: "flex min-w-0 items-center gap-1.5" },
        e(lane.Icon, { size: 13, style: { color: lane.color } }),
        e("span", {
          className: "truncate text-[11px] font-medium uppercase tracking-[0.04em]",
          style: { color: lane.titleColor }
        }, lane.title)
      ),
      e("span", { className: "text-[11px] font-medium tabular-nums", style: { color: lane.color } }, tasks.length)
    ),
    e("div", { className: "flex flex-col gap-1.5" },
      tasks.length
        ? tasks.map((task: any) => e(TaskRow, { key: task.id, task, color: lane.color, showBar: lane.showBar, me }))
        : e("span", { className: "text-[11px] leading-4 text-muted" }, "—")
    )
  );
}

interface TaskProgressBoardProps {
  apiToken?: string;
  compact?: boolean;
  // 10C 状态同源:CockpitApp 把共享的 workflow_runs 轮询流(useWorkflowRunsStream)透传进来,
  // 后台任务推进/完成时该流自动刷新 → 本板自动重渲染,无需手动刷新。
  // CockpitSidebar 仅传 apiToken(不传 stream),此时本板内部自起一个 hook 实例兜底。
  stream?: WorkflowRunsStream;
}

export function TaskProgressBoard({ apiToken = "", stream, compact = false }: TaskProgressBoardProps) {
  // 没传 stream 时(如 CockpitSidebar 用法)在内部起一个 hook 实例;传了就复用上层共享流。
  // 注:hooks 不能条件调用,故无条件 call,再在下面择一使用——内部实例在有 stream 时也无害(同源端点)。
  const localStream = useWorkflowRunsStream(apiToken, { intervalMs: 5000, limit: 30, recentMinutes: 5 });
  const activeStream = stream ?? localStream;
  const { payload, loading, error, refresh: refreshQueue } = activeStream;
  // 每任务重试态:id → 'loading' | 'done' | 错误串。重试成功后 refreshQueue 让该任务离开失败桶。
  const [retrying, setRetrying] = useState<Record<string, any>>({});
  const [showAllQueue, setShowAllQueue] = useState(false);
  // 当前用户身份:拉一次 /api/auth/me(失败静默,me=null 全部回退「用户 N」口径)。
  const [me, setMe] = useState<MeIdentity | null>(null);
  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    void fetch(buildApiUrl("/api/auth/me"), { headers: { Authorization: `Bearer ${apiToken}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: any) => {
        if (!alive || !data) return;
        const userId = String(data?.user?.id ?? data?.id ?? "");
        const staffId = String(data?.staff?.id ?? data?.staff_id ?? data?.user?.staff_id ?? "");
        if (userId || staffId) setMe({ userId, staffId });
      })
      .catch(() => { /* 身份拉取失败不影响任务板本体 */ });
    return () => { alive = false; };
  }, [apiToken]);
  // 「只看我的」:开=活跃/排队/最近完成只留自己发起的;记忆到 localStorage。
  const [mineOnly, setMineOnly] = useState<boolean>(() => {
    try { return window.localStorage.getItem(MINE_ONLY_STORAGE_KEY) === "1"; } catch { return false; }
  });
  const toggleMineOnly = useCallback(() => {
    setMineOnly((v) => {
      const next = !v;
      try { window.localStorage.setItem(MINE_ONLY_STORAGE_KEY, next ? "1" : "0"); } catch { /* 忽略 */ }
      return next;
    });
  }, []);

  const handleRetry = useCallback(async (task: any) => {
    const id = task?.id;
    if (!id || !apiToken) return;
    setRetrying((s) => ({ ...s, [id]: "loading" }));
    try {
      await retryTask(apiToken, id);
      setRetrying((s) => ({ ...s, [id]: "done" }));
      void refreshQueue();
    } catch (err) {
      setRetrying((s) => ({ ...s, [id]: err instanceof Error ? err.message : "重试失败" }));
    }
  }, [apiToken, refreshQueue]);

  const rawActiveTasks = useMemo(() => asArray(payload?.active), [payload]);
  const rawRecentTasks = useMemo(() => asArray(payload?.recent), [payload]);
  // mineOnly 过滤;关着时自己的任务在泳道内置顶(排队区不动序——位次必须是真实执行序)。
  const activeTasks = useMemo(
    () => (mineOnly && me ? rawActiveTasks.filter((t: any) => taskIsMine(t, me)) : rawActiveTasks),
    [rawActiveTasks, mineOnly, me],
  );
  const recentTasks = useMemo(
    () => (mineOnly && me ? rawRecentTasks.filter((t: any) => taskIsMine(t, me)) : rawRecentTasks),
    [rawRecentTasks, mineOnly, me],
  );
  // 排队区按真实执行序排(后端 queue_position 按 claim 顺序;无该字段的按 created_at 升序垫后)——
  // 此前直接用合并列表(updated_at 倒序)切片,屏上 #1 可能是最后才跑的(2026-06-12 主管裁令)。
  const queuedTasks = useMemo(() => {
    const queued = activeTasks.filter((task: any) => task?.status === "queued");
    return queued.slice().sort((a: any, b: any) => {
      const pa = Number(a?.queue_position); const pb = Number(b?.queue_position);
      if (Number.isFinite(pa) && Number.isFinite(pb)) return pa - pb;
      if (Number.isFinite(pa)) return -1;
      if (Number.isFinite(pb)) return 1;
      return String(a?.created_at || "").localeCompare(String(b?.created_at || ""));
    });
  }, [activeTasks]);
  const laneTasks = useMemo(() => {
    const nonQueued = activeTasks.filter((task: any) => task?.status !== "queued");
    return {
      search: sortMineFirst(nonQueued.filter((task: any) => task?.stage === "search"), me),
      thinking: sortMineFirst(nonQueued.filter((task: any) => task?.stage === "thinking"), me),
      summarizing: sortMineFirst(nonQueued.filter((task: any) => task?.stage === "summarizing"), me),
    };
  }, [activeTasks, me]);
  // mineOnly 开着时,headline 计数用本地过滤后的数(后端 counts 是全局口径,会对不上)。
  const activeTotal = mineOnly && me
    ? activeTasks.length
    : Number(payload?.counts?.active_total ?? activeTasks.length) || 0;
  const queueTotal = mineOnly && me
    ? queuedTasks.length
    : Number(payload?.counts?.queued ?? queuedTasks.length) || 0;
  // payload 现为强类型(TaskQueueResponse),其 speed_light 是 Row(unknown 值);此处局部松绑
  // 成 Record<string, any> 供 React 子节点直接渲染(level/policy 为后端动态字段,非编译期已知形状)。
  const speedLight = (payload?.speed_light || {}) as Record<string, any>;
  const lanes = LANES.map((lane: any) => ({
    ...lane,
    tasks: (laneTasks as any)[lane.key] || [],
  }));
  // 主管裁令(2026-06-12):排队要能看清"前面还有谁"——默认 5 条;点「+N 更多」展开看全部(showAllQueue)。
  const visibleQueue = showAllQueue ? queuedTasks : queuedTasks.slice(0, 5);
  // queueTotal 受后端 LIMIT 50 截断(counts.queued 最多 ~49);展开时以本地实际拿到的 queuedTasks 为准。
  const remainingQueue = Math.max(0, queueTotal - Math.min(visibleQueue.length, queuedTasks.length));
  // 账号分析等队列任务 ~8 秒即完成,若按 session 过滤会"一闪而过"再无痕迹——
  // 即使 payload 暂缺 search_session_id 也保底留在「最近完成」(无 session 时不可点开)。
  const visibleRecent = recentTasks.filter((task) => taskCanOpen(task) || task?.kind === "账号分析").slice(0, 2);
  // 失败桶:失败任务多半不可点开(taskCanOpen=false)且非账号分析,会被 visibleRecent 滤掉,
  // 故单独取一份(cap 4)。recentTasks 已只含 compact 端点的近窗口数据,无需再按时间过滤。
  const failedRecent = recentTasks.filter((task) => task?.status === "failed").slice(0, 4);
  // 乱晃修复:此前挂 !loading,每 2.5s 轮询 loading 翻 true→灰条消失、翻 false→重现,整块上下跳 ~28px。
  // 改挂 !!payload——后台刷新时上次 payload 留存,灰条稳住不闪;仅首次连接(无 payload)隐藏,此时 header 已显「连接中」。
  const emptyActive = activeTotal === 0 && !!payload && !error;

  if (compact) {
    const compactLanes = [
      { key: "search", label: "搜索", tasks: laneTasks.search, color: "var(--ds-accent)" },
      { key: "thinking", label: "思考", tasks: laneTasks.thinking, color: "var(--ds-accent-2)" },
      { key: "summarizing", label: "总结", tasks: laneTasks.summarizing, color: "var(--ds-good)" },
      { key: "queued", label: "排队", tasks: queuedTasks, color: "var(--ds-warn)" },
    ];
    const statusText = activeTotal > 0 ? `${activeTotal} 处理中` : queueTotal > 0 ? `${queueTotal} 排队` : "空闲";

    return e("div", {
      className: "vkpi-task-progress vkpi-task-progress--compact w-full",
      title: "真实任务阶段 · 点击顶栏任务进度可查看明细",
    },
      e("div", { className: "vkpi-task-progress__compact-head" },
        e("span", { className: "vkpi-task-progress__compact-title" }, "任务队列"),
        e("span", { className: `vkpi-task-progress__compact-status ${activeTotal > 0 ? "is-active" : ""}` }, statusText)
      ),
      e("div", { className: "vkpi-task-progress__compact-lanes" },
        compactLanes.map((lane) => {
          const progressValues = lane.tasks
            .map((task: any) => Number(task?.progress_pct ?? task?.progress))
            .filter((value: number) => Number.isFinite(value));
          const progress = progressValues.length
            ? Math.round(progressValues.reduce((sum: number, value: number) => sum + value, 0) / progressValues.length)
            : null;
          const running = lane.tasks.length > 0;
          return e("div", { key: lane.key, className: `vkpi-task-progress__compact-lane ${running ? "is-running" : "is-idle"}` },
            e("span", { className: "vkpi-task-progress__compact-name" }, lane.label),
            e("span", { className: "vkpi-task-progress__compact-bar" },
              e("i", {
                className: progress === null && running ? "is-indeterminate" : "",
                style: {
                  width: progress !== null ? `${Math.max(6, Math.min(100, progress))}%` : running ? "100%" : "34%",
                  background: running ? lane.color : undefined,
                },
              })
            ),
            e("span", { className: "vkpi-task-progress__compact-value" }, progress !== null ? `${progress}` : running ? `${lane.tasks.length}` : "--")
          );
        })
      ),
      e("div", { className: "vkpi-task-progress__compact-foot" },
        loading && !payload ? "连接中" : error && !payload ? "暂不可用" : `真实阶段 · ${queueTotal} 排队`
      )
    );
  }

  return e("div", {
    className: "vkpi-task-progress w-full rounded-xl border border-line bg-card px-3 py-3 shadow-[0_18px_44px_rgba(0,0,0,0.28)]"
  },
    e("div", { className: "mb-3.5 flex items-center justify-between gap-2" },
      e("div", { className: "flex min-w-0 items-center gap-1.5" },
        e(Zap, { size: 15, className: "shrink-0 text-good" }),
        e("span", { className: "truncate text-[13px] font-medium text-ink" }, "任务进度")
      ),
      e("div", { className: "flex shrink-0 items-center gap-1.5" },
        // 「只看我的」开关(身份拉到才显示;记忆到 localStorage)
        me && e("button", {
          type: "button",
          onClick: toggleMineOnly,
          title: mineOnly ? "当前只显示我发起的任务,点击看全部" : "只看我发起的任务",
          className: `rounded border px-1.5 py-0.5 text-[9.5px] font-medium transition-colors ${
            mineOnly
              ? "border-warn bg-warn-soft text-warn"
              : "border-line text-muted hover:text-ink-2"
          }`,
        }, "只看我的"),
        e("span", { className: "text-[11px] text-muted tabular-nums" },
          loading && !payload ? "连接中" : `${activeTotal} 活跃`
        ),
        e("span", {
          className: "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[9.5px] font-medium tabular-nums",
          style: {
            borderColor: `color-mix(in srgb, ${lightColor(speedLight)} 34%, transparent)`,
            color: lightColor(speedLight),
            background: `color-mix(in srgb, ${lightColor(speedLight)} 8%, transparent)`,
          },
          title: speedLight?.policy || "调度速度灯",
        },
          e("span", { className: "h-1.5 w-1.5 rounded-full", style: { background: lightColor(speedLight) } }),
          speedLight?.level || "L1"
        )
      )
    ),
    error && e("div", { className: "mb-2 rounded border border-warn-soft bg-warn-soft px-2 py-1 text-[10px] leading-4 text-warn" },
      payload ? "任务进度连接中 · 保留上次状态" : error
    ),
    emptyActive && e("div", { className: "mb-2 rounded border border-line bg-panel px-2 py-1.5 text-center text-[10.5px] text-muted" },
      "暂无运行中任务"
    ),
    e("div", { className: "flex flex-col gap-2.5" },
      lanes.map((lane) => e(TaskLane, { key: lane.key, lane, tasks: lane.tasks, me }))
    ),
    e("div", { className: "mt-3 border-t border-line pt-2.5" },
      e("div", { className: "flex items-center justify-between gap-2" },
        e("div", { className: "flex min-w-0 items-center gap-1.5" },
          e(Clock3, { size: 12, className: "text-muted" }),
          e("span", { className: "truncate text-[11px] text-muted" }, "排队等待")
        ),
        e("span", { className: "text-[11px] font-medium text-ink-2 tabular-nums" }, queueTotal)
      ),
      e("div", { className: "mt-1.5 flex flex-col gap-1" + (showAllQueue ? " max-h-64 overflow-y-auto pr-1" : "") },
        visibleQueue.length
          ? visibleQueue.map((task, index) => e("button", {
            key: task.id,
            type: "button",
            onClick: taskCanOpen(task) ? () => openTaskOrigin(task) : undefined,
            disabled: !taskCanOpen(task),
            className: "flex min-w-0 items-start gap-1.5 text-left disabled:cursor-default"
          },
            e("span", { className: "w-[18px] shrink-0 text-[10px] text-muted tabular-nums" }, `#${Number.isFinite(Number(task?.queue_position)) ? Number(task.queue_position) : index + 1}`),
            e("span", { className: "min-w-0 flex-1" },
              e("span", { className: "flex min-w-0 items-center gap-1" },
                e("span", { className: "min-w-0 truncate text-[11px] text-ink-2" }, taskLabel(task)),
                initiatorChipNode(task, me)
              ),
              etaHumanText(task) && e("span", { className: "block truncate text-[10px] text-good" }, etaHumanText(task)),
              taskRetryText(task) && e("span", { className: "block truncate text-[10px] text-muted" }, taskRetryText(task))
            )
          ))
          : e("span", { className: "text-[11px] text-muted" }, "—")
      ),
      // 「+N 更多任务」可点展开/收起全部排队(此前是死文字);queueTotal 受后端 LIMIT 50 截断。
      (remainingQueue > 0 || showAllQueue) && (queuedTasks.length > 5) && e("button", {
        type: "button",
        onClick: () => setShowAllQueue((v) => !v),
        className: "mt-1 flex w-full items-center gap-1.5 text-left text-muted hover:text-ink-2 transition-colors"
      },
        showAllQueue
          ? e("span", { className: "text-[10.5px]" }, "收起 ▲")
          : e(React.Fragment, null,
              e("span", { className: "w-[18px] shrink-0 text-[10px] text-muted tabular-nums" }, `+${remainingQueue}`),
              e("span", { className: "truncate text-[11px]" }, `更多任务…(展开看全部 ${queuedTasks.length} 条) ▼`)
            )
      )
    ),
    // 2026-06-12 波5 R7:无 target 的条目不再假装可点 — 按钮禁用、「可打开」标签按实际情况显示
    // 2026-06-15:并入失败桶 — 标题改「最近完成/失败」,失败任务按桶分类并提供重试入口。
    (visibleRecent.length || failedRecent.length) ? e("div", { className: "mt-3 border-t border-line pt-2.5" },
      e("div", { className: "mb-1.5 flex items-center justify-between gap-2" },
        e("span", { className: "text-[11px] text-muted" }, failedRecent.length ? "最近完成/失败" : "最近完成"),
        visibleRecent.some((task) => taskCanOpen(task)) && e("span", { className: "text-[10px] text-good" }, "可打开")
      ),
      visibleRecent.length ? e("div", { className: "flex flex-col gap-1" },
        visibleRecent.map((task) => {
          const canOpen = taskCanOpen(task);
          return e("button", {
            key: `recent-${task.id}`,
            type: "button",
            onClick: canOpen ? () => openTaskOrigin(task) : undefined,
            disabled: !canOpen,
            title: canOpen ? "" : "无可跳转的所属对象",
            className: `flex min-w-0 items-center justify-between gap-2 rounded-md border border-good-soft bg-good-soft px-2 py-1 text-left ${canOpen ? "hover:border-good" : "cursor-default opacity-60"}`
          },
            e("span", { className: "truncate text-[10.5px] text-ink-2" }, taskLabel(task)),
            canOpen && e("span", { className: "shrink-0 text-[10px] text-good" }, "打开")
          );
        })
      ) : null,
      failedRecent.length ? e("div", { className: `flex flex-col gap-1 ${visibleRecent.length ? "mt-1" : ""}` },
        failedRecent.map((task) => {
          const bucket = taskFailBucket(task);
          const st = retrying[task.id];
          return e("div", {
            key: `failed-${task.id}`,
            className: "flex min-w-0 items-center justify-between gap-2 rounded-md border border-crit-soft bg-crit-soft px-2 py-1"
          },
            e("div", { className: "flex min-w-0 items-center gap-1.5" },
              e("span", {
                className: "shrink-0 rounded px-1 py-0.5 text-[9px] font-medium",
                style: { color: bucket.tone, background: `${bucket.tone}1a`, borderColor: `${bucket.tone}33` }
              }, bucket.label),
              e("span", { className: "truncate text-[10.5px] text-ink-2", title: task.error || "" }, taskLabel(task))
            ),
            e("button", {
              type: "button",
              disabled: st === "loading" || st === "done",
              onClick: () => handleRetry(task),
              className: `shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium ${st === "done" ? "border-good-soft text-good cursor-default" : st === "loading" ? "border-line text-muted cursor-wait" : "border-crit-soft text-crit hover:bg-crit-soft"}`,
              title: typeof st === "string" && st !== "loading" && st !== "done" ? st : ""
            }, st === "loading" ? "重试中…" : st === "done" ? "已重排" : "重试")
          );
        })
      ) : null
    ) : null
  );
}
