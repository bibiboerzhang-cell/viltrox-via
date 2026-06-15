# Events 模块

V-KPI Events 页面,从单文件 mockup 拆解为 **33 个 ESM 模块**。

## 目录结构

```
events/
├── index.html                       ← 独立预览入口
│
├── pages/
│   ├── EventsPage.js                ← 主入口 (export default)
│   └── EventDetailView.js           ← 详情页 (7 tabs)
│
├── components/
│   ├── EventCard.js                 列表卡片
│   └── PlaceholderTab.js            占位 tab (现场/复盘暂用)
│
├── modals/                          ★ 9 个 modal
│   ├── NewEventModal.js             新建/编辑 (initialData 共用)
│   ├── ExpenseEntryModal.js         费用录入 (含 AI 发票识别 mockup)
│   ├── StockManagerModal.js         ★ 公司库存表 (全局,跨 event)
│   ├── DeleteConfirmModal.js        通用删除确认
│   ├── NewTaskModal.js              手动新建任务
│   ├── AiGenerateTasksModal.js      AI 一键生成任务模板
│   ├── AddMaterialModal.js          添加物料 (含自定义类别)
│   ├── AddProductPrepModal.js       添加产品准备 (从库存挑 OR 手动)
│   └── InviteKolModal.js            邀请 KOL
│
├── tabs/                            (7 个,KolsTab 复用 KOL Pool)
│   ├── OverviewTab.js               倒计时 + KPI + 地图 + 团队
│   ├── BudgetExpensesTab.js         预算 vs 实际 + 费用流水
│   ├── TasksTab.js                  任务清单 (含 doneBy/checklist)
│   ├── KolsTab.js                   邀请 KOL
│   ├── MaterialsTab.js              wrapper (营销物料 / 产品准备)
│   ├── MarketingMaterialsPanel.js   营销物料表 + CSV 导出
│   └── ProductPrepPanel.js          产品准备 (二分: 已有 vs 需新寄)
│
├── shared/                          静态可复用
│   ├── constants.js                 EVENT_TYPES / EVENT_STATUS / EXPENSE_CATEGORIES /
│   │                                TASK_KINDS / EQUIP_SOURCE / MAT_SOURCE / ITEM_STATUS /
│   │                                PHASE_LABELS / MATERIAL_CATEGORIES / MATERIAL_SOURCE /
│   │                                PRODUCT_CATEGORIES / PRODUCT_SOURCES
│   ├── helpers.js                   TODAY / daysUntil / fmtMoney / fmtMoneyShort / sum / healthColor
│   └── lookups.js                   ownerById / ownerByInitial / kolById / projectById
│
└── data/                            ★ 全是 mock — Codex 接入后整个目录可删
    ├── team.js                      → GET /api/admin/users (成员列表)
    ├── projects.js                  → GET /api/admin/vkpi/projects
    ├── kol-pool.js                  → GET /api/admin/vkpi/kol-pool
    ├── events.js                    → GET /api/admin/vkpi/events
    ├── expenses.js                  → GET /api/admin/vkpi/events/{id}/expenses
    ├── tasks.js                     → GET /api/admin/vkpi/events/{id}/tasks
    ├── materials.js                 → GET /api/admin/vkpi/events/{id}/materials
    ├── product-prep.js              → GET /api/admin/vkpi/events/{id}/product-preps
    ├── stock.js                     → GET /api/admin/vkpi/stock-inventory (跨 event)
    └── ai-templates.js              → POST /api/admin/vkpi/llm/generate-tasks (LLM 生成)
```

## 快速预览

```bash
cd events/
python3 -m http.server 8000
# 浏览器打开 http://localhost:8000
```

ESM 必须用 HTTP server。

## 集成到 V-KPI 前端

```js
import EventsPage from "./events/pages/EventsPage.js";

function App() {
  if (activeNav === "events") return <EventsPage currentUser={user} />;
}
```

---

## ★ 接入清单 (按优先级)

### P0: 替换 data/ mock

每个 data/*.js 都标了对应的 API,详见 `../../schema/api-spec.md`。

**特别注意 `data/team.js`**:
- 现在 hardcoded 4 个人 (J/M/T/K)
- 必须改成 `GET /api/admin/users?role=internal` 拉真实成员
- 数据结构相同: `{ id, name, initial, color, email?, role? }`
- 当成员 > 4 个时,各处 modal 的"团队选择 chip"会自动 wrap 多行 (已用 flex-wrap)

### P1: 写操作改 API

| Mockup 操作 | Backend API |
|------------|-------------|
| 新建 Event | `POST /api/admin/vkpi/events` |
| 编辑 Event (右上 ... → 编辑) | `PATCH /api/admin/vkpi/events/{id}` |
| 删除 Event | `DELETE /api/admin/vkpi/events/{id}` |
| 加成员到 team | `POST /api/admin/vkpi/events/{id}/members` |
| 邀请新成员 (邮箱) | `POST /api/admin/users/invite` |
| 录入费用 | `POST /api/admin/vkpi/events/{id}/expenses` |
| 新建任务 | `POST /api/admin/vkpi/events/{id}/tasks` |
| 勾选任务/checklist | `PATCH /api/admin/vkpi/events/{id}/tasks/{tid}` |
| 添加物料/产品准备 | `POST .../materials` / `POST .../product-preps` |
| 邀请 KOL | `POST /api/admin/vkpi/events/{id}/kols` |
| 库存表更新 | `PATCH /api/admin/vkpi/stock-inventory/{sku}` |

### P2: 接 LLM (9 个用例,见 integrations/llm/)

- AI 生成任务清单 (`AiGenerateTasksModal` 替换假 setTimeout)
- 发票 OCR (`ExpenseEntryModal` 拍照按钮)
- 费用类目自动分类
- 预算超支预警 + 调拨建议
- 任务延期风险预测
- KOL 邀请话术生成
- 现场 lead OCR (名片识别)
- 复盘文档自动起草
- 快递异常解读

### P3: 接 Memory + Reminders

- **Memory**: Event 完结时写入,新建时拉历史经验
- **Reminders**: cron + push (任务 ddl / event 临近 / 预算超支 / KOL 邀请超时)

详见 `../../integrations/memory/` 和 `../../integrations/reminders/`。

---

## 团队扩展 (从 hardcoded 4 人 → users 表)

### 当前 mockup (data/team.js)
```js
export const TEAM = [
  { id: "j", name: "Jianbo", initial: "J", color: "#a855f7" },
  // ... 4 个
];
```

### 真实 V-KPI 后端结构
```sql
-- users 表 (扩展现有 users)
ALTER TABLE users ADD COLUMN initial VARCHAR(4);   -- 头像缩写
ALTER TABLE users ADD COLUMN avatar_color VARCHAR(7);  -- hex
ALTER TABLE users ADD COLUMN role VARCHAR(32);     -- internal / external / kol_manager
ALTER TABLE users ADD COLUMN invite_status VARCHAR(16);  -- active / pending / disabled

-- event_team_members 表
CREATE TABLE event_team_members (
  event_id VARCHAR(64) REFERENCES events(id) ON DELETE CASCADE,
  user_id VARCHAR(64) REFERENCES users(id),
  is_owner BOOLEAN DEFAULT FALSE,
  joined_at TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (event_id, user_id)
);
```

### 邀请新成员流程
1. 用户在 NewEventModal 团队选择 → 点 "邀请新人" (新加)
2. 输入邮箱 → `POST /api/admin/users/invite`
3. 后端发邮件邀请 + 创建 user (invite_status=pending)
4. 该 user 在团队选择 chip 里立刻显示 (带 "待激活" 角标)
5. 被邀请人点邮箱链接注册 → invite_status=active

详见 `../../integrations/team-invite/`。
