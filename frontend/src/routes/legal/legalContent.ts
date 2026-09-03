// 公测法务页文案(L-legal-dsar)。全部为「草案,待法务审」——正式版本前以 NDA / 测试协议为准。
// 双语内容在本文件内成对维护(不过 t() 词表:法务长文不属于门面词表,英文版必须整段审定而非逐句机翻)。
// 保留期一节引用 W6 数据保留策略的键名(docs/vkpi/data-retention-policy.md),数值优先取
// /api/public/legal/policy 的现值,接口不可用时回落这里的默认值并明示「默认值」。

export type Lang = "zh" | "en";
export const LEGAL_PAGE_KEYS = ["terms", "privacy", "data-sources", "request"] as const;
export type LegalPageKey = (typeof LEGAL_PAGE_KEYS)[number];
export const LEGAL_DRAFT_VERSION = "2026-09-02-draft";
export const DSAR_SLA_DAYS_FALLBACK = 30;
export const CONTACT_EMAIL_PLACEHOLDER = "privacy@viltrox.com";

export function isLegalPageKey(value: unknown): value is LegalPageKey {
  return typeof value === "string" && (LEGAL_PAGE_KEYS as readonly string[]).includes(value);
}

export function pick(lang: Lang, zh: string, en: string): string {
  return lang === "en" ? en : zh;
}

export interface RetentionRow {
  bucket: string;
  policyKey: string;
  days: number;
  defaultDays: number;
  labelZh: string;
  labelEn: string;
}

// 与 backend/app/api/routers/dsar_public.py RETENTION_POLICY_KEYS 同键同默认(W6 策略键名)。
export const RETENTION_FALLBACK: readonly RetentionRow[] = [
  { bucket: "apify_payload", policyKey: "VKPI_RETENTION_APIFY_PAYLOAD_DAYS", days: 90, defaultDays: 90,
    labelZh: "第三方平台原始抓取载荷(任务终态后)", labelEn: "Raw platform fetch payloads (after job completion)" },
  { bucket: "comments", policyKey: "VKPI_RETENTION_COMMENTS_DAYS", days: 180, defaultDays: 180,
    labelZh: "公开评论原文", labelEn: "Public comment text" },
  { bucket: "portal_tokens", policyKey: "VKPI_PORTAL_TOKEN_TTL_DAYS", days: 90, defaultDays: 90,
    labelZh: "创作者门户访问令牌", labelEn: "Creator portal access tokens" },
  { bucket: "suppressed_contacts", policyKey: "contact_suppression", days: 0, defaultDays: 0,
    labelZh: "已抑制(勿联系)的联系方式明文——即时清理", labelEn: "Suppressed (do-not-contact) contact details — cleared immediately" },
];
export const RETENTION_TASK_KEY = "vkpi_data_retention_purge";
export const RETENTION_GATE_ENV = "VKPI_DATA_RETENTION_PURGE";

export interface LegalSection {
  h: string;
  p?: string[];
  li?: string[];
}
export interface LegalDoc {
  title: string;
  intro: string;
  sections: LegalSection[];
}

export const PAGE_TITLES: Record<LegalPageKey, { zh: string; en: string }> = {
  terms: { zh: "服务条款", en: "Terms of Service" },
  privacy: { zh: "隐私政策", en: "Privacy Policy" },
  "data-sources": { zh: "数据来源声明", en: "Data Sources Statement" },
  request: { zh: "删除 / 勿联系 / 查询申请", en: "Deletion, Do-Not-Contact & Access Requests" },
};

export const DRAFT_NOTICE = {
  zh: "草案 · 待法务审阅。本页为公测期间的工作草稿,尚未经法务确认;正式版本发布前,以你与 Viltrox 签署的保密协议(NDA)或测试协议为准。",
  en: "Draft — pending legal review. This page is a working draft for the beta and has not been approved by counsel; until the final version is published, the NDA or beta agreement you signed with Viltrox prevails.",
};

const TERMS: Record<Lang, LegalDoc> = {
  zh: {
    title: "服务条款(草案)",
    intro: "V-KPI 是 Viltrox 面向内部团队与受邀公测者提供的营销数据分析工具。访问或使用本平台,即表示你同意本条款。",
    sections: [
      { h: "1. 适用范围", p: ["本条款适用于 V-KPI 平台的全部页面、接口与导出内容(以下统称「服务」)。公测期间,服务按邀请提供,仅供评估与反馈,不构成任何商业承诺。"] },
      { h: "2. 账号与访问", li: ["账号由 Viltrox 管理员邀请创建,仅限被邀请人本人使用,不得转借或共享。", "你须妥善保管密码与登录会话;发现异常访问请立即通知我们。", "我们可依据安全或合规需要随时暂停、限制或注销账号,并会尽量提前告知。"] },
      { h: "3. 可接受使用", li: ["不得导出、复制或向第三方传播平台内的创作者资料、联系方式与分析结果(NDA 约束)。", "不得绕过板块授权、联系方式脱敏或任何访问控制。", "不得使用服务向创作者发送骚扰、欺骗或违反平台规则的信息。", "不得对服务接口进行自动化批量请求、逆向或压力测试。"] },
      { h: "4. 保密", p: ["公测期间你看到的数据、功能、指标口径与缺陷信息均属保密信息;除法律要求外,未经书面许可不得披露。"] },
      { h: "5. 知识产权", p: ["平台软件、界面与分析产出归 Viltrox 所有;创作者的公开内容归其原作者所有,平台仅以链接、摘要与统计方式引用,不主张任何权利。"] },
      { h: "6. 第三方平台", p: ["我们与 YouTube、Instagram、TikTok、Bilibili 等平台无隶属关系;你在这些平台上的行为受其各自条款约束。"] },
      { h: "7. 免责与责任限制", p: ["服务按「现状」提供。平台给出的评分、匹配度、预测与建议均为辅助参考,不构成对任何合作效果、销售或收益的承诺;在法律允许的最大范围内,Viltrox 不对使用或无法使用服务造成的间接损失承担责任。"] },
      { h: "8. 终止", p: ["你可随时停止使用;公测结束、协议终止或违反本条款时,我们将停用账号并按隐私政策处理你的账号数据。"] },
      { h: "9. 变更", p: ["条款如有变更,我们将在本页更新版本号并在平台内提示;继续使用即视为接受。"] },
      { h: "10. 管辖法律与争议", p: ["【待法务确认】适用法律与争议解决地在正式版本中填入。"] },
      { h: "11. 联系我们", p: ["条款相关问题请通过页脚邮箱联系(公测期间为占位邮箱,正式邮箱待确认)。"] },
    ],
  },
  en: {
    title: "Terms of Service (draft)",
    intro: "V-KPI is a marketing analytics tool that Viltrox provides to its internal team and invited beta testers. By accessing or using the platform you agree to these terms.",
    sections: [
      { h: "1. Scope", p: ["These terms cover every page, API and export of the V-KPI platform (the \"Service\"). During the beta the Service is offered by invitation, for evaluation and feedback only, and does not constitute a commercial commitment."] },
      { h: "2. Accounts and access", li: ["Accounts are created by invitation from a Viltrox administrator and are personal to the invitee; do not lend or share them.", "Keep your password and login session safe and tell us immediately about any suspicious access.", "We may suspend, restrict or close an account for security or compliance reasons, with notice where practical."] },
      { h: "3. Acceptable use", li: ["Do not export, copy or pass on creator profiles, contact details or analysis results to third parties (NDA).", "Do not bypass board permissions, contact masking or any other access control.", "Do not use the Service to send harassing, deceptive or platform-rule-breaking messages to creators.", "Do not run automated bulk requests, reverse engineering or load tests against the Service."] },
      { h: "4. Confidentiality", p: ["Data, features, metric definitions and defect information you see during the beta are confidential; do not disclose them without written permission unless required by law."] },
      { h: "5. Intellectual property", p: ["The platform software, interface and analytical output belong to Viltrox. Creators' public content remains theirs; the platform references it only through links, excerpts and statistics and claims no rights in it."] },
      { h: "6. Third-party platforms", p: ["We are not affiliated with YouTube, Instagram, TikTok, Bilibili or any other platform; your activity there is governed by their terms."] },
      { h: "7. Disclaimers and limitation of liability", p: ["The Service is provided \"as is\". Scores, fit estimates, forecasts and suggestions are decision support only and are not promises about any collaboration outcome, sales or revenue. To the fullest extent permitted by law, Viltrox is not liable for indirect losses arising from use of, or inability to use, the Service."] },
      { h: "8. Termination", p: ["You may stop using the Service at any time. When the beta ends, the agreement terminates or these terms are breached, we deactivate the account and handle account data as described in the Privacy Policy."] },
      { h: "9. Changes", p: ["We update the version on this page and notify you in the product when the terms change; continued use means acceptance."] },
      { h: "10. Governing law and disputes", p: ["[Pending legal review] Governing law and venue will be inserted in the final version."] },
      { h: "11. Contact", p: ["Questions about these terms go to the email in the footer (a placeholder during the beta; the final address is to be confirmed)."] },
    ],
  },
};

const PRIVACY: Record<Lang, LegalDoc> = {
  zh: {
    title: "隐私政策(草案)",
    intro: "本政策说明 V-KPI 平台处理哪些个人数据、来源、用途、保留多久,以及你(平台用户或被收录的创作者)享有的权利。",
    sections: [
      { h: "1. 适用范围", p: ["适用于平台用户(Viltrox 员工与受邀测试者)以及平台收录的创作者(数据主体)。"] },
      { h: "2. 我们处理的数据", li: [
        "平台用户:姓名、工作邮箱、登录记录、操作审计与界面偏好。",
        "创作者(来自公开平台页面):账号名、主页链接、头像、简介、粉丝数与播放量等公开统计、公开视频/帖子的标题与链接、公开评论文本,以及创作者自行公开的商务联系方式。",
        "合作过程数据:由员工录入的沟通、寄样、发布与结算记录。",
        "我们不采集需登录才可见的内容、私信、支付信息或任何敏感个人信息。",
      ] },
      { h: "3. 数据来源", p: ["公开平台抓取(详见「数据来源声明」)、员工手工录入,以及创作者在合作过程中主动提供的信息。"] },
      { h: "4. 用途与法律依据", li: [
        "寻找、评估并联系适合 Viltrox 产品的合作创作者(正当利益)。",
        "履行与创作者的合作、寄样与结算(合同履行)。",
        "改进产品、保障安全与满足审计要求(正当利益 / 法定义务)。",
        "【待法务确认】各辖区适用的法律依据与告知方式。",
      ] },
      { h: "5. 自动化分析", p: ["我们使用软件(包括人工智能模型服务)对公开内容做摘要、分类与匹配度评估。这些结果只作为内部人工决策的参考,不会对创作者产生具有法律效力或类似重大影响的自动化决定。"] },
      { h: "6. 数据保留期", p: ["保留期由代码强制执行:每日任务 " + RETENTION_TASK_KEY + " 按下表清理;放量闸 " + RETENTION_GATE_ENV + " 默认关闭(只报数不删),开启后按表执行。创作者档案与视频证据的保留期尚未定案(待办)。"] },
      { h: "7. 共享与处理者", li: ["云主机与数据库托管服务商。", "第三方公开数据获取服务商(只接收公开页面地址与抓取指令)。", "人工智能模型服务商(只接收去标识化的公开内容片段)。", "错误监控与日志服务(不含邮箱与令牌明文)。", "我们不出售个人数据。跨境传输安排【待法务确认】。"] },
      { h: "8. 你的权利", li: ["查询:了解我们是否持有你的数据及其内容。", "删除:要求删除你的创作者档案及其关联记录。", "勿联系:要求我们停止通过任何渠道联系你;联系方式会进入抑制名单并从外联通道立即清除。", "更正与反对:更正不准确的信息,或反对特定处理。", "提交方式:本站「删除 / 勿联系 / 查询申请」表单,或页脚邮箱;我们会在 30 天内回复,并可能要求你证明账号归属。"] },
      { h: "9. 安全", li: ["按板块授权的访问控制;联系方式默认脱敏,揭示动作留审计。", "一次性令牌只存摘要;传输全程 HTTPS。", "第三方原始载荷与评论按保留期自动清理。"] },
      { h: "10. Cookie 与本地存储", p: ["登录会话使用必要 Cookie;主题、语言与看板布局等偏好保存在你的浏览器本地存储中。平台不含广告或跨站追踪。"] },
      { h: "11. 未成年人", p: ["平台不面向 16 周岁以下人士;如发现收录了未成年人的数据,我们将删除。"] },
      { h: "12. 变更与联系", p: ["政策变更会更新本页版本号;问题请通过页脚邮箱联系(公测期间为占位邮箱)。"] },
    ],
  },
  en: {
    title: "Privacy Policy (draft)",
    intro: "This policy explains which personal data the V-KPI platform processes, where it comes from, why, how long we keep it, and the rights you have as a platform user or as a creator listed on the platform.",
    sections: [
      { h: "1. Scope", p: ["Applies to platform users (Viltrox staff and invited testers) and to creators listed on the platform (data subjects)."] },
      { h: "2. Data we process", li: [
        "Platform users: name, work email, login records, action audit trail and interface preferences.",
        "Creators (from public platform pages): handle, profile link, avatar, bio, public statistics such as followers and views, titles and links of public videos/posts, public comment text, and business contact details the creator has published.",
        "Collaboration data: outreach, sample shipping, publication and settlement records entered by staff.",
        "We do not collect login-walled content, private messages, payment details or any special-category data.",
      ] },
      { h: "3. Sources", p: ["Public platform pages (see the Data Sources Statement), manual entry by staff, and information creators share with us during a collaboration."] },
      { h: "4. Purposes and legal basis", li: [
        "Finding, evaluating and contacting creators who fit Viltrox products (legitimate interest).",
        "Performing collaborations, sample shipping and settlement (contract).",
        "Improving the product, keeping it secure and meeting audit duties (legitimate interest / legal obligation).",
        "[Pending legal review] Jurisdiction-specific legal bases and notice mechanics.",
      ] },
      { h: "5. Automated analysis", p: ["We use software, including AI model services, to summarise, classify and score public content for fit. Results only support internal human decisions; no decision with legal or similarly significant effect on a creator is made automatically."] },
      { h: "6. Retention", p: ["Retention is enforced in code: the daily task " + RETENTION_TASK_KEY + " purges according to the table below. The gate " + RETENTION_GATE_ENV + " is off by default (count only, no deletion) and enforces the table once enabled. Retention for creator profiles and video evidence is still to be decided (open item)."] },
      { h: "7. Sharing and processors", li: ["Cloud hosting and database providers.", "Third-party public-data fetch services (they receive only public page URLs and fetch instructions).", "AI model providers (they receive only de-identified public content excerpts).", "Error monitoring and logging (never plaintext emails or tokens).", "We do not sell personal data. Cross-border transfer arrangements: [pending legal review]."] },
      { h: "8. Your rights", li: ["Access: learn whether we hold data about you and what it is.", "Deletion: ask us to delete your creator profile and linked records.", "Do not contact: ask us to stop contacting you on any channel; the contact details go on a suppression list and are removed from outreach immediately.", "Rectification and objection: correct inaccurate information or object to specific processing.", "How: use the request form on this site or the email in the footer. We reply within 30 days and may ask you to prove you own the account."] },
      { h: "9. Security", li: ["Board-level access control; contact details are masked by default and every reveal is audited.", "One-time tokens are stored as digests only; all traffic is HTTPS.", "Third-party raw payloads and comments are purged automatically per the retention table."] },
      { h: "10. Cookies and local storage", p: ["Login sessions use an essential cookie; theme, language and board layout preferences live in your browser's local storage. There is no advertising or cross-site tracking."] },
      { h: "11. Minors", p: ["The platform is not intended for anyone under 16; we delete data of minors if we become aware of it."] },
      { h: "12. Changes and contact", p: ["Changes update the version on this page; questions go to the footer email (a placeholder during the beta)."] },
    ],
  },
};

const DATA_SOURCES: Record<Lang, LegalDoc> = {
  zh: {
    title: "数据来源声明(草案)",
    intro: "V-KPI 收录的创作者与市场数据全部来自公开可见的平台页面与 Viltrox 自有渠道。本声明说明我们取什么、不取什么、怎么取。",
    sections: [
      { h: "1. 原则", li: ["只采集无需登录即可公开查看的内容;不登录、不破解、不越权。", "尊重各平台的服务条款与 robots 规则【待法务逐平台核对】。", "抓取频率受限流与预算闸约束;结果只用于内部分析,不对外再发布。"] },
      { h: "2. 来源清单", li: ["公开创作者页面:YouTube、Instagram、TikTok、Bilibili 等平台的公开档案、公开视频/帖子与公开评论。", "Viltrox 自有账号与官方渠道的内容与统计。", "经销商与行业活动的公开网页。", "Viltrox 自有店铺的订单归因数据(仅用于合作效果归因)。", "员工手工录入的合作记录。"] },
      { h: "3. 我们不做的事", li: ["不采集私密内容、私信或需登录才可见的信息。", "不采集创作者未公开的联系方式;公开商务邮箱在平台内默认脱敏。", "不模拟真人登录、不使用他人账号凭证。", "不采集未成年人数据。"] },
      { h: "4. 第三方获取服务与原始载荷", p: ["我们通过第三方公开数据获取服务抓取上述页面。原始载荷在任务完成后按 VKPI_RETENTION_APIFY_PAYLOAD_DAYS(默认 90 天)清理,公开评论原文按 VKPI_RETENTION_COMMENTS_DAYS(默认 180 天)清理。"] },
      { h: "5. 数据质量与纠错", p: ["公开数据可能过时或不完整;平台会标注数据新鲜度。创作者可通过申请表要求更正或删除。"] },
      { h: "6. 退出", p: ["提交「勿联系」申请后,你自报的联系方式会立即进入抑制名单;员工审批后,档案内的其他联系方式一并抑制。提交「删除」申请并经身份核验后,档案及关联记录将被级联删除。"] },
    ],
  },
  en: {
    title: "Data Sources Statement (draft)",
    intro: "Every creator and market record in V-KPI comes from publicly visible platform pages or Viltrox's own channels. This statement explains what we collect, what we do not, and how.",
    sections: [
      { h: "1. Principles", li: ["We only collect content that is publicly visible without logging in; no logins, no circumvention, no privileged access.", "We respect each platform's terms and robots rules [pending per-platform legal check].", "Fetch frequency is bounded by rate limits and a budget gate; results are used for internal analysis only and never republished."] },
      { h: "2. Sources", li: ["Public creator pages: public profiles, public videos/posts and public comments on YouTube, Instagram, TikTok, Bilibili and similar platforms.", "Content and statistics of Viltrox's own accounts and official channels.", "Public web pages of dealers and industry events.", "Order attribution data from Viltrox's own store (used only to attribute collaboration outcomes).", "Collaboration records entered manually by staff."] },
      { h: "3. What we do not do", li: ["We do not collect private content, direct messages or login-walled information.", "We do not collect unpublished contact details; published business emails are masked by default inside the platform.", "We do not simulate human logins or use anyone's credentials.", "We do not collect data about minors."] },
      { h: "4. Fetch services and raw payloads", p: ["We fetch the pages above through third-party public-data services. Raw payloads are purged after job completion per VKPI_RETENTION_APIFY_PAYLOAD_DAYS (default 90 days); public comment text per VKPI_RETENTION_COMMENTS_DAYS (default 180 days)."] },
      { h: "5. Data quality and corrections", p: ["Public data can be stale or incomplete; the platform labels freshness. Creators can request corrections or deletion through the request form."] },
      { h: "6. Opting out", p: ["After a do-not-contact request, the contact details you provide are suppressed immediately; once staff approve, any other contact details on file are suppressed too. After a verified deletion request, the profile and linked records are deleted in cascade."] },
    ],
  },
};

const REQUEST: Record<Lang, LegalDoc> = {
  zh: {
    title: "删除 / 勿联系 / 查询申请(草案)",
    intro: "如果你是被平台收录的创作者(或其授权代表),可以在这里提交申请。我们会在 30 天内通过你留下的邮箱回复。",
    sections: [
      { h: "流程", li: ["提交后你会得到一个回执号(DSAR-xxxxxxxx),请保留以便跟进。", "「勿联系」申请:你自报的邮箱会立即进入抑制名单;档案内的其他联系方式在员工审批后一并抑制。", "「删除」申请:员工核验账号归属后执行级联删除,并保留一份删除收据用于合规问询。", "「查询」申请:人工整理后通过邮箱答复。", "为防止冒用,我们可能请你在平台账号上做一次简单的归属验证。"] },
    ],
  },
  en: {
    title: "Deletion, Do-Not-Contact & Access Requests (draft)",
    intro: "If you are a creator listed on the platform (or their authorised representative), submit your request here. We reply within 30 days to the email you provide.",
    sections: [
      { h: "How it works", li: ["You receive a reference (DSAR-xxxxxxxx) after submitting; keep it for follow-up.", "Do-not-contact: the email you provide is suppressed immediately; other contact details on file are suppressed once staff approve.", "Deletion: after staff verify account ownership, the profile is deleted in cascade and a deletion receipt is kept for compliance.", "Access: compiled manually and answered by email.", "To prevent impersonation we may ask for a simple ownership check on your platform account."] },
    ],
  },
};

export const LEGAL_DOCS: Record<LegalPageKey, Record<Lang, LegalDoc>> = {
  terms: TERMS,
  privacy: PRIVACY,
  "data-sources": DATA_SOURCES,
  request: REQUEST,
};

export const HUB_BLURBS: Record<LegalPageKey, { zh: string; en: string }> = {
  terms: { zh: "使用范围、账号、可接受使用与免责。", en: "Scope, accounts, acceptable use and disclaimers." },
  privacy: { zh: "我们处理哪些数据、保留多久、你的权利。", en: "What we process, how long we keep it, your rights." },
  "data-sources": { zh: "只取公开平台内容;取什么、不取什么。", en: "Public platform content only; what we take and what we do not." },
  request: { zh: "创作者删除、勿联系与查询申请表。", en: "Creator deletion, do-not-contact and access request form." },
};
