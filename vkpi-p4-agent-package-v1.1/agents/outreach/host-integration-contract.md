# Outreach Host Integration Contract

## 目的

Outreach Agent 只负责独立组件和后端接口。宿主页面接入由主控线程单独完成,防止 Agent 大范围改 KOL 或 Project 页面。

## 组件

`OutreachTimelinePanel`

## Props contract

| prop | type | 来源 | 说明 |
|---|---|---|---|
| `kolId` | `number | string` | KOL 详情页当前 KOL | 必填 |
| `projectId` | `number | string | null` | 项目详情页当前项目 | 可选 |
| `staff` | `StaffSummary` | 当前登录用户上下文 | 用于权限和审计展示 |
| `apiToken` | `string` | 宿主 API token | 不在组件内自行读取 localStorage |
| `readonly` | `boolean` | 宿主权限判断结果 | 无写权限时只读 |
| `onChanged` | `() => void` | 宿主刷新函数 | 记录新增/删除后回调 |

## StaffSummary

```ts
type StaffSummary = {
  id: number | string;
  name?: string;
  role?: string;
  isOwner?: boolean;
};
```

## 宿主页面允许改动

宿主接入 PR 只允许 1-3 行级别改动:

- import `OutreachTimelinePanel`
- 在 KOL 或 Project detail tabs 数组中加入一个 tab
- 传入 `kolId / projectId / staff / apiToken / readonly / onChanged`

## 禁止行为

- 禁止 Agent 大范围重写宿主页面。
- 禁止 Agent 改宿主页面布局系统。
- 禁止 Agent 自行读取全局 auth 状态。
- 禁止 Agent 复制 KOL 或 Project 数据加载逻辑。

## 空态

| 情况 | 显示 |
|---|---|
| 无沟通记录 | `暂无沟通记录,可以添加第一次联系记录` |
| 无写权限 | `只读视图: 当前账号无权限添加沟通记录` |
| API 失败 | `沟通记录加载失败,请稍后重试` |
| 缺少 kolId | `缺少 KOL 标识,无法加载沟通记录` |

## 验收

- 独立组件可以渲染空态。
- 有数据时显示时间线。
- 无权限时没有写入按钮。
- 宿主接入不超过约定范围。
