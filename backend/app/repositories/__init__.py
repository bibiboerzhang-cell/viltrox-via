"""Repository 层(L2)—— SQL 统一管理:权限/scope/测试/复用集中。

渐进迁移:新代码优先用 repository;老代码逐域搬入。每个 repo 只读写自己域的表。
红线:repo 不做业务裁决、不触 viltrox_fit_score;只封装"取数/写数"。
"""
