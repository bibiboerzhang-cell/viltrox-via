"""发布只读白名单覆盖闸(2026-08-25 车道 3·(1))。

**为什么存在。** 发布验收窗口内 ``backend/app/core/release_validation.py`` 只放行
已登记的只读 GET,其余一律 503。今年两次发车失败都是同一个病:前端新挂的板块在
轮询一个没登记的 GET —— 2026-08-24 ``/my-kol/sku-play-overview``、2026-08-25
``DashboardTaskQueueCard`` 轮询 ``/api/admin/system/models``(浏览器候选闸抓到 22
次 503)。两次都是**发布包已经建好之后**才发现。

**这道闸做什么。** 静态扫出前端源码里能确定的 GET 端点字面量,与白名单求差集;
差集里出现新面孔就红,并指名路径与调用它的前端文件行号。

**判据(只许收紧,不许放宽):**

* ``BASELINE_UNREGISTERED`` = 建闸当天已经存在的历史欠账(74 条)。清单内的路径
  允许继续红着,但**不许再加新的**;
* 清单里的某条一旦被登记进白名单,本测试同样会红,要求把它从清单里删掉 ——
  棘轮只往一个方向走;
* ``EXEMPTIONS`` = 确实不该进白名单的端点,每条必须写理由。

**诚实边界(故意不粉饰):** 只处理静态可判定的字面量。路径里带 ``${id}`` 这类
运行期拼接、或路径由函数形参传入的调用点一律**跳过并计数**,计数在失败信息里
如实印出来 —— 宁可报"我没覆盖到 122 个点",也不假装覆盖率 100%。方法为
GET 之外(POST/PUT/DELETE)不在只读契约范围内,直接排除。

重拍基线:``.venv/bin/python tests/test_release_read_whitelist_coverage.py --refresh``
(只在**确认某条已登记进白名单**后用来收紧;新增欠账请去登记白名单,不要刷基线)。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

from app.core.release_validation import (  # noqa: E402
    release_validation_request_allowed,
)
import frontend_get_endpoint_scan as scanner  # noqa: E402

FRONTEND_SRC = ROOT / "frontend" / "src"

# 扫描器自身失灵(改坏、前端搬家)不许让闸门静默转绿:低于这些下限即视为扫描失效。
MIN_FILES_SCANNED = 600
MIN_GET_CALL_SITES = 150


# 豁免:GET 形状但**不该**进只读白名单,每条必须写清楚为什么。
EXEMPTIONS: dict[str, str] = {
    "/api/admin/vkpi/metrics/report/export": (
        "人点「导出」才发起的二进制附件下载(PDF/XLSX),挂载即轮询的板块不会碰它,"
        "验收窗口内不可能自动触发;返回体也不是只读 JSON 契约的形状。"
        "见 backend/app/api/routers/vkpi_metrics.py:91。"
    ),
}


# --- baseline begin (generated; do not edit by hand) ---
BASELINE_UNREGISTERED: frozenset[str] = frozenset(
    {
        "/api/admin/dashboard/kols",
        "/api/admin/staff/invite/capabilities",
        "/api/admin/staff/invite/status",
        "/api/admin/vkpi/actions/ledger/recent",
        "/api/admin/vkpi/activity/recent",
        "/api/admin/vkpi/agents/capabilities",
        "/api/admin/vkpi/agents/workspace-digest",
        "/api/admin/vkpi/analysis-cache",
        "/api/admin/vkpi/analytics/daily-digest/status",
        "/api/admin/vkpi/analytics/products",
        "/api/admin/vkpi/analytics/suggestions",
        "/api/admin/vkpi/audit/overview",
        "/api/admin/vkpi/brand-signals/preview",
        "/api/admin/vkpi/budgets/usage-by-cron",
        "/api/admin/vkpi/budgets/usage-by-provider",
        "/api/admin/vkpi/channel/dealer-fit",
        "/api/admin/vkpi/channel/indie-site-actions",
        "/api/admin/vkpi/channel/mix",
        "/api/admin/vkpi/channels/official-daily-report",
        "/api/admin/vkpi/comment-intelligence/overview",
        "/api/admin/vkpi/dashboard/agents-status",
        "/api/admin/vkpi/dashboard/agents/inbox",
        "/api/admin/vkpi/dashboard/kol-distribution",
        "/api/admin/vkpi/dashboard/system-health",
        "/api/admin/vkpi/gifted/funnel",
        "/api/admin/vkpi/goaffpro/affiliates",
        "/api/admin/vkpi/industry-data/competitor-brain/review-suggestions",
        "/api/admin/vkpi/industry-data/competitor-brain/status",
        "/api/admin/vkpi/industry-data/content-brain/status",
        "/api/admin/vkpi/industry-data/market-external-daily-plan/v0",
        "/api/admin/vkpi/industry-data/market-intelligence/v0",
        "/api/admin/vkpi/industry-data/product-campaign-card",
        "/api/admin/vkpi/industry-data/projects",
        "/api/admin/vkpi/inventory/groups",
        "/api/admin/vkpi/kol-pool/competitors/dashboard",
        "/api/admin/vkpi/kol-pool/discovery/federated-search",
        "/api/admin/vkpi/kol-pool/resolve",
        "/api/admin/vkpi/kol-recall",
        "/api/admin/vkpi/learning/memory-feedback-backlog",
        "/api/admin/vkpi/learning/miss-review",
        "/api/admin/vkpi/learning/recommendation-feedback-backlog",
        "/api/admin/vkpi/learning/shadow-evals",
        "/api/admin/vkpi/local-workers/board",
        "/api/admin/vkpi/local-workers/devices",
        "/api/admin/vkpi/market/brand-pulse",
        "/api/admin/vkpi/market/comment-opportunities",
        "/api/admin/vkpi/metrics/high-value-kols",
        "/api/admin/vkpi/metrics/industry-board",
        "/api/admin/vkpi/metrics/movers",
        "/api/admin/vkpi/metrics/report",
        "/api/admin/vkpi/metrics/report-types",
        "/api/admin/vkpi/my-kol/shares",
        "/api/admin/vkpi/operating-review/status",
        "/api/admin/vkpi/ops/health-sentinel",
        "/api/admin/vkpi/outreach/three-promises",
        "/api/admin/vkpi/product-analysis/recommendation-runs",
        "/api/admin/vkpi/product-analysis/recommendations",
        "/api/admin/vkpi/recall/semantic",
        "/api/admin/vkpi/settings/api-key-pool",
        "/api/admin/vkpi/settings/budgets",
        "/api/admin/vkpi/settings/business-integrations",
        "/api/admin/vkpi/settings/comment-alerts",
        "/api/admin/vkpi/settings/control-status",
        "/api/admin/vkpi/settings/feature-flags",
        "/api/admin/vkpi/settings/firewall/control-status",
        "/api/admin/vkpi/settings/platform-crawl",
        "/api/admin/vkpi/strategy/simulate",
        "/api/admin/vkpi/sync/industry/failures",
        "/api/admin/vkpi/sync/overview",
        "/api/admin/vkpi/task-queue",
        "/api/marketing/attribution/amazon",
        "/api/marketing/budget-pools",
        "/api/marketing/campaigns",
        "/api/marketing/product-catalog",
    }
)
# --- baseline end ---


@pytest.fixture(scope="module")
def scan() -> scanner.ScanResult:
    if not FRONTEND_SRC.is_dir():
        pytest.skip("frontend sources are not present in this checkout")
    return scanner.scan_frontend(FRONTEND_SRC, repo_root=ROOT)


def _unregistered(result: scanner.ScanResult) -> set[str]:
    return {
        path
        for path in result.paths
        if not release_validation_request_allowed("GET", path)
    }


def test_scanner_still_sees_the_frontend(scan: scanner.ScanResult) -> None:
    """扫描器失灵不许让闸门静默转绿。"""

    assert scan.files_scanned >= MIN_FILES_SCANNED, (
        f"只扫到 {scan.files_scanned} 个前端源文件(下限 {MIN_FILES_SCANNED})。"
        "前端目录搬家或扫描器过滤写坏了,这道闸此刻是假绿的。"
    )
    assert len(scan.get_calls) >= MIN_GET_CALL_SITES, (
        f"只解析出 {len(scan.get_calls)} 个静态 GET 调用点(下限 {MIN_GET_CALL_SITES})。"
        "解析器退化会让差集凭空变空 —— 先修扫描器,别调下限。"
    )


def test_no_new_unregistered_get_endpoint(scan: scanner.ScanResult) -> None:
    """新出现的未登记 GET 端点 = 红。"""

    unregistered = _unregistered(scan)
    known = set(BASELINE_UNREGISTERED) | set(EXEMPTIONS)
    fresh = unregistered - known
    if not fresh:
        return
    detail = scanner.format_gap(fresh, scan)
    raise AssertionError(
        f"{len(fresh)} 个前端会发起的 GET 端点没登记进 reviewed-read 白名单,"
        "发布验收窗口内它们会回 503 并被浏览器候选闸判死:\n"
        f"{detail}\n\n"
        "修法(择一,不许改判据):\n"
        "  1. 确认端点纯 SELECT 零 provider 后,追加到 backend/app/core/"
        "release_validation.py 的 _CAPTURED_READ_ONLY_GET_PATHS,并写上理由注释;\n"
        "  2. 端点确实不该被放行(会写库/调 provider/只在人点按钮时发起),"
        "把它加进本文件 EXEMPTIONS 并写清理由;\n"
        "  3. 前端不该在挂载时轮询它 —— 改前端。\n"
        f"当前静态覆盖率 {scan.static_coverage():.1%}"
        f"(已判定 {len(scan.get_calls)} 点 / 跳过 {len(scan.skipped)} 点:"
        f"{scan.skipped_by_reason()})。"
    )


def test_baseline_only_shrinks(scan: scanner.ScanResult) -> None:
    """基线里的路径一旦登记成功就必须从基线里删掉(棘轮只许收紧)。"""

    unregistered = _unregistered(scan)
    stale = sorted(set(BASELINE_UNREGISTERED) - unregistered)
    assert not stale, (
        f"{len(stale)} 条历史欠账已经不再是欠账(白名单已登记,或前端已不再调用),"
        "请把它们从 BASELINE_UNREGISTERED 删掉,让棘轮咬紧:\n  "
        + "\n  ".join(stale)
        + "\n(可直接运行 .venv/bin/python tests/"
        "test_release_read_whitelist_coverage.py --refresh)"
    )


def test_exemptions_are_justified_and_disjoint(scan: scanner.ScanResult) -> None:
    """豁免必须有理由、不与基线重叠、且确实还没被放行。"""

    assert not (set(EXEMPTIONS) & set(BASELINE_UNREGISTERED)), (
        "同一路径不能既是豁免又是历史欠账:"
        f"{sorted(set(EXEMPTIONS) & set(BASELINE_UNREGISTERED))}"
    )
    for path, reason in EXEMPTIONS.items():
        assert path.startswith("/"), f"豁免项不是路径:{path}"
        assert len(reason.strip()) >= 20, f"豁免 {path} 缺理由"
        assert not release_validation_request_allowed("GET", path), (
            f"{path} 已经在白名单里放行了,豁免条目是死条目,请删掉它。"
        )


def test_scan_reports_its_own_blind_spots(scan: scanner.ScanResult) -> None:
    """跳过的调用点必须留痕:有文件、有行号、有原因。"""

    assert scan.skipped, "一个跳过都没有 = 解析器把动态路径当成静态了,不可信"
    for item in scan.skipped:
        assert item.reason, "跳过必须带原因"
        assert item.file and item.line > 0, "跳过必须能定位到文件行"
    assert 0.0 < scan.static_coverage() <= 1.0


# --------------------------------------------------------------------------
# 解析器本身的单测(合成输入,不依赖仓库现状)
# --------------------------------------------------------------------------


def _scan(source: str) -> scanner.ScanResult:
    calls, skipped, non_get = scanner.scan_source(source, relative_path="synthetic.ts")
    return scanner.ScanResult(
        get_calls=tuple(calls),
        skipped=tuple(skipped),
        non_get_calls=non_get,
        files_scanned=1,
    )


def test_plain_literal_get_is_collected() -> None:
    result = _scan('await apiFetch<Row>("/api/admin/system/models", {}, token);')
    assert [c.path for c in result.get_calls] == ["/api/admin/system/models"]
    assert result.get_calls[0].line == 1


def test_query_string_is_stripped_but_path_kept() -> None:
    result = _scan(
        "apiFetch(`/api/admin/vkpi/strategy/simulate?sku=${encodeURIComponent(sku)}`, {}, t);"
    )
    assert [c.path for c in result.get_calls] == ["/api/admin/vkpi/strategy/simulate"]


def test_interpolated_path_is_skipped_not_guessed() -> None:
    result = _scan("apiFetch(`/api/admin/vkpi/kol-pool/${id}/videos`, {}, t);")
    assert not result.get_calls
    assert [s.reason for s in result.skipped] == ["interpolated_path"]


def test_post_is_out_of_scope() -> None:
    result = _scan('apiFetch("/api/admin/vkpi/tasks", { method: "POST" }, t);')
    assert not result.get_calls and not result.skipped
    assert result.non_get_calls == 1


def test_runtime_method_is_reported_not_assumed_get() -> None:
    result = _scan('apiFetch("/api/admin/vkpi/tasks", { method: verb }, t);')
    assert not result.get_calls
    assert [s.reason for s in result.skipped] == ["runtime_method"]


def test_spread_init_is_reported_not_assumed_get() -> None:
    result = _scan('apiFetch("/api/admin/vkpi/tasks", { ...init }, t);')
    assert [s.reason for s in result.skipped] == ["runtime_method"]


def test_file_local_constant_is_resolved() -> None:
    source = (
        'const ENDPOINT = "/api/admin/vkpi/dashboard/tasks";\n'
        "export function load(t: string) { return apiFetch(ENDPOINT, {}, t); }\n"
    )
    result = _scan(source)
    assert [c.path for c in result.get_calls] == ["/api/admin/vkpi/dashboard/tasks"]


def test_build_api_url_wrapper_is_unwrapped() -> None:
    result = _scan('void fetch(buildApiUrl("/api/auth/me"), { cache: "no-store" });')
    assert [c.path for c in result.get_calls] == ["/api/auth/me"]


def test_use_cached_get_is_always_a_get() -> None:
    result = _scan('const x = useCachedGet<T>("/health", { token, ttl: 60000 });')
    assert [c.path for c in result.get_calls] == ["/health"]


def test_helper_definition_is_not_a_call_site() -> None:
    source = "export async function apiFetch<T>(\n  path: string,\n  init = {},\n) {}\n"
    result = _scan(source)
    assert not result.get_calls and not result.skipped


def test_commented_out_and_stringified_calls_are_ignored() -> None:
    source = (
        '// apiFetch("/api/admin/vkpi/ghost", {}, t);\n'
        '/* apiFetch("/api/admin/vkpi/ghost2", {}, t); */\n'
        'const doc = "apiFetch(\\"/api/admin/vkpi/ghost3\\")";\n'
    )
    result = _scan(source)
    assert not result.get_calls


def test_member_fetch_on_unknown_owner_is_ignored() -> None:
    result = _scan('cache.fetch("/api/admin/vkpi/nope");')
    assert not result.get_calls and not result.skipped


def test_window_fetch_still_counts() -> None:
    result = _scan('window.fetch("/api/admin/vkpi/dashboard");')
    assert [c.path for c in result.get_calls] == ["/api/admin/vkpi/dashboard"]


def test_regex_literal_does_not_break_masking() -> None:
    source = (
        'const base = String(x).replace(/\\/+$/, "");\n'
        'apiFetch("/api/admin/vkpi/dashboard", {}, t);\n'
    )
    result = _scan(source)
    assert [c.path for c in result.get_calls] == ["/api/admin/vkpi/dashboard"]


def test_external_url_is_not_reported_as_endpoint() -> None:
    result = _scan('fetch("https://example.com/api/x");')
    assert not result.get_calls
    assert [s.reason for s in result.skipped] == ["external_url"]


def test_trailing_slash_normalizes() -> None:
    assert scanner.normalize_path("/api/admin/vkpi/dashboard/") == "/api/admin/vkpi/dashboard"
    assert scanner.normalize_path("/") == "/"
    assert scanner.normalize_path("/api/x?a=1#frag") == "/api/x"


# --------------------------------------------------------------------------
# 基线重拍(只用于收紧)
# --------------------------------------------------------------------------


def _refresh() -> int:
    result = scanner.scan_frontend(FRONTEND_SRC, repo_root=ROOT)
    unregistered = sorted(_unregistered(result) - set(EXEMPTIONS))
    body = "\n".join(f'        "{path}",' for path in unregistered)
    # 标记串拼出来,免得本函数自己的源码被下面的正则再匹配一次(踩过一次)。
    begin = "# --- baseline " + "begin (generated; do not edit by hand) ---"
    end = "# --- baseline " + "end ---"
    block = (
        f"{begin}\n"
        "BASELINE_UNREGISTERED: frozenset[str] = frozenset(\n"
        "    {\n"
        f"{body}\n"
        "    }\n"
        ")\n"
        f"{end}"
    )
    path = Path(__file__)
    original = path.read_text(encoding="utf-8")
    # 只认行首的那一块,且只替换一次:MULTILINE 锚点 + count=1。
    pattern = re.compile(
        "^" + re.escape(begin) + ".*?^" + re.escape(end),
        re.S | re.M,
    )
    updated, replaced = pattern.subn(lambda _match: block, original, count=1)
    if replaced != 1:
        print("baseline markers not found — refusing to rewrite")
        return 1
    if updated == original:
        print("baseline unchanged")
        return 0
    path.write_text(updated, encoding="utf-8")
    print(f"baseline refreshed: {len(unregistered)} unregistered GET endpoints")
    return 0


if __name__ == "__main__":  # pragma: no cover - maintenance entry point
    if "--refresh" in sys.argv:
        raise SystemExit(_refresh())
    raise SystemExit("usage: python tests/test_release_read_whitelist_coverage.py --refresh")
