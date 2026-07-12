import React from "react";
import { SignaturePanel } from "../components/SignaturePanel";
import { AudienceGeoPanel } from "../components/AudienceGeoPanel";
import { CooperationPanel } from "../components/KOLDetailDrawer.Panels";
import { KOLVideoAnalysisPanel, type AnalysisBundle } from "../components/KOLVideoAnalysisPanel";
import { detailBundleAnalysisItems, detailBundleAnalysisSummary, videoAnalysisSources } from "../components/KOLDetailDrawer.helpers";
import { AudienceEstimateExtra } from "./KolProfileBoardPage.modules";
import type { Row } from "./KolProfileBoardPage.charts";

// KOL 档案 · 内嵌模块收编包装族(signature/audience/coop/videos 四件;
//   金样板 = MyKolBoardPage.embeds 非侵入收编手法 1:1)。
//   手法:**绝不改 components/ 旧组件文件**,只用包装容器的 Tailwind 任意变体选择器
//   隐藏重复标题块、压平旧卡壳(外层 px-5 py-3 border-b 白壳);功能控件与真读数
//   (深析×N 徽 / 置信徽 / 代理口径角标 / 合作状态徽 / 折叠 chevron / 动作钮 /
//   展开全部钮)全部保留可见。旧组件自带写死色属零改动豁免区(收编不重涂)。
//   容器挂 vkpi-embed 类 + data-embed 属性,变体写成 [&.vkpi-embed[data-embed]>…]
//   → 特异性 (0,3,1)+!,稳压换肤层 !important(金样板同注释)。
// 红线:本文件零新增网络逻辑(SignaturePanel/AudienceGeoPanel/CooperationPanel 自取,
//   KOLVideoAnalysisPanel 由 detail-bundle 直喂 preloadedBundles 零重复请求);
//   纯展示绝不写 fit 分;新增样式全 token 零写死色;零 opacity 修饰类。

const EMBED = "vkpi-embed";

/* ---- SectionFold 族(SignaturePanel / AudienceGeoPanel):外层 div.px-5.py-3.border-b
   压平;折叠头 button 里 icon svg + 标题 span 隐藏,真读数徽(深析×N/置信/代理口径)
   与 chevron 保留 —— 折叠功能与诚实信号一个不丢。 ---- */
const FOLD_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!border-0 [&.vkpi-embed[data-embed]>div]:!px-0 [&.vkpi-embed[data-embed]>div]:!py-0",
  "[&>div>button>svg:first-child]:hidden [&>div>button>span:first-of-type]:hidden",
].join(" ");

/* ---- CooperationPanel:外层 div.px-5.py-3.border-b 压平;折叠头里标题 span 隐藏,
   合作状态徽(第二个 span)+ 折叠箭头 + 四动作钮 + 时间线全部保留。 ---- */
const COOP_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!border-0 [&.vkpi-embed[data-embed]>div]:!px-0 [&.vkpi-embed[data-embed]>div]:!py-0",
  "[&>div>button>div>span:first-of-type]:hidden",
].join(" ");

/* ---- KOLVideoAnalysisPanel:section 壳压平;自带头行(标题 + 计数徽 + 表名脚注)
   整行隐藏 —— 真计数由 ModuleCard 卡头 cnt 接手(detail-bundle summary 同源真值),
   表名口径进 SrcChip,卡面零术语。 ---- */
const VIDEOS_TRIM = [
  "[&.vkpi-embed[data-embed]>section]:!border-0 [&.vkpi-embed[data-embed]>section]:!px-0 [&.vkpi-embed[data-embed]>section]:!py-0",
  "[&>section>div:first-child]:hidden",
].join(" ");

/* ============ 四件包装(取数各自自持;kolId/reload 变化由调用方 key 重挂载重取) ============ */

export function SignatureEmbed({ apiToken, kolId }: { apiToken: string; kolId: number }) {
  return (
    <div data-embed="signature" className={`${EMBED} ${FOLD_TRIM}`}>
      <SignaturePanel apiToken={apiToken} kolPoolId={kolId} />
    </div>
  );
}

export function AudienceEmbed({ apiToken, kolId, item }: { apiToken: string; kolId: number; item: Row | null }) {
  return (
    <div>
      <div data-embed="audience" className={`${EMBED} ${FOLD_TRIM}`}>
        <AudienceGeoPanel apiToken={apiToken} kolPoolId={kolId} />
      </div>
      {/* 档案侧估算附注(detail-bundle 字段;面板自身失败也不吞这半截) */}
      {item ? <AudienceEstimateExtra item={item} /> : null}
    </div>
  );
}

export function CoopEmbed({ apiToken, kolId }: { apiToken: string; kolId: number }) {
  return (
    <div data-embed="coop" className={`${EMBED} ${COOP_TRIM}`}>
      <CooperationPanel apiToken={apiToken} kolPoolId={kolId} />
    </div>
  );
}

export function VideoAnalysisEmbed({ apiToken, item, bundle }: { apiToken: string; item: Row | null; bundle: Row | null }) {
  // detail-bundle 直喂:videos=item.video_evidence,preloadedBundles/summary=video_analysis
  // (恒传数组 → 面板内部不再逐视频回源,零重复请求)
  const videos = React.useMemo(() => videoAnalysisSources(item || {}, []), [item]);
  const preloaded = React.useMemo<AnalysisBundle[]>(() => detailBundleAnalysisItems(bundle || {}), [bundle]);
  const summary = React.useMemo(() => detailBundleAnalysisSummary(bundle || {}), [bundle]);
  return (
    <div data-embed="videos" className={`${EMBED} ${VIDEOS_TRIM}`}>
      <KOLVideoAnalysisPanel apiToken={apiToken} videos={videos} preloadedBundles={preloaded} summary={summary} />
    </div>
  );
}
