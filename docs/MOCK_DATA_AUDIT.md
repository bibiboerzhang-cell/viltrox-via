# Mock 数据审计(gate_9)

红线:**绝不让 mock 伪装成真数据**。本文记录 mock 分布 + 标注机制,确保用户永不误判。

## 现状(本地基线扫描)
- **前端 mock:0**(三套 mock 早已退役 → 唯一真源,见 [[vkpi-staff-unified]])。
- **后端含 "mock" 字样:仅 4 文件**(均为测试/注释/降级占位,非业务伪装)。

## 标注机制(已落地)
- **`DataStatusBadge`**(`frontend/src/components/vkpi/common/DataStatusBadge.tsx`):
  统一角标 `ready / partial / missing / mock / stale / awaiting` —— 真实/部分/待接入/样例/过期/待数据 一眼可分。
- **后端诚实降级**:缺数据返回 `status: awaiting_data / awaiting_m5 / no_projects` 等,**绝不填 0 冒充**(贯穿 metrics/industry_board/prediction/report)。
- **市场预估 / GMV**:真订单(Shopify)未接入前一律标 `awaiting_data`,confidence 封顶 medium。

## 规则(继续遵守)
1. 任何卡片/指标若数据非 100% 真实,必须挂 `DataStatusBadge` 标明状态。
2. 新功能缺真数据时返回 `awaiting_*` 状态 + 诚实 note,严禁伪造数字占位。
3. 残留的 4 处后端 "mock" 仅限测试夹具/异常降级占位,不得进入面向用户的业务返回。

## 验收
用户在任意页面都能区分"真实 vs 待接入 vs 样例";无任何 mock 数字以真实身份出现在 KPI/大盘/报告。
