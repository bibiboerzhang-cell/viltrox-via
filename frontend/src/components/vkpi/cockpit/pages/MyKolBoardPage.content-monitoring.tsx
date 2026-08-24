import React from "react";
import {
  enableMyKolContentMonitoring,
  getMyKolContentMonitoring,
  pauseMyKolContentMonitoring,
  type VkpiKolContentMonitoringResponse,
} from "../../../../services/vkpi/myKolBoard-api";
import { SectionLabel } from "./MarketVoicePage.dialogs";
import { ReceiptLine } from "./MyKolBoardPage.receipt";
import type { FlowReceipt } from "../../pages/myKol/PoolEvidenceContent.helpers";

const ACTION =
  "inline-flex min-h-8 items-center justify-center rounded-lg border border-line px-2.5 py-1 text-[10.5px] font-medium text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent disabled:cursor-default disabled:text-muted disabled:hover:border-line disabled:hover:bg-transparent";
const FIELD = "min-h-8 rounded-lg border border-line bg-card px-2 py-1 text-[10.5px] text-ink-2 outline-none focus:border-accent";

function detail(err: unknown, fallback: string): string {
  return String((err as { detail?: unknown; message?: unknown })?.detail || (err as Error)?.message || fallback).slice(0, 120);
}
function stamp(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) return "—";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw.replace("T", " ").slice(0, 16);
  return parsed.toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function jobLabel(value: unknown): string {
  const status = String(value || "").trim().toLowerCase();
  const labels: Record<string, string> = {
    queued: "已排队",
    already_queued: "已在队列",
    running: "运行中",
    retrying: "重试中",
    done: "已完成",
    blocked: "被闸门拦截",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] || (status ? status : "尚无任务");
}

export function KolContentMonitoringSection({
  apiToken,
  kolPoolId,
  paidActionsReadOnly,
  paidActionsReadOnlyHint,
}: {
  apiToken: string;
  kolPoolId: number;
  paidActionsReadOnly: boolean;
  paidActionsReadOnlyHint: string;
}) {
  const [data, setData] = React.useState<VkpiKolContentMonitoringResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [cadenceHours, setCadenceHours] = React.useState(24);
  const [receipt, setReceipt] = React.useState<FlowReceipt | null>(null);
  const requestRef = React.useRef(0);

  const load = React.useCallback(async (silent = false) => {
    if (!apiToken || !kolPoolId) return null;
    const requestId = ++requestRef.current;
    if (!silent) setLoading(true);
    setError("");
    try {
      const response = await getMyKolContentMonitoring(apiToken, kolPoolId);
      if (requestRef.current !== requestId) return null;
      setData(response && typeof response === "object" ? response : null);
      const cadence = Number(response?.subscription?.cadence_hours);
      if (Number.isFinite(cadence) && cadence >= 6 && cadence <= 168) setCadenceHours(cadence);
      return response;
    } catch (err) {
      if (requestRef.current === requestId) setError(detail(err, "内容跟进状态读取失败"));
      return null;
    } finally {
      if (requestRef.current === requestId) setLoading(false);
    }
  }, [apiToken, kolPoolId]);

  React.useEffect(() => {
    setData(null);
    setError("");
    setReceipt(null);
    setBusy(false);
    setCadenceHours(24);
    void load();
    return () => {
      requestRef.current += 1;
    };
  }, [load]);

  const subscription = data?.subscription || null;
  const ownSubscription = data?.own_subscription === true;
  const ownActive = ownSubscription && subscription?.status === "active";
  const schedulerEnabled = data?.scheduler?.enabled;
  // 写权限必须同时由 viewer-context 与本端点真值确认；任一未加载/失败都 fail closed。
  const canWrite = !paidActionsReadOnly && data?.can_enable_or_pause_own === true;

  const runAction = async (action: "enable" | "pause") => {
    if (!canWrite || busy) return;
    setBusy(true);
    setReceipt(null);
    try {
      const response = action === "enable"
        ? await enableMyKolContentMonitoring(apiToken, kolPoolId, cadenceHours)
        : await pauseMyKolContentMonitoring(apiToken, kolPoolId);
      const allowed = action === "enable"
        ? new Set(["enabled", "resumed", "already_active"])
        : new Set(["paused", "already_paused", "not_subscribed"]);
      const status = String(response?.status || "");
      if (!allowed.has(status)) {
        setReceipt({ text: `服务端未确认${action === "enable" ? "开启" : "暂停"}：${status || "未知状态"}`, tone: "error" });
        return;
      }
      const current = await load(true);
      if (!current) {
        setReceipt({ text: `${action === "enable" ? "订阅登记" : "暂停请求"}已获端点确认，但最新状态重读失败；请点“重读”核实。`, tone: "info" });
        return;
      }
      setReceipt(action === "enable"
        ? { text: "订阅已登记；本次没有直接调用平台。后台巡检运行后，“上次成功”才会更新。", tone: "ok" }
        : { text: "本人订阅已暂停；旧在途任务会由服务端代际闸失效。", tone: "ok" });
    } catch (err) {
      setReceipt({ text: `${action === "enable" ? "开启" : "暂停"}失败：${detail(err, "请重试")}`, tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const windowSize = Number(subscription?.window?.max_posts) || 12;
  const statusText = ownActive
    ? "本人已订阅"
    : ownSubscription
      ? "本人已暂停"
      : subscription?.status === "active"
        ? "团队已有订阅（共享只读）"
        : "尚未订阅";

  return (
    <div className="mb-[22px]">
      <div className="flex flex-wrap items-center gap-2">
        <SectionLabel>内容跟进</SectionLabel>
        {data ? (
          <span className={`mb-2 rounded-full border px-2 py-0.5 text-[9.5px] font-medium ${ownActive ? "border-good bg-good-soft text-good" : "border-line text-muted"}`}>
            {statusText}
          </span>
        ) : null}
        <button type="button" className="mb-2 ml-auto text-[9.5px] text-muted transition-colors hover:text-ink" onClick={() => { setReceipt(null); void load(); }} disabled={loading || busy}>
          {loading ? "读取中…" : "重读"}
        </button>
      </div>

      {error ? (
        <div className="rounded-lg border border-crit bg-crit-soft px-3 py-2 text-[11px] text-crit">
          内容跟进状态读取失败：{error}。为避免误操作，开启/暂停已锁定。
        </div>
      ) : !data ? (
        <div className="py-2 text-[11px] text-muted">{loading ? "内容跟进状态读取中…" : "暂无可核实状态"}</div>
      ) : (
        <div className="rounded-[10px] border border-line bg-panel px-3 py-2.5">
          <div className="grid gap-x-4 gap-y-1 text-[10.5px] leading-5 text-ink-2 sm:grid-cols-2">
            <span>频率：{subscription?.cadence_hours ? `每 ${subscription.cadence_hours} 小时` : "未设置"}</span>
            <span>下次到期：{stamp(subscription?.next_due_at)}</span>
            <span>最近入队：{stamp(subscription?.last_enqueued_at)}</span>
            <span>上次成功：{stamp(subscription?.last_success_at)}</span>
            <span>最近任务：{jobLabel(subscription?.last_job_status)}</span>
            <span>后台巡检：{schedulerEnabled === true ? "已开启" : schedulerEnabled === false ? "未开启" : "状态不可用"}</span>
          </div>
          <div className="mt-1.5 text-[10px] leading-4 text-muted">
            范围仅限最近 {windowSize} 条内容，不代表频道完整历史。订阅登记 ≠ 已抓取；以“上次成功”和已采集内容为准。
          </div>
          {schedulerEnabled !== true ? (
            <div className="mt-2 rounded-lg border border-warn bg-warn-soft px-2.5 py-1.5 text-[10px] leading-4 text-ink-2">
              {schedulerEnabled === false ? "后台巡检当前未开启" : "后台巡检状态暂不可核实"}，即使订阅为 active 也不能宣称已经自动补采。
            </div>
          ) : null}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {!ownActive ? (
              <>
                <label className="text-[10px] text-muted" htmlFor={`content-monitor-cadence-${kolPoolId}`}>跟进频率</label>
                <select
                  id={`content-monitor-cadence-${kolPoolId}`}
                  aria-label="内容跟进频率"
                  className={FIELD}
                  value={cadenceHours}
                  disabled={!canWrite || busy}
                  onChange={(event) => setCadenceHours(Number(event.target.value))}
                >
                  <option value={12}>每 12 小时</option>
                  <option value={24}>每天</option>
                  <option value={48}>每 2 天</option>
                  <option value={168}>每周</option>
                </select>
                <button type="button" className={ACTION} disabled={!canWrite || busy} title={!canWrite ? paidActionsReadOnlyHint : "显式登记本人订阅；不会在本请求内调用平台"} onClick={() => { void runAction("enable"); }}>
                  {busy ? "提交中…" : ownSubscription ? "恢复跟进" : "开启我的跟进"}
                </button>
              </>
            ) : (
              <button type="button" className={ACTION} disabled={!canWrite || busy} title={!canWrite ? paidActionsReadOnlyHint : "暂停本人订阅并使旧在途任务失效"} onClick={() => { void runAction("pause"); }}>
                {busy ? "提交中…" : "暂停我的跟进"}
              </button>
            )}
          </div>
          <ReceiptLine msg={receipt} />
        </div>
      )}
    </div>
  );
}
