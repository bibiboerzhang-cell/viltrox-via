# V-KPI Evals (promptfoo skeleton)

W9「评估在线余量」的 prompt/skill 评估骨架。**当前为就位骨架,尚未实跑**
(本仓未装 promptfoo;需 Node + 联网装依赖)。骨架已可离线验证:custom provider
包装本项目确定性 skill（creator_match + 注入桩 preview），零 LLM、零 DB。

## 目录

```
evals/
  promptfooconfig.yaml               # promptfoo 配置 + 5 条黄金用例(creator_match 场景)
  package.json                       # 隔离的 devDependencies(promptfoo);不进主 package.json
  providers/creator_match_provider.py# Python custom provider:call_api(prompt, options, context) -> {"output": <json>}
  README.md
```

## 依赖(隔离,不进主构建)

promptfoo 是 Node CLI。装到本目录的 devDependencies 或用 npx,**绝不进主
`package.json` / 主 `requirements.txt`**:

```bash
cd evals
npm install            # 装本目录 devDependencies(promptfoo)
# 或免安装:npx promptfoo@latest ...
```

## 运行

provider 由 promptfoo 的 Node 进程以本仓 `.venv` 的 python 起子进程 import;
provider 内部已把 `<repo>/backend` 与 `<repo>` 注入 `sys.path`(等价
`PYTHONPATH=backend:.`)。指定 python 解释器:

```bash
cd evals
export PROMPTFOO_PYTHON=../.venv/bin/python
npx promptfoo@latest eval -c promptfooconfig.yaml
npx promptfoo@latest view          # 结果面板
```

## 黄金用例口径

`providers/creator_match_provider.py` 把 prompt(一段 JSON,如
`{"product": "viltrox af 85mm", "market": "US"}`)解析成 creator_match 的 input,
注入 skill 自带的确定性桩 preview(`_fixture_preview`)后跑 `run(record=False)`。
故断言(某 handle 应命中 / 无解产品应空返)确定性可复现,不依赖活库当下数据、
不真烧 LLM。5 条用例取自 `backend/app/domains/marketing_brain/skills/creator_match.py`
的 fixture 场景(alice_lens / bob_outdoor / carol_portrait / dave_vlog + 一条无解空返)。

离线自证(不装 promptfoo 也能验 provider 通):

```bash
cd <repo>
PYTHONPATH=backend:. .venv/bin/python -c "import importlib.util,json; \
spec=importlib.util.spec_from_file_location('p','evals/providers/creator_match_provider.py'); \
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); \
print(json.loads(m.call_api('{\"product\":\"viltrox af 85mm\",\"market\":\"US\"}')['output'])['recommendations'])"
```

## 换成真 LLM 护栏(后续)

把 provider 里的 `creator_match.run(...)` 换成真实 LLM 端点(需 LLM 代理 + key,
见 runtime_env.sh 的 HTTPS_PROXY),即从「skill 确定性回归」升级为「prompt 回归护栏」。
