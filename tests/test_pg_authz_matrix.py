"""PG-5 — 权限矩阵(两身份跨用户 scope 隔离),对真 Postgres 跑。

背景:大多数 authz 决策由 ``app.domains.access.scope`` 单点裁决,但 sqlite 假库
从不带真身份/真数据跑端到端,越权漏洞(某端点漏挂 scope 收口)不会被单测暴露。
这套测试挂 ``pg`` marker、造两名真员工 + owner 身份,对读/写端点参数化断言:

  ① 读隔离:员工不能读别人负责的 project / event / attribution(→403 或过滤空)
  ② 写隔离:员工不能改/删别人的实体(→403)
  ③ staff_id hint 伪造:员工传别人 staff_id,后端降为 own(不落到别人名下 / 不读别人)
  ④ 敏感读:员工不能看 AI 花费 / 成本 / 预算(→403);GMV/佣金按 staff 过滤(不含别人)
  ⑤ 管理层闸:员工不能触发烧 LLM 的周报 / 官号日报 / 画质扫描 generate(→403)
  ⑥ viewer 共享成员:能读活动,但不能写 evidence/geocode/finalize(本 session 修的 events 越权)

身份(真库现有行,见 users / staff):
  owner  = uid 1     (staff 40,is_owner=1)  → can_view_all,正对照(能读/能进管理层闸)
  员工 A = uid 7952  (staff 7676,employee,vkpi:write / kol_ops:read)
  员工 B = uid 7953  (staff 7677,employee,同款权限)

隔离:被测实体(项目/活动/短链/归因)由 fixture 用一条独立 autocommit 连接造(committed,
故 app 连接池读得到),tag 前缀唯一 + teardown 按 tag/id 精确清,幂等可重复跑。TestClient
直连 app.main.app,身份用 ``make_token`` 自签 JWT(HS256,aud/iss/uid 与生产一致)。

红线:只读断言 + 造/清测试数据;不改任何 backend 产品代码(发现越权只报不修)。
PG 不可达时整文件 skip(不红)—— 见 conftest 的 pg marker。
"""
from __future__ import annotations

import secrets
from typing import Any, Iterator

import pytest

pytestmark = [
    pytest.mark.pg,
    pytest.mark.usefixtures("pg_test_identities"),
]


# ── 身份常量(真库现有行)───────────────────────────────────────────────────
OWNER_UID = 1
A_UID, A_SID = 7952, 7676
B_UID, B_SID = 7953, 7677

VKPI = "/api/admin/vkpi"


# ── 基础设施 fixtures ───────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def client() -> Any:
    """TestClient over the real app; the app runs on the live PG in this env."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def tokens() -> dict[str, str]:
    """Self-signed HS256 JWTs (aud=vos-app, iss=viltrox-vos) for each identity.

    Minted once per module so the per-token user cache stays stable across tests.
    Token ``role`` is irrelevant to authz — the backend re-derives permissions from
    the ``staff`` row by uid — but is set to a truthful value anyway.
    """
    from app.core.security import make_token

    return {
        "owner": make_token(OWNER_UID, "admin"),
        "A": make_token(A_UID, "employee"),
        "B": make_token(B_UID, "employee"),
    }


def _h(tokens: dict[str, str], who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens[who]}"}


@pytest.fixture(scope="module")
def admin_conn(pg_dsn: str) -> Iterator[Any]:
    """A dedicated autocommit connection for seeding + read-back.

    Autocommit so seeded rows are immediately visible to the app's own pooled
    connection, and so read-backs of rows the endpoints committed see the latest
    state. Never used by product code — test scaffolding only.
    """
    import psycopg

    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    raw.autocommit = True
    try:
        yield raw
    finally:
        raw.close()


@pytest.fixture(scope="module")
def seeded(admin_conn: Any) -> Iterator[dict[str, Any]]:
    """Create entities OWNED BY employee B, committed; purge everything in teardown.

    All ids carry a unique random ``tag`` prefix so concurrent/repeat runs never
    collide and teardown can delete by prefix (idempotent).
    """
    tag = "pgauthz_" + secrets.token_hex(5)
    ids: dict[str, Any] = {"tag": tag}
    cur = admin_conn.cursor()
    try:
        # Project owned by B: assigned+creator=B, private (not restricted / not public).
        cur.execute(
            "INSERT INTO vkpi_projects "
            "(project_uid, project_name, assigned_staff_id, created_by_staff_id, restricted, is_public) "
            "VALUES (%s, %s, %s, %s, false, false) RETURNING id",
            (f"{tag}_proj", "authz matrix project", B_SID, B_SID),
        )
        ids["project_id"] = int(cur.fetchone()[0])

        # Event owned by B (private) — used for read-isolation asserts.
        ids["event_id"] = f"{tag}_evt"
        cur.execute(
            "INSERT INTO vkpi_events (id, title, start_date, end_date, owner_id, team_ids, is_public) "
            "VALUES (%s, %s, current_date, current_date, %s, '[]'::jsonb, false)",
            (ids["event_id"], "authz matrix event", B_SID),
        )

        # Dedicated event for destructive-write asserts (kept separate so a PATCH/DELETE
        # that unexpectedly *succeeds* cannot corrupt the read fixtures).
        ids["del_event_id"] = f"{tag}_evtdel"
        cur.execute(
            "INSERT INTO vkpi_events (id, title, start_date, end_date, owner_id, team_ids, is_public) "
            "VALUES (%s, %s, current_date, current_date, %s, '[]'::jsonb, false)",
            (ids["del_event_id"], "authz matrix del event", B_SID),
        )

        # Event owned by B where A is a *viewer* share member (⑥): read yes, write no.
        ids["viewer_event_id"] = f"{tag}_evtv"
        cur.execute(
            "INSERT INTO vkpi_events (id, title, start_date, end_date, owner_id, team_ids, is_public) "
            "VALUES (%s, %s, current_date, current_date, %s, '[]'::jsonb, false)",
            (ids["viewer_event_id"], "authz matrix viewer event", B_SID),
        )
        cur.execute(
            "INSERT INTO vkpi_event_members (event_id, staff_id, role) VALUES (%s, %s, 'viewer')",
            (ids["viewer_event_id"], A_SID),
        )

        # Short link owned by B + one confirmed commission attribution linked to it
        # (④ GMV/佣金 过滤;① attribution 读过滤).
        cur.execute(
            "INSERT INTO vkpi_links (link_uid, slug, destination_url, staff_id, created_by_staff_id) "
            "VALUES (%s, %s, 'https://example.com', %s, %s) RETURNING id",
            (f"{tag}_lnk", f"{tag}_slug", B_SID, B_SID),
        )
        ids["link_id"] = int(cur.fetchone()[0])
        ids["link_slug"] = f"{tag}_slug"

        ids["attr_ref_b"] = f"{tag}_attrB"
        cur.execute(
            "INSERT INTO vkpi_sales_attributions "
            "(source_platform, source_ref, staff_id, link_id, revenue_cents, commission_cents, confidence, occurred_at) "
            "VALUES ('manual', %s, %s, %s, 50000, 7777, 'confirmed', now())",
            (ids["attr_ref_b"], B_SID, ids["link_id"]),
        )

        yield ids
    finally:
        # FK-safe purge; also anything the tests created under this tag (forged attribution,
        # enqueued retrospective jobs). LIKE prefix matches the random-hex tag uniquely.
        like = tag + "%"
        cur.execute("DELETE FROM vkpi_sales_attributions WHERE source_ref LIKE %s", (like,))
        if ids.get("project_id"):
            cur.execute(
                "DELETE FROM apify_jobs WHERE payload->>'project_id' = %s",
                (str(ids["project_id"]),),
            )
        cur.execute("DELETE FROM vkpi_event_members WHERE event_id LIKE %s", (like,))
        cur.execute("DELETE FROM vkpi_events WHERE id LIKE %s", (like,))
        cur.execute("DELETE FROM vkpi_links WHERE link_uid LIKE %s", (like,))
        if ids.get("project_id"):
            cur.execute("DELETE FROM vkpi_projects WHERE id = %s", (ids["project_id"],))


# ── ① 读隔离(员工看不到别人的实体)─────────────────────────────────────────
def test_read_project_denied_for_other_employee(client, tokens, seeded):
    """A 直读 B 独占项目详情 → 403(IDOR:list 侧已 own-only,详情必须同口径)。"""
    r = client.get(f"{VKPI}/projects/{seeded['project_id']}", headers=_h(tokens, "A"))
    assert r.status_code == 403, r.text


def test_read_project_allowed_for_owner(client, tokens, seeded):
    """正对照:owner(can_view_all)读同一项目 → 200(证明不是无脑 403)。"""
    r = client.get(f"{VKPI}/projects/{seeded['project_id']}", headers=_h(tokens, "owner"))
    assert r.status_code == 200, r.text


def test_read_event_denied_for_other_employee(client, tokens, seeded):
    """A 直读 B 独占活动 → 403(活动预算/团队/费用不外泄)。"""
    r = client.get(f"{VKPI}/events/{seeded['event_id']}", headers=_h(tokens, "A"))
    assert r.status_code == 403, r.text


def test_read_event_allowed_for_owner(client, tokens, seeded):
    r = client.get(f"{VKPI}/events/{seeded['event_id']}", headers=_h(tokens, "owner"))
    assert r.status_code == 200, r.text


def test_read_attribution_excludes_other_employee(client, tokens, seeded):
    """A 的归因列表按 staff 过滤,绝不含 B 名下的行(过滤空 = 隔离成立)。"""
    r = client.get(f"{VKPI}/attribution?limit=500", headers=_h(tokens, "A"))
    assert r.status_code == 200, r.text
    refs = [x.get("source_ref") for x in r.json().get("attributions", [])]
    assert seeded["attr_ref_b"] not in refs


def test_read_attribution_visible_to_owner(client, tokens, seeded):
    r = client.get(f"{VKPI}/attribution?limit=500", headers=_h(tokens, "owner"))
    assert r.status_code == 200, r.text
    refs = [x.get("source_ref") for x in r.json().get("attributions", [])]
    assert seeded["attr_ref_b"] in refs


# ── ② 写隔离(员工不能改/删别人的实体)──────────────────────────────────────
def test_write_event_patch_denied_for_other_employee(client, tokens, seeded):
    r = client.patch(
        f"{VKPI}/events/{seeded['del_event_id']}",
        json={"title": "hacked"},
        headers=_h(tokens, "A"),
    )
    assert r.status_code == 403, r.text


def test_write_event_delete_denied_for_other_employee(client, tokens, seeded):
    r = client.delete(f"{VKPI}/events/{seeded['del_event_id']}", headers=_h(tokens, "A"))
    assert r.status_code == 403, r.text


def test_write_project_invoice_extract_denied_for_other_employee(client, tokens, seeded):
    """项目写口(发票 AI 抽取入队)对非归属员工 → 403(有 assert_project_access,正例)。"""
    r = client.post(
        f"{VKPI}/projects/{seeded['project_id']}/invoice-extract/enqueue",
        json={"file_url": "https://example.com/a.pdf"},
        headers=_h(tokens, "A"),
    )
    assert r.status_code == 403, r.text


def test_write_project_add_member_denied_for_other_employee(client, tokens, seeded):
    """把自己加进别人的项目 → 403(转授动作严格 gate,正例)。"""
    r = client.post(
        f"{VKPI}/projects/{seeded['project_id']}/members",
        json={"staff_id": A_SID, "role": "editor"},
        headers=_h(tokens, "A"),
    )
    assert r.status_code == 403, r.text


def test_write_project_retrospective_generate_denied_for_other_employee(client, tokens, seeded):
    """A 对 B 独占项目触发「复盘生成」→ 应 403。

    这是本矩阵抓到的越权:该端点只挂 ``require_tab(vkpi,write)``(对全员恒真),
    delegate ``enqueue_project_retrospective`` 从不调用 ``scope.assert_project_access``,
    与同域其它写口(invoice-extract / contract-polish / contracts/generate / members
    全部 assert)不一致 —— A 拿到 200 并把一条 project_retrospective_aggregate LLM 作业
    塞进 apify_jobs(既跨项目越权,又越权烧 LLM 预算)。断言 403 现会失败以暴露缺陷;
    修好后转为回归护栏。enqueued 作业由 seeded teardown 按 project_id 清理。
    """
    r = client.post(
        f"{VKPI}/projects/{seeded['project_id']}/retrospective/generate",
        headers=_h(tokens, "A"),
    )
    assert r.status_code == 403, (
        "employee A (no access to B's project) enqueued a project-retrospective LLM job "
        f"and got status={r.status_code}; expected 403. body={r.text}"
    )


# ── ③ staff_id hint 伪造(后端降为 own)──────────────────────────────────────
def test_forge_staff_id_on_attribution_create_is_rejected_for_employee(client, tokens, seeded, admin_conn):
    """A 伪造 B 的 staff_id 创建人工归因 → 403 且不得落库。

    人工归因现在是受管理权限和授权证据双重保护的真实业务写口，
    普通员工不再拥有“先创建、再降级为自己”的权限。
    """
    ref = f"{seeded['tag']}_forge"
    r = client.post(
        f"{VKPI}/attribution",
        json={"source_platform": "manual", "source_ref": ref, "staff_id": B_SID, "revenue_cents": 100},
        headers=_h(tokens, "A"),
    )
    assert r.status_code == 403, r.text
    cur = admin_conn.cursor()
    cur.execute("SELECT staff_id FROM vkpi_sales_attributions WHERE source_ref = %s", (ref,))
    row = cur.fetchone()
    assert row is None, f"forged attribution unexpectedly persisted for staff_id={row[0]}"


def test_forge_staff_id_on_attribution_list_stays_own(client, tokens, seeded):
    """A 用 ?staff_id=B 请求归因列表 → 仍只回 A 自己的行,绝不回 B 的(hint 被降级)。"""
    r = client.get(f"{VKPI}/attribution?staff_id={B_SID}&limit=500", headers=_h(tokens, "A"))
    assert r.status_code == 200, r.text
    refs = [x.get("source_ref") for x in r.json().get("attributions", [])]
    assert seeded["attr_ref_b"] not in refs


# ── ④ 敏感读(成本/预算/AI 花费 → 403;GMV/佣金按 staff 过滤)──────────────────
@pytest.mark.parametrize(
    "path",
    [
        f"{VKPI}/budgets",                    # 预算总览(管理层闸)
        f"{VKPI}/budgets/usage-by-provider",  # 各 provider 的 AI 花费
        f"{VKPI}/budgets/usage-by-cron",      # 各 cron 的 AI 花费
        f"{VKPI}/product-costs",              # 产品成本
        f"{VKPI}/costs/1",                    # 单条成本详情(管理层闸,id 任意)
    ],
)
def test_sensitive_read_denies_employee(client, tokens, path):
    r = client.get(path, headers=_h(tokens, "A"))
    assert r.status_code == 403, f"{path} leaked to employee: {r.status_code} {r.text}"


def test_sensitive_read_ai_spend_allowed_for_owner(client, tokens):
    """正对照:owner 能读 AI 花费(证明闸是身份闸,不是全员 403)。"""
    r = client.get(f"{VKPI}/budgets/usage-by-provider", headers=_h(tokens, "owner"))
    assert r.status_code == 200, r.text


def test_gmv_commission_hidden_from_other_employee(client, tokens, seeded):
    """A 的 GMV/佣金汇总不含 B 独占短链(佣金按 staff 过滤,不越权可见)。"""
    r = client.get(f"{VKPI}/attribution/gmv-summary?limit=500", headers=_h(tokens, "A"))
    assert r.status_code == 200, r.text
    slugs = [it.get("slug") for it in r.json().get("items", [])]
    assert seeded["link_slug"] not in slugs


def test_gmv_commission_visible_to_link_owner(client, tokens, seeded):
    """正对照:B(短链归属人)在自己的 GMV 汇总里看得到该短链佣金。"""
    r = client.get(f"{VKPI}/attribution/gmv-summary?limit=500", headers=_h(tokens, "B"))
    assert r.status_code == 200, r.text
    slugs = [it.get("slug") for it in r.json().get("items", [])]
    assert seeded["link_slug"] in slugs


# ── ⑤ 管理层闸(员工不能触发烧 LLM 的 generate)──────────────────────────────
@pytest.mark.parametrize(
    "path",
    [
        f"{VKPI}/weekly-reports/generate-all",
        f"{VKPI}/weekly-reports/generate-for-staff/{B_SID}",
        f"{VKPI}/channels/official-daily-report/run",
        f"{VKPI}/channels/official-visual/scan",
    ],
)
def test_management_gate_denies_employee(client, tokens, path):
    """这些端点烧 LLM 预算,必须管理层闸;非管理层一律 403(403 在 Depends 层触发,无副作用)。"""
    r = client.post(path, headers=_h(tokens, "A"))
    assert r.status_code == 403, f"{path} reachable by employee: {r.status_code} {r.text}"


# ── ⑥ viewer 共享成员(能读活动,不能写)──────────────────────────────────────
def test_viewer_member_can_read_shared_event(client, tokens, seeded):
    """A 是 B 活动的 viewer 共享成员 → 读放行(200),证明隔离不是无脑 403。"""
    r = client.get(f"{VKPI}/events/{seeded['viewer_event_id']}", headers=_h(tokens, "A"))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    "suffix,body",
    [
        ("/evidence", {"kind": "other", "filename": "x.jpg"}),  # F3 写证据(发票金额进汇总)
        ("/geocode", {"address": "Shenzhen"}),                   # F2 写回经纬度
        ("/retrospective/finalize", None),                        # F4 落库复盘快照
    ],
)
def test_viewer_member_cannot_write_shared_event(client, tokens, seeded, suffix, body):
    """viewer 共享成员对活动的写操作一律 403(本 session 修的 events 越权,固化)。"""
    url = f"{VKPI}/events/{seeded['viewer_event_id']}{suffix}"
    if body is None:
        r = client.post(url, headers=_h(tokens, "A"))
    else:
        r = client.post(url, json=body, headers=_h(tokens, "A"))
    assert r.status_code == 403, f"viewer member wrote {suffix}: {r.status_code} {r.text}"
