# P5 单轨执行交接记录

> 单轨执行(Codex 缺席至 P5 收口)。每个**窗口动作**一行:迁移号 / 重载时刻 / PID / 对账结果。
> Codex 回归首日凭此对账。分支 `codex/dashboard-real`,**不 push**。
> 授权令(驻留,批2–批5 有效)见对话回执;部署不变量:**C3 端点切异步必须在 C2 worker 分支重载生效之后**。

## Commit 流水(代码,非执行)
| commit | 内容 | 闸 | 执行影响 |
|---|---|---|---|
| `eea9ff22` | feat(projects): clear materials-tab mock facade(批1 假面清除) | 无 | 纯前端;dist 已 build(`4fd65963`)待浏览器验收 |
| `4fd65963` | feat(projects): contract extract enqueue helper + kernel split(C1) | 无 | 纯 domain 新增,未入队执行、未碰 worker/端点 |
| `(C2)` | feat(worker): project_contract_extract branch + handler | 闸C | **代码已提交,worker 未重载** → 运行中仍是旧 worker |

## 窗口动作(迁移 / worker 重载 / 铁律对账)
| 时刻 | 动作 | migration | worker PID(前→后) | admin master | 对账(fit_score 零写入) |
|---|---|---|---|---|---|
| 2026-06-11 14:12 | pg_dump 出工作树 | — | — | — | `~/vkpi-db-backups/viltrox2-pre106-20260611T141238.dump`(48M,TOC 2453,可 restore) |
| 2026-06-11 14:13 | apply 106 | 106 ✅(净增 2 行预算 scope,幂等;schema_migrations 已记) | — | — | cron=$5 / single=$1 已入 vkpi_provider_budget_caps |
| 2026-06-11 14:14 | worker 重载(单次) | — | 68403 → **61087** | — | 新进程载入 _process_project_contract_extract + _process_project_retrospective(AST 实证),回显正常、空转健康 |
| 2026-06-11 14:15 | admin-web HUP + npm build | — | — | 46951(workers 61574/61575) | C3/C9 端点 403=已注册;dist=36c3ae70 |
| 2026-06-11 14:15 | 铁律对账 | — | — | — | retrospective_aggregate.py 写 SQL 触 fit/kol_pool=**0**(5 处命中全为 docstring/diagnostics 标志);kol_pool 指纹基线 行1123 / fit_score合计 507.4200 / fit_reason非空 1123(跑复盘任务后应不变) |

| 2026-06-11 14:5x | **Window A-fix**:worker ScopeDenied 修复 + 泳道 ETA | — | 61087 → **67105** | 46951(67244/67246) | commit `4ce7a9b7`;job 896 根因=worker 伪 staff 过不了项目 scope(API 入队已 scope,worker 改无 scope 直取+全程兜底 _mark_failed);queue_view 增 queue_position/ahead/eta;TaskProgressBoard 排队区显示前方/约X分钟(冻结区按用户直接指令加法);UI 通知卡深色化 `b0833b78` |
| 待办 | 残留:合同行(job 896 target)卡 processing → 浏览器「删除+重传」即解;job_execution_ledger 两条 5/20 孤儿 `vkpi_official_channel_sync` 在排队区显示为『搜索/抓取·未命名』(R3 欠账,清理归 P6) | — | — | — | 手动 UPDATE 被守卫拦(协议⑥),命令已报用户 |
| 2026-06-11 15:0x–16:18 | **Window B-fix**:复盘聚合三连修(用户授权重载+重跑) | — | 67105→75118→77965→**79067** | — | ① `9f8124cb` worker staff.id 反查(job 900 FK 违例:user 108→真 staff 84;`_resolve_job_staff`);② `edaeb38d` max_output_tokens 1200→4000 + 解析失败不写垃圾 cache;③ `7194ba09` 路由 openai(gemini-flash 思考吃光预算 out=43→574 仍截断)。**job 903=done**:insight 干净中文 / highlights4·risks3·next_steps2 / gpt-5.4-mini / 零触 fit_score。指纹 1123/507.4200/1123 不变;cache(project:3998)=ready |
| 同窗 UI | 合同表单回填 `619646da` + deliverables 整理只读 `3f790c62`(去 JSON 代码)+ 泳道 ETA `4ce7a9b7` + 通知卡深色 `b0833b78` | — | — | — | 纯前端,dist 已 build |

## 模型路由表(2026-06-11 追认,复盘→openai 已批)
| 场景 | provider/model | 理由一句 |
|---|---|---|
| 复盘聚合(project_retrospective_v1) | **openai / gpt-5.4-mini**(`preferred_provider`,失败按链回退) | 需完整结构化 JSON;gemini-flash 思考吃光 maxOutputTokens 致截断(out=43/574 两次实证) |
| 视频深析(final_v1/keyframe_qa) | **gemini**(既有链路) | 多模态视频理解主力,链路冻结语义不动 |
| 合同提取(project_contract_extract) | **claude / Opus**(claude_contract_extract.py) | PDF 条款逐字提取,证据保真(库存永远原文) |

**复盘成本实测(job 903 / call 1646)**:in 13,244 + out 492 tok ≈ **$0.0043/次**,占 single_call $1 上限 0.43%。$5 cron 预算:典型 ~1,100 次;最坏口径(输出吃满 4000 tok ≈ $0.013)~380 次——首批 1-3 项目验证完全无压力。
⚠️ 记账盲点(闸A telemetry):`cost_cents` 整数地板除,亚美分调用记 0 → `current_spend` 不累计,预算护栏对复盘这种小额调用实际"看不见花费"。属 gateway 平台层,P6 一并考虑(改 microcents 或按 estimated_cost_usd 累计)。

**P6 立案 · gemini thinking 饥饿(平台级,记档不修)**:`llm_gateway._call_google` 无 `thinkingConfig`,gemini-flash 动态思考计入 maxOutputTokens——凡要求**结构化 JSON 输出**的场景都会重演截断(复盘只是首例)。P6 方案候选:generationConfig 加 thinkingConfig budget / 按 purpose 路由非 thinking 模型 / 输出截断检测重试。

**激活完成(commit 38d44af3/2a92f719/39011c89/36c3ae70 + 4ce7a9b7/b0833b78 + 9f8124cb/edaeb38d/7194ba09 全生效)。** 合同异步链 + 复盘聚合 + 费用估算 + 请求合一 现已在浏览器可用。回滚序:G4→G3→G2→G1 + 106 down + worker/admin 重载回旧。

## 一波备稿(未提交,全程 tsc+py_compile 绿,未激活)
> 单轨执行;基线 HEAD e90f28b8。改 11 文件 + 新增 3 文件(retrospective_aggregate.py、migration 106 up/down)。

**合同链(C3-C5 + 3b)**:extract/upload 端点改入队(`vkpi_projects.py`);contracts 上传尾部改 enqueue;`queue_view` 合同提取→思考中;前端去 150s 死等改轮询(`ProjectDetailView`/`projects-api.ts`);合同提取 prompt 加中文字段输出(`claude_contract_extract.py`,evidence/数字/日期保留原文)。
**复盘链(C6-C11)**:migration 106 预算 scope(cron=$5/single=$1)+ `connection.py` 序列;`retrospective_aggregate.py`(enqueue+run,Top-N≤15 按 views,600tok/视频,零触 kol_pool);worker 第5早返回分支+handler;generate/GET 端点(R1 失败可见);复盘卡真 LLM 展示+R3「AI聚合·未定标」/「模板·非AI」徽章+轮询;`queue_view` 复盘→总结中。
**批4(费用三件)**:productCost 接 SKU 目录估算(5 文件串线 + 「估」标识);合同确认→签约费幂等入账本(`cost_type=cash_fee`,source_ref 去重);今日提醒诚实化「仅本机」。
**批5**:video-analysis 双请求合一(后端逗号多值向后兼容 + 前端一次取回)。
**待办(下一聚焦段)**:批4 截图/合同 stub 接真文件 + ProjectEvidenceForms 接主路径(最重一块,单独做)。

## 待执行(gated)
- **Window A(合同异步)**:C2 worker 分支重载 → 然后 C3 端点切异步 + C4 泳道 + C5 前端 → 批2 验收。**无 migration**(合同复用现有预算 scope)。需:②窗口检查(0 running 且 60s 可 claim=0)+ ④单次重载。
- **Window B(复盘)**:C6 migration 106(预算 scope cron=$5/single=$1)+ C7 域 + C8 worker 分支 → apply 106 + 重载 → C9/C10/C11 → 批3 验收。需:①口头确认当日 pg_dump 在工作树之外 + ③迁移三段式过目。
