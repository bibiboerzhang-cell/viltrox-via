# Projects 域工程宣告(2026-06-12)

三条既成事实的工程口径,后续改动以此为准,不再逐案讨论。

## 1. vkpi 域是 Postgres-only,不再维护 sqlite 双轨

`backend/app/db/connection.py` 保留的是 sqlite 风格的 SQL 书写面(`?` 占位符、
`lastrowid`、`PRAGMA table_info`),由 `connection_sql_translation.py` 翻译后跑在
池化 Postgres 上——这是**书写习惯兼容层,不是运行时双轨**。代码里早已既成的
Postgres 专属用法包括:`NOW()`、`jsonb` 操作、`DISTINCT ON`、`INSERT ... RETURNING`。
因此:

- 新代码可以直接使用 Postgres 语义,无需为 sqlite 留退路;
- `backend/app/domains/settings/notifications.py` 的 `_db_bool`(bool/int 双轨适配)
  属于历史残留,不要在新代码中模仿;
- domain 层占位符仍统一写 `?`(交由翻译层处理),不要混用 `%s`。

## 2. 静态路由必须排在同前缀参数路由之前

FastAPI 按声明顺序匹配。`/projects/summary` 这类静态路径若声明在
`/projects/{project_id}` 之后,会被参数路由捕获并在 `int()` 转换处炸出 422/500。
此类事故项目史上已录得四案。正确示范见
`backend/app/api/routers/vkpi_projects.py`:`/projects/logistics-sync/enqueue`、
`/projects/contract-templates` 均声明在 `/projects/{project_id}` 之前。

规则:**在任何 router 中新增静态路由时,必须放在同前缀的参数路由声明之前**;
review 时把"路由顺序"作为必查项。

## 3. INSERT 后取行一律 RETURNING,禁止 ORDER BY id DESC 回查

`INSERT ... ; SELECT * FROM t ORDER BY id DESC LIMIT 1` 在并发下会取到别人的行,
属于数据正确性 bug,不是风格问题。Postgres-only(见第 1 条)意味着
`INSERT ... RETURNING *` 始终可用,且翻译层/连接池已验证支持
(`conn.execute(...).fetchone()` 直接可取)。

已按此口径改造的点(2026-06-12,P2 批):

- `backend/app/domains/evidence/messages.py` `create_message`
- `backend/app/domains/projects/workflow_evidence.py` `add_project_message`
- `backend/app/domains/costs/ledger.py` `add_cost`(仅改取行方式;
  source_ref 幂等更新逻辑未动)

规则:**新写或改到任何 INSERT 后需要回读该行的代码,一律 `RETURNING *`
(或显式列清单),不得用 ORDER BY id DESC / lastrowid 回查。**
