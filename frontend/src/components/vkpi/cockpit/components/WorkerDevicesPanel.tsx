// W4 本地算力节点面板:设备卡(名字/平台/在线小点[last_seen_at 5min 窗]/当前任务/信任级/最近错误)
// + 最近 10 条租约小表(leased/submitted/validated/expired)+ 按 task_type 分组计数。
// 数据:GET /api/admin/vkpi/local-workers/board(W4 聚合端点,一次请求喂面板,30s 轮询);
//      board 不可用时回退 GET /api/admin/vkpi/local-workers/devices(W1 端点,租约表安静缺席)。
// 诚实态:表未建/无节点 → 空态「尚无本地节点,启动方式见 tools/local_worker/README」;
//        接口失败 → 显示「读取失败」不编数字。红线:纯展示,零触 viltrox_fit_score/rule_v0。
// U2:在线小点改为 FreshnessDot 呼吸灯(在线=绿轻呼吸/离线=灰静态,语义与 title 零变;
//     reduced-motion 降级为静态,见 ui/motion.css)。
import React from "react";
import { AlertTriangle, Cpu, Loader2 } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { relativeFromNow } from "../../lib/timeLocal";
import { FreshnessDot } from "./ui/FreshnessDot";

const ONLINE_WINDOW_MS = 5 * 60 * 1000;
const POLL_INTERVAL_MS = 30 * 1000;
const BOARD_PATH = "/api/admin/vkpi/local-workers/board";
const DEVICES_PATH = "/api/admin/vkpi/local-workers/devices";

type BoardCurrentTask = {
  lease_id?: number;
  job_id?: number | null;
  task_type?: string;
  issued_at?: string | null;
  expires_at?: string | null;
};

type BoardLastError = {
  lease_id?: number;
  task_type?: string;
  error_code?: string;
  notes?: string;
  at?: string | null;
};

type BoardDevice = {
  id?: number;
  device_id?: string;
  device_name?: string;
  platform?: string;
  staff_id?: number | null;
  status?: string;
  trust_level?: number;
  last_seen_at?: string | null;
  online?: boolean;
  current_task?: BoardCurrentTask | null;
  last_error?: BoardLastError | null;
  lease_stats?: Record<string, number>;
};

type BoardLease = {
  id?: number;
  job_id?: number | null;
  device_id?: string;
  device_name?: string;
  task_type?: string;
  status?: string;
  effective_status?: string;
  issued_at?: string | null;
  submitted_at?: string | null;
  result_validated?: number;
  error_code?: string | null;
};

type BoardTypeCount = {
  task_type?: string;
  total?: number;
  by_status?: Record<string, number>;
};

type BoardResp = {
  status?: string;
  reason?: string;
  online_window_minutes?: number;
  devices?: BoardDevice[];
  recent_leases?: BoardLease[];
  task_type_counts?: BoardTypeCount[];
  totals?: { devices?: number; online?: number; active_leases?: number };
};

interface WorkerDevicesPanelProps {
  apiToken?: string;
}

function isOnline(d: BoardDevice): boolean {
  if (typeof d.online === "boolean") return d.online;
  if (!d.last_seen_at) return false;
  const seen = new Date(d.last_seen_at).getTime();
  return Number.isFinite(seen) && Date.now() - seen <= ONLINE_WINDOW_MS;
}

const LEASE_STATUS_STYLE: Record<string, string> = {
  leased: "bg-sky-500/10 text-sky-200",
  submitted: "bg-amber-500/10 text-amber-200",
  validated: "bg-emerald-500/10 text-emerald-200",
  expired: "bg-rose-500/10 text-rose-300",
  rejected: "bg-rose-500/10 text-rose-300",
};

const LEASE_STATUS_LABEL: Record<string, string> = {
  leased: "在租",
  submitted: "已提交",
  validated: "已校验",
  expired: "已过期",
  rejected: "已拒收",
};

function LeaseStatusChip({ status }: { status?: string }) {
  const key = String(status || "").toLowerCase() || "leased";
  return (
    <span className={"rounded px-1.5 py-0.5 text-[9px] " + (LEASE_STATUS_STYLE[key] || "bg-slate-500/10 text-slate-300")}>
      {LEASE_STATUS_LABEL[key] || key}
    </span>
  );
}

// W1 /devices 回退:响应形状防御归一(数组 或 {devices|items:[...]})。
function normalizeDevicesFallback(payload: unknown): BoardResp {
  const obj = (payload && typeof payload === "object" ? payload : {}) as Record<string, unknown>;
  const list = Array.isArray(payload)
    ? payload
    : Array.isArray(obj.devices)
      ? obj.devices
      : Array.isArray(obj.items)
        ? obj.items
        : [];
  return { status: "ready", devices: list as BoardDevice[], recent_leases: [], task_type_counts: [] };
}

function DeviceCard({ device }: { device: BoardDevice }) {
  const online = isOnline(device);
  const task = device.current_task;
  const err = device.last_error;
  const stats = device.lease_stats || {};
  const validated = Number(stats.validated) || 0;
  return (
    <div className="rounded-lg border border-white/[0.06] bg-black/20 px-2.5 py-2">
      <div className="flex items-center gap-1.5">
        {/* U2 呼吸灯:在线=绿轻呼吸(调用方 5min 心跳判定,state 显式覆盖);离线=灰静态(常态非事故) */}
        <FreshnessDot
          state={online ? "fresh" : "stale"}
          staleTone="muted"
          ts={device.last_seen_at}
          title={online ? "在线(5 分钟内有心跳)" : "离线(5 分钟内无心跳)"}
        />
        <span className="truncate text-[11px] font-medium text-slate-200" title={device.device_id || ""}>
          {device.device_name || device.device_id || "(未命名节点)"}
        </span>
        {device.platform ? (
          <span className="shrink-0 rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] text-slate-400">
            {device.platform}
          </span>
        ) : null}
        <span className="ml-auto shrink-0 rounded bg-purple-500/10 px-1.5 py-0.5 text-[9px] text-purple-200" title="信任级别">
          T{Number(device.trust_level) || 0}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9.5px] text-slate-500">
        <span>
          {device.last_seen_at ? "心跳 " + relativeFromNow(device.last_seen_at) : "尚无心跳"}
        </span>
        {validated > 0 ? <span className="text-emerald-300/80">已校验 ×{validated}</span> : null}
      </div>
      {task ? (
        <div className="mt-1 truncate rounded bg-sky-400/[0.06] border border-sky-300/10 px-1.5 py-1 text-[9.5px] text-sky-100/90">
          在跑:{task.task_type || "(未知任务)"}
          {task.job_id != null ? " · job #" + task.job_id : ""}
        </div>
      ) : (
        <div className="mt-1 text-[9.5px] text-slate-600">空闲(无在租任务)</div>
      )}
      {err && err.error_code ? (
        <div className="mt-1 flex items-start gap-1 text-[9.5px] text-rose-300/90">
          <AlertTriangle size={9} className="mt-0.5 shrink-0" />
          <span className="truncate" title={err.notes || err.error_code}>
            最近错误:{err.error_code}
            {err.task_type ? " @ " + err.task_type : ""}
            {err.at ? " · " + relativeFromNow(err.at) : ""}
          </span>
        </div>
      ) : null}
    </div>
  );
}

export function WorkerDevicesPanel({ apiToken = "" }: WorkerDevicesPanelProps) {
  const [data, setData] = React.useState<BoardResp | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [errored, setErrored] = React.useState(false);

  React.useEffect(() => {
    if (!apiToken) {
      setData(null);
      setErrored(false);
      return;
    }
    let cancelled = false;
    const load = () => {
      setLoading(true);
      apiFetch<BoardResp>(BOARD_PATH, {}, apiToken)
        .then((payload) => {
          if (cancelled) return;
          setData(payload && typeof payload === "object" ? payload : null);
          setErrored(false);
        })
        .catch(() => {
          // board 未接线时回退 W1 /devices(租约小表安静缺席)
          apiFetch<unknown>(DEVICES_PATH, {}, apiToken)
            .then((payload) => {
              if (cancelled) return;
              setData(normalizeDevicesFallback(payload));
              setErrored(false);
            })
            .catch(() => {
              if (cancelled) return;
              setData(null);
              setErrored(true);
            });
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    };
    load();
    const timer = window.setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [apiToken]);

  if (!apiToken) return null;

  const devices = Array.isArray(data?.devices) ? data!.devices! : [];
  const leases = (Array.isArray(data?.recent_leases) ? data!.recent_leases! : []).slice(0, 10);
  const typeCounts = Array.isArray(data?.task_type_counts) ? data!.task_type_counts! : [];
  const onlineCount = devices.filter(isOnline).length;
  const empty = !errored && devices.length === 0;

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 backdrop-blur-xl">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu size={14} className={onlineCount > 0 ? "text-emerald-300" : "text-slate-500"} />
          <h3 className="text-sm font-semibold text-white">本地算力节点</h3>
          {devices.length > 0 ? (
            <span className="rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] tabular-nums text-slate-400">
              在线 {onlineCount}/{devices.length}
            </span>
          ) : null}
        </div>
        {loading ? <Loader2 size={12} className="animate-spin text-slate-500" /> : null}
      </div>

      {errored ? (
        <div className="text-[10px] leading-relaxed text-amber-300/90">
          节点看板读取失败(接口不可用),稍后自动重试
        </div>
      ) : empty ? (
        <div className="text-[10px] leading-relaxed text-slate-500">
          尚无本地节点,启动方式见 tools/local_worker/README
          {data?.status === "empty" && data?.reason ? (
            <div className="mt-1 text-[9px] text-slate-600">{data.reason}</div>
          ) : null}
        </div>
      ) : (
        <>
          <div className="space-y-1.5">
            {devices.slice(0, 8).map((d, i) => (
              <DeviceCard key={d.device_id || String(i)} device={d} />
            ))}
            {devices.length > 8 ? (
              <div className="text-[9px] text-slate-600">另有 {devices.length - 8} 台节点未展开</div>
            ) : null}
          </div>

          {typeCounts.length > 0 ? (
            <div className="mt-2 flex flex-wrap items-center gap-1">
              <span className="text-[9px] text-slate-500">任务类型:</span>
              {typeCounts.slice(0, 6).map((t, i) => (
                <span
                  key={t.task_type || String(i)}
                  className="rounded border border-white/[0.06] bg-black/20 px-1.5 py-0.5 text-[9px] text-slate-300"
                  title={Object.entries(t.by_status || {})
                    .map(([k, v]) => (LEASE_STATUS_LABEL[k] || k) + " " + v)
                    .join(" / ")}
                >
                  {t.task_type} ×{Number(t.total) || 0}
                </span>
              ))}
            </div>
          ) : null}

          {leases.length > 0 ? (
            <div className="mt-2">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">最近租约</div>
              <div className="space-y-1">
                {leases.map((l, i) => (
                  <div key={l.id ?? i} className="flex items-center gap-1.5 text-[9.5px]">
                    <LeaseStatusChip status={l.effective_status || l.status} />
                    <span className="truncate text-slate-300" title={l.device_id || ""}>
                      {l.task_type || "(未知任务)"}
                    </span>
                    <span className="truncate text-slate-600">{l.device_name || l.device_id || ""}</span>
                    <span className="ml-auto shrink-0 tabular-nums text-slate-600">
                      {l.issued_at ? relativeFromNow(l.issued_at) : ""}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
