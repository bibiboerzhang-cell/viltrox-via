"""local_workers — 本地算力 Worker 域(W1 地基件)。

安全模型(本 MVP 的存在意义):
- 本地 worker 永不持有长期 API key,一切凭「短期任务 token」(HMAC 签发,见 registry.py);
- 本地上传结果一律先落 staging 表(vkpi_local_task_leases.result_json),
  服务端校验(hash/结构/URL 匹配)通过后才可信;
- 本地路径绝不直写核心业务表(evidence/kol_pool/分数列),落库由服务端校验桥走既有函数;
- 红线:绝不写 viltrox_fit_score、绝不触 rule_v0。

模块分工:
- registry.py(W1):设备注册/心跳/领活/token 签发验证/租约过期回收;
- validation.py(W2):提交结果的深度服务端校验(从 registry import 契约函数)。
"""
