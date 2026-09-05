# 第一批、第二批与业务连接器：实现及接入清单

日期：2026-09-04。范围：本地代码候选与离线回归；不是线上接入或发布证明。

本轮补齐 YouTube／Instagram／TikTok 结果边界，X／Reddit 作者检索与刷新链路，以及 Shopify／GoAffPro／Amazon 报表连接器。已有未提交的稳定性修复予以保留。未提交、推送、部署、重启服务，未使用真实平台凭据调用接口或操作业务数据库。

## 1. 平台接入准备

凭据通过现有服务端配置或受权限保护的管理接口录入，不放在源码、浏览器参数或聊天里。

| 平台 | 用户接入时准备 | 本轮实现与边界 |
| --- | --- | --- |
| YouTube | `YOUTUBE_API_KEY`，兼容 `GOOGLE_YOUTUBE_API_KEY`；需要备用 Actor 时另配 `APIFY_TOKEN`、`APIFY_YOUTUBE_ACTOR_ID` 及预算 | 保留官方 API 与受控备用路径；增量更新按实际发布时间过滤、分页有界；空、失败、部分成功分别返回。 |
| Instagram | `APIFY_TOKEN`；确认 `APIFY_INSTAGRAM_ACTOR_ID`、`APIFY_INSTAGRAM_POSTS_ACTOR_ID` 的访问权限与预算 | 账号、帖子与补充资料的失败独立表达；费用或运行结果不明时不继续切换 Actor。不是 Meta 自有账号 OAuth 管理器。 |
| TikTok | `APIFY_TOKEN`；确认 `APIFY_TIKTOK_ACTOR_ID` 的访问权限与预算 | 账号内容抓取有界；区分无内容、缺配置、失败、截断结果。不是 TikTok 官方发布接口。 |
| X | `APIFY_TOKEN`、`APIFY_X_SEARCH_ACTOR_ID`、`APIFY_X_ACCOUNT_ACTOR_ID`；可显式共用 `APIFY_X_ACTOR_ID` | 作者发现及账号刷新支持已核对的 `apidojo/twitter-scraper-lite` 契约。只有 Bearer Token 不启用未绑定预算的官方直连；不会自动挑选付费 Actor。 |
| Reddit | `REDDIT_CLIENT_ID`、`REDDIT_CLIENT_SECRET`、明确的 `REDDIT_USER_AGENT`，安装 PRAW，并确认平台数据访问授权 | OAuth 搜索公开投稿、作者资料及本人帖子；不使用社区订阅数或 karma 冒充粉丝。粉丝未知时明确待核验，不降低现有 3000 门槛。 |

默认检索仍是 YouTube／Instagram／TikTok。X／Reddit 需明确选择或在需求中明确指定；不会因为模型扩展平台而自动增加付费调用。X／Reddit 的帖子作为独立帖子证据存取，不伪装成视频。

共享 Actor 数据集上限为 2000 条；读取到第 2001 条即识别超限，返回部分结果及运行标识。未完成、结果未知、读取失败或超限不会被当作完整成功结算，也不自动重跑。对外调度已发出的并发请求不能撤销；保护会阻止后续扩量，不承诺撤回已经发生的费用。

每日库存维护扩展至五个平台，但总上限仍为每天 5 个新维护任务；这是任务数，不是外部 API 请求次数或费用上限。原有锁、冷却、游标与预算约束保留。真实定时执行、恢复耗时及成功率需要接入后的运行证据。

## 2. Shopify：凭据、真实队列同步与财务证据

管理前缀：`/api/admin/vkpi/shopify`，继续使用现有角色权限。

1. 新店铺可调用 `POST /client-credentials/connect`，提供 `shop_domain`、`client_id`、`client_secret` 和可选 `api_version`。已有令牌可调用 `POST /creds`，提供 `shop_domain`、`access_token`、`webhook_secret` 和可选 `api_version`。
2. `POST /probe` 成功后确认 connected；`GET /creds` 仅显示脱敏状态。需要订单读取权限、应用安装与组织授权；不会自动申请全部历史订单权限。
3. 设置实际可访问的 HTTPS `PUBLIC_BASE_URL`（兼容 `APP_BASE_URL`／`WEBHOOK_BASE_URL`），调用 `POST /webhooks/register`。原生回调为 `/api/vkpi/webhooks/shopify/orders`、`/api/vkpi/webhooks/shopify/refunds`，保留原始请求体 HMAC 校验。
4. 连接成功且 Redis Stream 可用后，可调用 `POST /sync` 或 `POST /backfill`。没有持久队列时拒绝排队，不退回进程内后台任务。任务类型为 `vkpi_shopify_order_sync`。
5. `{}` 默认同步最近 24 小时；自定义 `window.start_at`、`window.end_at` 必须为带时区的时间，窗口不超过 31 天，开始时间在最近 60 天以内，结束时间不晚于当前时间。
6. 每页 20 单、每轮最多 5 页（100 单），合作式执行期限 90 秒；平台请求超时受剩余期限限制。返回 `sync_uid`、`task_id`，通过 `GET /status` 查看实际任务记录。
7. 部分完成不标记为完整成功；使用 `{"resume_sync_uid":"已签发的任务 UID"}` 续跑，服务端读取保存的窗口、筛选条件和游标。不能提交任意游标。相同任务重投从最后完整页继续；completed／partial／duplicate 或所有页已落库的任务不再次外调，仍有未完成页的 failed 任务可从已存断点恢复。

重要边界：API 回填是参考证据，不等于原生签名 Webhook，不加入已验证财务总额。订单原始总额和负向退款分开入账，退款按店铺币种处理；重放不得双计。离线样本已覆盖 API→签名订单→API 重拉→退款重放。超过单次读取 20 条 line items 的订单明确失败并保留恢复信息；这一类大订单须专项验收，当前不宣称已完整支持。

API 版本继续使用用户保存配置，仓库默认 2026-04，本轮未擅自升级。

## 3. GoAffPro：连接状态、分页和佣金口径

管理前缀：`/api/admin/vkpi/goaffpro`。

- `POST /creds` 保存 `access_token`，以及可选 `public_token`、`private_token`、`api_base`。默认 API 基址为 `https://api.goaffpro.com/v1`；保存或变更凭据后是 pending，不直接显示 connected。
- `POST /sync` 完成只读权限探测；成功才 connected，失败保留明确错误。`GET /creds` 脱敏。
- `POST /sync-sales` 做有界分页同步；显式 `limit` 是单页有界写同步，仍会写入数据库，并非预览。只读查看使用 `GET /orders`。`POST /sync-metrics` 同步指标。新分页受最多 200 页、60 秒启动期限限制；重复页、总量变化或部分失败不覆盖已有完整快照。
- `/kol/{id}/link` 沿用作者与 affiliate／coupon 映射。优先已知 affiliate；只有订单不含 affiliate_id 且存在唯一优惠码映射才可回退，显式未知 affiliate_id 或映射冲突不猜测。
- 只汇总 approved／paid／confirmed／completed 等已确认状态，退款、待确认单不混入；不同币种分组，未经汇率口径确认不合并。

## 4. Amazon：报表导入，不是自动 API 拉单

接口：`POST /api/admin/vkpi/attribution/amazon/upload`，multipart 文件，并沿用现有功能开关、角色与人工授权字段 `authorization_ref`、`authorization_reason`、`confirmed_by_human=true`。

- UTF-8 CSV／TSV，最多 20 MiB、100000 行；至少有 revenue／commission／orders 一类业务列。
- 日期及币种须由文件列或表单默认值明确提供；仅明确 `_usd` 的金额列允许隐含 USD，且不能与显式币种冲突。
- 推荐列：`date,tag,asin,revenue,commission,currency,type`。可额外提供 `transaction_id` 或 `source_ref` 以使用交易级身份。
- refund／return／returned／refunded 按负向金额处理；无效金额、非有限数、日期、币种或类型在整批写入前拒绝。
- 相同交易身份、相同金额及币种可安全重传；同一文件内相同身份但金额／币种冲突拒绝。跨次上传按身份更新已有记录，可更正金额／币种，因此不同文件不是不可变账本；人工确认后再导入修订文件。
- 无交易身份时按 tag＋ASIN＋marketplace＋date＋currency＋type 作为每日快照更新，不累加重复上传。不要把同一日快照拆成多个增量文件上传，后一个会更新前一个；应上传完整日快照或使用交易身份。

## 5. 共用 LLM 与失败保护

JSON 调用统一默认最多 2 次尝试、硬上限 3 次；嵌套调用继承剩余期限。准备、校验、缓存与记账耗时同样纳入期限检查。缓存重新验证本次 JSON 要求，过期返回和结构不匹配不能绕过约束。

结果或费用不明时保留对应预留，不按免费失败处理，也不继续切换模型放大消费。已明确产生的费用仍需记账，即使结果超过期限不能交给业务侧。真实额度、429、供应商账单对账仍需接入后验证。

## 6. 接入后逐项验收

按平台先各接一个受控账号／店铺，用明确预算验证，再扩量。以下是待做验收，不是本轮已完成结果：

1. 凭据缺失、撤销、权限不足、限流：界面和任务状态准确，不显示空结果成功。
2. 作者发现→确认身份→本人内容证据→资格结果→入库→每日刷新；Reddit 粉丝未知保留待核验。
3. 超时、Worker 中断、Redis 暂时不可用、重复投递及分页恢复：无重复任务或重复记账，并能定位失败原因。
4. Shopify 一单多次 Webhook、部分退款、全额退款、跨币种、API 与 Webhook 同单、超过 20 条明细订单。
5. GoAffPro 多页订单、重复页、凭据撤销及 affiliate／coupon 冲突；Amazon 重传、退款、币种冲突与完整日快照。
6. 连续观察定时触发与完成记录、队列积压、数据更新时间和费用；测试通过不等于线上调度成功。
7. 发布前另做受控 PostgreSQL／迁移、云端 SHA／worker、浏览器和真实业务验收。当前候选的静态门禁回执不能替代这些项目。

## 契约参考

这些资料用于接口契约核对，不代表已取得平台权限或账号接入成功：[Shopify Webhook 校验](https://shopify.dev/docs/apps/build/webhooks/verify-deliveries)、[Shopify Orders](https://shopify.dev/docs/api/admin-graphql/latest/queries/orders)、[Shopify Webhook 字段](https://shopify.dev/docs/api/webhooks/latest)、[GoAffPro Admin API](https://api.goaffpro.com/docs/admin/)、[Amazon 报表说明](https://affiliate-program.amazon.com/help/node/topic/GQ5FS7J76MT59WLW)、[X Actor 输入](https://apify.com/apidojo/twitter-scraper-lite/input-schema)、[PRAW Redditor](https://praw.readthedocs.io/en/stable/code_overview/models/redditor.html)。
