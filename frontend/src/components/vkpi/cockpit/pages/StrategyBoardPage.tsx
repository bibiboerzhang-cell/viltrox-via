import { IndustryBenchmarkPanel } from "../components/IndustryBenchmarkPanel";
import { CategoryTracksPanel } from "../components/CategoryTracksPanel";
import { StrategySimPanel } from "../components/StrategySimPanel";
import { StrategyPerformancePanel } from "../components/StrategyPerformancePanel";

// 战略台(战略大脑波收口壳,2026-07-06 用户点单):四块合屏——
//   ① 行业对照(我们在行业里站在哪)② 新赛道机会(下一个该进的品类/焦段)
//   ③ 策略模拟器(这笔预算怎么花最划算)④ 战略表现(我们的判断到底准不准)。
// 纯组装零逻辑:四面板各自拉取各自判空,任一块失败安静缺席不影响其余。

export function StrategyBoardPage({ apiToken = "" }: { apiToken?: string }) {
  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1400px] mx-auto">
      <div className="mb-2">
        <h1 className="text-lg font-semibold text-white">战略台</h1>
        <div className="text-[11px] text-slate-500">
          行业对照 · 新赛道机会 · 策略模拟 · 战略表现 —— 全部库内真数据,每个数字带口径可追溯</div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="space-y-4">
          <IndustryBenchmarkPanel apiToken={apiToken} />
          <StrategySimPanel apiToken={apiToken} />
        </div>
        <div className="space-y-4">
          <CategoryTracksPanel apiToken={apiToken} />
          <StrategyPerformancePanel apiToken={apiToken} />
        </div>
      </div>
    </div>
  );
}
