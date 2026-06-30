export interface AiTaskTemplate {
  phase: string;
  title: string;
  kind: string | null;
  suggested: boolean;
}

export const AI_TASK_TEMPLATES: Record<string, AiTaskTemplate[]> = {
  tradeshow: [
    { phase: "4w", title: "确认展位规格 + 位置", kind: null, suggested: true },
    { phase: "4w", title: "注册参展商 + 申请保险", kind: null, suggested: true },
    { phase: "4w", title: "预订机票 + 酒店", kind: null, suggested: true },
    { phase: "4w", title: "设备清单 + 货源确定", kind: "equipment", suggested: true },
    { phase: "2w", title: "展位设计稿 final", kind: null, suggested: true },
    { phase: "2w", title: "宣传物料 — 海报 + Brochure", kind: "materials", suggested: true },
    { phase: "2w", title: "礼品/周边定制 + 库存确认", kind: "materials", suggested: true },
    { phase: "2w", title: "KOL 邀请发送", kind: null, suggested: false },
    { phase: "2w", title: "媒体邀请 + brief", kind: null, suggested: true },
    { phase: "2w", title: "海关报关材料 (跨境)", kind: null, suggested: true },
    { phase: "1w", title: "设备装箱 + 运输", kind: "equipment", suggested: true },
    { phase: "1w", title: "现场签到 iPad app 配置", kind: null, suggested: true },
    { phase: "1w", title: "KOL 现场接待清单", kind: null, suggested: false },
    { phase: "live", title: "展位搭建", kind: null, suggested: true },
    { phase: "live", title: "媒体接待", kind: null, suggested: true },
    { phase: "live", title: "Lead 收集 + 录入", kind: null, suggested: true },
    { phase: "live", title: "KOL 现场拍摄协调", kind: null, suggested: false },
    { phase: "after", title: "费用报销 + 凭证归档", kind: null, suggested: true },
    { phase: "after", title: "Lead 跟进分配", kind: null, suggested: true },
    { phase: "after", title: "复盘文档 + ROI 计算", kind: null, suggested: true },
  ],
  media: [
    { phase: "4w", title: "确认场地 + 时段", kind: null, suggested: true },
    { phase: "4w", title: "媒体邀请名单 + brief", kind: null, suggested: true },
    { phase: "2w", title: "演示流程脚本", kind: null, suggested: true },
    { phase: "2w", title: "样机准备 + 现场设备清单", kind: "equipment", suggested: true },
    { phase: "2w", title: "媒体 kit 准备 (ZIP)", kind: "materials", suggested: true },
    { phase: "1w", title: "场地布置确认", kind: null, suggested: true },
    { phase: "1w", title: "餐饮预订", kind: null, suggested: true },
    { phase: "live", title: "媒体接待 + 演示", kind: null, suggested: true },
    { phase: "live", title: "现场 Q&A 记录", kind: null, suggested: true },
    { phase: "after", title: "媒体回访 + 文章追踪", kind: null, suggested: true },
  ],
  webinar: [
    { phase: "4w", title: "确定直播平台 + 时段", kind: null, suggested: true },
    { phase: "2w", title: "PPT + 演示脚本", kind: null, suggested: true },
    { phase: "2w", title: "宣传海报 + 注册页", kind: "materials", suggested: true },
    { phase: "1w", title: "设备测试 (摄像头 + 麦克风 + 网络)", kind: "equipment", suggested: true },
    { phase: "1w", title: "彩排", kind: null, suggested: true },
    { phase: "live", title: "直播执行", kind: null, suggested: true },
    { phase: "after", title: "录像剪辑 + 上传", kind: null, suggested: true },
    { phase: "after", title: "线索回收 + Q&A 文档", kind: null, suggested: true },
  ],
  kol_meetup: [
    { phase: "4w", title: "确定场地 + 餐饮", kind: null, suggested: true },
    { phase: "4w", title: "KOL 邀请", kind: null, suggested: true },
    { phase: "2w", title: "礼品准备", kind: "materials", suggested: true },
    { phase: "2w", title: "演示样机准备", kind: "equipment", suggested: true },
    { phase: "1w", title: "现场流程确认", kind: null, suggested: true },
    { phase: "live", title: "现场执行 + 拍摄", kind: null, suggested: true },
    { phase: "after", title: "Thank you 邮件 + 反馈收集", kind: null, suggested: true },
  ],
  internal: [
    { phase: "2w", title: "场地预订", kind: null, suggested: true },
    { phase: "2w", title: "团建活动策划", kind: null, suggested: true },
    { phase: "1w", title: "通知 + 报名", kind: null, suggested: true },
    { phase: "live", title: "活动执行", kind: null, suggested: true },
  ],
};
