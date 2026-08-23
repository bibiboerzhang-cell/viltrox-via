import { useCallback, useEffect, useRef, useState } from "react";
import { listStaffGroups, toUiGroup } from "../../../../services/vkpi/groups-api";

// 员工分组(staff-groups)状态 + 观察清单分组入口 —— 从 CockpitApp 抽出以守千行卫兵,行为不变。

/** 真 staff-groups 列表(替代曾经写死的 "KOL Operations"):apiToken 就绪即拉;refresh 供增删改后重拉。 */
export function useStaffGroups(apiToken: string | undefined) {
  const [staffGroups, setStaffGroups] = useState<any[]>([]);
  const refreshStaffGroups = useCallback(async () => {
    if (!apiToken) return;
    try {
      const res = await listStaffGroups(apiToken);
      setStaffGroups((res.items || []).map(toUiGroup));
      // 波 C·C3:分组增删改后广播,MY KOL「观察清单」等按分组取数的模块据此重读。
      window.dispatchEvent(new CustomEvent("vkpi:staff-groups-changed"));
    } catch (err) {
      setStaffGroups([]);
    }
  }, [apiToken]);
  useEffect(() => { refreshStaffGroups(); }, [refreshStaffGroups]);
  return { staffGroups, refreshStaffGroups };
}

// 波 C·C3:MY KOL「观察清单」模块的分组管理入口(入口与分组管理同处)。
// 监听 window 事件 vkpi:open-team-groups:
//   detail.mode="new" → 直接开新建分组编辑器;
//   detail.groupId    → 在当前 staffGroups 里找到该组 → 开该组编辑器;
//   其余 / 找不到     → 打开团队浮层(分组列表 / 新建 / 编辑 / 删除都在那里)。
// openGroupEditor 在 CockpitApp 里每帧新建闭包,staffGroups 也每次刷新换引用 —— 两者都走 ref,
// 监听只挂一次,行为与原内联版本完全一致(从 CockpitApp 抽出以守千行卫兵)。

export interface TeamGroupsOpenDetail {
  mode?: string;
  groupId?: string;
}

export interface UseTeamGroupsOpenerOptions {
  staffGroups: any[];
  openGroupEditor: (mode?: string, group?: any) => void;
  openTeamModal: () => void;
}

export function useTeamGroupsOpener({ staffGroups, openGroupEditor, openTeamModal }: UseTeamGroupsOpenerOptions): void {
  const staffGroupsRef = useRef<any[]>(staffGroups);
  staffGroupsRef.current = staffGroups;
  const openGroupEditorRef = useRef(openGroupEditor);
  openGroupEditorRef.current = openGroupEditor;
  const openTeamModalRef = useRef(openTeamModal);
  openTeamModalRef.current = openTeamModal;

  useEffect(() => {
    const onOpenTeamGroups = (event: Event) => {
      const detail = ((event as CustomEvent)?.detail || {}) as TeamGroupsOpenDetail;
      if (detail.mode === "new") {
        openGroupEditorRef.current("new");
        return;
      }
      if (detail.groupId) {
        const target = staffGroupsRef.current.find((g: any) => String(g?.id) === String(detail.groupId));
        if (target) {
          openGroupEditorRef.current("edit", target);
          return;
        }
      }
      openTeamModalRef.current();
    };
    window.addEventListener("vkpi:open-team-groups", onOpenTeamGroups);
    return () => window.removeEventListener("vkpi:open-team-groups", onOpenTeamGroups);
  }, []);
}
