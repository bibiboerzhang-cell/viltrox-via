# P5 收口对账包(终稿 · 2026-06-11 封卷)

> 单轨执行(Codex 缺席至 P5 收口)。Codex 回归首日凭此对账。分支 `codex/dashboard-real`,**不 push**。
> 授权令(驻留,批2–批5)见对话回执;部署不变量:**C3 端点切异步必须在 C2 worker 分支重载生效之后**(已遵守)。
> **状态:P5 封卷。** 五批 + 18 项扫描高优 + #5 物料链 + 口径一致化(#6/#9/#10/#11)全部落地并激活;contract:10 语义豁免、幂等补键路径均经裁决批准。

## 一、完整 commit 流水(P5 全卷,自批1起)
| commit | 内容 | 闸 |
|---|---|---|
| `eea9ff22` | 批1:物料 tab 假面清除 | 无 |
| `4fd65963` | C1 合同提取 enqueue helper + 内核拆分 | 无 |
| `e90f28b8` | C2 worker 合同提取分支 + handler | 闸C |
| `38d44af3` | G1 合同异步链(C3-C5 + m1-m3 轮询契约) | 闸B/C |
| `2a92f719` | G2 复盘聚合链(C6-C11,migration 106,零触 fit_score) | 闸A/B/C |
| `39011c89` | G3 费用真值三件(SKU 估算/合同费入账/提醒诚实化) | 闸A/C |
| `36c3ae70` | G4 video-analysis 双请求合一 | 闸B |
| `2174f916` | 激活窗口记录(106 apply + worker/admin 重载) | 闸D |
| `b0833b78` | 通知/确认卡深色化(白底不可读修复) | 无 |
| `4ce7a9b7` | Window A-fix:worker ScopeDenied + 泳道排队位次/ETA | 闸C |
| `228be591` | 文档:Window A-fix 记录 | — |
| `619646da` | 合同确认表单提取值回填(useState 只跑一次 bug) | 无 |
| `1bb40ad9` | 文档:KOL 四环漏斗盘点 + 施工卡 C1-C10 草案 | — |
| `2eb36290` | 文档:Projects 全盘扫描 18 项问题 + 修复序列 | — |
| `9f8124cb` | Window B-fix①:worker staff.id 反查(job 900 FK 违例) | 闸C |
| `3f790c62` | deliverables 整理只读渲染(去 JSON 代码) | 无 |
| `edaeb38d` | Window B-fix②:复盘 token 1200→4000 + 解析失败不写垃圾 cache | 闸C |
| `7194ba09` | Window B-fix③:复盘路由 openai(gemini thinking 截断) | 闸C(路由追认已批) |
| `07555356`/`c2db335f` | 文档:B-fix 记录 + 模型路由表 + job903 成本 + P6 立案 | — |
| `f80f3487` | 钱口径诚实语义(null='—'/0=$0,汇总卡接 detail.roi 真值) | 闸C(裁决①②) |
| `d4d26175` | 合同费账本带 assignment/kol_pool 归属键(历史行幂等补) | 闸C(裁决①) |
| `d2428ac0` | 文档:钱口径激活 + 预算护栏风险注记(裁决④) | — |
| `0c45469c` | #5 物料链:证据表单接线 + stub 诚实化 + 深色作用域 | 闸B/C |
| `f89fbd8f` | 文档:#5 段记录 | — |
| `2b822af1` | #6 合同表单补 cancellation/revision terms | 闸C |
| `f418a7f0` | #9/#10/#11 估徽章 + 漏斗流失/取消独立列 | 闸C |

**最终 dist=`f418a7f0`;后端终态=`d4d26175` 已 HUP;worker 终态=`7194ba09` 已重载。**

## 历史:原 commit 流水(收口前快照)
| commit | 内容 | 闸 | 执行影响 |
|---|---|---|---|
| `eea9ff22` | feat(projects): clear materials-tab mock facade(批1 假面清除) | 无 | 纯前端;dist 已 build(`4fd65963`)待浏览器验收 |
| `4fd65963` | feat(projects): contract extract enqueue helper + kernel split(C1) | 无 | 纯 domain 新增,未入队执行、未碰 worker/端点 |
| `e90f28b8` | feat(worker): project_contract_extract branch + handler(C2) | 闸C | 已随 Window A 重载生效 |

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
| 2026-06-11 16:4x | **钱口径上屏**(裁决①②③):汇总卡接 detail.roi 真值 + null('—',链路不存在)/0($0,有链路值为零)语义 + 明细行假 0→null + 合同费 metadata 补 assignment/kol_pool 键(历史行借重确认幂等补) | — | — | 46951(87311/87312) | `f80f3487`(前端)+ `d4d26175`(后端);dist=d4d26175。逐 KOL 分钱确认不做,并入四环链路工程(links 补 assignment 维) |
| 2026-06-11 17:0x | **#5 物料链聚焦段**(裁决⑤,既批):onUploadEvidenceFile/onAddProjectShipment 断链补接(Dashboard→…→MaterialsTab 全链);物料 tab 挂真 ProjectEvidenceForms(条款附件/物流凭证真文件上传走 /evidence/uploads;消息/内容待接自禁用);泳道『合同』→切合同归档真链,『截图』去乐观+1 假反馈;浅色表单组件加 .vkpi-campaign-evidence-forms 深色作用域覆盖 | — | — | — | `0c45469c`;纯前端,dist=0c45469c;后端 stub 未动(合同路径前端绕开) |

## 模型路由表(2026-06-11 追认,复盘→openai 已批)
| 场景 | provider/model | 理由一句 |
|---|---|---|
| 复盘聚合(project_retrospective_v1) | **openai / gpt-5.4-mini**(`preferred_provider`,失败按链回退) | 需完整结构化 JSON;gemini-flash 思考吃光 maxOutputTokens 致截断(out=43/574 两次实证) |
| 视频深析(final_v1/keyframe_qa) | **gemini**(既有链路) | 多模态视频理解主力,链路冻结语义不动 |
| 合同提取(project_contract_extract) | **claude / Opus**(claude_contract_extract.py) | PDF 条款逐字提取,证据保真(库存永远原文) |

**复盘成本实测(job 903 / call 1646)**:in 13,244 + out 492 tok ≈ **$0.0043/次**,占 single_call $1 上限 0.43%。$5 cron 预算:典型 ~1,100 次;最坏口径(输出吃满 4000 tok ≈ $0.013)~380 次——首批 1-3 项目验证完全无压力。
⚠️ 记账盲点(闸A telemetry):`cost_cents` 整数地板除,亚美分调用记 0 → `current_spend` 不累计,预算护栏对复盘这种小额调用实际"看不见花费"。属 gateway 平台层,P6 一并考虑(改 microcents 或按 estimated_cost_usd 累计)。
**风险注记(裁决④)**:这意味着**复盘的 $5 预算护栏当前形同虚设**(亚美分全记 0)。gateway 修复前,复盘批量跑的真实开销只能靠 `vkpi_llm_calls` 的 token 手工核,**不能信 current_spend**。首批 1-3 项目无碍;**全量批跑前此项必须先修**。

**P6 立案 · gemini thinking 饥饿(平台级,记档不修)**:`llm_gateway._call_google` 无 `thinkingConfig`,gemini-flash 动态思考计入 maxOutputTokens——凡要求**结构化 JSON 输出**的场景都会重演截断(复盘只是首例)。P6 方案候选:generationConfig 加 thinkingConfig budget / 按 purpose 路由非 thinking 模型 / 输出截断检测重试。

**激活完成(commit 38d44af3/2a92f719/39011c89/36c3ae70 + 4ce7a9b7/b0833b78 + 9f8124cb/edaeb38d/7194ba09 全生效)。** 合同异步链 + 复盘聚合 + 费用估算 + 请求合一 现已在浏览器可用。
**回滚序(2026-06-11 上线审查修订,沙箱实测)**:~~G4→G3→G2→G1 逐项 revert~~ **已实测在 G3 卡冲突,不可执行**。可执行路径三档:
- 档1 仅前端:`git checkout f418a7f0 -- frontend/src && npm run build` 重发 dist(不动后端);
- 档2 整段代码:`git revert --no-commit 38d44af3^..HEAD` → `npm run build` → psql 执行 `migrations/106_vkpi_project_retrospective_budget_down.sql`(仅 DELETE 两 budget scope)→ worker/admin 重载;
- 档3 数据灾难:`pg_restore` 自 `~/vkpi-db-backups/viltrox2-pre106-20260611T141238.dump`(50MB,TOC 2453 已验可读)。
档2 后验证:铁律指纹仍 1123/507.420/1123、schema_migrations 无 106 行、/health 200。

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

---

# 收口对账(终录 · 2026-06-11)

## 二、重载史(全卷)
| 序 | 时刻 | 对象 | PID 前→后 | 载入内容 |
|---|---|---|---|---|
| 1 | 14:14 | worker(单次,授权令④) | 68403→61087 | G1/G2 handler(AST 实证) |
| 2 | 14:15 | admin HUP | 46951(61574/61575) | C3/C9 端点 |
| 3 | 14:5x | worker(A-fix) | 61087→67105 | ScopeDenied 修复 |
| 4 | 14:5x | admin HUP | 46951(67244/67246) | queue_view ETA |
| 5 | 15:0x | worker(B-fix①,用户授权) | 67105→75118 | staff.id 反查 |
| 6 | 15:1x | worker(B-fix②) | 75118→77965 | token 4000+解析加固 |
| 7 | 15:2x | worker(B-fix③) | 77965→**79067(终态)** | openai 路由 |
| 8 | 16:4x | admin HUP | 46951(**87311/87312 终态**) | 合同费归属键 |

## 三、铁律对账全记录(fit_score / kol_pool 零写入)
- 基线指纹:`vkpi_kol_pool` 行 1123 / fit_score 合计 507.4200 / fit_reason 非空 1123
- 复跑核验:Window A 激活后 ✅ / 复盘 job 901 后 ✅ / job 902 后 ✅ / job 903 后 ✅ —— **四次全程不变**
- 复盘 result 静态扫描:`fit_score|fit_reason|rule_v0|rubric` 命中 0;cache 仅写 `target_type='project'`
- 评分语义(rule_v0/V6/rubric)、KOL Pool 前端、合同提取 prompt(3b 撤改后零 diff)全程未触
- 守卫拦截记录 2 次(单行 UPDATE、未授权重载),均按协议⑥停手报告,无绕过

## 四、P6 移交清单(正式)
| # | 事项 | 来源 | 性质 |
|---|---|---|---|
| P6-1 | **gemini thinking 饥饿**:`_call_google` 无 thinkingConfig,凡结构化 JSON 输出场景必截断(复盘已实证 out=43/574)。方案候选:thinkingConfig budget / 按 purpose 路由 / 截断检测重试 | Window B-fix | 平台级缺陷 |
| P6-2 | **预算护栏亚美分盲区**:cost_cents 整数地板除→current_spend 不累计,复盘 $5 cap 形同虚设。**全量批跑复盘前必须先修**;期间真实开销靠 vkpi_llm_calls token 手工核 | 裁决④ | 平台级缺陷(高优) |
| P6-3 | job_execution_ledger 两条 5/20 孤儿 `vkpi_official_channel_sync`(排队区显示『搜索/抓取·未命名』) | 审计 R3 | 数据清理 |
| P6-4 | 逐 KOL 分钱:links 补 assignment 维归因键(键存在前任何逐 KOL 钱数都是编的) | 钱口径验源 | 链路工程(并入四环) |
| P6-5 | 物料 tab 消息/内容两表单句柄(onAddProjectMessage/onAddProjectContent)接入 | #5 段 | 接线 |
| P6-6 | 后端 stub `project_kol_action_stub` screenshot 分支接真文件存储(前端已诚实化) | 扫描 #5 | 接线 |
| P6-7 | i18n 全站专单(149 文件 ~3400 处,跨冻结区,见 plan 记档) | 批2 记档 | 专单 |
| P6-8 | 漏斗转化率 cohort 化(现为当前占比口径,已如实标注)+ 添加KOL选择器相关性过滤(扫描 #14/#16,低优) | 扫描 | 优化 |
| P6-9 | KOL 四环施工卡 C1-C10(收藏持久层→backfill→选择器切 My KOL→Dashboard funnel),见 `docs/KOL-funnel-survey-20260611.md` | 四环盘点 | 已批准启动序列(C1 起点) |
| P6-10 | 裁决记录遗珠(**归宿批6**):E 时间轴第二步 / 物流自动刷新(漏斗『每日刷新待接入』徽章转真)/ 服务端分页 / 7天自动化 / noUnusedLocals 守卫 / repairCenter CTA | 历次裁决 | 批6 |

## 五、下一单(既定,P5 卷外)
1. **晨间解冻减法仪式**(一 commit):删 V615Sidebar 死文件 + GEN2 假徽章收敛真假两档 + TaskProgressBoard 休眠监听整组删除(三条件已验:生产者 0 调用点/合同已走队列回显/无其他 dispatcher)
2. Jianbo 浏览器扫 Pool → **冻结解除**
3. 并行开工:KOL Pool 差量诊断(四盲区)+ 四环 C1(migration 107 三段式,vkpi_kol_pool_favorites)

**P5 封卷。**

## 封卷后 · 上线预备窗口(2026-06-11 晚)
| commit | 内容 | 性质 |
|---|---|---|
| `6124c74a` | 上线审查 PV-1(formatMoney null 守卫)+ PV-2(V6 路径 evidence 三 props)+ PV-4(回滚序改写三档)〔原 F1/F2/F4,F 字头收编归 Pool 总册专属〕 | 前端阻断修复,dist=6124c74a |
| `bc68d94f` | **产品成本晋升**:staging 834 行(5/19 飞书导入,全 CNY,从未晋升)→ catalog **667 行**(126 规范 SKU 匹配 + 541 配件原文保留;CNY→USD @7.20 估算口径,note 留 ¥原值;幂等脚本 `backend/scripts/promote_product_costs.py`) | 数据晋升(用户授权"直接改好"),无需重载;指纹复验不变 |

效果:费用/物流 tab 产品成本估算出真数字;新建项目产品 datalist(369 官网 SKU 之外含全部成本目录项)复活。**PV-3(员工可见性,原 F3)仍待用户拍板,明早写窗口执行。**

---
# KOL-Pool 卷(战役 handover,2026-06-11 起)
| 时刻 | 动作 | commit | 对账 |
|---|---|---|---|
| 06-11 | B1 减法仪式(V615Sidebar/假徽章/休眠监听) | `75d6cc84` | 纯减法,fit 指纹不动;待"无恙"收口 |
| 06-11 | B2 差量报告 + 107 备稿(未注册) | `a52a8bcc`/`f92faca4` | 第〇段验收件 |
| 06-12 | 停摆诊断 + D 裁决 + E5/E6 移交 | `8ec1ed9e` | 双重主动闸,非故障 |
| 06-12 | 自走令 d1-d6(一单一 commit) | `65b6b2e0`→`ae69f01c` | 全 tsc 绿+自审三问;d4 零 SQL 写入证明;dist=ae69f01c |
| 06-12 | B 心跳 + C 报价 + G dry-run 清单 | (本 commit) | 13 job 死因=ENABLE_SCHEDULER=0;qualified 真实面 25 行;C5 清单 781 对待过目 |
| 等待 | "无恙"→C3/C4 commit;"apply"→107+C2;"清单过目"→C5;"报价"→脉冲 | — | 四拍齐等 |
