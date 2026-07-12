import React from "react";
import { ProjectDueListCard } from "../../pages/projects/ProjectDueListCard";
import { MODULE_SOURCES } from "./ProjectsBoardPage.embeds";
import { XbCard, xbNoToken } from "./crossBoardModules.shell";

// Dashboard 跨板块拉卡 · Projects 一件(履约待办)。
//   数据 = ProjectDueListCard 原件(GET /api/admin/vkpi/projects/due-list 卡内自取,
//   own-only 由服务端裁剪);点行 = 派发既有 vkpi:open-project-task 事件(CockpitApp
//   全局监听:切 Projects 板块 + 直开该项目详情,泳道 target_type=project 同管道)。
//   卡头计数不接(count 是内嵌组件内部 state,无外部口 —— 不摆会说谎的徽,
//   ProjectsBoardPage.embeds 同裁决)。
//   收编容器 = embeds DUE_TRIM 同口径(常量未导出 → 按「绝不改板块族文件」复制;
//   源串改动请同步:ProjectsBoardPage.embeds.tsx DUE_TRIM/EMBED)。
// 红线:纯读展示零写库;SrcChip 口径 = 源 MODULE_SOURCES 唯一注册表。

const BOARD_LABEL = "Projects";
const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

const EMBED = "vkpi-embed";
// 复制自 ProjectsBoardPage.embeds.tsx DUE_TRIM(压平旧卡壳 + 隐藏双写标题,刷新钮保留)
const DUE_TRIM = [
  "[&.vkpi-embed[data-embed]>section]:!mb-0 [&.vkpi-embed[data-embed]>section]:!rounded-none",
  "[&.vkpi-embed[data-embed]>section]:!border-0 [&.vkpi-embed[data-embed]>section]:!bg-transparent",
  "[&>section>header]:!px-0 [&>section>header]:!pt-0",
  "[&>section>header>div:first-child]:hidden",
  "[&>section>div]:!px-0",
].join(" ");

interface XbProps {
  apiToken: string;
  onOpenBoard: () => void;
}

export function ProjectsDueXbCard({ apiToken, onOpenBoard }: XbProps) {
  const openProject = React.useCallback((projectId: string) => {
    if (typeof window === "undefined") return;
    window.dispatchEvent(new CustomEvent("vkpi:open-project-task", { detail: { projectId } }));
  }, []);
  return (
    <XbCard
      title="履约待办"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      srcLabel={src("dueP").label}
      srcRows={[
        ...src("dueP").rows,
        ["当前阈值", "已签收 ≥ 7 天未推进"],
        ["跨板块", "点行 → Projects 板块直开该项目详情"],
      ]}
    >
      {apiToken ? (
        <div data-embed="due" className={`${EMBED} ${DUE_TRIM}`}>
          <ProjectDueListCard apiToken={apiToken} daysOverdue={7} onOpenProject={openProject} />
        </div>
      ) : (
        xbNoToken(BOARD_LABEL)
      )}
    </XbCard>
  );
}
