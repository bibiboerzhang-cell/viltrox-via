// Report markdown content from vkpi_v6.15.7_integrated.html

export function generateReportMarkdown({ period, language, sections }: any) {
    const today = new Date();
    const dateStr = today.toLocaleDateString(language === "zh" ? "zh-CN" : "en-US", { 
      year: "numeric", month: language === "zh" ? "long" : "long", day: "numeric" 
    });
    const isWeekly = period === "weekly";
    const periodStr = isWeekly 
      ? (language === "zh" ? "2026 年 5 月 17 日 – 5 月 24 日(过去 7 天)" : "May 17 – May 24, 2026 (Last 7 days)")
      : (language === "zh" ? "2026 年 4 月 25 日 – 5 月 24 日(过去 30 天)" : "Apr 25 – May 24, 2026 (Last 30 days)");
    
    // V6.10.4: 周报 vs 月报真实数据分离
    const m = isWeekly ? {
      // ── 周报数据(过去 7 天)──
      rosterDelta: "+3", rosterDeltaNum: 3, rosterNewDesc: "本周新签", rosterNewDescEn: "this week",
      activeAccounts: 101, activeKol: 89, activeMatrix: 12, prevActive: 87, prevActiveDesc: "上周", prevActiveDescEn: "last week",
      activeLabel: "本周有发帖账号", activeLabelEn: "Active this week",
      reach: "94.2M", reachKol: "73.8M", reachMatrix: "20.4M", reachPct: "+12.1%", prevReach: "84.0M",
      reachLabel: "7 天累计曝光", reachLabelEn: "7-Day Total Reach",
      er: "4.05%", erDelta: "+0.11%", prevEr: "3.94%", erKol: "4.32%", erMatrix: "2.95%",
      postsTotal: 287, postsKol: 263, postsMatrix: 24, postsAvgPerDay: 41,
      // 用于趋势图(7 天数据点)
      trendPoints: [1038,1039,1040,1040,1041,1041,1041],
      // Top KOL 一周 reach
      topKol: [
        { name: "@PeterLindgren",     val: 11.2, dis: "11.2M" },
        { name: "@MattiHaapoja",      val: 7.4,  dis: "7.4M"  },
        { name: "@DSLRVideoShooter",  val: 4.6,  dis: "4.6M"  },
        { name: "@TokyoLens",         val: 2.8,  dis: "2.8M"  },
        { name: "@PotatoJet",         val: 2.4,  dis: "2.4M"  },
      ],
      // 平台一周 reach
      platforms: [
        { name: "Instagram", val: 36.4, dis: "36.4M", c: "#ec4899" },
        { name: "YouTube",   val: 30.1, dis: "30.1M", c: "#ef4444" },
        { name: "TikTok",    val: 18.7, dis: "18.7M", c: "#06b6d4" },
        { name: "Weibo",     val: 5.8,  dis: "5.8M",  c: "#f59e0b" },
        { name: "Other",     val: 3.2,  dis: "3.2M",  c: "#64748b" },
      ],
      // Good news 关键数字(周维度)
      peterFollowerDelta: "+3.1k", peterReachDelta: "+11.2M", peterConvPct: "47.2%",
      mattiFollowerDelta: "+2.1k", mattiPosts: 5, mattiPrevPosts: 3, mattiPostsPct: "+66.7%",
      dslrFollowerDelta: "+1.3k", dslrPosts: 6, dslrReach: "4.6M",
      potatoBack: "本周复发首条内容,单条 4.2M 曝光", potatoBackEn: "Broke silence with first post this week, 4.2M views",
      officialFollowerDelta: "+3.8k", officialPosts: 8, officialReach: "5.5M",
      cinemaFollowerDelta: "+2.1k", cinemaEr: "5.12%",
      labFollowerDelta: "+0.9k", labReach: "2.1M",
      brandMention24h: "+127%",  // 24h 数据,周月通用
      cinegearProgress: "78% (18/22)", cinegearBudgetUsed: "$124k", cinegearBudget: "$185k",
      // 沉默池(累积,周月差不多)
      silentTotal: 740, silentPctOfKol: "72.3%", silent60: 234, silent3060: 312, silent1430: 194,
      newSilentThisPeriod: 12, newSilentDesc: "本周新增 12 个 KOL 进入 7 天沉默",
      newSilentDescEn: "12 KOLs entered 7-day silent pool this week",
      // 沉默 KOL 例子(周和月都用同样)
      cineEuleFollowerDelta: "-87", cineEuleNewFollowers: "88.86k",
      sentimentTotalComments: 1932, sentimentPos: 76.0, sentimentNeu: 16.0, sentimentNeg: 8.0,
      sentimentPeriodDesc: "(过去 7 天 · 1,932 条评论)",
      sentimentPeriodDescEn: "(Past 7 days · 1,932 comments)",
    } : {
      // ── 月报数据(过去 30 天)──
      rosterDelta: "+12", rosterDeltaNum: 12, rosterNewDesc: "本月新增", rosterNewDescEn: "this month",
      activeAccounts: 301, activeKol: 287, activeMatrix: 14, prevActive: 273, prevActiveDesc: "上 30 天", prevActiveDescEn: "prev 30 days",
      activeLabel: "本月有发帖账号", activeLabelEn: "Active in 30D",
      reach: "367.61M", reachKol: "287.61M", reachMatrix: "80.0M", reachPct: "+18.4%", prevReach: "310.4M",
      reachLabel: "30 天累计曝光", reachLabelEn: "30-Day Total Reach",
      er: "3.94%", erDelta: "+0.18%", prevEr: "3.76%", erKol: "4.21%", erMatrix: "2.87%",
      postsTotal: 1256, postsKol: 1124, postsMatrix: 132, postsAvgPerDay: 42,
      // 趋势图(30 天数据点)
      trendPoints: [1013,1015,1017,1018,1020,1022,1024,1025,1027,1029,1030,1032,1033,1034,1035,1036,1037,1038,1039,1040,1040,1040,1041,1041,1041,1041,1041,1041,1041,1041],
      // Top KOL 30 天 reach
      topKol: [
        { name: "@PeterLindgren",     val: 38.4, dis: "38.4M" },
        { name: "@MattiHaapoja",      val: 24.8, dis: "24.8M" },
        { name: "@DSLRVideoShooter",  val: 18.2, dis: "18.2M" },
        { name: "@PotatoJet",         val: 12.7, dis: "12.7M" },
        { name: "@TokyoLens",         val: 9.4,  dis: "9.4M"  },
      ],
      // 平台 30 天 reach
      platforms: [
        { name: "Instagram", val: 142.3, dis: "142.3M", c: "#ec4899" },
        { name: "YouTube",   val: 118.5, dis: "118.5M", c: "#ef4444" },
        { name: "TikTok",    val: 72.4,  dis: "72.4M",  c: "#06b6d4" },
        { name: "Weibo",     val: 22.1,  dis: "22.1M",  c: "#f59e0b" },
        { name: "Other",     val: 12.3,  dis: "12.3M",  c: "#64748b" },
      ],
      peterFollowerDelta: "+12.4k", peterReachDelta: "+38.4M", peterConvPct: "47.2%",
      mattiFollowerDelta: "+8.7k", mattiPosts: 15, mattiPrevPosts: 9, mattiPostsPct: "+66.7%",
      dslrFollowerDelta: "+5.2k", dslrPosts: 23, dslrReach: "18.2M",
      potatoBack: "两个月沉默后复发首条内容,单条视频 12.7M 曝光", potatoBackEn: "Broke 2-month silence with first post; single video reached 12.7M views",
      officialFollowerDelta: "+15.2k", officialPosts: 32, officialReach: "22.1M",
      cinemaFollowerDelta: "+8.4k", cinemaEr: "4.84%",
      labFollowerDelta: "+3.8k", labReach: "8.2M",
      brandMention24h: "+127%",
      cinegearProgress: "78% (18/22)", cinegearBudgetUsed: "$124k", cinegearBudget: "$185k",
      silentTotal: 740, silentPctOfKol: "72.3%", silent60: 234, silent3060: 312, silent1430: 194,
      newSilentThisPeriod: 58, newSilentDesc: "本月新增 58 个 KOL 进入 30 天沉默",
      newSilentDescEn: "58 KOLs entered 30-day silent pool this month",
      cineEuleFollowerDelta: "-340", cineEuleNewFollowers: "88.86k",
      sentimentTotalComments: 8247, sentimentPos: 76.0, sentimentNeu: 16.0, sentimentNeg: 8.0,
      sentimentPeriodDesc: "(过去 30 天 · 8,247 条评论)",
      sentimentPeriodDescEn: "(Past 30 days · 8,247 comments)",
    };
    
    const t = language === "zh"
      // ───────────────────── 中文版 ─────────────────────
      ? {
          title:      `# Viltrox 营销${isWeekly ? "周" : "月"}报`,
          period:     "**统计周期:**",
          generated:  "**生成时间:**",
          author:     "**生成方:** V-KPI Dashboard · 内部辅助系统",
          
          kpiH:       "## 📊 一、KPI 总览",
          kpiSub:     `**矩阵规模:** 1,041 个活跃账号(KOL 签约 1,023 + 公司自营矩阵 18)\n**统计周期:** ${periodStr}\n\n`,
          kpiTable:   `| 指标 | 当前值 | 环比 | KOL | 公司账号 | 数据源 |\n` +
                      `|------|--------|------|-----|----------|--------|\n` +
                      `| 签约/矩阵总数 | **1,041** | ↑ ${m.rosterDelta}(${m.rosterNewDesc} ${m.rosterDeltaNum} 个 KOL) | 1,023 | 18 | V-KPI 数据库 |\n` +
                      `| ${m.activeLabel} | **${m.activeAccounts}** | ↑ +${m.activeAccounts - m.prevActive}(vs ${m.prevActiveDesc} ${m.prevActive}) | ${m.activeKol} | ${m.activeMatrix} | Aspire 内容追踪 |\n` +
                      `| ${m.reachLabel} | **${m.reach}** | ↑ ${m.reachPct}(vs 上${isWeekly ? "周" : "月"} ${m.prevReach}) | ${m.reachKol} | ${m.reachMatrix} | Aspire + 平台 API |\n` +
                      `| 加权 ER | **${m.er}** | ↑ ${m.erDelta}(vs 上${isWeekly ? "周" : "月"} ${m.prevEr}) | ${m.erKol} | ${m.erMatrix} | 计算项 |\n` +
                      `| ${isWeekly ? "7" : "30"} 天发帖总数 | **${m.postsTotal}** | 日均 ${m.postsAvgPerDay} 帖 | ${m.postsKol} | ${m.postsMatrix} | Aspire |\n` +
                      `| 归因 GMV | -- | 数据管线 40% 完成 | -- | -- | ⏳ 待 Shopify 接入 |\n` +
                      `| 平均 ROI | -- | 数据管线 10% 完成 | -- | -- | ⏳ 待成本数据接入 |\n\n` +
                      `**KOL 合作状态拆解(1,023 总数 · 累积):**\n` +
                      `- 长期合作 287 (28.1%) — 主力创作者池\n` +
                      `- 正在拍摄 42 (4.1%) — 当下进行中项目\n` +
                      `- 单次项目 565 (55.2%) — 完成或活跃单次合作\n` +
                      `- 谈判中 129 (12.6%) — 等签约\n\n` +
                      `**${isWeekly ? "7" : "30"} 天平台曝光分布:**\n` +
                      `- Instagram ${m.platforms[0].dis} · YouTube ${m.platforms[1].dis} · TikTok ${m.platforms[2].dis}\n` +
                      `- Weibo ${m.platforms[3].dis} · Other ${m.platforms[4].dis}\n\n`,
          
          kolVsCompanyH: "## ⚖️ 二、公司账号 vs KOL 详细对比",
          kolVsCompanyBody:
            `**核心差异(同样 1 个月数据):**\n\n` +
            `| 维度 | KOL Roster | Company Matrix | 效率分析 |\n` +
            `|------|------------|----------------|----------|\n` +
            `| 账号数 | 1,023 | 18 | KOL 体量 56.8x |\n` +
            `| 本月活跃账号 | 287 (28.1%) | 14 (77.8%) | 矩阵活跃率 2.8x |\n` +
            `| 本月发帖总数 | 1,124 | 132 | KOL 平均 3.9 帖 · 矩阵 9.4 帖 |\n` +
            `| 30D 总曝光 | 287.61M | 80.0M | KOL 占 78.2% · 矩阵 21.8% |\n` +
            `| 单账号月均曝光 | 281k | 4.44M | **矩阵单账号效率 15.8x** |\n` +
            `| 单帖平均曝光 | 256k | 606k | 矩阵单帖效率 2.4x |\n` +
            `| 加权 ER | 4.21% | 2.87% | **KOL ER 高 47%** |\n` +
            `| 评论 ER(Comment per impression) | 0.187% | 0.142% | KOL 互动密度高 |\n` +
            `| 单评论平均长度 | 18 字 | 12 字 | KOL 引发更深度讨论 |\n` +
            `| 内容类型主导 | 实战 / 评测 60% | 产品 demo 70% | 定位不同 |\n` +
            `| 平均粉丝数 | 142k | 8.9k(矩阵新建为主) | KOL 触达力强 |\n\n` +
            `**关键洞察:**\n` +
            `1. 矩阵账号"少而精",但 18 个账号的粉丝盘已经触顶(KOL 矩阵规模 56.8x 但曝光只占 78% 反映了 80/20 分布)\n` +
            `2. KOL Top 20%(约 205 个)贡献 80% 曝光(229M),其余 80% KOL 平均贡献只有 75k/月,长尾问题严重\n` +
            `3. 矩阵账号单帖效率高 2.4x,但 ER 反而低 47% — 矩阵内容偏"硬广",缺乏 KOL 的"真实感"\n` +
            `4. 矩阵账号在 Weibo 的 ER(3.42%) > KOL 在 Weibo 平均 ER(2.91%) — 矩阵在国内平台权威性更强\n` +
            `5. 公司账号粉丝平均 8.9k 偏低,主要是产品线/区域账号新建后未运营,需要资源倾斜\n\n` +
            `**定位建议:**\n` +
            `- **矩阵账号应聚焦**:产品教育 / 工程师 BTS / 实验室视角 / 官方政策更新(权威 + 教育内容)\n` +
            `- **KOL 应聚焦**:实战使用场景 / 个人创作风格融入 / 真实评测(信任 + 多样性)\n` +
            `- **协同**:KOL 拍摄镜头作品 → 矩阵账号转发并附技术解读 → 形成"创作 + 工程"双层内容\n\n`,
          
          platformH: "## 🎯 三、各平台 Deep Dive",
          platformBody:
            `### 3.1 Instagram(主战场 · 487 KOL + 9 矩阵 · 142.3M 曝光)\n\n` +
            `- **KOL 表现**:478 active · ER 4.18%(均值高于其他平台)· Top: @TokyoLens 6.9% ER\n` +
            `- **矩阵表现**:9 个账号 · Viltrox Official 主力(15.2k follower 增长)· 135mm LAB IG ER 4.84%\n` +
            `- **内容类型 ER 分布**:Reels **5.84%**(高)· Posts 4.12% · Stories **1.94%**(低)\n` +
            `- **发文频率**:KOL 平均 4.2 帖/月 · 矩阵 11.3 帖/月\n` +
            `- **最佳发布时间(US 时区)**:工作日 18:00-21:00 / 周末 10:00-13:00(基于历史 ER 中位数)\n` +
            `- **算法偏好**:Reels 优先 + Hashtag 5-10 个 + Carousel(轮播图)留存好\n` +
            `- 📈 **建议**:加大 Reels(目前矩阵 Reels 占比 23% → 目标 60%);减少 Stories 投入\n\n` +
            `### 3.2 YouTube(精品内容主战场 · 312 KOL + 7 矩阵 · 118.5M 曝光)\n\n` +
            `- **KOL 表现**:305 active · 平均 ER 4.05%(长视频拉低)· Top: @MattiHaapoja 8.7%\n` +
            `- **矩阵表现**:Viltrox Cinema 主力(YouTube ER 4.84%,矩阵最高)\n` +
            `- **内容类型 ER 分布**:Shorts **6.21%** · 8-15min 中视频 **4.92%** · 15min+ 长视频 2.78%\n` +
            `- **发文频率**:KOL 平均 1.8 帖/月(深内容)· 矩阵 4.2 帖/月\n` +
            `- **算法偏好**:Watch Time > CTR > Hook 前 15 秒留存 > 标题关键词\n` +
            `- 📈 **建议**:中视频(8-15min)效果最好,目前矩阵主推 5min 偏短;增加 Shorts(目前 12% → 30%)\n\n` +
            `### 3.3 TikTok(年轻流量入口 · 142 KOL + 0 矩阵 · 72.4M 曝光)\n\n` +
            `- **KOL 表现**:142 active · 平均 ER 3.84% · Top: @VideoActive 5.8%\n` +
            `- **矩阵表现**:⚠️ **0 个矩阵账号** — 公司在 TikTok 缺位\n` +
            `- **内容类型**:60s 内容 ER **4.92%** vs 1-3min 内容 ER 3.21%(短优于长)\n` +
            `- **算法偏好**:Hook 前 3 秒 + 完播率 + 评论引导 + 音乐 trending\n` +
            `- 📈 **建议**:**紧急建立 1-2 个矩阵 TikTok 账号**(产品 demo + 工程师视角);Hook 前 3 秒必须有产品演示\n\n` +
            `### 3.4 Weibo(国内市场 · 64 KOL + 2 矩阵 · 22.1M 曝光)\n\n` +
            `- **KOL 表现**:62 active · 平均 ER 2.91% · 国内创作者活跃度低于海外\n` +
            `- **矩阵表现**:Viltrox China + Viltrox 影视(中文)· 矩阵 ER 3.42%(平台均值之上)\n` +
            `- **内容类型**:话题营销 + 抽奖效果 1.6x ER · 评测视频 ER 中等\n` +
            `- **算法偏好**:热搜话题 + KOL 互推 + 视频置顶 24h\n` +
            `- 📈 **建议**:加大话题营销(目前 0 个主动话题运营)· 配合月度抽奖活动\n\n` +
            `### 3.5 Bilibili(深度科技内容 · 36 KOL + 0 矩阵 · 12.3M 曝光)\n\n` +
            `- **KOL 表现**:36 active · 平均 ER 4.21%(忠诚度高)\n` +
            `- **矩阵表现**:⚠️ **0 个矩阵账号** — 缺位\n` +
            `- **内容类型**:深度评测 / 教学 / 对比 ER 较高 · 长视频可承载\n` +
            `- 📈 **建议**:建议建立 1 个矩阵账号(工程师视角的产品深度内容)\n\n`,
          
          goodH:      "## ✅ 四、好消息(量化)",
          goodList:   `**KOL 贡献(${isWeekly ? "本周" : "本月"}):**\n` +
                      `- **@PeterLindgren**:${isWeekly ? "本周" : "本月"} ${m.peterFollowerDelta} follower(目前 1.42M),${isWeekly ? "本周" : "本月"}贡献 ${m.peterReachDelta} 曝光,135mm LAB 评测视频驱动了 ${m.peterConvPct} 的 Shopify 转化(VIA-peter-10 折扣码生效)\n` +
                      `- **@MattiHaapoja**:${isWeekly ? "本周" : "本月"} ${m.mattiFollowerDelta} follower,YouTube ER 8.7%(团队最高),${isWeekly ? "本周" : "本月"}发文 ${m.mattiPosts} 次(vs 上${isWeekly ? "周" : "月"} ${m.mattiPrevPosts} 次,${m.mattiPostsPct})\n` +
                      `- **@DSLRVideoShooter**:Instagram ${m.dslrFollowerDelta} follower,${isWeekly ? "本周" : "本月"}发帖 ${m.dslrPosts} 次,${isWeekly ? "周" : "月"}曝光 ${m.dslrReach}\n` +
                      `- **@PotatoJet**:${m.potatoBack}\n\n` +
                      `**公司矩阵(${isWeekly ? "本周" : "本月"}):**\n` +
                      `- Viltrox Official(Instagram 主账号)${m.officialFollowerDelta} follower,${isWeekly ? "本周" : "本月"} ${m.officialPosts} 帖,曝光 ${m.officialReach}\n` +
                      `- Viltrox Cinema(YouTube)${m.cinemaFollowerDelta} follower,ER ${m.cinemaEr}(矩阵最高)\n` +
                      `- Viltrox 135mm LAB(IG 产品线账号)${m.labFollowerDelta} follower,曝光 ${m.labReach}\n\n` +
                      `**品牌信号:**\n` +
                      `- VILTROX 品牌提及量 24h 内 ${m.brandMention24h}(基线 543 提及/天 → 当前 1,233 提及/天)\n` +
                      `- #CineGear2025 hashtag 在 Twitter/IG 上量明显增长(信号源:社交监测)\n` +
                      `- 行业话题度:FPV 无人机内容平均 ER 比静态高 2.3x(市场机会)\n\n` +
                      `**活动落实进度:**\n` +
                      `- CineGear LA 2026(Jun 13–15):物料 ${m.cinegearProgress} 完成,展位 #4520 确认,${m.cinegearBudget} 预算已落实 ${m.cinegearBudgetUsed}(67%)\n` +
                      `- Tokyo Creator Meetup(Jul 8):物料 90% 完成,@TokyoLens 已确认主持\n\n`,
          
          badH:       "## ⚠️ 五、问题与风险(量化)",
          badList:    `**KOL 流失/沉默(${isWeekly ? "本周" : "本月"}):**\n` +
                      `- **@CineEule**(YouTube):${isWeekly ? "本周" : "本月"} ${m.cineEuleFollowerDelta} follower(目前 ${m.cineEuleNewFollowers}),ER 1.2%(roster 最低),需评估是否继续合作\n` +
                      `- **@CameraConrad**(IG):沉默 14 天,前 30 天发文频率 3.2/周,目前 0/周\n` +
                      `- **@LondonLensman**(YT):沉默 21 天,合同还有 2 个月\n` +
                      `- **${m.newSilentDesc}**\n` +
                      `- **沉默池总累积 ${m.silentTotal}**:占 KOL roster ${m.silentPctOfKol},其中:\n` +
                      `  - 60+ 天未发文:${m.silent60} 个(应该考虑解约或转 inactive 状态)\n` +
                      `  - 30–60 天未发文:${m.silent3060} 个\n` +
                      `  - 14–30 天未发文:${m.silent1430} 个\n\n` +
                      `**竞品动作:**\n` +
                      `- Sony 发布 FE 85mm F1.4 GM II(2026/5/20)— 直接竞争 Viltrox AF 85mm F1.4 PRO 价位段,价格 $1,798 vs Viltrox $499\n` +
                      `- Sigma 56mm F1.4 DG DN 套装在 B&H 折扣 -$80(May 22 起)— 影响我们的 56mm 销售\n\n` +
                      `**数据基础设施阻塞:**\n` +
                      `- GMV 归因管线进度卡在 Shopify webhook 密钥轮转(Engineering),阻塞 5 步管线中第 3、4、5 步\n` +
                      `- 成本数据仍在 Google Sheets(Finance 维护),无法自动入库,导致 ROI 暂时无法计算\n` +
                      `- 国内平台数据(Weibo / Bilibili)抓取链路尚未稳定,可能造成 Total Exposure 低估约 4–8%\n\n`,
          
          sentimentH: "## 📣 六、品牌风评分析(社交评论)",
          sentimentBody: 
            `**总体情绪(过去 ${isWeekly ? "7" : "30"} 天 · ${m.sentimentTotalComments.toLocaleString()} 条评论分析):**\n` +
            `- 👍 正向 ${Math.round(m.sentimentTotalComments * m.sentimentPos / 100).toLocaleString()} (${m.sentimentPos}%) — 主要集中在画质 / 性价比 / 色彩还原\n` +
            `- 😐 中性 ${Math.round(m.sentimentTotalComments * m.sentimentNeu / 100).toLocaleString()} (${m.sentimentNeu}%) — 询问、使用问题、对比咨询\n` +
            `- 👎 负向 ${Math.round(m.sentimentTotalComments * m.sentimentNeg / 100).toLocaleString()} (${m.sentimentNeg}%) — 主要集中在对焦 / 配件 / 物流\n\n` +
            `**分平台情绪分布(${isWeekly ? "本周" : "本月"}):**\n` +
            `| 平台 | 评论总数 | 正向 | 中性 | 负向 | ER(评论/曝光) |\n` +
            `|------|---------|------|------|------|----------------|\n` +
            `| Instagram | 4,021 | 78.2% | 14.8% | 7.0% | 0.282% |\n` +
            `| YouTube   | 2,876 | 74.1% | 16.9% | 9.0% | 0.243% |\n` +
            `| TikTok    | 892   | 76.5% | 17.4% | 6.1% | 0.123% |\n` +
            `| Weibo     | 458   | 71.2% | 19.7% | 9.1% | 0.207% |\n\n` +
            `**好评关键词(Top 8):**\n` +
            `- "sharp / 锐利" — 488 次提及(主要谈 135mm LAB / 56mm)\n` +
            `- "color / 色彩" — 312 次(LAB 系列被称"开盖即用")\n` +
            `- "value / 性价比" — 287 次(对比 Sony GM 主要话题)\n` +
            `- "build quality / 做工" — 231 次(铜接环、金属外壳)\n` +
            `- "bokeh / 焦外" — 198 次(135mm 散景 vs Sony FE 135)\n` +
            `- "lightweight / 轻量" — 167 次(AF 系列对比 Sigma DG)\n` +
            `- "weather sealing / 防尘防水" — 124 次(LAB 系列卖点)\n` +
            `- "fast aperture / 大光圈" — 109 次\n\n` +
            `**差评关键词(Top 6):**\n` +
            `- "focus hunting / 拉风箱" — 89 次(主要 AF 75mm / 28mm 老款,需 firmware 更新)\n` +
            `- "tripod mount / 三脚架环" — 54 次(135mm LAB 没原厂三脚架环投诉)\n` +
            `- "shipping / 物流" — 42 次(欧洲发货时效)\n` +
            `- "noisy AF motor / 对焦马达声" — 38 次(老款 AF 系列)\n` +
            `- "rear filter / 后置滤镜" — 21 次(电影头无后插槽设计)\n` +
            `- "support / 售后" — 18 次(海外维修周期)\n\n` +
            `**产品满意度(基于评论关键词聚合):**\n` +
            `- 135mm LAB:⭐ 4.8/5(基于 1,234 条评论)\n` +
            `- AF 56mm F1.4 Pro:⭐ 4.7/5(基于 892 条)\n` +
            `- AF 85mm F1.4 Pro:⭐ 4.7/5(基于 786 条)\n` +
            `- AF 75mm F1.2:⭐ 4.3/5(基于 412 条,主要 focus hunting 拉低)\n` +
            `- Cine 27mm T2.0:⭐ 4.9/5(基于 187 条,新品口碑顶)\n\n` +
            `**对比竞品讨论(用户主动提及):**\n` +
            `- vs Sony G/GM 系列:1,287 次提及("性价比"压倒性优势)\n` +
            `- vs Sigma DG DN:678 次("做工差不多,价格便宜 30–40%")\n` +
            `- vs Tamron:412 次("更轻,光圈更大")\n\n`,
          
          contentRecsH: "## 💡 七、内容改进建议(基于数据)",
          contentRecsBody:
            `**✅ 高表现内容(模仿这种 · 加大投入):**\n\n` +
            `| 内容形式 | 平均 ER | vs 均值 | 备注 |\n` +
            `|----------|---------|---------|------|\n` +
            `| 实战拍摄 + 设备评测 | 6.24% | +58% | 最高表现,KOL 主力 |\n` +
            `| 产品 vs 竞品对比 | 5.81% | +47% | 用户决策类内容 |\n` +
            `| Reels / Shorts (60s 内) | 5.21% | +32% | 算法偏好,各平台通用 |\n` +
            `| BTS / Day-in-the-life | 5.4% | +37% | 真实感,KOL 优势 |\n` +
            `| 工程师/产品经理视角 | 4.92%(预估) | +25% | **矩阵账号机会** |\n\n` +
            `**⚠️ 低表现内容(需要改进 / 减少):**\n\n` +
            `| 内容形式 | 平均 ER | vs 均值 | 改进方向 |\n` +
            `|----------|---------|---------|----------|\n` +
            `| 纯产品 demo / 开箱 | 2.10% | -47% | 加入"使用场景"叙事 |\n` +
            `| 长篇硬广告 | 1.81% | -54% | 转为"教育 + 软植入" |\n` +
            `| Instagram Stories | 1.94% | -51% | 减少投入 / 转 Reels |\n` +
            `| 矩阵通用产品介绍 | 2.87% | -27% | 加入工程师视角 |\n` +
            `| YouTube 长视频(15min+)| 2.78% | -29% | 8-15min 中视频更好 |\n\n` +
            `**📋 具体改进建议(可执行):**\n\n` +
            `1. **内容形式优先级调整**:Reels/Shorts > 中视频(8-15min) > 静态 Posts > Stories\n` +
            `2. **主题排序**:实战 > 评测 > 对比 > 教程 > 硬广(减少硬广占比 35% → 10%)\n` +
            `3. **矩阵账号大改造**:增加"工程师视角"内容(目前 0% · 创作者反馈"想看")· 减少通用产品介绍 50%\n` +
            `4. **多平台联动**:同步发文(IG + YT + TT 同一内容多平台)的 ER 比单平台高 35%(数据基础)\n` +
            `5. **市场机会跟进**:加大 FPV 无人机内容(行业 ER 均值 7.8% vs 我们 3.94%)· 配合 AF 16mm F1.4 营销\n\n` +
            `**🎯 平台特定策略:**\n\n` +
            `- **Instagram**:Reels 占比 23% → 目标 **60%**(Reels ER 5.84% vs Stories 1.94%)\n` +
            `- **YouTube**:主推 **8-15min 中视频**(ER 4.92%);Shorts 占比 12% → **30%**(Shorts ER 6.21%)\n` +
            `- **TikTok**:**Hook 前 3 秒必有产品演示**(数据证明留存率差距 2.3x);**紧急建矩阵账号**\n` +
            `- **Weibo**:加大话题营销(目前 0 个主动话题运营)· 配合月度抽奖活动(抽奖 ER 1.6x)\n` +
            `- **Bilibili**:**建立 1 个工程师视角矩阵账号**(深度科技内容)· 主打中长视频\n\n` +
            `**🚨 内容缺位提醒:**\n` +
            `- 矩阵账号在 **TikTok / Bilibili 完全缺位** — 建议各建 1 个账号 (Q3 优先)\n` +
            `- "工程师 BTS"系列在所有平台缺位 — 但用户评论中提及次数 67 次(隐性需求)\n` +
            `- 国内长视频深度评测(B 站)市场未触达 — KOL 资源 36 个但矩阵 0\n\n`,
          
          eventsH:    "## 🎪 八、活动进度(完整)",
          eventsTable:`| 活动 | 日期 | 地点 | 物料完成度 | 已落实预算 | 关键 timeline | 状态 |\n` +
                      `|------|------|------|----------|----------|---------------|------|\n` +
                      `| CineGear LA 2026 | 6/13–15 | LA Convention Center #4520 | 78% (18/22) | $124k / $185k | 已确认 4 名 KOL on-site | 🟢 On track |\n` +
                      `| SCAD Spring Workshop | 6/22–24 | Savannah, SCAD Film | 45% | $14k / $28k | 学生报名 67/100 | 🟡 In prep |\n` +
                      `| Tokyo Creator Meetup | 7/8 | Tokyo Shibuya | 90% | $22k / $25k | @TokyoLens 主持已确认 | 🟢 On track |\n` +
                      `| Photokina Cologne | 9/18–21 | Cologne Messe | 22% | $28k / $120k | 展位申请审批中 | 🟡 Early planning |\n\n` +
                      `**预算总览:** 已承诺 $358k · 已落实 $188k (52.5%) · 余 $170k(主要在 Photokina 后期支出)\n` +
                      `**团队配置:** 跨 4 个活动共 8 人参与(Jianbo, Kevin, Maya, Tom 等)\n\n`,
          
          attrH:      "## 🔗 九、归因管线接入进度",
          attrBody:   `**归因 GMV(Attributed GMV)— 40% 完成 · ETA 6 月 5 日**\n` +
                      `- ✅ Shopify Webhook 注册(完成 5/12 · Engineering)\n` +
                      `- 🟡 订单同步(进行中 · Tom Liu · 5/22 启动,回填过去 90 天订单)\n` +
                      `- 🟡 UTM 标记(进行中 · Maya Wong · 5/24,VIA-{handle}-10 折扣码已上线 47 个)\n` +
                      `- ⚪ 归因逻辑(待开发 · Engineering · ETA 6/1)— 通过 UTM + cookie 匹配订单到 KOL\n` +
                      `- ⚪ 仪表板展示(待开发 · Engineering · ETA 6/5)\n\n` +
                      `**平均 ROI(Avg ROI)— 10% 完成 · ETA 6 月 15 日**\n` +
                      `- 🟡 GMV 集成(依赖上方 Shopify 管线)\n` +
                      `- 🟡 成本追踪(Finance · 5/28)— 当前在 Google Sheets,需要导入 PostgreSQL\n` +
                      `- ⚪ Campaign tagging(待 · Maya · 6/8)— 每笔支出标记 campaign_id\n` +
                      `- ⚪ 聚合计算(待 · Engineering · 6/10)\n` +
                      `- ⚪ Per-KOL ROI 下钻(待 · 6/15)\n\n` +
                      `**当前阻塞:** Shopify webhook 密钥轮转,需 Engineering 协调 IT 完成\n\n`,
          
          aiH:        "## 十、决策洞察",
          aiBody:     `> **今日洞察(置信度 92%):** 135mm LAB 营销活动整体表现优于平均 23%。@PeterLindgren 评测视频驱动本周 47.2% 转化(基于 VIA-peter-10 折扣码使用追踪)。**建议:** 增加 $50k 预算扩展 @PeterLindgren 后续内容(2 条 longform + 4 条 short)。\n\n` +
                      `**热门话题:** #CineGear2025 在 Twitter/IG 上量增长明显,过去 7 天 +212% 提及量\n` +
                      `**市场机会:** FPV 无人机相关内容平均 ER 7.8%(全平台均值 3.94% 的 2.0x),AF 16mm F1.4 适配此场景\n` +
                      `**风险监控:** Sony FE 85mm GM II 发布对 85mm F1.4 PRO 影响待观察(建议 7 日内做对比内容应对)\n\n`,
          
          footer:     `_本报告由 V-KPI 内部辅助系统自动生成 · ${dateStr} · 数据源:Aspire / V-KPI Database / Shopify(待接) / Manual Sync_`,
        }
      // ───────────────────── English ─────────────────────
      : {
          title:      `# Viltrox Marketing ${isWeekly ? "Weekly" : "Monthly"} Report`,
          period:     "**Period:**",
          generated:  "**Generated:**",
          author:     "**Author:** V-KPI Dashboard · Internal Marketing Intelligence",
          
          kpiH:       "## 📊 1. KPI Overview",
          kpiSub:     `**Roster Scale:** 1,041 active accounts (1,023 KOL signed + 18 company matrix)\n**Reporting Period:** ${periodStr}\n\n`,
          kpiTable:   `| Metric | Current | vs Last Period | KOL | Company | Source |\n` +
                      `|--------|---------|----------------|-----|---------|--------|\n` +
                      `| Active Roster | **1,041** | ↑ ${m.rosterDelta} (${m.rosterNewDescEn}, +${m.rosterDeltaNum} new) | 1,023 | 18 | V-KPI Database |\n` +
                      `| ${m.activeLabelEn} | **${m.activeAccounts}** | ↑ +${m.activeAccounts - m.prevActive} (vs ${m.prevActiveDescEn} ${m.prevActive}) | ${m.activeKol} | ${m.activeMatrix} | Aspire |\n` +
                      `| ${m.reachLabelEn} | **${m.reach}** | ↑ ${m.reachPct} (vs ${m.prevReach}) | ${m.reachKol} | ${m.reachMatrix} | Aspire + Platform APIs |\n` +
                      `| Weighted ER | **${m.er}** | ↑ ${m.erDelta} (vs ${m.prevEr}) | ${m.erKol} | ${m.erMatrix} | Computed |\n` +
                      `| ${isWeekly ? "7" : "30"}-Day Posts | **${m.postsTotal}** | Avg ${m.postsAvgPerDay}/day | ${m.postsKol} | ${m.postsMatrix} | Aspire |\n` +
                      `| Attributed GMV | -- | Pipeline 40% | -- | -- | ⏳ Pending Shopify |\n` +
                      `| Avg ROI | -- | Pipeline 10% | -- | -- | ⏳ Pending Cost data |\n\n` +
                      `**KOL Partnership Status (of 1,023 total · cumulative):**\n` +
                      `- Long-term Partner: 287 (28.1%) — core creator pool\n` +
                      `- In Production: 42 (4.1%) — currently shooting\n` +
                      `- One-off Project: 565 (55.2%) — single campaigns, completed or active\n` +
                      `- Pending Contract: 129 (12.6%) — awaiting signature\n\n` +
                      `**${isWeekly ? "7" : "30"}-Day Platform Reach:**\n` +
                      `- Instagram ${m.platforms[0].dis} · YouTube ${m.platforms[1].dis} · TikTok ${m.platforms[2].dis}\n` +
                      `- Weibo ${m.platforms[3].dis} · Other ${m.platforms[4].dis}\n\n`,
          
          kolVsCompanyH: "## ⚖️ 2. Company Matrix vs KOL Roster · Detailed Comparison",
          kolVsCompanyBody:
            `**Core Differences (Same 30-day window):**\n\n` +
            `| Dimension | KOL Roster | Company Matrix | Efficiency Analysis |\n` +
            `|-----------|------------|----------------|---------------------|\n` +
            `| Accounts | 1,023 | 18 | KOL 56.8x scale |\n` +
            `| Active this month | 287 (28.1%) | 14 (77.8%) | Matrix 2.8x activity rate |\n` +
            `| Total posts | 1,124 | 132 | KOL avg 3.9 · Matrix 9.4 per account |\n` +
            `| 30D total impressions | 287.61M | 80.0M | KOL 78.2% · Matrix 21.8% |\n` +
            `| Per-account monthly impressions | 281k | 4.44M | **Matrix 15.8x more efficient per account** |\n` +
            `| Per-post avg impressions | 256k | 606k | Matrix 2.4x more efficient per post |\n` +
            `| Weighted ER | 4.21% | 2.87% | **KOL ER 47% higher** |\n` +
            `| Comment ER | 0.187% | 0.142% | KOL drives deeper engagement |\n` +
            `| Avg comment length | 18 chars | 12 chars | KOL triggers richer discussion |\n` +
            `| Dominant content type | Field use / reviews 60% | Product demo 70% | Different positioning |\n` +
            `| Avg follower count | 142k | 8.9k (most matrix new) | KOL has greater reach |\n\n` +
            `**Key Insights:**\n` +
            `1. Matrix is "small but mighty" — 18 accounts hit a ceiling. KOL roster is 56.8x but only delivers 78% of impressions (classic 80/20).\n` +
            `2. KOL top 20% (~205 accounts) contributes 80% of impressions (229M). Bottom 80% averages 75k/month — severe long-tail.\n` +
            `3. Matrix is 2.4x per-post efficient, but 47% lower ER — matrix content too "salesy," lacks KOL authenticity.\n` +
            `4. Matrix ER on Weibo (3.42%) > KOL avg on Weibo (2.91%) — matrix has stronger authority in China.\n` +
            `5. Matrix avg follower 8.9k is low because product-line/regional accounts are new and underinvested.\n\n` +
            `**Positioning Recommendations:**\n` +
            `- **Matrix should focus on**: product education / engineer BTS / lab perspective / official policy (authority + education)\n` +
            `- **KOL should focus on**: field use cases / personal creative integration / authentic reviews (trust + diversity)\n` +
            `- **Synergy**: KOL shoots lens work → Matrix reposts with technical commentary → forms "creator + engineer" two-layer content\n\n`,
          
          platformH: "## 🎯 3. Platform Deep Dive",
          platformBody:
            `### 3.1 Instagram (Main Battlefield · 487 KOL + 9 Matrix · 142.3M Impressions)\n\n` +
            `- **KOL Performance**: 478 active · ER 4.18% (highest cross-platform mean) · Top: @TokyoLens 6.9% ER\n` +
            `- **Matrix Performance**: 9 accounts · Viltrox Official leading (+15.2k followers) · 135mm LAB IG ER 4.84%\n` +
            `- **Content Type ER**: Reels **5.84%** (high) · Posts 4.12% · Stories **1.94%** (low)\n` +
            `- **Posting Frequency**: KOL avg 4.2 posts/mo · Matrix 11.3 posts/mo\n` +
            `- **Best Posting Time (US TZ)**: Weekdays 18:00-21:00 / Weekends 10:00-13:00\n` +
            `- **Algorithm Bias**: Reels-first + 5-10 hashtags + Carousel retention is strong\n` +
            `- 📈 **Action**: Increase Reels share (Matrix currently 23% → target **60%**); reduce Stories investment\n\n` +
            `### 3.2 YouTube (Premium Content Battlefield · 312 KOL + 7 Matrix · 118.5M Impressions)\n\n` +
            `- **KOL Performance**: 305 active · avg ER 4.05% (long-form drags down) · Top: @MattiHaapoja 8.7%\n` +
            `- **Matrix Performance**: Viltrox Cinema leading (ER 4.84%, highest in matrix)\n` +
            `- **Content Type ER**: Shorts **6.21%** · 8-15min mid-form **4.92%** · 15min+ long-form 2.78%\n` +
            `- **Posting Frequency**: KOL avg 1.8/mo (deep content) · Matrix 4.2/mo\n` +
            `- **Algorithm Bias**: Watch Time > CTR > 15s hook retention > title keywords\n` +
            `- 📈 **Action**: Mid-form (8-15min) performs best; matrix currently pushing 5min is too short. Increase Shorts (12% → **30%**)\n\n` +
            `### 3.3 TikTok (Young Audience · 142 KOL + 0 Matrix · 72.4M Impressions)\n\n` +
            `- **KOL Performance**: 142 active · avg ER 3.84% · Top: @VideoActive 5.8%\n` +
            `- **Matrix Performance**: ⚠️ **0 matrix accounts** — Viltrox is absent on TikTok\n` +
            `- **Content Type**: 60s content ER **4.92%** vs 1-3min ER 3.21% (shorter wins)\n` +
            `- **Algorithm Bias**: 3s hook + completion rate + comment prompts + trending music\n` +
            `- 📈 **Action**: **URGENT — launch 1-2 matrix TikTok accounts** (product demo + engineer POV); 3s hook must show product\n\n` +
            `### 3.4 Weibo (China Market · 64 KOL + 2 Matrix · 22.1M Impressions)\n\n` +
            `- **KOL Performance**: 62 active · avg ER 2.91% · domestic creators less active than overseas\n` +
            `- **Matrix Performance**: Viltrox China + Viltrox Film(中文) · matrix ER 3.42% (above platform mean)\n` +
            `- **Content Type**: Topic marketing + giveaways 1.6x ER · review videos medium\n` +
            `- **Algorithm Bias**: Trending topics + KOL cross-promotion + 24h video pin\n` +
            `- 📈 **Action**: Increase topic marketing (currently 0 active campaigns) · pair with monthly giveaways\n\n` +
            `### 3.5 Bilibili (Deep Tech Content · 36 KOL + 0 Matrix · 12.3M Impressions)\n\n` +
            `- **KOL Performance**: 36 active · avg ER 4.21% (loyal audience)\n` +
            `- **Matrix Performance**: ⚠️ **0 matrix accounts** — absent\n` +
            `- **Content Type**: Deep reviews / tutorials / comparisons drive higher ER · long-form viable\n` +
            `- 📈 **Action**: Launch 1 matrix account (engineer POV for product deep-dives)\n\n`,
          
          goodH:      "## ✅ 4. Good News (Quantified)",
          goodList:   `**Top KOL Contributors (${isWeekly ? "this week" : "this month"}):**\n` +
                      `- **@PeterLindgren** (YouTube, 1.42M): ${m.peterFollowerDelta} followers ${isWeekly ? "this week" : "this month"}, contributed ${m.peterReachDelta} impressions. His 135mm LAB review drove ${m.peterConvPct} of Shopify conversions (via VIA-peter-10 discount code tracking).\n` +
                      `- **@MattiHaapoja** (YouTube): ${m.mattiFollowerDelta} followers, top team ER 8.7%, posted ${m.mattiPosts} times ${isWeekly ? "this week" : "this month"} (${m.mattiPostsPct} vs ${m.mattiPrevPosts} prior).\n` +
                      `- **@DSLRVideoShooter** (Instagram): ${m.dslrFollowerDelta} followers, ${m.dslrPosts} posts ${isWeekly ? "this week" : "this month"}, ${m.dslrReach} impressions.\n` +
                      `- **@PotatoJet** (YouTube): ${m.potatoBackEn}\n\n` +
                      `**Company Matrix Growth (${isWeekly ? "this week" : "this month"}):**\n` +
                      `- Viltrox Official (Main IG): ${m.officialFollowerDelta} followers, ${m.officialPosts} posts, ${m.officialReach} impressions.\n` +
                      `- Viltrox Cinema (YouTube): ${m.cinemaFollowerDelta} followers, ER ${m.cinemaEr} (highest in matrix).\n` +
                      `- Viltrox 135mm LAB (Product-line IG): ${m.labFollowerDelta} followers, ${m.labReach} impressions.\n\n` +
                      `**Brand Signals:**\n` +
                      `- VILTROX brand mentions surged ${m.brandMention24h} in 24h (baseline 543/day → 1,233/day).\n` +
                      `- #CineGear2025 hashtag trending on Twitter/IG (social monitoring source).\n` +
                      `- Industry trend: FPV drone content ER averages 2.3x higher than static (market opportunity).\n\n` +
                      `**Event Progress:**\n` +
                      `- CineGear LA 2026 (Jun 13–15): ${m.cinegearProgress} material complete, Booth #4520 confirmed, ${m.cinegearBudgetUsed} / ${m.cinegearBudget} budget locked (67%).\n` +
                      `- Tokyo Creator Meetup (Jul 8): 90% complete, @TokyoLens confirmed as host.\n\n`,
          
          badH:       "## ⚠️ 5. Issues & Risks (Quantified)",
          badList:    `**KOL Attrition / Silence (${isWeekly ? "this week" : "this month"}):**\n` +
                      `- **@CineEule** (YouTube): ${m.cineEuleFollowerDelta} followers (currently ${m.cineEuleNewFollowers}), ER 1.2% (lowest in roster). Recommend partnership review.\n` +
                      `- **@CameraConrad** (IG): silent 14 days, prior posting rate 3.2/week, currently 0/week.\n` +
                      `- **@LondonLensman** (YT): silent 21 days, contract has 2 months remaining.\n` +
                      `- **${m.newSilentDescEn}**\n` +
                      `- **Total silent pool: ${m.silentTotal} KOLs** (${m.silentPctOfKol} of roster):\n` +
                      `  - 60+ days inactive: ${m.silent60} (consider de-roster or move to inactive)\n` +
                      `  - 30–60 days inactive: ${m.silent3060}\n` +
                      `  - 14–30 days inactive: ${m.silent1430}\n\n` +
                      `**Competitor Moves:**\n` +
                      `- Sony released FE 85mm F1.4 GM II (May 20) — direct competition with Viltrox AF 85mm F1.4 PRO; $1,798 vs Viltrox $499.\n` +
                      `- Sigma 56mm F1.4 DG DN bundle discounted -$80 at B&H (May 22) — affecting our 56mm sales.\n\n` +
                      `**Data Infrastructure Blockers:**\n` +
                      `- GMV attribution pipeline blocked on Shopify webhook key rotation (Engineering), blocking steps 3–5 of 5.\n` +
                      `- Cost data still in Google Sheets (Finance), no automatic ingestion → ROI cannot compute.\n` +
                      `- Mainland China platforms (Weibo / Bilibili) scraping not fully stable, may underestimate Total Exposure by ~4–8%.\n\n`,
          
          sentimentH: "## 📣 6. Brand Sentiment Analysis (Social Comments)",
          sentimentBody:
            `**Overall Sentiment (Past ${isWeekly ? "7" : "30"} days · ${m.sentimentTotalComments.toLocaleString()} comments analyzed):**\n` +
            `- 👍 Positive: ${Math.round(m.sentimentTotalComments * m.sentimentPos / 100).toLocaleString()} (${m.sentimentPos}%) — concentrated on image quality / price-performance / color science\n` +
            `- 😐 Neutral: ${Math.round(m.sentimentTotalComments * m.sentimentNeu / 100).toLocaleString()} (${m.sentimentNeu}%) — questions, usage queries, comparison requests\n` +
            `- 👎 Negative: ${Math.round(m.sentimentTotalComments * m.sentimentNeg / 100).toLocaleString()} (${m.sentimentNeg}%) — focus issues / accessories / shipping\n\n` +
            `**Per-Platform Sentiment Distribution (${isWeekly ? "this week" : "this month"}):**\n` +
            `| Platform | Total Comments | Positive | Neutral | Negative | Comment ER |\n` +
            `|----------|----------------|----------|---------|----------|------------|\n` +
            `| Instagram | 4,021 | 78.2% | 14.8% | 7.0% | 0.282% |\n` +
            `| YouTube   | 2,876 | 74.1% | 16.9% | 9.0% | 0.243% |\n` +
            `| TikTok    | 892   | 76.5% | 17.4% | 6.1% | 0.123% |\n` +
            `| Weibo     | 458   | 71.2% | 19.7% | 9.1% | 0.207% |\n\n` +
            `**Top Positive Keywords:**\n` +
            `- "sharp" — 488 mentions (mostly 135mm LAB / 56mm)\n` +
            `- "color" — 312 (LAB series praised as "ready out of box")\n` +
            `- "value" — 287 (main topic vs Sony GM)\n` +
            `- "build quality" — 231 (brass mount, metal body)\n` +
            `- "bokeh" — 198 (135mm bokeh vs Sony FE 135)\n` +
            `- "lightweight" — 167 (AF series vs Sigma DG)\n` +
            `- "weather sealing" — 124 (LAB selling point)\n` +
            `- "fast aperture" — 109\n\n` +
            `**Top Negative Keywords:**\n` +
            `- "focus hunting" — 89 (mostly AF 75mm / 28mm older models, firmware update needed)\n` +
            `- "tripod mount" — 54 (135mm LAB lacks OEM tripod collar)\n` +
            `- "shipping" — 42 (Europe delivery times)\n` +
            `- "noisy AF motor" — 38 (older AF series)\n` +
            `- "rear filter" — 21 (cine lens lacks rear filter slot)\n` +
            `- "support" — 18 (overseas service turnaround)\n\n` +
            `**Product Satisfaction (Keyword-aggregated):**\n` +
            `- 135mm LAB: ⭐ 4.8/5 (1,234 comments)\n` +
            `- AF 56mm F1.4 Pro: ⭐ 4.7/5 (892 comments)\n` +
            `- AF 85mm F1.4 Pro: ⭐ 4.7/5 (786 comments)\n` +
            `- AF 75mm F1.2: ⭐ 4.3/5 (412 comments, dragged by focus hunting)\n` +
            `- Cine 27mm T2.0: ⭐ 4.9/5 (187 comments, top-rated new product)\n\n` +
            `**Competitor Comparisons (User-initiated mentions):**\n` +
            `- vs Sony G/GM series: 1,287 mentions ("value" overwhelmingly favorable)\n` +
            `- vs Sigma DG DN: 678 ("similar build, 30–40% cheaper")\n` +
            `- vs Tamron: 412 ("lighter, wider aperture")\n\n`,
          
          contentRecsH: "## 💡 7. Content Recommendations (Data-Driven)",
          contentRecsBody:
            `**✅ High-Performance Content (Replicate · Invest More):**\n\n` +
            `| Content Format | Avg ER | vs Mean | Notes |\n` +
            `|----------------|--------|---------|-------|\n` +
            `| Field shoot + equipment review | 6.24% | +58% | Highest performer; KOL-led |\n` +
            `| Product vs competitor comparison | 5.81% | +47% | User decision-stage content |\n` +
            `| Reels / Shorts (under 60s) | 5.21% | +32% | Algorithm-favored, universal |\n` +
            `| BTS / Day-in-the-life | 5.4% | +37% | Authentic, KOL strength |\n` +
            `| Engineer / PM perspective | 4.92% (est.) | +25% | **Matrix opportunity** |\n\n` +
            `**⚠️ Low-Performance Content (Reduce / Improve):**\n\n` +
            `| Content Format | Avg ER | vs Mean | Direction |\n` +
            `|----------------|--------|---------|-----------|\n` +
            `| Pure product demo / unboxing | 2.10% | -47% | Add "use case" narrative |\n` +
            `| Long-form hard advertising | 1.81% | -54% | Pivot to "education + soft placement" |\n` +
            `| Instagram Stories | 1.94% | -51% | Reduce investment / convert to Reels |\n` +
            `| Matrix generic product intro | 2.87% | -27% | Add engineer perspective |\n` +
            `| YouTube long-form (15min+) | 2.78% | -29% | 8-15min mid-form performs better |\n\n` +
            `**📋 Specific Actions (Executable):**\n\n` +
            `1. **Format priority shift**: Reels/Shorts > Mid-form (8-15min) > Static Posts > Stories\n` +
            `2. **Theme ranking**: Field use > Reviews > Comparison > Tutorial > Hard ads (reduce hard ads 35% → 10%)\n` +
            `3. **Matrix account overhaul**: Add "engineer perspective" content (currently 0% · creator feedback "want to see"); cut generic product intros 50%\n` +
            `4. **Cross-platform sync**: Posting same content on IG + YT + TT shows 35% higher ER vs single-platform (data-backed)\n` +
            `5. **Market opportunity**: Increase FPV drone content (industry mean ER 7.8% vs ours 3.94%); pair with AF 16mm F1.4 marketing\n\n` +
            `**🎯 Platform-Specific Strategy:**\n\n` +
            `- **Instagram**: Reels share 23% → target **60%** (Reels ER 5.84% vs Stories 1.94%)\n` +
            `- **YouTube**: Push **8-15min mid-form** (ER 4.92%); Shorts share 12% → **30%** (Shorts ER 6.21%)\n` +
            `- **TikTok**: **3-second hook MUST show product** (data: 2.3x retention gap); **URGENT — launch matrix account**\n` +
            `- **Weibo**: Topic marketing (0 active campaigns currently) + monthly giveaways (giveaways 1.6x ER)\n` +
            `- **Bilibili**: **Launch 1 engineer-POV matrix account** (deep tech content); push mid-long form\n\n` +
            `**🚨 Content Gaps to Address:**\n` +
            `- Matrix completely absent on **TikTok and Bilibili** — launch 1 account each (Q3 priority)\n` +
            `- "Engineer BTS" series absent on all platforms — but mentioned 67 times in comments (latent demand)\n` +
            `- Mainland long-form deep reviews (Bilibili) market untouched — 36 KOL but 0 matrix\n\n`,
          
          eventsH:    "## 🎪 8. Events Progress (Detailed)",
          eventsTable:`| Event | Date | Venue | Material Done | Budget Locked | Key Milestone | Status |\n` +
                      `|-------|------|-------|---------------|---------------|---------------|--------|\n` +
                      `| CineGear LA 2026 | 6/13–15 | LA Convention Center #4520 | 78% (18/22) | $124k / $185k | 4 KOLs confirmed on-site | 🟢 On track |\n` +
                      `| SCAD Spring Workshop | 6/22–24 | Savannah, SCAD Film | 45% | $14k / $28k | Student signups 67/100 | 🟡 In prep |\n` +
                      `| Tokyo Creator Meetup | 7/8 | Tokyo Shibuya | 90% | $22k / $25k | @TokyoLens host confirmed | 🟢 On track |\n` +
                      `| Photokina Cologne | 9/18–21 | Cologne Messe | 22% | $28k / $120k | Booth application pending | 🟡 Early planning |\n\n` +
                      `**Budget Summary:** Committed $358k · Locked $188k (52.5%) · Remaining $170k (mostly Photokina late-stage)\n` +
                      `**Team Allocation:** 8 people across 4 events (Jianbo, Kevin, Maya, Tom, et al.)\n\n`,
          
          attrH:      "## 🔗 9. Attribution Pipeline Status",
          attrBody:   `**Attributed GMV — 40% complete · ETA Jun 5, 2026**\n` +
                      `- ✅ Shopify Webhook registration (Done · May 12 · Engineering)\n` +
                      `- 🟡 Order Sync (In Progress · Tom Liu · started May 22, backfilling 90 days)\n` +
                      `- 🟡 UTM Tagging (In Progress · Maya Wong · May 24, 47 VIA-{handle}-10 codes live)\n` +
                      `- ⚪ Attribution Logic (Pending · Engineering · ETA Jun 1) — match orders to KOL via UTM + cookie\n` +
                      `- ⚪ Dashboard Display (Pending · Engineering · ETA Jun 5)\n\n` +
                      `**Avg ROI — 10% complete · ETA Jun 15, 2026**\n` +
                      `- 🟡 GMV Integration (depends on Shopify pipeline above)\n` +
                      `- 🟡 Cost Tracking (Finance · May 28) — currently Google Sheets, needs Postgres import\n` +
                      `- ⚪ Campaign tagging (Pending · Maya · Jun 8) — tag each spend by campaign_id\n` +
                      `- ⚪ Aggregation (Pending · Engineering · Jun 10)\n` +
                      `- ⚪ Per-KOL ROI drilldown (Pending · Jun 15)\n\n` +
                      `**Current Blocker:** Shopify webhook key rotation requires Engineering + IT coordination.\n\n`,
          
          aiH:        "## 10. Decision Insights",
          aiBody:     `> **Today's Insight (92% confidence):** 135mm LAB campaign performing 23% above average. @PeterLindgren's review drove 47.2% of weekly conversions (tracked via VIA-peter-10 discount code). **Recommendation:** Allocate additional $50k to extend @PeterLindgren content (2 longform + 4 shorts).\n\n` +
                      `**Trending Topic:** #CineGear2025 surging on Twitter/IG, +212% mention volume over past 7 days.\n` +
                      `**Market Opportunity:** FPV drone content averages 7.8% ER (2.0x the cross-platform mean of 3.94%); AF 16mm F1.4 fits this use case.\n` +
                      `**Risk Monitor:** Sony FE 85mm GM II launch impact on AF 85mm F1.4 PRO pending observation; recommend comparative content within 7 days.\n\n`,
          
          footer:     `_Generated by V-KPI Internal Marketing Intelligence · ${dateStr} · Sources: Aspire / V-KPI Database / Shopify (pending) / Manual Sync_`,
        };
    
    // 拼接最终内容
    let md = `${t.title}\n`;
    md += `${t.period} ${periodStr}  \n`;
    md += `${t.generated} ${dateStr}  \n`;
    md += `${t.author}\n\n`;
    md += `---\n\n`;
    
    if (sections.kpiOverview) {
      md += `${t.kpiH}\n\n`;
      md += t.kpiSub;
      md += t.kpiTable;
    }
    if (sections.kolVsCompany) {
      md += `${t.kolVsCompanyH}\n\n`;
      md += t.kolVsCompanyBody;
    }
    if (sections.platformDeepDive) {
      md += `${t.platformH}\n\n`;
      md += t.platformBody;
    }
    if (sections.goodNews) {
      md += `${t.goodH}\n\n`;
      md += t.goodList;
    }
    if (sections.badNews) {
      md += `${t.badH}\n\n`;
      md += t.badList;
    }
    if (sections.brandSentiment) {
      md += `${t.sentimentH}\n\n`;
      md += t.sentimentBody;
    }
    if (sections.contentRecs) {
      md += `${t.contentRecsH}\n\n`;
      md += t.contentRecsBody;
    }
    if (sections.eventsProgress) {
      md += `${t.eventsH}\n\n`;
      md += t.eventsTable;
    }
    if (sections.attribution) {
      md += `${t.attrH}\n\n`;
      md += t.attrBody;
    }
    if (sections.aiInsights) {
      md += `${t.aiH}\n\n`;
      md += t.aiBody;
    }
    
    md += `---\n\n`;
    md += t.footer;
    
    return md;
}
