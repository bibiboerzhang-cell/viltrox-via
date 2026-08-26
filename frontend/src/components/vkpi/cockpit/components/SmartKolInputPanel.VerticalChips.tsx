/**
 * 候选卡上的内容垂类小标（车道 3）。
 *
 * 一个人可以同时属于多个垂类，每个标都把「为什么算他是这一类」原样挂在 title 上
 * （命中了频道关键词/品牌标记/作品标题里的什么）。判不出的人显示“垂类未知”——
 * 不猜、不默认归进某一类。
 */
import type { CandidateVerticalTag } from "./SmartKolInputPanel.CandidateEvidence";

export function CandidateVerticalChips({ tags }: { tags: CandidateVerticalTag[] }) {
  return (
    <span data-testid="candidate-vertical-tags" className="mt-1 flex min-w-0 flex-wrap items-center gap-1">
      {tags.length ? tags.map((tag) => (
        <span
          key={tag.label}
          title={tag.reasons.length ? tag.reasons.join("\n") : "后端未返回判定依据"}
          className="rounded-full border border-teal-300/25 bg-teal-400/[0.08] px-1.5 py-0.5 text-[8.5px] font-medium text-teal-100/90"
        >{tag.label}</span>
      )) : (
        <span
          className="rounded-full border border-slate-300/15 bg-white/[0.025] px-1.5 py-0.5 text-[8.5px] text-slate-400"
          title="现有资料判不出他做哪一类内容；不猜、不默认归类"
        >垂类未知</span>
      )}
    </span>
  );
}
