##### SUMMARY
Projects 板块主体接线为真:合同归档全链(上传→worker提取→回填→确认→入账→下载→删除)、复盘 tab 闭环代码、参与/推进/添加KOL/时间轴/漏斗计数、video-analysis 双请求已合一、证据上传后端(/api/marketing/evidence/uploads,经 main.py:583 别名中间件解析到 /api/admin/vkpi)均已实现且可解析,无任何前端调用打到不存在的后端路由(6 路交叉确认:数据流扫描已逐条核对 26 个端点全部解析成功)。真正欠账集中在三类:(1) 钱口径在主流多KOL场景全断链——assignmentToProjectRow 硬编码 gmv/cost/clicks/orders=0,丢弃后端已算好的项目级 GMV/ROI/点击;合同费 cash_fee 账本行因 source_ref/metadata 口径不匹配进不了 KOL 明细行(DB 现已有 1 条 cash_fee,bug 已可观测)。(2) 证据上传能力在新页面路径被静默丢弃——onUploadEvidenceFile 声明并由 ProjectsPage 传入,但 ProjectDetailView 不解构不转发,CampaignMaterialsTab 仍是纯占位;已写好的 ProjectEvidenceForms 在新 UI 完全无入口。(3) 提取数据展示缺口——cancellation_terms/revision_terms 后端存了前端整体丢弃(类型有、渲染零),deliverables 摘要被 JSON.stringify 成乱码。截图/合同 assignment 级 stub 是审计日志-only 假按钮配乐观+1(非 404,纠正合同扫描的误判)。复盘聚合:project 级 cache=0 行、唯一一次 job(id 900)状态 failed——不是"未激活"而是"试跑一次即失败",需先根因再做激活验收。已修的两 bug(合同提取 ScopeDenied、确认表单回填 useEffect)已确认在位,不再报。

##### RANKED ISSUES
#1 [high] (broken_wiring) 参与行映射硬编码 gmv/cost/clicks/orders=0,主流多KOL场景钱口径全空
   ev: frontend/src/components/vkpi/hooks/useProjectDetail.ts:144-148 assignmentToProjectRow 写死 clicks:null/orders:null/gmv:0/cost:0/roi:null;而同文件 projectDetailToRow:182-216 已从 detail.roi.revenue_cents/cost_cents、detail.link_summary、detail.sales_attributions 读出真值。多KOL项目 baseRows=participatingRows 走 assignmentToProjectRow 不读真值。DB 现状:vkpi_cost_ledger 58 行(product54+shipping3+cash_fee1)、sales_attributions/link_clicks 有数据。
   fix: assignmentToProjectRow 按 assignment_id/kol_pool_id 从 detail.sales_attributions/link_clicks/costs 聚合回填 gmv/clicks/orders/cost,或后端 participating_kols 查询 LEFT JOIN 这三张表返回逐KOL金额。
#2 [high] (broken_wiring) onUploadEvidenceFile 在 ProjectDetailView 被静默丢弃,证据上传链在新页面不可达
   ev: frontend/src/domains/projects/projectDetailModel.ts:167 声明 onUploadEvidenceFile;frontend/src/components/vkpi/pages/ProjectsPage.tsx:30/83/385 已传入;但 frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx:91-113 props 解构完全没有 onUploadEvidenceFile(grep 0 处),组件体内零引用。后端 backend/app/api/routers/vkpi_evidence_assets.py:35 /evidence/uploads 已实现并落 /uploads/vkpi_evidence/。
   fix: ProjectDetailView 解构加 onUploadEvidenceFile 并下传给 CampaignMaterialsTab(连同 onUpsertProjectTerms/onAddProjectShipment)。
#3 [high] (empty_render) CampaignMaterialsTab 营销物料段纯静态占位,已写好的 ProjectEvidenceForms 无入口
   ev: ProjectDetailTabs.tsx:2114-2116 渲染固定文案『物料库尚未接入/产品图/参数手册/脚本…后续版本上线』;CampaignMaterialsTab props(~2054-2070)只有 onCopy/onPendingAction,无上传或证据写入句柄。完整可用的 4 表单组件在 frontend/src/components/vkpi/drawers/ProjectEvidenceForms.tsx 但只挂在旧版 ProjectDetailDrawer。
   fix: 在 assets 段引入 <ProjectEvidenceForms> 替换占位块,接 onUploadEvidenceFile/onUpsertTerms/onAddShipment;消息/内容两表单缺 onAddProjectMessage/onAddProjectContent 句柄(model 中未声明),本期可先接条款附件/物流凭证/截图三表单,消息与内容明确标注待接以免出现假表单。
#4 [high] (broken_wiring) 合同费 cash_fee 账本行进不了 KOL 明细行(source_ref/metadata 口径不匹配),DB 已有 1 行使 bug 可观测
   ev: 写:backend/app/domains/projects/contracts.py:644 source_ref=f"contract:{contract_id}" + :662 metadata={contract_id,from}(无 assignment_id/kol_pool_id);读:ProjectDetailTabs.tsx:131-137 costRowAmount 只按 source_ref 形如 assignment_contract:{assignmentId}/endsWith(:{assignmentId})/metadata.assignment_id/kol_pool_id 匹配 → 'contract:{id}' 命不中。DB 现已有 cash_fee 1 行,顶部汇总卡(行级无关)能对,KOL 明细合同费列(:1920)恒 0 回退残差推算。
   fix: _record_contract_fee_to_ledger 把该合同对应 assignment_id/kol_pool_id 写进 metadata(或 source_ref 用 assignment_contract:{assignment_id}),让 costRowAmount 按 KOL 命中。
#5 [high] (fake_button) 截图/合同(assignment 级)stub 是审计日志-only 假按钮配乐观+1(非 404;纠正合同扫描误判)
   ev: backend/app/domains/projects/workflow_evidence.py:600-621 project_kol_action_stub 仅 audit.log_business_event 后 return {status:'pending_integration'},不存文件;前端 ProjectDetailView.tsx:480-495 对 screenshot/contract 只 setEvidenceOverrides 本地+1 并弹『已记录操作』成功 toast。路由 vkpi_projects.py:280 /projects/{id}/kols/{ref}/{action_kind} 真实存在,/api/marketing 经 main.py:583 别名中间件重写到 /api/admin/vkpi——所以不是 404 断链(合同扫描的『agreed ContractUploadModal 提交 404』判定错误),是真实命中后只写审计日志的假按钮。
   fix: 把 stub 的 screenshot/contract 分支接真实文件存储(参照 contracts.create_contract_from_file 走 R2/evidence uploads),或前端未接入前移除乐观+1 并把按钮文案/notice 明确为『功能开发中』。
#6 [high] (empty_render) 合同 cancellation_terms/revision_terms 被前端整体丢弃:后端存了/返回了但不显示不可编辑不回传
   ev: DB vkpi_project_contracts 有这两列;projects-api.ts:81-82 VkpiProjectContract 类型含 cancellation_terms/revision_terms;但 ProjectDetailTabs.tsx ContractDraft(:464)、initialContractDraft、buildConfirmPayload(:501)、字段渲染列表(:646)只覆盖 breach_terms/payment_terms,grep cancellation/revision 在该文件 0 处渲染。
   fix: 在 ContractDraft/initialContractDraft/buildConfirmPayload 与字段渲染列表里补 cancellation_terms、revision_terms(连同 ConfidenceBadge),与 breach_terms 同构。
#7 [med] (empty_render) 复盘聚合从未成功跑过:project 级 cache=0,唯一一次 job(900)状态 failed,AI insight 从未真展示
   ev: SQL 实测:vkpi_analysis_cache 仅 contract:1/video:431,target_type='project'=0 行;apify_jobs project_retrospective_aggregate 仅 1 行 id=900 status='failed'。前端 ProjectDetailTabs.tsx:1464-1525 徽章/渲染都 gate 在 retroResult,无 result 恒显『模板·非AI』占位。
   fix: 不是纯『未激活』而是『试跑一次即失败』:先查 job 900 失败根因(worker run_project_retrospective LLM/写 cache 路径),修通后对一个有 ready final_v1 的项目重跑,确认 job done、cache(project) 落 ready、徽章翻『AI聚合·未定标』并渲染 insight/highlights/risks/next_steps 作为验收。
#8 [med] (empty_render) 卡头『交付』摘要把 deliverable 对象 JSON.stringify 成乱码
   ev: ProjectDetailTabs.tsx:585 交付:{compactList(contract.deliverables_json)};compactList(:402-410)对 object 项只取 record.description/item/title/name,否则 fallback JSON.stringify。但 deliverable 对象键是 platform/content_type/quantity/deadline/notes(claude_contract_extract.py:55-56 schema,DB 实测同),四候选键全不存在→整段打印原始 JSON。
   fix: compactList object 分支补 platform/content_type/quantity 组合(如 `${content_type||platform} ×${quantity}`),或为 deliverables 写专门 summarizeDeliverables 渲染函数替代裸 compactList。
#9 [med] (analysis_bug) SKU 估算『估』标识口径不一致:物料/物流段产品成本不打『估』,与财务 tab 矛盾
   ev: ProjectDetailTabs.tsx:2136 productCostAmount = costRowAmount(...,'product') || productCost(...) 回退 SKU 估算,:2184 直接 emerald 展示无『估』徽章。对比 CampaignFinanceTab:1919 算 productCostIsEstimate,:2013-2015 才有『估』徽章(title『按 SKU 成本目录单价估算』)。
   fix: 在 :2136 同样算 isEstimate=!costRowAmount(...,'product') && productCost(...)>0,:2184 估算分支补与财务 tab 同款『估』徽章。
#10 [med] (analysis_bug) 合同费列残差推算值无『估』标识,被当确凿合同费配『已签合同』徽章展示
   ev: ProjectDetailTabs.tsx:1922 contractFee = ledgerContractFee || Math.max(expenseAmount-shippingFee-productCostAmount,0);渲染 :2003-2005 直接 formatMoneyShort,:2027 配『已签合同』徽章,无 estimate 标记(产品成本列 :2013-2015 有『估』)。
   fix: 新增 contractFeeIsEstimate=!ledgerContractFee && contractFee>0,在 :2004 加同款『估』徽章+tooltip(按总支出倒推估算)。
#11 [med] (analysis_bug) 漏斗末列『已关闭』把 churned/cancelled/lost 全部折叠,口径失真
   ev: frontend/src/domains/projects/projectDetailModel.ts:199 stageIndex 对 terminalStages 或 cancelledStages 一律返回 primaryStageFlow.length-1(末列);funnel 渲染 ProjectDetailView.tsx:955-989 直接用 stageIndex。DB 实有 churned 160 + cancelled 6。时间轴(ProjectDetailTabs.tsx:280-281)却能分流失/停滞,两处口径不一致。
   fix: 漏斗增独立『流失/取消』聚合列(或 stageCounts 把 cancelledStages 单独计数),不并入 closed。
#12 [low] (analysis_bug) 承诺条数为 0 显示『待确认』(falsy 0)
   ev: ProjectDetailTabs.tsx:617 contract.deliverable_count || '待确认',数字 0 触发 falsy。
   fix: 改 contract.deliverable_count ?? '待确认'(nullish 而非 ||),与 fee 等字段口径一致。
#13 [low] (broken_wiring) confirm_contract 不重置 extraction_status,processing 中途确认会让『重新提取』永久卡『提取中』
   ev: backend/app/domains/projects/contracts.py:612-628 confirm_contract 只置 status='confirmed'+field_confirmed_json,不动 extraction_status;前端 ProjectDetailTabs.tsx:561 extracting=...||extraction_status==='processing' 卡禁用。仅 processing 时抢先确认的罕见竞态。
   fix: confirm_contract 在 status='confirmed' 时把 extraction_status 从 processing/needs_review 收敛为 'ready'。
#14 [low] (broken_wiring) 添加KOL选择器走全池、无项目相关性过滤
   ev: backend/app/domains/projects/workflow_projects.py:576-634 list_available_project_kols 仅 NOT EXISTS 排除已加入+可选模糊搜索,ORDER BY followers DESC;前端 getAvailableProjectKols projects-api.ts limit 500。整池按粉丝排,不按 product/platform/品牌相关性。
   fix: available 查询增按项目 product/platform/品牌或历史合作的相关性排序/过滤入参,或前端默认按 project.platform 预过滤。
#15 [low] (dead_code) 单方法版 getProjectVideoAnalysisCache 已无调用方(被 Multi 版取代)
   ev: projects-api.ts:151 export getProjectVideoAnalysisCache;全仓去 Multi/export 后 0 调用方,ProjectDetailView.tsx:171 已统一用 Multi 版。后端 vkpi_projects.py:79 保留 len==1 旧形状仅向后兼容。
   fix: 删除 projects-api.ts 单方法版 getProjectVideoAnalysisCache 及未用类型,保留 Multi 版。
#16 [low] (analysis_bug) 漏斗转化率 →rate 用相邻阶段当前在册数之比,非真实 cohort 流转率
   ev: frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx:959-960 rate=nextCount/count(均为当前各阶段占用数),非同批 KOL 历史转化。
   fix: 改用基于 stage_events 的历史进入计数算 cohort 转化,或 UI 标注为『当前占比』而非『转化率』。
#17 [low] (fake_button) 漏斗右上『状态·每日刷新待接入』徽章纯展示占位
   ev: frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx:947-952 带 pointer-events-none select-none + title 说明刷新待视频URL每日 job 接入。诚实度尚可。
   fix: 每日刷新 job 上线后改真按钮触发刷新;或保留但弱化视觉。低优先,可不动。
#18 [low] (broken_wiring) VkpiProjectRetrospectiveResult.provenance TS 类型缺字段(evidence_ids/totals/source_derive_method)
   ev: projects-api.ts:168 provenance 只声明 video_count/model/provider/generated_at/selection/top_n;后端 retrospective_aggregate.py:247-257 还写 evidence_ids/source_derive_method/totals。当前渲染(ProjectDetailTabs.tsx:1507)只用已声明字段,无运行期 bug。
   fix: provenance 类型补 evidence_ids?:number[]; totals?:{views?:number;engagement?:number}; source_derive_method?:string,为后续 UI 展示留类型支撑。

##### FIX SEQUENCE
- A1 钱口径回填(明细+汇总,最高用户可感知) [闸:A]
    scope: assignmentToProjectRow 按 assignment_id/kol_pool_id 从 detail.sales_attributions/link_clicks/costs 聚合回填 gmv/clicks/orders/cost/roi(或后端 participating_kols LEFT JOIN 三表返逐KOL金额);同步把合同费 ledger 写 metadata.assignment_id/kol_pool_id 让 costRowAmount 命中合同费列。纯前端聚合优先、零迁移。
    files: frontend/src/components/vkpi/hooks/useProjectDetail.ts; backend/app/domains/projects/contracts.py
- A2 证据上传接线(三表单真入口) [闸:A]
    scope: ProjectDetailView 解构 onUploadEvidenceFile 并下传 CampaignMaterialsTab;assets 段用 ProjectEvidenceForms 替换占位块,接 onUploadEvidenceFile/onUpsertProjectTerms/onAddProjectShipment;消息/内容两表单缺句柄,先标注待接不渲染假表单。
    files: frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx; frontend/src/components/vkpi/pages/projects/ProjectDetailTabs.tsx
- B1 假按钮诚实化 [闸:B]
    scope: 截图/合同 assignment 级 stub:接真实文件存储(走 evidence uploads/R2)或前端移除乐观+1、文案改『功能开发中』,去掉假成功 toast。
    files: backend/app/domains/projects/workflow_evidence.py; frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx
- B2 合同提取字段展示补全 [闸:B]
    scope: ContractDraft/initialContractDraft/buildConfirmPayload/字段渲染列表补 cancellation_terms+revision_terms(同 breach_terms 同构,带 ConfidenceBadge);deliverables 摘要写 summarizeDeliverables 替代裸 compactList(用 platform/content_type/quantity);deliverable_count 改 ?? 。
    files: frontend/src/components/vkpi/pages/projects/ProjectDetailTabs.tsx
- C1 估算口径与漏斗口径一致化 [闸:C]
    scope: 物料/物流段产品成本补『估』徽章(:2136/2184);合同费残差推算补 contractFeeIsEstimate『估』徽章(:1922/2004);漏斗增独立『流失/取消』列不并入 closed(model:199 + ProjectDetailView funnel);转化率标注或改 cohort。
    files: frontend/src/components/vkpi/pages/projects/ProjectDetailTabs.tsx; frontend/src/domains/projects/projectDetailModel.ts; frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx
- C2 复盘聚合根因+激活验收(先查后跑,无代码改前置) [闸:D]
    scope: 查 apify_jobs id=900 project_retrospective_aggregate 失败根因(worker run_project_retrospective LLM/写 cache 路径),修通后对有 ready final_v1 的项目重跑,验 job done/cache(project) ready/徽章翻 AI聚合·未定标/四字段渲染。
    files: backend/app/services/apify_jobs_worker.py; backend/app/domains/projects/retrospective_aggregate.py
- D1 清理与类型补全(无功能风险,最后做) [闸:D]
    scope: 删除单方法版 getProjectVideoAnalysisCache 死代码;补 provenance TS 类型字段;confirm_contract 收敛 extraction_status 竞态;接线后清理物料 onPendingAction 占位文案。
    files: frontend/src/services/vkpi/projects-api.ts; backend/app/domains/projects/contracts.py; frontend/src/components/vkpi/pages/projects/ProjectDetailTabs.tsx

##### HEALTHY KEEP
合同归档主链路全真接通(ProjectDetailTabs.tsx ContractArchiveCard + CampaignContractsTab + contracts.py + claude_contract_extract.py),DB 抽样 platforms_json/deliverable_count/field_confidence_json(16键全)/deliverables_json/must_include_json 正确落库,job 897/898/899 done、conf 徽章口径与后端 field_confidence 键一一对齐——查看PDF/重新提取/删除/保存/确认/DOCX skipped 分支均为真,别误改。合同上传 uploadContractFile→uploadProjectContract 真接通。证据上传后端 vkpi_evidence_assets.py:35 /evidence/uploads 已实现(落 /uploads/vkpi_evidence/),ProjectEvidenceForms.tsx 4 表单组件完整可用——是真路径,接线时复用别重写。快递追踪段真接通。所有 /api/marketing/* 端点经 main.py:583 marketing_api_alias_middleware 重写到 /api/admin/vkpi 真实路由,无任何 404 硬断链(26 端点已逐条核对;特别注意:submitProjectKolActionStub 的 /api/marketing/.../kols/.../{actionKind} 不是 404,路由 vkpi_projects.py:280 存在,问题在后端 stub 仅写审计日志——按 fake_button 处理,勿当 broken_wiring 改 URL 前缀)。复盘 tab 代码闭环正确:worker 分支已挂 apify_jobs_worker.py:1431、失败写 blocked 读端可见、R3 徽章按 retrospective.result 切换逻辑正确、空渲染保护齐全——代码无需改,只缺一次成功生成。video-analysis 双请求已合一(ProjectDetailView.tsx:171 getProjectVideoAnalysisCacheMulti,后端 vkpi_projects.py:78-84 by_method 拆分,QA 经 qaItemForAnalysis 真喂入卡片)。每 KOL final_v1 卡 ProjectVideoAnalysisCard 直渲真 LLM 6 层结果。参与KOL 表格/推进/停滞/流失/释放/添加KOL(全池)/时间轴事件/漏斗阶段计数接真数据。views/likes/comments/曝光真实(vkpi_kol_video_evidence 1068 条/18亿曝光)。已修两 bug 在位:合同提取 ScopeDenied(worker 已修)、确认表单回填 useEffect(ProjectDetailTabs.tsx:526 editedRef+extractionSig 回填已加)——不要回退。productCost SKU 估算/合同签约费入账本/今日提醒本机诚实化/财务 tab 估实标识本身写法正确,DB 现 cash_fee 1 行证实入账链路活着。


========== 各路 issues 明细(material/cost/kol_summary/dataflow) ==========

#### 物料 tab (MaterialSection / CampaignMaterialsTab) + 证据上传接线
物料 tab 的"营销物料"段是纯静态占位（"物料库尚未接入"），但真实上传链路其实已经全部存在：ProjectEvidenceForms.tsx 是完整可用的 4 表单组件（消息/内容/条款/物流），每个都带文件上传，wired 到 onUploadEvidenceFile → uploadMarketingEvidenceFile → POST /api/marketing/evidence/uploads（后端 vkpi_evidence_assets.py:35 已实现，返回 file_url 落 /uploads/vkpi_evidence/）。问题是这个真表单只挂在旧版 ProjectDetailDrawer（VkpiDashboard 路径），新页面版 CampaignMaterialsTab 完全没有渲染它。更糟的是 onUploadEvidenceFile 在 ProjectDetailViewProps 已声明、ProjectsPage 也已经传进去，但 ProjectDetailView 的 props 解构（92-114 行）根本没把它取出来转发——上传能力在新路径上被静默丢弃。这就是 #5 pending 这块最大欠账：真路径在 ProjectEvidenceForms + /api/marketing/evidence/uploads，假在 Campai
  [high/broken_wiring] onUploadEvidenceFile 在 ProjectDetailView 被静默丢弃（声明+传入但不解构不转发）
     ev: frontend/src/domains/projects/projectDetailModel.ts:167 声明 onUploadEvidenceFile;frontend/src/components/vkpi/pages/ProjectsPage.tsx:384 传入 onUploadEvidenceFile={onUploadEvidenceFile};但 frontend/src/co
     fix: ProjectDetailView 解构里加 onUploadEvidenceFile，并下传给 CampaignMaterialsTab（连同 onUpsertProjectTerms/onAddProjectShipment 一起）。
  [high/empty_render] CampaignMaterialsTab 营销物料段是纯静态占位，未渲染真实证据上传表单
     ev: frontend/src/components/vkpi/pages/projects/ProjectDetailTabs.tsx:2114-2118 渲染固定文案『物料库尚未接入 / 产品图 / 参数手册 / 脚本等物料管理与 LLM 起草将在后续版本上线』；CampaignMaterialsTab 的 props（2054-2070）只有 onCopy/onPendingAction，没有任何
     fix: 在 assets 段引入 <ProjectEvidenceForms projectId={project.id} onUploadEvidenceFile={...} onUpsertTerms={...} onAddShipment={...} />，替换/补充占位块。
  [med/broken_wiring] 新页面路径缺 onAddProjectMessage / onAddProjectContent 句柄，证据表单 4 表单只能接通 2 个
     ev: frontend/src/domains/projects/projectDetailModel.ts 中只声明了 onUpsertProjectTerms(165)/onAddProjectShipment(166)/onUploadEvidenceFile(167)，没有 onAddProjectMessage/onAddProjectContent;对比 VkpiDashboard.tsx:
     fix: 在 ProjectDetailViewProps + ProjectsPage 补 onAddProjectMessage/onAddProjectContent 并下传，或本期先只接 onUpsertTerms+onAddShipment+onUploadEvidenceFile（条款附件/物流凭证/截图能传），消息与内容两表单明确标注待接以免出现假表单。
  [low/dead_code] onPendingAction 物料占位：素材库功能开发中（诚实占位，非欺骗，但接线后应移除）
     ev: frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx:1042 onPendingAction=(label)=>setNotice({title:'素材库功能开发中', body:`${label} 功能开发中，敬请期待。`});CampaignMaterialsTab 中 onPendingAction 仅在 onC
     fix: 接入 ProjectEvidenceForms 后删除占位文案与 onPendingAction 物料分支提示。

#### Projects 费用 tab(成本录入/汇总/产品成本估算/合同费入账/今日提醒)
通读费用段后:四项任务点(productCost SKU 估算、合同签约费入账本、估/实际标识、今日提醒仅本机诚实化)本身都是真接线、写法正确,DB 也证实链路活着。但发现一个真实断链:刚修好的"合同费自动入账"(_record_contract_fee_to_ledger 写 source_ref='contract:{contract_id}'、metadata 不带 assignment_id/kol_pool_id)与前端 KOL 明细行的读取函数 costRowAmount(只按 assignment_id/kol_pool_id 匹配)对不上口径 —— 合同确认后 cash_fee 行虽进了顶部"合同费用"汇总卡,却进不了"KOL 费用明细"的合同费列,该列继续用残差推算且无任何"估"标。另一处轻度问题:合同费列的残差推算值被当作确凿合同费展示(配"已签合同"徽章),缺少产品成本列那样的"估"标识。DB 现状:vkpi_cost_ledger 只有 product(54)+shipping(3)行,0 条 cash_fee;vkpi_product_cost_catalog 0 行;合同表 1 份 status=extracted(未确认)、fee>0 —— 即"无 cash_fee 行/产品估算恒 0"是数据为空的预期表现,不是代码 bug。
  [high/broken_wiring] 合同费 cash_fee 账本行进不了 KOL 明细行(source_ref/metadata 口径与前端读取不匹配)
     ev: 写:backend/app/domains/projects/contracts.py:644 source_ref=f"contract:{contract_id}" + :662 metadata={contract_id,from}(无 assignment_id/kol_pool_id);读:frontend .../ProjectDetailTabs.tsx:131-137 costRo
     fix: _record_contract_fee_to_ledger 写 ledger 时把该合同对应 assignment_id/kol_pool_id 写进 metadata(或 source_ref 用 assignment_contract:{assignment_id}),让 costRowAmount 能按 KOL 命中。
  [med/analysis_bug] 合同费列残差推算值无'估'标识,被当确凿合同费展示
     ev: frontend .../ProjectDetailTabs.tsx:1922 contractFee = ledgerContractFee || Math.max(expenseAmount - shippingFee - productCostAmount,0);渲染 :2003-2005 直接 formatMoneyShort(contractFee),:2027 配'已签合同'徽章,无 
     fix: 参照 productCostIsEstimate,新增 contractFeeIsEstimate=!ledgerContractFee && contractFee>0,在 :2004 加同款'估'徽章+tooltip(按总支出倒推估算)。

#### 参与KOL tab + 数据汇总 tab + 时间轴 + 顶部漏斗
四块的「行为/写入链路」基本是真的:参与KOL 表格、推进/停滞/流失/释放按钮、添加KOL(走全池)、时间轴事件、漏斗阶段计数都接真数据。最大问题是「钱口径全断链」:多 KOL 项目(线上主流场景,38 项目 / 2184 assignment)的参与行映射 assignmentToProjectRow 把 gmv/cost/clicks/orders 全部硬编码为 0/null,而后端 detail 负载里 detail.roi / detail.link_summary / sales_attributions / cost_ledger 其实已算好真值——结果数据汇总 tab 的 归因GMV / ROI / Shopify点击 / 订单 永远显示 $0 / — / 0,KOL排名里每行 GMV 也恒为 —。views/likes/comments/曝光是真的(来自 vkpi_kol_video_evidence,1068 条 / 18亿曝光)。漏斗 8/9 阶段计数真,但 churned(160)+cancelled(6)被 stageIndex 折叠进末列「已关闭」,瓶颈提示是基于真实 stage/停留天数的启发式(非占位但口径粗)。添加KOL选择器确为全池(按 followers 排序,只排除已加入,无产品/平台/品牌相关性过滤)。一个纯展示占位:漏斗右上「状态·每日刷
  [high/broken_wiring] 参与行映射把 gmv/cost/clicks/orders 硬编码 0/null,丢弃后端已算好的项目级 GMV/ROI/点击
     ev: frontend/src/components/vkpi/hooks/useProjectDetail.ts:144-148 (clicks:null, orders:null, gmv:0, cost:0, roi:null);后端真值在 backend/app/domains/projects/workflow_detail.py:429-507 (revenue_cents/cost_cen
     fix: assignmentToProjectRow 增加按 assignment_id/kol_pool_id 从 detail.sales_attributions、detail.link_clicks、detail.costs 聚合 gmv/clicks/orders/cost 回填(或后端 participating_kols 查询 LEFT JOIN 这三张表返回逐KOL金额)。
  [med/analysis_bug] 漏斗末列「已关闭」把 churned/cancelled/lost 全部折叠进去,口径失真
     ev: frontend/src/domains/projects/projectDetailModel.ts:199 (terminalStages/cancelledStages → 返回 primaryStageFlow.length-1 即第9列 closed);funnel 渲染 ProjectDetailView.tsx:955-989 直接用 stageIndex;DB 实有 churned
     fix: 漏斗增加独立的「流失/取消」聚合列(或在 stageCounts 里把 cancelledStages 单独计数),不要并入 closed。
  [med/broken_wiring] 添加KOL选择器走全池、无项目相关性过滤(已知项确认+定位)
     ev: backend/app/domains/projects/workflow_projects.py:576-634 list_available_project_kols 仅 NOT EXISTS 排除已加入 + 可选模糊搜索,ORDER BY followers DESC;前端 getAvailableProjectKols projects-api.ts:274-281 limit 500
     fix: available 查询增加按项目 product/platform/品牌或历史合作做相关性排序与过滤入参,或前端默认按 project.platform 预过滤。
  [low/fake_button] 漏斗右上「状态·每日刷新待接入」徽章为纯展示占位
     ev: frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx:947-952 (pointer-events-none select-none,title 说明刷新待视频URL每日job接入)
     fix: 接入每日刷新 job 后改为真按钮触发刷新;或保留但弱化视觉避免误以为可操作。
  [low/analysis_bug] 漏斗转化率 →rate 用相邻阶段当前在册数之比,非真实 cohort 流转率
     ev: frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx:959-960 (rate = nextCount/count,nextCount/count 均为当前各阶段占用数)
     fix: 改用基于 stage_events 的历史进入计数算 cohort 转化,或在 UI 上把该比率标注为「当前占比」而非「转化率」。

#### Projects 详情数据流 / 泳道 / 全局接线(ProjectDetailView + projects-api.ts ↔ vkpi_projects.py)
通读了 ProjectDetailView 全部 useEffect/轮询/handler、projects-api.ts 全部 26 个端点,并逐条对照 vkpi_projects.py 路由(经 main.py:587 的 /api/marketing→/api/admin/vkpi 别名中间件解析)。结论:① 没有"前端调了后端没有/404"的硬断链——所有端点都能解析到真实路由(campaigns/budget-pools/offboard 落在 vkpi_operations.py,available-kols 落在 vkpi_kol_pool.py)。② video-analysis 双请求已确认合一:ProjectDetailView.tsx:171 一次 getProjectVideoAnalysisCacheMulti(['final_v1','final_v1_keyframe_qa']),后端 vkpi_projects.py:78-84 按 by_method 拆分,QA 结果经 qaItemForAnalysis 真实喂入 ProjectVideoAnalysisCard。③ 任务泳道 ETA/重试映射(TaskProgressBoard.tsx:64-92)渲染正常,排队区 taskEtaText 已挂在 visibleQueue 下(345 行)。真正
  [high/fake_button] 截图上传 / 合同(assignment 级)action 是假按钮:后端只写审计日志,不存文件,UI 乐观 +1 误导
     ev: backend/app/domains/projects/workflow_evidence.py:600-621 project_kol_action_stub 仅 audit.log_business_event 后 return {status:'pending_integration'};前端 ProjectDetailView.tsx:480-495 submitActionStub 对
     fix: 把 project_kol_action_stub 的 screenshot/contract 分支接真实文件存储(参照 contracts.create_contract_from_file 走 R2),或前端在未接入前移除乐观 +1 并把按钮文案/notice 明确为「功能开发中」。
  [high/broken_wiring] 合同 cancellation_terms / revision_terms 字段被前端整体丢弃:后端存了/返回了,但表单不显示、不可编辑、confirm 不回传
     ev: DB vkpi_project_contracts 有 cancellation_terms、revision_terms 列;projects-api.ts:80-81 VkpiProjectContract 类型也含这两字段;但 ProjectDetailTabs.tsx ContractDraft(interface 451-466)、initialContractDraft、buildCo
     fix: 在 ContractDraft / initialContractDraft / buildConfirmPayload 与字段渲染列表里补 cancellation_terms、revision_terms 两项(连同 ConfidenceBadge),与 breach_terms 同构。
  [low/dead_code] 单方法版 getProjectVideoAnalysisCache 已无调用方(被 Multi 版取代)
     ev: projects-api.ts:151 export getProjectVideoAnalysisCache;全仓 grep 去掉 Multi/export 后 0 个调用方,ProjectDetailView.tsx:171 已统一用 getProjectVideoAnalysisCacheMulti。后端 vkpi_projects.py:79 仍保留 len==1 旧形状仅为向后兼容。
     fix: 删除 projects-api.ts 中单方法版 getProjectVideoAnalysisCache(及其未用类型),保留 Multi 版即可。
  [low/other] 漏斗「状态 · 每日刷新待接入」是诚实占位,非假按钮(已加 pointer-events-none),确认无需改
     ev: ProjectDetailView.tsx:947-952 该 pill 带 pointer-events-none select-none + title 说明「真刷新功能将在视频 URL 每日刷新 job 接入后启用」。
     fix: 无需修改;等每日刷新 job 上线后替换为真实按钮即可。
