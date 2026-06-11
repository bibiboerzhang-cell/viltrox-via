# KOL Pool 需求总册 · 2026-06-11 重编
(来源:5月原型会话 / 6月施工计划 / 审计+四环survey / 裁决记录)

══ A · 已建成并验真(成品资产,只许接线不许重做)══
 A1 统一智能输入框:URL/ID/文字自动分流——视频URL→已在库"只析
    此视频"/新人"建档并析";账号URL→execute auto+增量since;
    文字→语义召回(TikTok 分支按裁决=显示风险提示,不灰掉)
 A2 Pool 总数大窗:1123 全量列表+搜索+平台/已分析筛选 chips+
    窗内可发起分析
 A3 13 区块详情 Drawer【成品铁律:1:1 保留】:头部+bio+顶部4指标
    (Real ER/HHI/Loyalty/Trend)+11维雷达+联系&代表作+视频深析+
    设备&升级机会+地理分布·Reach+V6 Fit 10项breakdown+Viltrox
    适配+推荐产品线+风险点+合作历史
 A4 搜索会话 hub(ledger tier):session 表+orchestrator,
    入队→泳道→轮询契约
 A5 智能输入的泳道联动(终态回填/partial/ETA)

══ B · 进行中(本周战役)══
 B1 解冻减法仪式:删 V615Sidebar 死文件+GEN2 假徽章收敛+休眠监听
    (⚠️ GEN2 假徽章删=对;原型 Discover/Signals/Agents 真数据
    徽章是想要项,删假≠放弃真,真徽章挂 D6)
 B2 差量诊断四盲区:交互健康/性能基线/数据新鲜度/机器词
    (+本令 a+/a++ 两问)
 B3 四环漏斗 C1-C10【已批】:107 收藏表→三端点→Pool/Drawer
    收藏入口→My KOL 改读收藏→backfill 721(C6 铁前置)→
    Projects 选择器切 My KOL+修活"已关注"死筛选→写入侧防绕过→
    取消收藏在役软禁止(409+清单+force)→Dashboard 四环聚合
    (顺修 stage 双拼写)

══ C · 已批排队(P5 后裁决在档)══
 C1 My KOL 优化前五:收藏持久化(=B3)/漏斗阶段真实化+measured
    独立环/团队矩阵假数据剥离(可独立先做)/viltroxOnly 开关
    语义/列表性能+硬编码默认选中
 C2 批6 UI(Pool 切面):层级重排(智能输入+最近任务上黄金位,
    六统计卡折叠摘要条)/默认列砍至5列其余进 Drawer/骨架屏/
    机器词白名单/英雄区不均匀化
 C3 实时规范:数据新鲜度时间戳 pill(TopBar+sync 弹层)/
    载入即死字段接轮询或时间戳
 C4 连通:Pool↔Projects 双向跳转(项目里 KOL→Drawer/Drawer
    合作历史→项目详情)/任务条目→所属 KOL

══ D · 原型遗产·状态待核(差量诊断顺手查,核完销账或入队)══
 D1 ContactModal 邮件流 V6.15.4:产品 chip 选择器(读
    recommended_product_lines)/选品自动重生成主题/AI 写信按钮
    重写主题+正文/正文按 KOL 信号分支(合作史/竞品/loyalty/
    geo/trend)/规则:切品只动主题,正文重写须显式点 AI
 D2 "Why V6 Fit = N?"四 bullet 解释区(纯前端规则,读 v6_breakdown)
 D3 数据新鲜度 pill(=C3 前端件)
 D4 lux 视觉基线全量对齐:Sora/Inter/渐变数字/微动效/入场动画
 D5 原型 7 模式按钮=纯演示,不迁移
 D6 侧栏真数据徽章(Discover/Signals/Agents)——等数据侧支撑

══ E · 数据侧依赖(Pool 的血,Codex 回归单)══
 E1 深析覆盖 148/1122 → 600+(铺量 wave)
 E2 V6 Fit 10 因子列不落库——"—"是诚实空值,breakdown 全亮需
    因子持久化(评分语义冻结,只读不改算法)
 E3 llm_v6_fit 改名/贴"LLM 分·未定标"标签(P0 旧账,闸C)
 E4 auto-fanout 批量分析:闸住,待小批成本验证+真实并发数确认

══ F · 双轨发现(新需求 2026-06-11,设计先行)══
 F1 对话式分流:问句输入("我们有个xxx镜头想找KOL")→识别找人
    意图→追问平台(TK/IG/FB/YT 多选 chips)→带平台约束召回。
    单发输入框升级为可追问
 F2 库内召回 15:既有语义召回+平台过滤,按 V6 Fit 排序
 F3 全网发现 15【闸A+闸C,设计稿先行零施工】:Gemini Google
    Search grounding 泛行业搜索→候选卡(名/平台/粉丝/链接/
    一句话理由)→落"外部候选暂存区",绝不直接入 kol_pool。
    设计稿要件:grounding 单次成本实测/每问预算上限/候选质量
    校验(最低粉丝/平台白名单/机构号过滤)/暂存区表结构
    (一张表,migration 三段式)——设计稿过闸后才施工
 F4 暂存区→建档:人工勾选→复用 A1 新人管线(析代表视频→建档
    →全量同步);建档前 handle/channel_id 撞库查重
 旅程合龙:问→选平台→30 候选(15库内+15全网)→勾选→My KOL
    →进项目——F 是旅程前半,B3 漏斗是后半,缺一不成"聪明可用"

══ 红线(全程不变量)══
 viltrox_fit_score 唯一写点 pool.py:838 / 禁碰 /enrich 与
 run_kol_pool_gemini_single / 主列表默认排序 COALESCE(fit,0) DESC
 / 13 区块不简化 / rule_v0+rubric 冻结 / 新 LLM 产物一律 llm_
 前缀+未定标标签
