"""Channel Brain domain package (5 脑架构 · Channel 层)。

CB 系列刀:官号内容计划(CB1)/ 独立站承接(CB3)/ Dealer 适配评分(CB4)…
纯读、零 LLM、零采集、零写库;绝不触碰 viltrox_fit_score / rule_v0。

【并行安全】本包由多把 CB 刀并行建设,各子模块彼此独立。包 __init__ 故意**不**
eager-import 任何子模块——避免「某子模块尚未落地时整包 import 崩」的竞态。
调用方一律直接按需引入具体子模块(如 `from app.domains.channel import dealer_scoring`),
与本文件是否列出无关。
"""

__all__: list[str] = []
