"""Marketing Brain skills 包 —— 每个 skill 是 thin wrapper(schema 化 + 证据 + record_skill_run),
不重写算法,底层复用 recommendations / kol 等既有服务。LLM 步骤经可注入 model_fn,默认走规则不真烧。"""
