// Verbatim from vkpi_v6.15.7_integrated.html

export const SIGNALS_ALERTS = [
  {
    id: "sig-sony-85gm",
    severity: "high",  // high / medium / low / info
    icon: "alert",
    title: "Sony FE 85mm F1.4 GM II 发布",
    desc: "Sony 发布新一代 85mm,直接对标 AF 85mm PRO,$1,798 vs $499",
    time: "2h ago",
    sources: [
      { name: "NewShooter",        mentions: 287, url: "#" },
      { name: "DPReview",           mentions: 89,  url: "#" },
      { name: "Reddit r/cinematography", mentions: 54, url: "#" },
      { name: "Twitter",            mentions: 21, url: "#" },
      { name: "Petapixel",          mentions: 11, url: "#" },
    ],
    totalMentions: 462,
    trendPct: "+287%",
    thumbnails: [
      "https://images.unsplash.com/photo-1502920917128-1aa500764cbd?w=200",
      "https://images.unsplash.com/photo-1606983340126-99ab4feaa64a?w=200",
    ],
    summary: "Sony 在 5/20 发布 FE 85mm F1.4 GM II,售价 $1,798。重量比 GM I 减少 20%,光圈环带 Click/Declick 切换,采用 XD Linear 马达。主打人像和电影拍摄场景,定位 premium 用户。",
    impact: [
      { level: "🔴", text: "直接竞争 AF 85mm F1.4 PRO ($499)" },
      { level: "🔴", text: "价位高 3.6x,但定位 premium 用户" },
      { level: "🟡", text: "我们性价比优势仍在,需做对比突显" },
      { level: "🟢", text: "GM 二代有 click/declick — 我们 PRO 没有" },
    ],
    actions: [
      "7 日内出对比视频($499 vs $1,798)",
      "强化 KOL 真实使用对比(@PeterLindgren 适合)",
      "上线着陆页\"why $499 is enough\"",
    ],
  },
  {
    id: "sig-cinegear",
    severity: "medium",
    icon: "trending",
    title: "#CineGear2025 +212% mentions",
    desc: "主话题度持续增长,关联 hashtag 也在涨",
    time: "1h ago",
    sources: [
      { name: "Twitter",   mentions: 624, url: "#" },
      { name: "Instagram", mentions: 412, url: "#" },
      { name: "LinkedIn",  mentions: 167, url: "#" },
    ],
    totalMentions: 1203,
    trendPct: "+212%",
    thumbnails: [
      "https://images.unsplash.com/photo-1574717024653-61fd2cf4d44d?w=200",
    ],
    summary: "#CineGear2025 hashtag 在过去 7 天讨论量 +212%,关联话题 #cinematography #filmgear 同步增长。我们的展位 #4520 已被多位 KOL 主动提及。",
    impact: [
      { level: "🟢", text: "我们 CineGear LA 2026 物料 78% 完成,顺势营销机会" },
      { level: "🟡", text: "对手品牌也在抓住热度(Sigma, Tamron 已发推)" },
    ],
    actions: [
      "Pre-launch 内容推一波蹭流量",
      "矩阵账号 @ViltroxOfficial 加入话题讨论",
      "联动 @PeterLindgren 做 CineGear preview 内容",
    ],
  },
  {
    id: "sig-fpv",
    severity: "low",
    icon: "lightbulb",
    title: "FPV drone content ER 7.8% 行业均值",
    desc: "FPV 内容行业平均 ER 远高于我们 3.94%",
    time: "3h ago",
    sources: [
      { name: "Reddit r/videography", mentions: 47, url: "#" },
      { name: "YouTube trends",       mentions: 28, url: "#" },
      { name: "TikTok #FPV",           mentions: 14, url: "#" },
    ],
    totalMentions: 89,
    trendPct: "+18%",
    thumbnails: [
      "https://images.unsplash.com/photo-1473968512647-3e447244af8f?w=200",
    ],
    summary: "FPV(第一人称视角)无人机内容在过去 30 天保持 7.8% 平均 ER,是我们整体 ER 的 2.0x。AF 16mm F1.4 适合此场景,但当前 0 个 FPV KOL。",
    impact: [
      { level: "🟢", text: "AF 16mm F1.4 完美适配 FPV 场景" },
      { level: "🟡", text: "需要孵化 FPV 垂类创作者" },
    ],
    actions: [
      "孵化 3-5 个 FPV 创作者合作",
      "AF 16mm F1.4 营销联动 FPV 内容",
      "矩阵账号试拍 FPV demo(工程视角)",
    ],
  },
  {
    id: "sig-brand-mention",
    severity: "info",
    icon: "chart",
    title: "VILTROX 提及量 +127% (24h)",
    desc: "品牌讨论量自然增长,无负面",
    time: "5h ago",
    sources: [
      { name: "Brand24",  mentions: 1233, url: "#" },
      { name: "Google",   mentions: 412,  url: "#" },
    ],
    totalMentions: 1645,
    trendPct: "+127%",
    thumbnails: [],
    summary: "过去 24h VILTROX 品牌提及量从基线 543 提及/天 跃升至 1,233 提及/天,主要驱动:@PeterLindgren 135mm LAB 评测视频 + #CineGear2025 热度。",
    impact: [
      { level: "🟢", text: "自然增长,无负面舆情" },
      { level: "🟢", text: "@PeterLindgren 内容驱动效果显著" },
    ],
    actions: [
      "无需立即行动",
      "继续监控 24h",
    ],
  },
];
