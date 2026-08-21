import type React from "react";
import type { LibraryFilter } from "../../../../services/vkpi/myKolBoard-api";
import { LibraryChips } from "./MyKolBoardPage.dialogs";

type LibraryPage = {
  total?: number;
  has_more?: boolean;
  next_cursor?: string | null;
};

export function KolLibraryFilterControls({
  filter,
  onFilter,
  platformOptions,
  vKolCount,
  staff,
  mine,
  vKolIds,
  vIdsTruncated,
  vIdsNote,
}: {
  filter: LibraryFilter;
  onFilter: (next: LibraryFilter) => void;
  platformOptions: Array<{ platform: string; count: number }>;
  vKolCount: number | null;
  staff?: React.ComponentProps<typeof LibraryChips>["staff"];
  mine?: React.ComponentProps<typeof LibraryChips>["mine"];
  vKolIds: ReadonlySet<number> | null;
  vIdsTruncated: boolean;
  vIdsNote: string;
}) {
  return (
    <div className="mb-2">
      <LibraryChips filter={filter} onFilter={onFilter} platformOptions={platformOptions} vKolCount={vKolCount} staff={staff} mine={mine} />
      {filter.stage ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10.5px] leading-5 text-muted">
          <button
            type="button"
            onClick={() => onFilter({ ...filter, stage: null })}
            className="inline-flex min-h-7 items-center gap-1 rounded-[7px] border border-accent bg-accent-soft px-2.5 py-1 font-semibold text-accent"
            title="来自合作漏斗点段 · 点此移除该过滤"
          >
            漏斗阶段:{filter.stage.label} ✕
          </button>
          <span>按行内项目阶段匹配(段计数=指派行,与库行数口径不同,如实不强对齐)</span>
        </div>
      ) : null}
      {filter.vOnly && vKolIds == null ? (
        <div className="mt-1.5 text-[10.5px] leading-5 text-warn">V 名单未就绪(board-ext 未返回)——暂按「已挂项目」近似过滤,如实降级。</div>
      ) : null}
      {filter.vOnly && vKolIds != null && vIdsTruncated ? (
        <div className="mt-1.5 text-[10.5px] leading-5 text-warn">
          {vIdsNote || "V 名单超封顶被截断——过滤只覆盖名单内 KOL,全量以计数为准(如实降级)。"}
        </div>
      ) : null}
    </div>
  );
}

export function KolLibraryPageActions({
  page,
  rowCount,
  filteredCount,
  loadingMore,
  loadMoreError,
  onLoadMore,
  onOpenList,
}: {
  page?: LibraryPage;
  rowCount: number;
  filteredCount: number;
  loadingMore: boolean;
  loadMoreError: string;
  onLoadMore?: () => void | Promise<void>;
  onOpenList: () => void;
}) {
  return (
    <>
      {page ? (
        <div className="mt-2 flex items-center justify-between gap-2 rounded-[9px] border border-line px-3 py-2.5 text-[11px] leading-5 text-muted">
          <span>已渐进展示 {rowCount.toLocaleString()} / {Number(page.total || rowCount).toLocaleString()} 条</span>
          {page.has_more && page.next_cursor ? (
            <button
              type="button"
              disabled={loadingMore || !onLoadMore}
              onClick={() => void onLoadMore?.()}
              className="min-h-8 rounded-[7px] border border-line-strong px-3 py-1.5 text-accent hover:border-accent disabled:cursor-wait disabled:opacity-50"
            >
              {loadingMore ? "加载中…" : "加载更多 50 条"}
            </button>
          ) : <span>已到末页</span>}
        </div>
      ) : null}
      {loadMoreError ? <div className="mt-1 text-[11px] leading-5 text-warn">{loadMoreError}</div> : null}
      <button
        type="button"
        onClick={onOpenList}
        className="mt-2 min-h-9 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[11.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
      >
        ≡ {page ? "查看已加载" : "查看全量"} {filteredCount} 条 · 点单条连续翻
      </button>
    </>
  );
}
