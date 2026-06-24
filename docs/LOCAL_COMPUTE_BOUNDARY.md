# 本地计算边界(I4)

哪些算力可下放本地 worker,哪些必须留中心 —— 安全与一致性底线。

## ✅ 适合本地(worker lease 领取)
- 视频下载 / 转码 / 抽帧 / 缩略图
- metadata 抓取(账号/视频基础信息)
- 预处理(文本清洗、分块、去重)
- 媒体上传到 R2(凭中心签发的临时凭证)

## ❌ 必须留中心(绝不下放本地)
- **权限判断 / RBAC scope**(只在服务端裁决)
- **主数据库写入**(业务真值落库只走中心 API)
- **API 密钥**(LLM/Apify/17track key 永不发客户端;本地按 lease 任务向中心申请,中心用 [[token_broker]] 选 key 后代调或签发短期受限凭证)
- **最终审计 / ledger**(成本、动作台账只中心写)
- **viltrox_fit_score 等核心评分**(红线:任何端都不写)

## 协调机制
- [I2 Worker Lease](../backend/app/domains/platform/worker_lease.py):云端建租约 → 本地 `acquire_lease` 领取 → 跑完 `complete_lease` 上传 result_ref;到期未完 `expire_stale` 重派。
- [I1 Token Broker](../backend/app/domains/platform/token_broker.py):中心保管 key 元数据(零明文),`pick_token` 轮转;本地只拿任务不拿 key。

## 验收
本地 worker 挂掉 / 断网 → 主系统数据零影响(租约到期自动重派;无半写状态);密钥从不离开服务端。
