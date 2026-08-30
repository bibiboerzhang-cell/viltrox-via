-- Reproducible report rows derived from the reviewed current-run evidence in
-- score-evidence.json. These VALUES statements are rendering transforms, not
-- substitutes for the original gate, coverage, static-analysis, or runtime evidence.

SELECT
  43.7584 AS current_score,
  40.44 AS previous_score,
  3.3184 AS score_delta,
  36.2416 AS gap_to_80,
  0.3375 AS formal_static_coverage,
  0.3925 AS formal_potential_coverage,
  0.95 AS formal_required_coverage,
  1.0 AS local_acceptance_rate,
  53 AS local_acceptance_passed,
  1244.9 AS local_acceptance_p95_ms,
  0.72413793 AS function_mount_rate,
  21 AS mounted_boards,
  29 AS navigation_entries,
  2 AS gate_failed_steps,
  18 AS gate_steps,
  4.885 AS gate_minutes;

WITH dimension_comparison(dimension, series, score, delta_vs_previous, confidence, basis) AS (
  VALUES
    ('代码健康', '上一轮', 74.41, 0.0, '中', '静态代理 + fresh coverage'),
    ('代码健康', '本轮', 75.0214, 0.6114, '中', '静态代理 + fresh coverage'),
    ('架构健康', '上一轮', 30.0, 0.0, '中', '全系统 import graph'),
    ('架构健康', '本轮', 47.612, 17.612, '中', '后端 AST + 前端相对 import graph'),
    ('演进风险', '上一轮', 52.35, 0.0, '低', 'Git history proxy'),
    ('演进风险', '本轮', 47.4, -4.95, '低', '120/180天历史；按人合并身份'),
    ('工程交付', '上一轮', 65.0, 0.0, '低', '构建实测 + 两个中性缺失值'),
    ('工程交付', '本轮', 65.0, 0.0, '低', '构建实测 + 两个中性缺失值')
)
SELECT * FROM dimension_comparison;

WITH dimension_detail(rank, dimension, previous, current, delta, positive, constraint_note) AS (
  VALUES
    (1, '代码健康', 74.41, 75.0214, 0.6114, 'CC≤10 82.916%；重复代理2.967%', '分支覆盖55.584%；maxCC=67'),
    (2, '架构健康', 30.0, 47.612, 17.612, '后端cross-core SCC降到0', '前端跨域循环1；fan-out 185；D p90 0.829'),
    (3, '演进风险', 52.35, 47.4, -4.95, 'Hotspot代理83.5', '按人BF=1；Temporal p95=0.333；历史不足180天'),
    (4, '工程交付', 65.0, 65.0, 0.0, '4.89分钟；前端构建/测试转绿', 'Critical-fix、CFR、required gate仍未知')
)
SELECT rank, dimension, previous, current, delta, positive, constraint_note AS "constraint"
FROM dimension_detail
ORDER BY rank;

WITH gate_status(rank, check_name, status, evidence, meaning) AS (
  VALUES
    (1, '候选稳定性', 'PASS', 'gate与coverage前后3类指纹一致', '本轮测量可归属于同一工作树快照'),
    (2, 'Canonical gate', 'FAIL', '18步中2步失败；exit 1', '不具备发布资格'),
    (3, '后端测试', 'FAIL', '9,178 passed / 4 failed；coverage另见第5个失败', '门禁棘轮、过时断言与共享状态风险未收口'),
    (4, '前端测试与构建', 'PASS', '2,055 tests；tsc/build/chunk/budget全过', '前端静态发布链明显改善'),
    (5, '千行卫兵', 'FAIL', 'vkpi_kol_pool_search.py = 1002行', '仍有一个硬阻断文件'),
    (6, '本地只读验收', 'PASS', '53/53；p95 1,244.9ms', '当前本机运行闭环可用'),
    (7, '浏览器/云端/日志canary', 'NOT RUN', '无独立回执', '不能声称release-ready或已上线'),
    (8, '安全与供应链', 'PARTIAL', '前端0漏洞；后端81 unique advisories；缺SAST/SBOM/license gate', '安全门尚未完整证明')
)
SELECT rank, check_name AS "check", status, evidence, meaning
FROM gate_status
ORDER BY rank;

WITH sensitivity(rank, case_name, score, use_note) AS (
  VALUES
    (1, '全系统 + 按人BF=1 + 历史可比中性缺失值', 43.7584, '主分'),
    (2, '当前gate失败令质量项=0；CFR中性', 40.0084, '更保守的当前门禁口径'),
    (3, '缺失交付指标全部=0', 35.0084, '严格缺失下限'),
    (4, '再计入强制Quality Gate未证明的5分扣分', 30.0084, '最严格证据下限'),
    (5, '把邮箱身份当不同维护者（BF=2）', 52.5084, '不建议；会高估人员冗余'),
    (6, '仅看后端cross-core循环', 52.7584, '后端治理参考，不是全系统主分')
)
SELECT rank, case_name AS "case", score, use_note AS "use"
FROM sensitivity
ORDER BY rank;

WITH priorities(rank, priority, action, exit_gate, score_effect) AS (
  VALUES
    (1, 'P0', '修复4个canonical失败与coverage额外顺序失败', '同一候选连续3次canonical全绿', '交付可信度与正式证据'),
    (2, 'P0', '拆分1002行路由并收回软棘轮增长', '>1000文件=0；增长文件=0', '门禁与可维护性'),
    (3, 'P0', '将maxCC 67降到≤50', 'max production CC≤50', '解除5分硬扣与79.9 grade cap'),
    (4, 'P0', '拆除前端跨domain/service循环', '全系统cross-module cycles=0', '解除5分硬扣并提升架构'),
    (5, 'P1', '核心域三人轮值、CODEOWNERS与交接演练', 'people-normalized BF≥3', '解除5分硬扣并提升演进性'),
    (6, 'P1', '分支覆盖先到60%，再向80%推进', 'fresh combined branch≥60% / ≥80%', '代码健康'),
    (7, 'P1', 'required CI + DORA/CFR/MTTR + backend audit/SAST/SBOM/license', '正式证据覆盖≥95%，各维≥90%', '从估算分进入formal score')
)
SELECT * FROM priorities ORDER BY rank;
