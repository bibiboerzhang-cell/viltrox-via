# Module Ownership v1.1

本文件不写死人工统计数字。所有数量以 `scripts/verify_agent_boundary.py` 的实时输出为准。

## 必须先运行

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
python3 vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py
```

## 统计口径

| 字段 | 口径 |
|---|---|
| services 子目录数量 | `backend/app/services/*/` 下的直接子目录,排除 `__pycache__` 等 |
| vkpi 顶层 py 文件数量 | `backend/app/services/vkpi/*.py`,排除 `__init__.py` |
| routers 数量 | `backend/app/api/routers/*.py`,排除 `__init__.py` |
| unknown 模块清单 | ownership 未决或候选 unknown 名称命中的模块 |
| 模块归属状态 | `owned` 或 `unknown` |

## 当前建议归属模型

| 模块 | 建议 owner | 说明 |
|---|---|---|
| `vkpi` | `vkpi-core` | V-KPI 当前主业务域 |
| `kol` | `kol-domain` | KOL 主数据和候选相关 |
| `media` | `media-domain` | 图片、视频、代理、媒体分析 |
| `scraping` | `data-ingestion` | 外部抓取适配器 |
| `ingestion` | `data-ingestion` | 导入和数据接入 |
| `ai` | `ai-platform` | LLM 客户端和 AI 公共能力 |
| `intelligence` | `ai-platform` | 智能分析、推理和分类 |
| `memory` | `ai-platform` | 记忆和上下文模块 |
| `scoring` | `ai-platform` | 评分和 ranking |
| `audit` | `governance` | 审计日志 |
| `security` | `identity-access` | 安全和访问控制 |
| `auth` | `identity-access` | 登录和身份认证 |
| `system` | `platform-infra` | 系统配置和内部状态 |
| `monitoring` | `ops-monitoring` | 健康检查、监控、告警 |
| `cache` | `platform-infra` | 缓存 |
| `jobs` | `platform-infra` | 后台任务 |
| `scheduler` | `platform-infra` | 定时任务 |
| `commerce` | `commerce` | Shopify/Amazon/归因等商业数据 |
| `creators` | `creator-workflow` | 创作者工作流 |
| `deepsight` | `future-isolate` | 未来深度分析方向,不进入当前 P4 主链 |
| `party` | `future-isolate` | 与当前 V-KPI 收口无直接关系,隔离处理 |
| `rewards` | `future-isolate` | 积分/激励方向,暂不进入当前 P4 主链 |
| `verification` | `identity-access` | 身份/资质校验方向,归入身份访问控制 |
| `via` | `vos-future-isolate` | 未来 V-OS/VIA 方向,不应混入 V-KPI 主链 |

## 需要 Phase 0B 决策的模块

| 模块 | 决策问题 | 推荐动作 |
|---|---|---|
| `student_identity` | 是否仍属于当前产品 | 若仅 V-OS 未来用,标记 `future-isolate` |
| `trust` | 安全、审核还是商业信誉 | 决定归 `governance` 或 `identity-access` |

## Phase 0B 默认决策记录

| 模块 | 决策 | 理由 |
|---|---|---|
| `verification` | `identity-access` | 与身份、资质、访问控制更相关,后续若承担业务审核再独立拆分 |
| `deepsight` | `future-isolate` | 当前 P4 以治理和真实可用为主,不把未来深度分析混入主链 |
| `party` | `future-isolate` | 非当前 V-KPI 内测必需模块 |
| `rewards` | `future-isolate` | 激励/积分属于未来产品层,避免污染当前 P4 收口 |

## Agent 边界原则

- Agent 只能修改自己的 owner 目录。
- 跨模块修改必须由主控线程单独做 host integration PR。
- shared/types 变更必须单独 review。
- 任何 migration 都必须有独立审查,不能夹在 feature Agent 里。
