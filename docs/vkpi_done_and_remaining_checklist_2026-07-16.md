# V-KPI 当前已完成与未完成清单

更新日期：2026-07-16 EDT  
适用范围：本地候选版本及 `www.viltroxtest.com` 测试云端发布  
真实性原则：工程能力、运行可靠性、AI 生产就绪度和真实业务闭环分开验收，不互相折算。

## A. 已完成并有代码/测试证据

- [x] Workflow Lease、Fence Token、CAS、恢复 Sweeper 和单写者状态机。
- [x] PostgreSQL 共享 LLM Fleet Breaker 进入主 LLM 调用边界。
- [x] Scheduler 任务 Allowlist、手动运行请求与陈旧 Fire 恢复地基。
- [x] Shopify 设置页一步接入向导：Token、店铺身份、Webhook、原子启用；未授权时保持待接入。
- [x] Advisor 上下文选择、证据引用、Helpful/Unhelpful/纠正反馈和 Memory Candidate 入口。
- [x] KOL 账号/视频深析展示、焦段矩阵和产品推荐改为结合机身、常用镜头、内容证据、系列和价格匹配，不再默认只推 EPIC。
- [x] AI Today 工程链收口为 Gemini 2.5 Pro + Google Search 发现，再由 Claude Opus 4.7 生成证据约束策略；任一阶段失败则如实降级。
- [x] Dashboard 跨页模块目录、今日焦点、市场热词和视觉曲线展示修复。
- [x] Dealer/Event 数据合同、地图验证和 Event Radar 投影已实现。
- [x] Nikon、Canon、Sony、Godox 官方大目录定位为“待匹配组织候选”，不将总部/线上渠道冒充实体店。
- [x] 首批 5 家官网已证明实体门店完成本地发布：B&H NYC、Adorama NYC、Samy's LA、Orange County、Pasadena。
- [x] 首批门店保留并写入门店名、完整地址、电话、官网、官网来源和地址级坐标。
- [x] 迁移 265–270 完成一次性隔离 PostgreSQL 上下迁移演练；本地主库已前进到 270。
- [x] 最近一次全量静态门禁：后端 3,842 passed、前端 1,046 passed、TypeScript、生产构建、Chunk Budget、红线和代码硬化通过。

## B. 本轮发布链必须继续完成

- [ ] 将 Web、前端产物、4 个 Apify Worker、1 个 Redis Worker 和 Scheduler 全部重启并对齐最终提交 SHA。
- [ ] 在最终 SHA 上重跑严格运行信任门禁、48/48 只读 API 验收、21/21 浏览器页面验收和重启后日志 Canary。
- [ ] 生成并验证干净工作树的不可变发布候选。
- [ ] 推送 `codex/dashboard-real` 并校验远端分支 SHA。
- [ ] 使用 PostgreSQL staging clone 和原子切换同步到 `www.viltroxtest.com`，禁止脏树/跳过门禁发布。
- [ ] 云端迁移到 270 后再发布同一份 5 店清单，并证明地图 5 点、电话和官网入口可见。
- [ ] 确认公开 URL 使用干净根路径，地址栏不暴露 `#cockpit`。

## C. 可以上测试云，但必须如实降级的能力

- [ ] AI Today 的双模型网络连通已经 Canary 证明，但独立信任根和签名 30-case 评测尚未完成；云端必须保持 `degraded/not production ready`，不得用旧快照冒充联合模型成果。
- [ ] Gemini 发现、Claude 建议和前端视频卡之间还缺少结构化 `source_ids/candidate_ids` 逐条绑定；当前只能声明为证据约束的描述性辅助。
- [ ] Workflow 内部已是 fenced single-writer，但部分第三方副作用仍是 at-least-once，不得宣称全局 exactly-once。

## D. 上线后继续的工程任务

- [ ] 将 Gemini 发现结果改为带 URL、`source_ids`、claim spans 和 video candidate ID 的结构化合同。
- [ ] 读取端只把完整 pipeline v1 快照认定为 Gemini + Claude 联合 ready，旧单阶段快照显式标为 stale/degraded。
- [ ] 按批处理 Nikon/Canon/Sony/Godox 候选组织：Gemini + Google Search 判断实体店，再从零售商官网保留名称、地址、电话、官网和坐标；无实体店证据则不上图。
- [ ] 经销商官网活动与线下课程通过 exact dealer-event relation 自动同步 Event Radar。
- [ ] 继续收敛 6 个千行白名单文件、无界列表、慢查询与剩余 N+1，优先按实测运行收益排序。
- [ ] 完成 Scheduler 长时间 Fire 证据、72 小时稳定性和云端恢复演练，不用单次绿灯替代持续运行证明。

## E. 等外部授权或真实业务数据

- [ ] Shopify 真实店铺授权、历史订单、Webhook HMAC、库存和订单同步。
- [ ] 真实库存、成本、销售归因、GMV 和 ROI 分母。
- [ ] 至少 5 条非演示人工反馈、5 条 Finalized Outcome 和 5 条带真实 Actual 的 Prediction Eval。
- [ ] 真实寄样、履约、销售结果和持续回传，用非零分母证明学习效果。
- [ ] 在有足够可审计反馈前，不启动对生产策略的自动参数更新，不把规则调整宣称为已验证模型训练。

## 发布裁决

- 基础功能可以先上 `www.viltroxtest.com` 测试站，前提是 B 组全部通过。
- AI Today 和真实业务未就绪不阻塞测试站上线，但必须保持待授权/降级/空状态，不得显示伪造结果。
- 当 B 组任何一项失败时，不得宣称云端已更新或发布验收完成。
