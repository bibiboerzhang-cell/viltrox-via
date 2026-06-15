// Verbatim from vkpi_v6.15.7_integrated.html

export const WORK_REMINDERS = [
  { id: 1, status: "todo",     priority: "high",   iconKey: "users",    iconColor: "#ef4444", title: "跟进 @TokyoLens 合同",        desc: "已 3 天未回复 · CineGear 现场名额",         time: "3 天前",   source: "system",  link: "kol:tokyolens" },
  { id: 2, status: "todo",     priority: "high",   iconKey: "send",     iconColor: "#fbbf24", title: "审核 @PeterLindgren 视频",   desc: "135mm LAB · 待审批 28 小时",              time: "1 天前",   source: "system",  link: "publish:135-peter" },
  { id: 3, status: "todo",     priority: "medium", iconKey: "userplus", iconColor: "#3b82f6", title: "联系 @ManyShotsKL(AI 推荐)", desc: "135mm LAB · 匹配度 89%",                  time: "今早",     source: "ai",      link: "kol:manyshots" },
  { id: 4, status: "todo",     priority: "medium", iconKey: "warning",  iconColor: "#f59e0b", title: "复核 GMV 异常",               desc: "月报 5/24 · Shopify 数据偏差 12%",         time: "今早",     source: "system",  link: "report:202605" },
  { id: 5, status: "todo",     priority: "low",    iconKey: "package",  iconColor: "#10b981", title: "确认 @MattiHaapoja 物料签收", desc: "物流单 8845 · 是否启动拍摄",               time: "2 天前",   source: "system",  link: "kol:matti" },
  { id: 6, status: "todo",     priority: "low",    iconKey: "filetext", iconColor: "#64748b", title: "回顾 5/17 周报 action items", desc: "3 项未完成 · 跨周延期",                    time: "1 周前",   source: "manual",  link: "report:w17" },
  { id: 7, status: "done",     priority: "medium", iconKey: "check",    iconColor: "#10b981", title: "签 @JamesPopsys 合同",        desc: "已完成 · 5/22",                           time: "3 天前",   source: "system",  link: null },
  { id: 8, status: "done",     priority: "high",   iconKey: "check",    iconColor: "#10b981", title: "审核 56mm 上市贴 IG",         desc: "已完成 · 5/23",                           time: "2 天前",   source: "system",  link: null },
  { id: 9, status: "ignored",  priority: "low",    iconKey: "x",        iconColor: "#64748b", title: "联系 @LensRangerJP",          desc: "已忽略 · 重复推荐",                       time: "1 周前",   source: "ai",      link: null },
];
