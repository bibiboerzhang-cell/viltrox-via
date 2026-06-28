// Verbatim from vkpi_v6.15.7_integrated.html

export const CANDIDATE_KIND_INFO = {
  existing_fresh:          { label: "已有库 · 已更新",  short: "已更新",  bg: "rgba(34,197,94,0.10)",  border: "rgba(34,197,94,0.30)",  fg: "#86efac", group: "existing" },
  existing_stale:          { label: "已有库 · 刷新中",  short: "刷新中",  bg: "rgba(251,191,36,0.10)", border: "rgba(251,191,36,0.30)", fg: "#fde68a", group: "existing" },
  existing_low_confidence: { label: "已有库 · 待补全",  short: "待补全",  bg: "rgba(251,146,60,0.12)", border: "rgba(251,146,60,0.30)", fg: "#fdba74", group: "existing" },
  new_promoted:            { label: "新发现 · 高潜",   short: "高潜",    bg: "rgba(168,85,247,0.14)", border: "rgba(168,85,247,0.35)", fg: "#c4b5fd", group: "new" },
  new_validated:           { label: "新发现 · 已校验",  short: "已校验",  bg: "rgba(6,182,212,0.12)",  border: "rgba(6,182,212,0.30)",  fg: "#67e8f9", group: "new" },
  new_discovered:          { label: "新发现 · 校验中",  short: "校验中",  bg: "rgba(148,163,184,0.10)", border: "rgba(148,163,184,0.25)", fg: "#cbd5e1", group: "new" },
};
