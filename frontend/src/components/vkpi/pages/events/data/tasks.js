export const TASKS_DATA = {
  evt_cinegear: [
    // ── 4 周前 ──
    { id: "t1", phase: "4w", title: "确认展位规格", owner: "M", collaborators: [], dueDate: "4/20",
      done: true, doneAt: "4/19", doneBy: "M",
      checklist: [
        { label: "展位面积 (10x10 / 10x20)", done: true, value: "10x10" },
        { label: "展位位置确认", done: true, value: "B14" },
        { label: "电源 + 网络需求提交", done: true },
      ],
      notes: "10x10 标准展位 · 位置 B14 · 主通道旁",
    },
    { id: "t2", phase: "4w", title: "设备清单 + 货源确定", owner: "T", collaborators: ["J"], dueDate: "4/25",
      done: true, doneAt: "4/23", doneBy: "T",
      kind: "equipment",
      details: {
        items: [
          { name: "Viltrox 135mm LAB", source: "in_stock_sample", qty: 3, status: "ready", note: "样品库存 3 只" },
          { name: "Viltrox 27mm T2 Cine", source: "in_stock_sample", qty: 2, status: "ready", note: "样品库存 2 只" },
          { name: "Viltrox 56mm Pro", source: "new_purchase", qty: 4, status: "purchased", note: "新采购 4 只 · 5/15 到货" },
          { name: "演示用 Sony FX6 机身", source: "rental", qty: 1, status: "rented", note: "BorrowLenses 租 4 天" },
        ]
      },
      notes: "Demo 用,展会后 56mm 退回 inventory",
    },
    { id: "t3", phase: "4w", title: "预订机票 (5 人)", owner: "M", collaborators: [], dueDate: "5/01",
      done: true, doneAt: "4/28", doneBy: "M",
      checklist: [
        { label: "Maya SFO→LAX 6/9", done: true, value: "United UA1234" },
        { label: "Jianbo LAX→LAX (本地)", done: true, value: "—" },
        { label: "Tom NYC→LAX 6/9", done: true, value: "Delta DL567" },
        { label: "Kevin SEA→LAX 6/9", done: true, value: "Alaska AS890" },
        { label: "Sam Chen LA 本地",  done: true },
      ],
    },
    { id: "t4", phase: "4w", title: "预订酒店", owner: "M", collaborators: [], dueDate: "5/05",
      done: true, doneAt: "5/04", doneBy: "M",
      notes: "AC Hotel by Marriott · 步行 8 分钟到 LCC · 4 间 4 晚",
    },
    { id: "t5", phase: "4w", title: "注册参展商 + 申请保险", owner: "J", collaborators: ["T"], dueDate: "4/15",
      done: true, doneAt: "4/12", doneBy: "J",
      checklist: [
        { label: "参展商注册", done: true },
        { label: "展会保险 ($1M liability)", done: true },
        { label: "Booth setup pass × 5", done: true },
      ],
    },
    
    // ── 2 周前 ──
    { id: "t6", phase: "2w", title: "宣传物料 — 海报 + Brochure", owner: "T", collaborators: ["M"], dueDate: "5/12",
      done: true, doneAt: "5/12", doneBy: "T",
      kind: "materials",
      details: {
        items: [
          { name: "主海报 36x24 (4 张)", source: "ship", qty: 4, status: "shipped", note: "DHL 已寄 · 6/8 到 LA" },
          { name: "Brochure A4 中英", source: "ship", qty: 200, status: "shipped", note: "已印 · 跟海报一起寄" },
          { name: "名片 (5 套)", source: "carry_on", qty: 500, status: "ready", note: "团队自带" },
        ]
      },
    },
    { id: "t7", phase: "2w", title: "展位设计稿 final", owner: "M", collaborators: [], dueDate: "5/18",
      done: true, doneAt: "5/17", doneBy: "M",
      notes: "设计稿 v3 final · 已交付 LCC 搭建团队",
    },
    { id: "t8", phase: "2w", title: "KOL 邀请发送 (8 位)", owner: "J", collaborators: [], dueDate: "5/15",
      done: true, doneAt: "5/14", doneBy: "J",
      checklist: [
        { label: "Sam Chen 邀请 + 接机", done: true },
        { label: "Caleb Pike 邀请", done: true },
        { label: "Mark Bone 邀请", done: true },
        { label: "Diana Park 邀请", done: true },
        { label: "Matti Haapoja 邀请", done: true, value: "待回复" },
        { label: "Sara Dietschy 邀请", done: true, value: "已拒绝" },
        { label: "另外 2 位 backup", done: true },
      ],
    },
    { id: "t9", phase: "2w", title: "媒体邀请 + brief (3 家)", owner: "M", collaborators: [], dueDate: "5/20",
      done: true, doneAt: "5/19", doneBy: "M",
      checklist: [
        { label: "B&H Photo", done: true, value: "确认" },
        { label: "DPReview", done: true, value: "确认" },
        { label: "Engadget", done: true, value: "确认" },
      ],
    },
    { id: "t10", phase: "2w", title: "海关报关材料 (设备)", owner: "T", collaborators: [], dueDate: "5/22",
      done: true, doneAt: "5/22", doneBy: "T",
      notes: "ATA Carnet 已申请",
    },
    { id: "t11", phase: "2w", title: "礼品/周边定制 + 库存确认", owner: "M", collaborators: ["J"], dueDate: "5/27",
      done: false, alert: "warn",
      kind: "materials",
      details: {
        items: [
          { name: "Viltrox 帆布袋", source: "new_purchase", qty: 200, status: "in_production", note: "工厂出货 5/25" },
          { name: "镜头帽 (周边)", source: "in_stock", qty: 300, status: "ready", note: "库存充足" },
          { name: "贴纸 (限定款)", source: "ship", qty: 500, status: "printing", note: "5/27 完成 · DHL 寄 LA" },
        ]
      },
      notes: "⚠ 帆布袋工厂进度紧张,Maya 5/25 确认",
    },
    { id: "t12", phase: "2w", title: "KOL 现场接待清单", owner: "J", collaborators: [], dueDate: "5/28",
      done: false,
      checklist: [
        { label: "每个 KOL 拍摄时段表", done: false },
        { label: "接送车安排", done: false },
        { label: "现场摄影协助 (Tom 兼任)", done: true },
        { label: "KOL 答谢餐 (6/12 晚)", done: true, value: "已预订 The Original" },
      ],
    },
    
    // ── 1 周前 ──
    { id: "t13", phase: "1w", title: "设备装箱 + 运输", owner: "T", collaborators: [], dueDate: "6/01",
      done: false, kind: "equipment",
      details: {
        items: [
          { name: "镜头组 + 配件", source: "ship", qty: 10, status: "pending", note: "DHL · 4 箱 · 防震包装" },
          { name: "演示电视/支架", source: "ship", qty: 2, status: "pending", note: "Pelican 大箱 × 2" },
        ]
      },
    },
    { id: "t14", phase: "1w", title: "行程 + 酒店确认", owner: "M", collaborators: [], dueDate: "6/02", done: false },
    { id: "t15", phase: "1w", title: "媒体 brief + KOL info pack 发送", owner: "M", collaborators: [], dueDate: "6/03", done: false },
    { id: "t16", phase: "1w", title: "现场签到 iPad app 配置", owner: "K", collaborators: ["T"], dueDate: "6/03", done: false,
      notes: "Lead 收集 app · 配字段 + 测试",
    },
    
    // ── 现场 ──
    { id: "t17", phase: "live", title: "展位搭建", owner: "T", collaborators: ["K"], dueDate: "6/10 09:00", done: false },
    { id: "t18", phase: "live", title: "媒体接待 (3 家)", owner: "M", collaborators: [], dueDate: "6/10-6/13", done: false },
    { id: "t19", phase: "live", title: "KOL 现场拍摄协调", owner: "J", collaborators: ["T"], dueDate: "6/10-6/13", done: false },
    { id: "t20", phase: "live", title: "Lead 收集 + 录入", owner: "K", collaborators: [], dueDate: "6/10-6/13", done: false },
    
    // ── 复盘 ──
    { id: "t21", phase: "after", title: "费用报销 + 凭证归档", owner: "All", collaborators: [], dueDate: "6/20", done: false },
    { id: "t22", phase: "after", title: "Lead 跟进分配", owner: "J", collaborators: ["M"], dueDate: "6/16", done: false },
    { id: "t23", phase: "after", title: "KOL 视频内容收集 + 验收", owner: "J", collaborators: [], dueDate: "6/25", done: false },
    { id: "t24", phase: "after", title: "复盘文档 + ROI 计算", owner: "M", collaborators: ["J"], dueDate: "6/30", done: false },
  ],
};
