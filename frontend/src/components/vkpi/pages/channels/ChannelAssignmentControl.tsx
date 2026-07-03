// C7-A3 官方账号分配控件:矩阵页选中账号后,显示/调整「哪个成员负责这个官号」。
// 数据源 = /api/admin/vkpi/channels/assignments(vkpi_channel_assignments,迁移 209);
// owner/管理层看到下拉可改(后端 PUT 硬拦非管理层),成员只读展示负责人。
// 模块级快照缓存 + 订阅广播:矩阵页控件与 WorkspacePage 成员工作台共用同一份数据,不重复拉。
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getChannelAssignments,
  putChannelAssignment,
  type VkpiChannelAssignmentRow,
  type VkpiChannelAssignmentsResponse,
  type VkpiChannelAssignmentStaffOption,
} from '../../../../services/vkpi/channel-api';
import { formatLocal } from '../../lib/timeLocal';
import type { OfficialChannelAccount } from './channelTypes';
import './channelAssignment.css';

const ASSIGNMENTS_STALE_MS = 30_000;
const DEFAULT_ROLE = 'owner';

export type ChannelAssignmentsSnapshot = {
  key: string;
  fetchedAt: number;
  available: boolean;
  canManage: boolean;
  meStaffId: number | null;
  assignments: VkpiChannelAssignmentRow[];
  staffOptions: VkpiChannelAssignmentStaffOption[];
};

let snapshot: ChannelAssignmentsSnapshot | null = null;
let inflight: Promise<ChannelAssignmentsSnapshot> | null = null;
const listeners = new Set<() => void>();

function cacheKey(apiToken?: string) {
  return apiToken ? apiToken.slice(-10) : 'no-token';
}

function notify() {
  listeners.forEach((listener) => listener());
}

function mapSnapshot(key: string, payload: VkpiChannelAssignmentsResponse): ChannelAssignmentsSnapshot {
  return {
    key,
    fetchedAt: Date.now(),
    available: payload.available !== false,
    canManage: Boolean(payload.can_manage),
    meStaffId: typeof payload.me_staff_id === 'number' ? payload.me_staff_id : null,
    assignments: Array.isArray(payload.assignments) ? payload.assignments : [],
    staffOptions: Array.isArray(payload.staff_options) ? payload.staff_options : [],
  };
}

function loadSnapshot(apiToken: string, force = false): Promise<ChannelAssignmentsSnapshot> {
  const key = cacheKey(apiToken);
  if (!force && snapshot && snapshot.key === key && Date.now() - snapshot.fetchedAt < ASSIGNMENTS_STALE_MS) {
    return Promise.resolve(snapshot);
  }
  if (inflight) return inflight;
  inflight = getChannelAssignments(apiToken)
    .then((payload) => {
      snapshot = mapSnapshot(key, payload);
      notify();
      return snapshot;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

function assignmentRole(row: VkpiChannelAssignmentRow): string {
  return String(row.role || DEFAULT_ROLE);
}

// 共享 hook:矩阵页分配控件 + 成员工作台「我负责的官号」共用。
export function useChannelAssignments(apiToken?: string) {
  const [, setTick] = useState(0);
  const [loading, setLoading] = useState(() => !snapshot);
  const [error, setError] = useState('');

  useEffect(() => {
    const listener = () => setTick((value) => value + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  useEffect(() => {
    if (!apiToken) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(!snapshot);
    setError('');
    loadSnapshot(apiToken)
      .catch((requestError) => {
        if (!cancelled) setError(requestError instanceof Error ? requestError.message : '分配数据加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  const setAssignment = useCallback(
    async (channelId: number, staffId: number | null) => {
      if (!apiToken) throw new Error('未登录，无法调整分配。');
      const previous = snapshot;
      // 乐观更新:先改本地快照立即反馈,PUT 失败整体回滚
      if (snapshot) {
        const staffOption = snapshot.staffOptions.find((option) => Number(option.id) === Number(staffId));
        const rest = snapshot.assignments.filter(
          (row) => !(Number(row.channel_id) === channelId && assignmentRole(row) === DEFAULT_ROLE),
        );
        snapshot = {
          ...snapshot,
          assignments: staffId
            ? [
                ...rest,
                {
                  channel_id: channelId,
                  staff_id: staffId,
                  role: DEFAULT_ROLE,
                  staff_name: staffOption?.name || `Staff ${staffId}`,
                },
              ]
            : rest,
        };
        notify();
      }
      try {
        await putChannelAssignment(apiToken, channelId, staffId, DEFAULT_ROLE);
        await loadSnapshot(apiToken, true);
      } catch (requestError) {
        snapshot = previous;
        notify();
        throw requestError;
      }
    },
    [apiToken],
  );

  return {
    loading,
    error,
    available: snapshot ? snapshot.available : false,
    canManage: snapshot ? snapshot.canManage : false,
    meStaffId: snapshot ? snapshot.meStaffId : null,
    assignments: snapshot ? snapshot.assignments : ([] as VkpiChannelAssignmentRow[]),
    staffOptions: snapshot ? snapshot.staffOptions : ([] as VkpiChannelAssignmentStaffOption[]),
    setAssignment,
  };
}

export function ChannelAssignmentControl({ account, apiToken }: { account: OfficialChannelAccount; apiToken?: string }) {
  const { loading, error, available, canManage, meStaffId, assignments, staffOptions, setAssignment } =
    useChannelAssignments(apiToken);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');

  const current = useMemo(
    () =>
      assignments.find(
        (row) => Number(row.channel_id) === Number(account.id) && assignmentRole(row) === DEFAULT_ROLE,
      ),
    [assignments, account.id],
  );

  if (!apiToken) return null;

  const handleChange = async (value: string) => {
    setSaveError('');
    setSaving(true);
    try {
      await setAssignment(Number(account.id), value ? Number(value) : null);
    } catch (requestError) {
      setSaveError(requestError instanceof Error ? requestError.message : '分配保存失败');
    } finally {
      setSaving(false);
    }
  };

  const currentName = current ? current.staff_name || `Staff ${current.staff_id}` : '';
  const isMine = current && meStaffId != null && Number(current.staff_id) === Number(meStaffId);

  return (
    <div className="mykol-assignment" aria-label="官方账号负责成员分配">
      <span className="mykol-assignment__label">负责成员</span>
      {canManage ? (
        <select
          className="mykol-assignment__select"
          value={current?.staff_id ? String(current.staff_id) : ''}
          disabled={saving || (loading && !assignments.length) || !available}
          onChange={(event) => {
            void handleChange(event.target.value);
          }}
        >
          <option value="">未分配</option>
          {staffOptions.map((option) => (
            <option key={String(option.id)} value={String(option.id)}>
              {option.name || `Staff ${option.id}`}
            </option>
          ))}
        </select>
      ) : (
        <b className={`mykol-assignment__value ${current ? 'is-assigned' : ''}`}>
          {current ? `${currentName}${isMine ? '（我）' : ''}` : '未分配'}
        </b>
      )}
      {saving ? <em>保存中…</em> : null}
      {!saving && current?.assigned_at ? <em>分配于 {formatLocal(current.assigned_at)}</em> : null}
      {saveError || (error && !assignments.length) ? (
        <i className="mykol-assignment__error">{saveError || error}</i>
      ) : null}
      {!available && !loading && !error ? (
        <i className="mykol-assignment__error">分配表未启用（迁移 209 未应用），暂为只读。</i>
      ) : null}
    </div>
  );
}
