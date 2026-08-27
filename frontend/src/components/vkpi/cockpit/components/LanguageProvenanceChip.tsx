// 语言这一格的门面件。规矩:
//   · 他自己填的 —— 正常显示,不加角标;
//   · 我们推断的 —— 值旁边挂「推断」角标,悬停能看到依据(个人简介 / 作品标题);
//   · 判不出来 —— 显示「未知」,不留空。
//
// 配色刻意避开同屏的其它标注:合格=绿、未通过=红、待验收=琥珀、
// 活跃度未知=天蓝,所以「推断」用紫,「未知」用灰,不撞色也不抢眼。

import type { LanguageProvenance } from "./LanguageProvenance";

const INFERRED_BADGE = "rounded border border-violet-300/25 bg-violet-400/[0.10] px-1 text-[9.5px] leading-[15px] text-violet-100";

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
      {provenance.origin === "inferred" ? <span className={INFERRED_BADGE}>{provenance.originLabel}</span> : null}
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
  // 三态在详情页要一眼分得开,所以「未知」也带自己的小字,不能只剩一个孤零零的词
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
        {provenance.origin === "inferred" ? <span className={INFERRED_BADGE}>{provenance.originLabel}</span> : null}
      </span>
      <span className="text-[8.5px] leading-3 text-slate-500">{note}</span>
      {/* 他填的和我们推断的对不上时如实说出来,不悄悄抹平成一个值。 */}
      {provenance.divergenceLabel ? (
        <span className="text-[8.5px] leading-3 text-amber-200/80">{provenance.divergenceLabel}</span>
      ) : null}
    </span>
  );
}
