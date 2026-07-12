import React from "react";
import { CatDonutBody } from "./MarketVoicePage.charts";
import { EmptyLine, ErrorCard, LoadingLine } from "./MarketVoicePage.modules";
import { useQueueData } from "./ReplyQueueBoardPage.actions";
import { FunnelBody, MODULE_SOURCES, queueCounts } from "./ReplyQueueBoardPage.modules";
import { XbCard, xbNoToken } from "./crossBoardModules.shell";

// Dashboard 跨板块拉卡 · 回复队列两件(意向构成环图 / 处理进度漏斗)。
//   数据 = 源板块导出的 useQueueData hook 原件(GET /api/admin/vkpi/reply-queue
//   单次全量 ≤500 卡内自取)+ queueCounts 同一份口径函数;渲染件 = CatDonutBody /
//   FunnelBody 原件。源板块内环图分段点开该类队列列表(页级弹窗);跨板块视图
//   点段 = 跳回复队列板块(口径行如实注明)。
// 红线:纯读展示零动作端点;SrcChip 口径 = 源 MODULE_SOURCES 唯一注册表。

const BOARD_LABEL = "回复队列";
const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

interface XbProps {
  apiToken: string;
  onOpenBoard: () => void;
}

/** 源板块 gate() 同构:未登录 / 首拉失败 / 读取中 → 诚实卡。 */
function queueGate(apiToken: string, queue: ReturnType<typeof useQueueData>): React.ReactNode | null {
  if (!apiToken) return xbNoToken(BOARD_LABEL);
  if (queue.error && !queue.items) return <ErrorCard title="reply-queue 读取失败" text={queue.error} />;
  if (queue.loading && !queue.items) return <LoadingLine text="回复队列读取中…" />;
  if (!queue.items) return <EmptyLine text="暂无数据。" />;
  return null;
}

export function ReplyIntentXbCard({ apiToken, onOpenBoard }: XbProps) {
  const queue = useQueueData(apiToken);
  const counts = React.useMemo(() => (queue.items ? queueCounts(queue.items) : null), [queue.items]);
  const gate = queueGate(apiToken, queue);
  return (
    <XbCard
      title="意向构成"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      cnt={counts ? `${counts.byIntent.length} 类` : undefined}
      srcLabel={src("intent").label}
      srcRows={[...src("intent").rows, ["跨板块", "点分段 → 回复队列板块(分类列表在源板块内展开)"]]}
    >
      {gate ??
        (counts!.total === 0 ? (
          <EmptyLine text="队列 0 条,环图诚实不画。" />
        ) : (
          <CatDonutBody categories={counts!.byIntent} totalMatched={counts!.total} onSelect={() => onOpenBoard()} />
        ))}
    </XbCard>
  );
}

export function ReplyFunnelXbCard({ apiToken, onOpenBoard }: XbProps) {
  const queue = useQueueData(apiToken);
  const counts = React.useMemo(() => (queue.items ? queueCounts(queue.items) : null), [queue.items]);
  const gate = queueGate(apiToken, queue);
  return (
    <XbCard
      title="处理进度"
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      cnt={counts ? `${counts.byStatus.replied || 0} 已回` : undefined}
      srcLabel={src("funnel").label}
      srcRows={[...src("funnel").rows, ["跨板块", "起草 / 回复动作在回复队列板块内进行"]]}
    >
      {gate ?? <FunnelBody counts={counts!} />}
    </XbCard>
  );
}
