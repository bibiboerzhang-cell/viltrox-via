// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html

export const REFRESH_STATE_INFO = {
  fresh:   { stripe: "transparent",            title: "数据新鲜" },
  stale:   { stripe: "rgba(251,191,36,0.55)",  title: "数据过期 · 后台刷新中" },
  warming: { stripe: "rgba(251,146,60,0.55)",  title: "正在补全 / 校验中" },
  queued:  { stripe: "rgba(168,85,247,0.55)",  title: "已入 Top 30 fast 队列" },
};
