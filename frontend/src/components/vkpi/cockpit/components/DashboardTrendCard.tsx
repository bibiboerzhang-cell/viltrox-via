import React from "react";
import { TrendingUp } from "lucide-react";
import { AreaChart } from "./viz/AreaChart";

function compact(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  if (number >= 1e9) return `${(number / 1e9).toFixed(2)}B`;
  if (number >= 1e6) return `${(number / 1e6).toFixed(2)}M`;
  if (number >= 1e3) return `${(number / 1e3).toFixed(1)}K`;
  return number.toLocaleString();
}

function metricScope(metric: any, scope: string) {
  return metric?.data?.[scope] || metric?.data?.all || {};
}

export function DashboardTrendCard({ metrics = [], scope = "all" }: { metrics?: any[]; scope?: string }) {
  const exposure = metrics.find((metric) => metric?.id === "exposure");
  const engagement = metrics.find((metric) => metric?.id === "engagement");
  const exposureData = metricScope(exposure, scope);
  const engagementData = metricScope(engagement, scope);
  const exposureTrend = Array.isArray(exposureData.spark) ? exposureData.spark.map(Number).filter(Number.isFinite) : [];
  const engagementTrend = Array.isArray(engagementData.spark) ? engagementData.spark.map(Number).filter(Number.isFinite) : [];
  const hasTrend = exposureTrend.length >= 2 || engagementTrend.length >= 2;

  return (
    <article className="vkpi-dashboard-trend">
      <header>
        <div>
          <h3><TrendingUp size={14} />曝光与互动趋势</h3>
          <span>真实 KPI 窗口</span>
        </div>
        <small>30D</small>
      </header>
      <div className="vkpi-dashboard-trend__stats">
        <div>
          <strong>{compact(exposureData.value)}</strong>
          <span>曝光量</span>
        </div>
        <div>
          <strong>{Number.isFinite(Number(engagementData.value)) ? `${Number(engagementData.value).toFixed(2)}%` : "--"}</strong>
          <span>互动率</span>
        </div>
        <div>
          <strong>{scope === "company" ? "公司账号" : scope === "kol" ? "KOL" : "全部"}</strong>
          <span>当前口径</span>
        </div>
      </div>
      {hasTrend ? (
        <div className="vkpi-dashboard-trend__chart" aria-label="曝光与互动真实趋势">
          {exposureTrend.length >= 2 ? <AreaChart data={exposureTrend} height={154} color="var(--ds-accent)" /> : null}
          {engagementTrend.length >= 2 ? <AreaChart data={engagementTrend} height={154} color="var(--ds-good)" grid={false} /> : null}
        </div>
      ) : (
        <div className="vkpi-dashboard-trend__empty">趋势累计中，当前接口没有足够连续窗口</div>
      )}
      <footer>
        <span><i className="is-exposure" />曝光量</span>
        <span><i className="is-engagement" />互动率</span>
        <small>不使用 lifetime 数据补齐</small>
      </footer>
    </article>
  );
}

