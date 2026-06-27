# Repository 层(L2)· 渐进迁移计划

## 为什么
当前 ~1769 处 SQL 散落在 router/service/domain,scope 收口、测试、复用都难。L2 把"取数/写数"集中到 repository。

## 模式
- `repositories/base.py`:`BaseRepository`(conn/fetch_one/fetch_all/execute/scalar/exists/table)。
- `repositories/<域>_repo.py`:每域一个 repo,只读写本域表;**不做业务裁决、零触 viltrox_fit_score**。
- 调用方(domain/service)用 repo,不再写裸 SQL。

## 渐进迁移(稳做,不一次性大改)
1. ✅ **试点**:`KolPoolRepository` + 迁移 `discovery/enroll.py`(本刀,行为不变,回归绿)。
2. ⬜ 高频域逐个搬:kol_pool 读路径 → projects → events → metrics。每域:建 repo → 迁该域调用方 → 跑该域测试 → 提交。
3. ⬜ 巨型 router 瘦身(L1)配合:router 只做参数/权限/调 service,service 调 repo。

## 原则
- **每刀行为不变**:抽取 SQL 进 repo,调用方改调 repo,**输出必须与改前一致**(回归绿才提交)。
- **不为迁而迁**:优先迁"多处复用 + 需 scope 收口"的查询;一次性裸查可后排。
- **可回滚**:每刀独立、绿到绿。

## 验收
新代码默认用 repo;裸 SQL 占比逐刀下降;同一查询不再多处复制。
