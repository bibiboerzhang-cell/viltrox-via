import React from "react";
import EventDetailView from "../../pages/events/pages/EventDetailView";
import NewEventModal, { type NewEventSubmit } from "../../pages/events/modals/NewEventModal";
import DeleteConfirmModal from "../../pages/events/modals/DeleteConfirmModal";
import StockManagerModal from "../../pages/events/modals/StockManagerModal";
import type { toUiEvent } from "../../../../services/vkpi/events-api";
import type { CurrentUserVm, EventVm, StockItem, UiStaff } from "../../pages/events/shared/types";

// Events · 详情/表单 embeds 包装族(MyKolBoardPage.embeds 同手法:复杂 tab/表单
//   非侵入收编,pages/events 旧组件文件零改动)。
//   EventDetailTakeover = 旧 EventsPage 选中活动后的整页接管(详情七 tab:概览 /
//   预算+费用 / 任务 / 参与 KOL / 物料(营销物料+产品准备)/ 现场(诚实未排期占位)/
//   复盘,连同 编辑活动 / 删除活动 两弹窗)—— 交互与数据通路 1:1 平移,详情内
//   全部 CRUD(任务/费用/邀约/物料/产品/复盘/团队/分享)照旧走 events-api 真后端。
//   旧组件硬编码 slate/hex 由 cockpit-reference.css 通用换肤层(非 dashboard 页
//   [class*=…] token 重映射)接管,无需 trim 选择器 —— 与 MyKol 不同,events 旧件
//   没有第二套面板皮肤,零 !important 对抗。
//   EventsBoardModals = 看板层两只旧弹窗(新建活动 / 公司库存管理)原样透传。
// 红线:本文件零直连网络(取数/落库全在旧组件与 page 层);不触 viltrox_fit_score /
//   rule_v0;不改 pages/events 任何文件(旧 EventsPage 保留为回滚垫)。

export interface DetailTakeoverProps {
  ev: EventVm;
  currentUser: CurrentUserVm;
  staff: UiStaff[];
  token: string;
  stock: StockItem[];
  setStock: React.Dispatch<React.SetStateAction<StockItem[]>>;
  projectOptions: Array<Record<string, any>>;
  onBack: () => void;
  /** 编辑弹窗保存 → 页层 handleUpdateEvent(乐观 + PATCH 落库,旧页同款) */
  onUpdateEvent: (updated: EventVm) => void;
  /** 删除确认 → 页层 handleDeleteEvent(乐观 + DELETE 落库,旧页同款) */
  onDeleteEvent: (id: string) => void;
  /** 团队增删 → 页层乐观 + PATCH team_ids 落库(旧页同款) */
  onUpdateTeam: (teamIds: string[]) => void;
  /** 复盘 tab 落库后回传真值 → 页层同步列表行 */
  onEventPatched: (uiRow: ReturnType<typeof toUiEvent>) => void;
}

export function EventDetailTakeover({
  ev,
  currentUser,
  staff,
  token,
  stock,
  setStock,
  projectOptions,
  onBack,
  onUpdateEvent,
  onDeleteEvent,
  onUpdateTeam,
  onEventPatched,
}: DetailTakeoverProps) {
  const [editing, setEditing] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  return (
    <>
      <EventDetailView
        ev={ev}
        currentUser={currentUser}
        staff={staff}
        token={token}
        onBack={onBack}
        onEdit={() => setEditing(true)}
        onDelete={() => setDeleting(true)}
        onUpdateTeam={onUpdateTeam}
        onEventPatched={onEventPatched}
        stock={stock}
        setStock={setStock}
      />
      {editing && (
        <NewEventModal
          initialData={ev}
          teamOptions={staff}
          projects={projectOptions}
          onClose={() => setEditing(false)}
          onSubmit={(data: NewEventSubmit) => {
            setEditing(false);
            // 旧 EventsPage 编辑提交映射 1:1:表单扁平字段 → EventVm 嵌套形态
            onUpdateEvent({
              ...ev,
              ...data,
              budgetTotal: data.budget,
              location: { ...ev.location, name: data.locName, city: data.city, country: data.country },
              teamUserIds: data.teamIds,
              relatedProjectIds: data.projectIds,
            } as EventVm);
          }}
        />
      )}
      {deleting && (
        <DeleteConfirmModal
          title={`删除 "${ev.title}"?`}
          subtitle="所有费用 / 任务 / 物料数据将一起删除 · 不可撤销"
          onClose={() => setDeleting(false)}
          onConfirm={() => {
            setDeleting(false);
            onDeleteEvent(ev.id);
          }}
        />
      )}
    </>
  );
}

/* ============ 看板层旧弹窗透传(新建活动 / 公司库存) ============ */

export function EventsBoardModals({
  showNew,
  onCloseNew,
  onCreate,
  staff,
  currentUserId,
  projectOptions,
  showStock,
  onCloseStock,
  stock,
  setStock,
  token,
}: {
  showNew: boolean;
  onCloseNew: () => void;
  onCreate: (data: NewEventSubmit) => void;
  staff: UiStaff[];
  currentUserId: string;
  projectOptions: Array<Record<string, any>>;
  showStock: boolean;
  onCloseStock: () => void;
  stock: StockItem[];
  setStock: React.Dispatch<React.SetStateAction<StockItem[]>>;
  token: string;
}) {
  return (
    <>
      {showNew && (
        <NewEventModal
          teamOptions={staff}
          currentUserId={currentUserId}
          projects={projectOptions}
          onClose={onCloseNew}
          onSubmit={onCreate}
        />
      )}
      {showStock && <StockManagerModal stock={stock} setStock={setStock} token={token} onClose={onCloseStock} />}
    </>
  );
}
