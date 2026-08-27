// 语言这一格的门面件。规矩:
//   · 他自己填的 —— 正常显示,不加角标(**四档里只有这一档没有角标**);
//   · 我们推断的 —— 值旁边挂「推断」角标,悬停能看到依据(个人简介 / 作品标题);
//   · 看不出是谁填的 —— 值旁边挂「来源不明」角标,悬停说清楚是怎么回事;
//   · 判不出来 —— 显示「未知」,不留空。
//
// 「没有角标」在这一格里是一句**有内容的话**,它说的是「这是他自己填的」。所以角标不是
// 逐档 if 挂上去的,而是**除了自报,其余一律挂**:将来后端再加一档,漏挂角标的后果
// 就是那一档被默认读成「他自己填的」—— 替他伪造了一句声明。
//
// 第五种形态:服务端试着判断过、但它自己觉得把握不够、没敢当结论的那一票。
// 它**不是第五档** —— 归属就是「未知」,所以走未知那一支、绝不挂「推断」角标
// (挂了就是升格),只把那行小字换成「试着判断过,但把握不够」。
// 界面上仍然只有四档,与说明讲的档数对得上。
//
// 配色刻意避开同屏的其它标注:合格=绿、未通过=红、待验收=琥珀、
// 活跃度未知=天蓝,所以「推断」用紫,「来源不明」与「未知」用灰,不撞色也不抢眼。

import type { LanguageProvenance } from "./LanguageProvenance";

const BADGE_BASE = "rounded border px-1 text-[9.5px] leading-[15px]";
const INFERRED_BADGE = `${BADGE_BASE} border-violet-300/25 bg-violet-400/[0.10] text-violet-100`;
// 「来源不明」不是一句判断,是一句「我们说不准」—— 用最不抢眼的灰,别看着像结论。
const PROJECTED_BADGE = `${BADGE_BASE} border-slate-400/25 bg-slate-400/[0.10] text-slate-200`;

/** 自报以外的档一律挂角标;文案统一由 provenance.originLabel 给,门面不另起一份。 */
function OriginBadge({ provenance }: { provenance: LanguageProvenance }) {
  if (provenance.origin === "self_reported" || provenance.origin === "unknown") return null;
  return (
    <span className={provenance.origin === "inferred" ? INFERRED_BADGE : PROJECTED_BADGE}>
      {provenance.originLabel}
    </span>
  );
}

export function LanguageProvenanceCell({
  provenance,
  testId,
}: {
  provenance: LanguageProvenance;
  testId?: string;
}) {
  if (provenance.origin === "unknown") {
    return (
      <span data-testid={testId} className="text-slate-500" title={provenance.title}>
        {provenance.displayLabel}
      </span>
    );
  }
  return (
    <span data-testid={testId} className="inline-flex items-center gap-1" title={provenance.title}>
      {/* displayLabel 已经按代码 / 整词分别定好大小写,别再用 CSS 全大写喊回去。 */}
      <span className="text-slate-300">{provenance.displayLabel}</span>
      <OriginBadge provenance={provenance} />
    </span>
  );
}

/** 详情页版本:地方大,把依据直接写出来,不用悬停才看得到。 */
export function LanguageProvenanceDetail({
  provenance,
  testId,
}: {
  provenance: LanguageProvenance;
  testId?: string;
}) {
  // 四档在详情页要一眼分得开,所以「未知」也带自己的小字,不能只剩一个孤零零的词
  // ——但它只说得出「我们这里没有」,绝不替他说他填了什么,也不替系统声称我们试过推。
  // 小字统一由 LanguageProvenance.noteLabel 给,免得同一句话在两处各写一份、各错一份。
  if (provenance.origin === "unknown") {
    return (
      <span data-testid={testId} className="flex flex-col" title={provenance.title}>
        <span className="text-slate-400">{provenance.displayLabel}</span>
        <span className="text-[8.5px] leading-3 text-slate-500">{provenance.noteLabel}</span>
      </span>
    );
  }
  const note = provenance.noteLabel;
  return (
    <span data-testid={testId} className="flex flex-col" title={provenance.title}>
      <span className="inline-flex items-center gap-1">
        <span className="text-slate-200">{provenance.nameLabel}</span>
        <OriginBadge provenance={provenance} />
      </span>
      <span className="text-[8.5px] leading-3 text-slate-500">{note}</span>
      {/* 他填的和我们推断的对不上时如实说出来,不悄悄抹平成一个值。 */}
      {provenance.divergenceLabel ? (
        <span className="text-[8.5px] leading-3 text-amber-200/80">{provenance.divergenceLabel}</span>
      ) : null}
    </span>
  );
}
