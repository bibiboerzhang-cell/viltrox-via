import React, { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Activity,
  Archive,
  Bell,
  Bot,
  Box,
  Brain,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Command,
  Compass,
  Database,
  FileText,
  Gauge,
  Globe2,
  Layers,
  Library,
  MessageCircle,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Radar,
  Rocket,
  Search,
  ShieldCheck,
  Sparkles,
  Sun,
  Target,
  TrendingUp,
  Users,
  Zap,
  type LucideIcon,
} from "lucide-react";
import "./command-os-prototype.css";

type Mode = "simple" | "deep";

type NavKey =
  | "dashboard"
  | "gtmCommand"
  | "intelligent"
  | "replyQueue"
  | "sku360"
  | "kol-pool"
  | "my-kol"
  | "projects"
  | "events"
  | "shopify"
  | "dealers"
  | "triage"
  | "dataQuery"
  | "marketTrends"
  | "skillStudio"
  | "kolProfile"
  | "launchpad"
  | "autonomy"
  | "marketVoice"
  | "creativeLibrary"
  | "strategyBoard";

type NavItem = {
  key: NavKey;
  label: string;
  labelEn: string;
  icon: LucideIcon;
  signal: "core" | "live" | "learn" | "ops";
  simple?: boolean;
};

type NavGroup = {
  id: string;
  label: string;
  icon: LucideIcon;
  items: NavKey[];
};

type PanelCopy = {
  title: string;
  subtitle: string;
  signal: string;
  decision: string;
  actions: string[];
  metrics: Array<{ label: string; value: string; trend: string }>;
};

const NAV_ITEMS: NavItem[] = [
  { key: "dashboard", label: "总览", labelEn: "Overview", icon: Gauge, signal: "core", simple: true },
  { key: "gtmCommand", label: "GTM Command", labelEn: "Launch OS", icon: Compass, signal: "core", simple: true },
  { key: "intelligent", label: "Intelligent 问答", labelEn: "Ask", icon: Sparkles, signal: "live", simple: true },
  { key: "replyQueue", label: "回复队列", labelEn: "Inbox", icon: MessageCircle, signal: "ops", simple: true },
  { key: "sku360", label: "SKU 360°", labelEn: "Product Map", icon: Box, signal: "live", simple: true },
  { key: "kol-pool", label: "KOL Pool", labelEn: "Creator Graph", icon: Users, signal: "core", simple: true },
  { key: "my-kol", label: "MY KOL", labelEn: "Owned Accounts", icon: CircleDot, signal: "learn", simple: true },
  { key: "projects", label: "Projects", labelEn: "Execution", icon: Layers, signal: "ops", simple: true },
  { key: "events", label: "Events", labelEn: "Moments", icon: Bell, signal: "ops" },
  { key: "shopify", label: "Shopify", labelEn: "DTC", icon: Archive, signal: "ops" },
  { key: "dealers", label: "Dealers", labelEn: "Retail", icon: Globe2, signal: "live", simple: true },
  { key: "triage", label: "Triage", labelEn: "QA", icon: ShieldCheck, signal: "ops" },
  { key: "dataQuery", label: "Data Query", labelEn: "Evidence", icon: Database, signal: "learn" },
  { key: "marketTrends", label: "Market Trends", labelEn: "Radar", icon: TrendingUp, signal: "live" },
  { key: "skillStudio", label: "Skill Studio", labelEn: "Agent Lab", icon: Bot, signal: "learn" },
  { key: "kolProfile", label: "KOL 档案", labelEn: "Profile", icon: FileText, signal: "learn" },
  { key: "launchpad", label: "发射台", labelEn: "Launchpad", icon: Rocket, signal: "core", simple: true },
  { key: "autonomy", label: "自治驾照", labelEn: "Autonomy", icon: ShieldCheck, signal: "learn" },
  { key: "marketVoice", label: "市场之声", labelEn: "Voice", icon: Radar, signal: "live", simple: true },
  { key: "creativeLibrary", label: "创意资产库", labelEn: "Creative", icon: Library, signal: "learn" },
  { key: "strategyBoard", label: "战略台", labelEn: "Strategy", icon: Target, signal: "core", simple: true },
];

const GROUPS: NavGroup[] = [
  { id: "command", label: "今日指挥", icon: Command, items: ["dashboard", "gtmCommand", "intelligent", "replyQueue"] },
  { id: "brain", label: "增长大脑", icon: Brain, items: ["strategyBoard", "launchpad", "marketVoice", "marketTrends", "sku360"] },
  { id: "network", label: "执行网络", icon: Zap, items: ["kol-pool", "my-kol", "projects", "dealers", "events", "shopify"] },
  { id: "learning", label: "证据学习", icon: Database, items: ["kolProfile", "creativeLibrary", "dataQuery", "triage"] },
  { id: "system", label: "自治系统", icon: Bot, items: ["autonomy", "skillStudio"] },
];

const PANEL_COPY: Record<NavKey, PanelCopy> = {
  dashboard: {
    title: "每日增长指挥盘",
    subtitle: "把市场信号、项目履约、KOL 证据和渠道反馈压缩成当天路线。",
    signal: "本周 300W EVO 搜索热度和视频证据同时抬升。",
    decision: "优先验证美国创作者 + 线下 dealer 双线联动。",
    actions: ["看 6 个高信号项目", "确认 3 个库存风险", "把 2 条失败视频放入重试池"],
    metrics: [
      { label: "市场信号", value: "84", trend: "+12" },
      { label: "执行健康", value: "71%", trend: "+5%" },
      { label: "学习样本", value: "1,248", trend: "+96" },
    ],
  },
  gtmCommand: {
    title: "GTM Command",
    subtitle: "从新品、国家、预算推导渠道、内容、人群和执行节奏。",
    signal: "新镜头上市窗口需要先锁定样片场景，再扩展 KOL。",
    decision: "美国走 creator proof，德国走 dealer trust，日本走官方账号教育。",
    actions: ["生成 3 套打法", "切出 dealer 任务", "为独立站补充内容落点"],
    metrics: [
      { label: "上市路径", value: "3", trend: "ready" },
      { label: "置信度", value: "76%", trend: "+8%" },
      { label: "风险项", value: "5", trend: "-2" },
    ],
  },
  intelligent: {
    title: "智能问答",
    subtitle: "只暴露结论和证据入口，复杂检索沉到系统底层。",
    signal: "同一问题可切换精简回答和深度证据链。",
    decision: "默认输出行动答案，需要时展开来源、SQL、视频证据。",
    actions: ["回答市场问题", "追溯证据", "转成行动任务"],
    metrics: [
      { label: "可回答域", value: "9", trend: "+2" },
      { label: "证据覆盖", value: "68%", trend: "+9%" },
      { label: "待补数据", value: "14", trend: "-3" },
    ],
  },
  replyQueue: {
    title: "回复队列",
    subtitle: "让员工处理沟通，但系统决定优先级和下一步。",
    signal: "高价值 KOL 超过 24 小时未回复会自动抬升。",
    decision: "先处理会影响项目窗口和出片节奏的沟通。",
    actions: ["催 4 个高价值合作", "补 5 个联系方式", "关闭 3 个低质量线索"],
    metrics: [
      { label: "待处理", value: "42", trend: "-11" },
      { label: "高价值", value: "9", trend: "+3" },
      { label: "超时", value: "6", trend: "-4" },
    ],
  },
  sku360: {
    title: "SKU 360°",
    subtitle: "把产品卖点、地区反馈、内容题材和渠道表现放到一张产品地图。",
    signal: "85mm 人像内容在日本和韩国账号互动率更稳。",
    decision: "人像样片先走官方账号和中腰部创作者。",
    actions: ["补全 SKU 卖点", "找地区样片", "标注适配人群"],
    metrics: [
      { label: "SKU 覆盖", value: "37", trend: "+4" },
      { label: "强机会", value: "8", trend: "+2" },
      { label: "内容缺口", value: "12", trend: "-1" },
    ],
  },
  "kol-pool": {
    title: "KOL Pool",
    subtitle: "不是名单库，而是可验证的创作者证据图谱。",
    signal: "创作者历史视频风格比粉丝量更能解释转化。",
    decision: "优先找能证明产品场景的人，而不是只找大号。",
    actions: ["筛 10 个创作者", "补齐证据", "标记合作风险"],
    metrics: [
      { label: "有效 KOL", value: "6,812", trend: "+128" },
      { label: "证据视频", value: "18k", trend: "+420" },
      { label: "可联系", value: "54%", trend: "+6%" },
    ],
  },
  "my-kol": {
    title: "MY KOL",
    subtitle: "把公司自有账号和合作关系纳入增长判断。",
    signal: "官方账号适合教育型内容，但不适合所有上市阶段。",
    decision: "先用官方账号建立解释，再让 KOL 放大具体场景。",
    actions: ["看账号日指标", "挑 2 条可复用脚本", "标记可二次分发内容"],
    metrics: [
      { label: "账号健康", value: "82", trend: "+7" },
      { label: "可复用视频", value: "24", trend: "+5" },
      { label: "互动波动", value: "低", trend: "stable" },
    ],
  },
  projects: {
    title: "Projects",
    subtitle: "把寄样、履约、出片、回收数据串成真实执行闭环。",
    signal: "项目卡点通常来自物流、创作者节奏和素材缺口。",
    decision: "先救会影响上市窗口的项目，不平均用力。",
    actions: ["观察 3 个项目", "处理 2 个物流异常", "复盘 1 个失败合作"],
    metrics: [
      { label: "进行中", value: "116", trend: "+9" },
      { label: "风险项目", value: "13", trend: "-4" },
      { label: "按期率", value: "73%", trend: "+6%" },
    ],
  },
  events: {
    title: "Events",
    subtitle: "把展会、上市节点和平台事件变成内容窗口。",
    signal: "线下事件之后 72 小时是内容追投窗口。",
    decision: "把 Cine Gear 相关素材转成创作者二次传播任务。",
    actions: ["归档事件资产", "绑定项目", "生成追投名单"],
    metrics: [
      { label: "事件窗口", value: "7", trend: "+1" },
      { label: "待复用资产", value: "31", trend: "+8" },
      { label: "过期风险", value: "4", trend: "-2" },
    ],
  },
  shopify: {
    title: "Shopify / 独立站",
    subtitle: "把内容流量落到产品页和转化链路，而不是只看曝光。",
    signal: "内容增长但落地页解释不足会损失转化。",
    decision: "为 300W EVO 单独做场景证据落点。",
    actions: ["检查产品页", "补创作者证据", "标记内容来源"],
    metrics: [
      { label: "产品页", value: "18", trend: "+2" },
      { label: "内容落点", value: "44", trend: "+6" },
      { label: "追踪缺口", value: "9", trend: "-3" },
    ],
  },
  dealers: {
    title: "Dealers",
    subtitle: "让渠道判断进入产品上市路线，而不是事后看销量。",
    signal: "实体渠道适合承接高信任、高客单价产品。",
    decision: "德国 dealer 先吃教育内容，美国 dealer 吃样片证明。",
    actions: ["挑 6 个重点 dealer", "匹配内容资产", "同步活动节奏"],
    metrics: [
      { label: "重点渠道", value: "52", trend: "+5" },
      { label: "地区机会", value: "11", trend: "+2" },
      { label: "待补反馈", value: "17", trend: "-6" },
    ],
  },
  triage: {
    title: "Triage",
    subtitle: "把数据异常、质量问题和上线风险放到一个审核入口。",
    signal: "证据不完整的结论不能进入自动推荐。",
    decision: "低置信任务只生成观察，不自动派发。",
    actions: ["审核 8 条数据", "隔离 2 个异常", "重算 4 个评分"],
    metrics: [
      { label: "待审核", value: "28", trend: "-7" },
      { label: "阻断项", value: "3", trend: "-1" },
      { label: "自动通过", value: "81%", trend: "+4%" },
    ],
  },
  dataQuery: {
    title: "Data Query",
    subtitle: "深度模式才打开的证据工作台，默认不压到用户面前。",
    signal: "所有推荐都需要能追到数据来源和计算口径。",
    decision: "把 SQL、缓存、证据文件作为后台能力，不做主界面负担。",
    actions: ["查证据链", "导出样本", "保存分析口径"],
    metrics: [
      { label: "可查表", value: "43", trend: "+6" },
      { label: "缓存命中", value: "62%", trend: "+10%" },
      { label: "口径差异", value: "5", trend: "-2" },
    ],
  },
  marketTrends: {
    title: "Market Trends",
    subtitle: "外部趋势进入系统，但最终只输出方向和风险。",
    signal: "Reddit 和 YouTube 评论更适合发现痛点，不直接决定投放。",
    decision: "外部信号先做辅助权重，必须和内部履约结果交叉验证。",
    actions: ["合并 4 类信号", "提取痛点", "标记竞争对手异动"],
    metrics: [
      { label: "趋势源", value: "6", trend: "+2" },
      { label: "痛点主题", value: "19", trend: "+5" },
      { label: "竞品异动", value: "4", trend: "+1" },
    ],
  },
  skillStudio: {
    title: "Skill Studio",
    subtitle: "把可重复的分析步骤沉淀成可审核的智能体技能。",
    signal: "智能体先做建议和核验，不直接越权执行。",
    decision: "每个 agent 都要绑定输入、输出、成本和审核状态。",
    actions: ["拆 3 个技能", "检查成本", "加入人工审核"],
    metrics: [
      { label: "可用技能", value: "12", trend: "+3" },
      { label: "待审核", value: "6", trend: "-1" },
      { label: "平均成本", value: "$0.18", trend: "-9%" },
    ],
  },
  kolProfile: {
    title: "KOL 档案",
    subtitle: "把创作者从账号变成可解释、可复盘、可继续合作的对象。",
    signal: "合作后的真实表现要反哺未来推荐。",
    decision: "强合作对象进入长期关系图谱，失败合作进入重试条件库。",
    actions: ["补档案", "看历史履约", "标注内容强项"],
    metrics: [
      { label: "完整档案", value: "2,914", trend: "+77" },
      { label: "可复盘合作", value: "638", trend: "+21" },
      { label: "高潜力", value: "186", trend: "+12" },
    ],
  },
  launchpad: {
    title: "发射台",
    subtitle: "新品从上市日期倒推内容、渠道、KOL 和 dealer 节奏。",
    signal: "上市不是发布当天，是前后 30 天的连续动作。",
    decision: "先建样片证明，再做地区扩散和渠道承接。",
    actions: ["生成倒排计划", "锁定首批 KOL", "分配 dealer 资产"],
    metrics: [
      { label: "待上市", value: "5", trend: "+1" },
      { label: "准备度", value: "64%", trend: "+11%" },
      { label: "缺口", value: "9", trend: "-3" },
    ],
  },
  autonomy: {
    title: "自治驾照",
    subtitle: "控制系统什么时候只能建议，什么时候可以自动执行。",
    signal: "越靠近外部沟通和成本支出，越需要审核阈值。",
    decision: "先开放低风险自动化，高价值动作保留人工批准。",
    actions: ["调阈值", "看失败样本", "审计 agent 输出"],
    metrics: [
      { label: "自动等级", value: "L2", trend: "+0" },
      { label: "需批准", value: "18", trend: "-5" },
      { label: "回滚记录", value: "2", trend: "ok" },
    ],
  },
  marketVoice: {
    title: "市场之声",
    subtitle: "把评论、论坛、竞品动向和平台反馈做成市场感知层。",
    signal: "用户痛点比热词更重要，热词只是入口。",
    decision: "痛点要回写到产品卖点、内容脚本和 dealer 话术。",
    actions: ["抽取评论痛点", "对齐 SKU", "生成话术要点"],
    metrics: [
      { label: "评论样本", value: "9.6k", trend: "+1.1k" },
      { label: "痛点聚类", value: "26", trend: "+4" },
      { label: "可行动", value: "14", trend: "+3" },
    ],
  },
  creativeLibrary: {
    title: "创意资产库",
    subtitle: "管理能被复用的内容资产，而不是只存文件。",
    signal: "强样片可以跨 KOL、dealer、独立站和官方账号复用。",
    decision: "资产需要绑定适用产品、地区、人群和效果。",
    actions: ["标注 12 个资产", "关联 SKU", "筛出可投放素材"],
    metrics: [
      { label: "有效资产", value: "412", trend: "+34" },
      { label: "可复用", value: "149", trend: "+18" },
      { label: "缺标签", value: "27", trend: "-9" },
    ],
  },
  strategyBoard: {
    title: "战略台",
    subtitle: "把产品、地区、渠道和内容打法变成可验证的增长方针。",
    signal: "300W EVO 适合先打高信任场景，不适合泛流量铺开。",
    decision: "预算先押美国 creator proof 和德国 dealer education。",
    actions: ["确认产品优先级", "分配地区预算", "创建复盘节点"],
    metrics: [
      { label: "策略路径", value: "4", trend: "+1" },
      { label: "预算覆盖", value: "78%", trend: "+6%" },
      { label: "预测风险", value: "中", trend: "watch" },
    ],
  },
};

const SIGNAL_LABELS = {
  core: "Core",
  live: "Live",
  learn: "Learn",
  ops: "Ops",
} as const;

const getItem = (key: NavKey) => NAV_ITEMS.find((item) => item.key === key) ?? NAV_ITEMS[0];

export default function CommandOSPrototype() {
  const prefersReducedMotion = useReducedMotion();
  const [mode, setMode] = useState<Mode>("simple");
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [activeKey, setActiveKey] = useState<NavKey>("gtmCommand");
  const [collapsed, setCollapsed] = useState(false);
  const [motionOn, setMotionOn] = useState(!prefersReducedMotion);
  const [query, setQuery] = useState("");
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>(() =>
    GROUPS.reduce<Record<string, boolean>>((acc, group) => {
      acc[group.id] = true;
      return acc;
    }, {}),
  );

  useEffect(() => {
    if (prefersReducedMotion) {
      setMotionOn(false);
    }
  }, [prefersReducedMotion]);

  const filteredGroups = useMemo(() => {
    const normalized = query.trim().toLowerCase();

    return GROUPS.map((group) => {
      const items = group.items
        .map(getItem)
        .filter((item) => mode === "deep" || item.simple)
        .filter((item) => {
          if (!normalized) return true;
          return `${item.label} ${item.labelEn}`.toLowerCase().includes(normalized);
        });
      return { ...group, items };
    }).filter((group) => group.items.length > 0);
  }, [mode, query]);

  const activeItem = getItem(activeKey);
  const activeCopy = PANEL_COPY[activeKey];
  const visibleCount = filteredGroups.reduce((sum, group) => sum + group.items.length, 0);

  const setModeAndKeepSelection = (nextMode: Mode) => {
    setMode(nextMode);
    const active = getItem(activeKey);
    if (nextMode === "simple" && !active.simple) {
      setActiveKey("gtmCommand");
    }
  };

  const motionProps = motionOn
    ? {
        initial: { opacity: 0, y: 8 },
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 0, y: -8 },
        transition: { duration: 0.28 },
      }
    : {
        initial: false as const,
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 1, y: 0 },
        transition: { duration: 0 },
      };

  return (
    <div className={`command-os command-os--${theme}`}>
      <div className="command-os__signal" aria-hidden="true">
        <div className={motionOn ? "command-os__scan is-on" : "command-os__scan"} />
        <div className="command-os__grid" />
      </div>

      <aside className={collapsed ? "command-os__sidebar is-collapsed" : "command-os__sidebar"}>
        <div className="command-os__brand">
          <button
            className="command-os__icon-button"
            type="button"
            aria-label={collapsed ? "展开导航" : "收起导航"}
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          </button>
          {!collapsed && (
            <div>
              <div className="command-os__eyebrow">V-KPI</div>
              <div className="command-os__brand-title">Market Command OS</div>
            </div>
          )}
        </div>

        {!collapsed && (
          <div className="command-os__search">
            <Search size={16} />
            <input
              aria-label="搜索功能"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search command"
            />
          </div>
        )}

        <nav className="command-os__nav" aria-label="Command navigation">
          {filteredGroups.map((group) => {
            const GroupIcon = group.icon;
            const isOpen = openGroups[group.id] ?? true;

            return (
              <section className="command-os__group" key={group.id}>
                {!collapsed && (
                  <button
                    className="command-os__group-button"
                    type="button"
                    onClick={() =>
                      setOpenGroups((current) => ({
                        ...current,
                        [group.id]: !(current[group.id] ?? true),
                      }))
                    }
                  >
                    <span>
                      <GroupIcon size={14} />
                      {group.label}
                    </span>
                    <ChevronDown className={isOpen ? "is-open" : ""} size={14} />
                  </button>
                )}

                <AnimatePresence initial={false}>
                  {(collapsed || isOpen) && (
                    <motion.div
                      className="command-os__group-items"
                      initial={motionOn ? { height: 0, opacity: 0 } : false}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={motionOn ? { height: 0, opacity: 0 } : undefined}
                      transition={{ duration: motionOn ? 0.2 : 0 }}
                    >
                      {group.items.map((item) => {
                        const Icon = item.icon;
                        const isActive = item.key === activeKey;
                        return (
                          <button
                            className={isActive ? "command-os__nav-item is-active" : "command-os__nav-item"}
                            key={item.key}
                            type="button"
                            title={collapsed ? item.label : undefined}
                            onClick={() => setActiveKey(item.key)}
                          >
                            <span className="command-os__nav-icon">
                              <Icon size={18} />
                            </span>
                            {!collapsed && (
                              <>
                                <span className="command-os__nav-copy">
                                  <span>{item.label}</span>
                                  <small>{item.labelEn}</small>
                                </span>
                                <span className={`command-os__badge command-os__badge--${item.signal}`}>
                                  {SIGNAL_LABELS[item.signal]}
                                </span>
                              </>
                            )}
                          </button>
                        );
                      })}
                    </motion.div>
                  )}
                </AnimatePresence>
              </section>
            );
          })}
        </nav>

        {!collapsed && (
          <div className="command-os__sidebar-footer">
            <div>
              <span>{visibleCount}</span>
              <small>/ {NAV_ITEMS.length} modules</small>
            </div>
            <div className="command-os__mini-bars" aria-hidden="true">
              <i />
              <i />
              <i />
            </div>
          </div>
        )}
      </aside>

      <main className="command-os__main">
        <header className="command-os__topbar">
          <div>
            <div className="command-os__eyebrow">Market Brain Preview</div>
            <h1>干净、透明、黑白分明的增长大脑入口</h1>
          </div>

          <div className="command-os__controls" aria-label="页面模式">
            <div className="command-os__segmented">
              <button
                className={mode === "simple" ? "is-active" : ""}
                type="button"
                onClick={() => setModeAndKeepSelection("simple")}
              >
                精简
              </button>
              <button
                className={mode === "deep" ? "is-active" : ""}
                type="button"
                onClick={() => setModeAndKeepSelection("deep")}
              >
                深度
              </button>
            </div>
            <button
              className="command-os__icon-button"
              type="button"
              aria-label={theme === "dark" ? "切换亮色" : "切换暗色"}
              onClick={() => setTheme((value) => (value === "dark" ? "light" : "dark"))}
            >
              {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <button
              className={motionOn ? "command-os__motion is-active" : "command-os__motion"}
              type="button"
              onClick={() => setMotionOn((value) => !value)}
            >
              <Activity size={16} />
              Motion
            </button>
          </div>
        </header>

        <section className="command-os__workspace">
          <div className="command-os__hero">
            <div className="command-os__hero-copy">
              <div className={`command-os__status command-os__status--${activeItem.signal}`}>
                <span />
                {SIGNAL_LABELS[activeItem.signal]} Signal
              </div>
              <AnimatePresence mode="wait">
                <motion.div key={activeKey} {...motionProps}>
                  <h2>{activeCopy.title}</h2>
                  <p>{activeCopy.subtitle}</p>
                </motion.div>
              </AnimatePresence>
            </div>

            <div className="command-os__live-panel">
              <div className="command-os__panel-top">
                <span>Decision Layer</span>
                <ChevronRight size={16} />
              </div>
              <AnimatePresence mode="wait">
                <motion.div key={`${activeKey}-decision`} {...motionProps}>
                  <strong>{activeCopy.signal}</strong>
                  <p>{activeCopy.decision}</p>
                </motion.div>
              </AnimatePresence>
              <div className={motionOn ? "command-os__path is-moving" : "command-os__path"} aria-hidden="true">
                <span />
              </div>
            </div>
          </div>

          <div className="command-os__metric-row">
            {activeCopy.metrics.map((metric) => (
              <motion.div
                className="command-os__metric"
                key={metric.label}
                layout={motionOn}
                transition={{ duration: motionOn ? 0.22 : 0 }}
              >
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
                <small>{metric.trend}</small>
              </motion.div>
            ))}
          </div>

          <div className="command-os__content-grid">
            <section className="command-os__module-pane">
              <div className="command-os__section-head">
                <div>
                  <span>Now</span>
                  <h3>今日行动线路</h3>
                </div>
                <button className="command-os__ghost-button" type="button">
                  派发
                </button>
              </div>

              <AnimatePresence mode="wait">
                <motion.ol className="command-os__action-list" key={`${activeKey}-actions`} {...motionProps}>
                  {activeCopy.actions.map((action, index) => (
                    <li key={action}>
                      <span>{String(index + 1).padStart(2, "0")}</span>
                      <p>{action}</p>
                    </li>
                  ))}
                </motion.ol>
              </AnimatePresence>
            </section>

            <section className="command-os__module-pane command-os__module-pane--map">
              <div className="command-os__section-head">
                <div>
                  <span>Route</span>
                  <h3>产品上市推演</h3>
                </div>
                <button className="command-os__ghost-button" type="button">
                  模拟
                </button>
              </div>

              <div className={motionOn ? "command-os__route is-moving" : "command-os__route"}>
                <div>
                  <strong>Product</strong>
                  <span>300W EVO</span>
                </div>
                <i />
                <div>
                  <strong>Market</strong>
                  <span>US / DE / JP</span>
                </div>
                <i />
                <div>
                  <strong>Channel</strong>
                  <span>KOL + Dealer + DTC</span>
                </div>
              </div>

              <div className="command-os__prediction">
                <span>预测</span>
                <strong>先证明场景，再扩大地区；先让系统给路线，员工只处理确认和例外。</strong>
              </div>
            </section>

            <section className="command-os__module-pane command-os__module-pane--wide">
              <div className="command-os__section-head">
                <div>
                  <span>Learning</span>
                  <h3>系统学习沉淀</h3>
                </div>
                <button className="command-os__ghost-button" type="button">
                  查看证据
                </button>
              </div>

              <div className="command-os__learning">
                {["产品适配", "地区反应", "内容风格", "渠道承接"].map((label, index) => (
                  <div key={label}>
                    <span>{label}</span>
                    <div>
                      <i style={{ width: `${72 + index * 6}%` }} />
                    </div>
                    <strong>{72 + index * 6}%</strong>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </section>
      </main>
    </div>
  );
}
