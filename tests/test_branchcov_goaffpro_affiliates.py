"""分支覆盖冲刺·integrations/goaffpro_connect_affiliates.py — 映射/拼链/写侧降级分支。

HTTP 原语(_get/_post/_patch)全部 monkeypatch 到 goaffpro_connect 模块上,
零真网络;resolve/find 的编排分支用模块级函数替身;断言均为具体返回结构。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.integrations import goaffpro_connect as gc  # noqa: E402
from app.domains.integrations import goaffpro_connect_affiliates as ga  # noqa: E402


class PatchMixin:
    def _patch(self, obj, name, value):
        original = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(lambda: setattr(obj, name, original))


class PureMappingTests(unittest.TestCase):
    def test_norm_base(self):
        self.assertEqual(ga._norm_base(""), ga._DEFAULT_API_BASE)
        self.assertEqual(ga._norm_base(None), ga._DEFAULT_API_BASE)
        self.assertEqual(ga._norm_base("https://x.com/api/"), "https://x.com/api")

    def test_map_affiliate_fallback_keys(self):
        out = ga._map_affiliate({"id": 1, "full_name": "N", "referral_code": "rc"})
        self.assertEqual(out["name"], "N")
        self.assertEqual(out["referral_code"], "rc")
        self.assertEqual(out["_raw_keys"], ["full_name", "id", "referral_code"])
        empty = ga._map_affiliate(None)
        self.assertEqual(empty["name"], "")

    def test_map_order_fallback_keys(self):
        out = ga._map_order({"order_id": 7, "ref_id": 3, "order_total": 9, "date": "d"})
        self.assertEqual(out["id"], 7)
        self.assertEqual(out["affiliate_id"], 3)
        self.assertEqual(out["total"], 9)
        self.assertEqual(out["created_at"], "d")

    def test_extract_list_variants(self):
        self.assertEqual(ga._extract_list([{"a": 1}, "junk"]), [{"a": 1}])
        self.assertEqual(ga._extract_list({"affiliates": [{"a": 1}]}, "affiliates"), [{"a": 1}])
        self.assertEqual(ga._extract_list({"data": "not-list"}, "data"), [])
        self.assertEqual(ga._extract_list("junk", "data"), [])

    def test_soft_error_detection(self):
        self.assertEqual(ga._soft_error({"error": "boom"}), "boom")
        self.assertEqual(ga._soft_error({"message": "warn", "affiliates": []}), "")
        self.assertEqual(ga._soft_error({"fine": 1}), "")
        self.assertEqual(ga._soft_error([1]), "")

    def test_default_store_url_env_override(self):
        original = os.environ.get("GOAFFPRO_STORE_URL")
        try:
            os.environ["GOAFFPRO_STORE_URL"] = "https://shop.example.com/"
            self.assertEqual(ga._default_store_url(), "https://shop.example.com")
            os.environ.pop("GOAFFPRO_STORE_URL", None)
            self.assertEqual(ga._default_store_url(), "https://www.viltrox.com")
        finally:
            if original is None:
                os.environ.pop("GOAFFPRO_STORE_URL", None)
            else:
                os.environ["GOAFFPRO_STORE_URL"] = original

    def test_extract_affiliate_wrappers(self):
        self.assertEqual(ga._extract_affiliate({"affiliate": {"id": 1}}), {"id": 1})
        self.assertEqual(ga._extract_affiliate({"data": {"id": 2}}), {"id": 2})
        self.assertEqual(ga._extract_affiliate({"id": 3}), {"id": 3})
        self.assertEqual(ga._extract_affiliate("junk"), {})

    def test_read_ref_code_only_real_ref_fields(self):
        self.assertEqual(ga._read_ref_code({"ref_code": "abc"}), "abc")
        self.assertEqual(ga._read_ref_code({"referral_code": "rc"}), "rc")
        # 绝不把 id/coupon 兜底成 ref 码(2026-06-17 修的 bug 语义)
        self.assertEqual(ga._read_ref_code({"id": 20394702, "coupon": "SAVE"}), "")
        self.assertEqual(ga._read_ref_code({"ref_code": {"nested": 1}}), "")
        self.assertEqual(ga._read_ref_code(None), "")


class MoneyAndLabelTests(unittest.TestCase):
    def test_to_cents(self):
        self.assertEqual(ga.to_cents(None), 0)
        self.assertEqual(ga.to_cents(""), 0)
        self.assertEqual(ga.to_cents("12.34"), 1234)
        self.assertEqual(ga.to_cents(5), 500)
        self.assertEqual(ga.to_cents("junk"), 0)

    def test_coupon_for_shapes(self):
        self.assertEqual(ga.coupon_for({"coupon": {"code": "HUNGKAI"}}), "HUNGKAI")
        self.assertEqual(ga.coupon_for({"coupon": "PLAIN"}), "PLAIN")
        self.assertEqual(ga.coupon_for({"coupons": [{"code": "L1"}]}), "L1")
        self.assertEqual(ga.coupon_for({"coupons": ["RAW"]}), "RAW")
        self.assertEqual(ga.coupon_for({"coupon": {"note": "x"}}), "")
        self.assertEqual(ga.coupon_for(None), "")

    def test_fmt_num(self):
        self.assertEqual(ga._fmt_num(10.0), "10")
        self.assertEqual(ga._fmt_num(10.5), "10.5")
        self.assertEqual(ga._fmt_num("junk"), "junk")
        self.assertEqual(ga._fmt_num(None), "")

    def test_commission_label(self):
        self.assertEqual(ga.commission_label({"commission": {"type": "percentage", "amount": 10}}), "10%")
        self.assertEqual(ga.commission_label({"commission": {"type": "fixed_amount", "amount": 5}}), "$5")
        self.assertEqual(ga.commission_label({"commission": {"type": "weird", "amount": 5}}), "5")
        self.assertEqual(ga.commission_label({"commission": {"type": "percentage"}}), "")
        self.assertEqual(ga.commission_label({"commission": "15%"}), "15%")
        self.assertEqual(ga.commission_label({}), "")
        self.assertEqual(ga.commission_label(None), "")


class ReferralLinkTests(unittest.TestCase):
    def test_existing_link_used_only_with_tracking(self):
        # 带 ref= 的现成链直接用
        raw = {"referral_link": "https://s.com/?ref=abc", "ref_code": "abc"}
        self.assertEqual(ga.referral_link(raw), "https://s.com/?ref=abc")
        # 光首页链(无追踪参数)被拒,改用 code 拼
        raw = {"url": "https://s.com/", "ref_code": "abc"}
        self.assertEqual(ga.referral_link(raw), f"{ga._default_store_url()}/?ref=abc")
        # 链接里含 code 本身也算追踪链
        raw = {"link": "https://s.com/r/ABC", "ref_code": "abc"}
        self.assertEqual(ga.referral_link(raw), "https://s.com/r/ABC")

    def test_no_code_falls_back_to_store_home(self):
        self.assertEqual(ga.referral_link({}), ga._default_store_url())
        self.assertEqual(ga.referral_link(None), ga._default_store_url())

    def test_explicit_ref_code_param_wins(self):
        self.assertEqual(ga.referral_link(None, "zz"), f"{ga._default_store_url()}/?ref=zz")


class CreateAffiliateTests(PatchMixin, unittest.TestCase):
    def test_transport_failure_passthrough(self):
        self._patch(gc, "_post", lambda path, payload: {
            "ok": False, "reason": "not_configured", "error": "no creds", "status_code": 401, "raw": {"e": 1},
        })
        out = ga.create_affiliate("KOL")
        self.assertEqual(out["ok"], False)
        self.assertEqual(out["reason"], "not_configured")
        self.assertEqual(out["status_code"], 401)
        self.assertEqual(out["raw"], {"e": 1})

    def test_soft_error_200_detected(self):
        self._patch(gc, "_post", lambda path, payload: {"ok": True, "data": {"message": "email required"}})
        out = ga.create_affiliate("KOL")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "email required")

    def test_missing_affiliate_id_is_failure(self):
        self._patch(gc, "_post", lambda path, payload: {"ok": True, "data": {"affiliate": {"name": "K"}}})
        out = ga.create_affiliate("KOL")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "create returned no affiliate_id")

    def test_success_normalizes_affiliate_id(self):
        captured: dict[str, Any] = {}

        def fake_post(path, payload):
            captured["path"], captured["payload"] = path, payload
            return {"ok": True, "data": {"affiliate_id": 123}}

        self._patch(gc, "_post", fake_post)
        out = ga.create_affiliate(" KOL ", "k@x.com", extra={"status": "approved", "name": "HACK"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["affiliate_id"], "123")
        self.assertEqual(out["affiliate"]["id"], "123")
        self.assertEqual(out["ref_code"], "")
        self.assertEqual(captured["path"], "admin/affiliates")
        self.assertEqual(captured["payload"]["name"], "KOL")  # extra 不许覆盖 name
        self.assertEqual(captured["payload"]["email"], "k@x.com")
        self.assertEqual(captured["payload"]["status"], "approved")


class SearchGetAffiliateTests(PatchMixin, unittest.TestCase):
    def test_search_requires_field_and_keyword(self):
        self.assertEqual(ga.search_affiliate("", "x"), [])
        self.assertEqual(ga.search_affiliate("email", " "), [])

    def test_search_failure_and_success(self):
        self._patch(gc, "_get", lambda path, params: {"ok": False})
        self.assertEqual(ga.search_affiliate("email", "a@b.com"), [])
        self._patch(gc, "_get", lambda path, params: {"ok": True, "data": {"affiliates": [{"id": 1}]}})
        self.assertEqual(ga.search_affiliate("email", "a@b.com"), [{"id": 1}])

    def test_get_affiliate_missing_id(self):
        self.assertEqual(ga.get_affiliate(""), {"ok": False, "reason": "missing_id"})

    def test_get_affiliate_matches_by_id_with_fallback(self):
        rows = [{"id": 9, "ref_code": "other"}, {"id": 5, "ref_code": "mine", "status": "approved"}]
        self._patch(gc, "_get", lambda path, params: {"ok": True, "data": {"affiliates": rows}})
        out = ga.get_affiliate(5)
        self.assertTrue(out["ok"])
        self.assertEqual(out["ref_code"], "mine")
        self.assertEqual(out["status"], "approved")
        # id 不在行里 → 退 rows[0]
        out = ga.get_affiliate(404)
        self.assertEqual(out["affiliate"]["id"], 9)

    def test_get_affiliate_transport_failure_passthrough(self):
        self._patch(gc, "_get", lambda path, params: {"ok": False, "reason": "not_configured"})
        self.assertEqual(ga.get_affiliate(5), {"ok": False, "reason": "not_configured"})


class ResolveAffiliateTests(PatchMixin, unittest.TestCase):
    def setUp(self):
        self._patch(ga, "time", SimpleNamespace(sleep=lambda s: None))

    def test_email_search_hit_short_circuits(self):
        hit = {"id": 7, "email": "A@B.com", "ref_code": "rc", "status": "approved",
               "coupon": {"code": "CP"}}
        self._patch(ga, "search_affiliate", lambda f, kw: [hit] if f == "email" else [])
        out = ga.resolve_affiliate("Name", "a@b.com")
        self.assertTrue(out["ok"])
        self.assertEqual(out["affiliate_id"], "7")
        self.assertEqual(out["ref_code"], "rc")
        self.assertEqual(out["coupon"], "CP")
        self.assertFalse(out["created"])

    def test_not_found_without_create(self):
        self._patch(ga, "search_affiliate", lambda f, kw: [])
        out = ga.resolve_affiliate("Name", "a@b.com")
        self.assertEqual(out, {"ok": False, "reason": "not_found", "affiliate_id": "",
                               "ref_code": "", "coupon": "", "status": ""})

    def test_create_requires_name(self):
        self._patch(ga, "search_affiliate", lambda f, kw: [])
        out = ga.resolve_affiliate("", "a@b.com", create=True)
        self.assertEqual(out["reason"], "no_name")

    def test_already_registered_error_finds_back_by_email(self):
        calls = {"n": 0}

        def fake_search(field, kw):
            calls["n"] += 1
            if calls["n"] <= 2:
                return []  # 建号前的两次搜索都空
            return [{"id": 8, "email": "a@b.com", "ref_code": "rc8"}]

        self._patch(ga, "search_affiliate", fake_search)
        self._patch(ga, "create_affiliate", lambda nm, em, extra=None: {"ok": False, "error": "Email already registered"})
        out = ga.resolve_affiliate("Name", "a@b.com", create=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["affiliate_id"], "8")
        self.assertFalse(out["created"])

    def test_create_failure_surfaces_reason_and_raw(self):
        self._patch(ga, "search_affiliate", lambda f, kw: [])
        self._patch(ga, "create_affiliate", lambda nm, em, extra=None: {
            "ok": False, "error": "server exploded", "status_code": 500, "raw": {"x": 1},
        })
        out = ga.resolve_affiliate("Name", "a@b.com", create=True)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "create_failed")
        self.assertEqual(out["error"], "server exploded")
        self.assertEqual(out["raw"], {"x": 1})

    def test_created_then_ref_code_backfilled_with_retry(self):
        self._patch(ga, "search_affiliate", lambda f, kw: [])
        self._patch(ga, "create_affiliate", lambda nm, em, extra=None: {
            "ok": True, "affiliate": {"id": "77"}, "ref_code": "", "affiliate_id": "77",
        })
        attempts = {"n": 0}

        def fake_get(aid):
            attempts["n"] += 1
            if attempts["n"] < 2:
                return {"ok": True, "ref_code": ""}  # 异步分配窗口:第一次还没有
            return {"ok": True, "ref_code": "late", "coupon": "CP", "status": "approved",
                    "affiliate": {"id": "77", "ref_code": "late"}}

        self._patch(ga, "get_affiliate", fake_get)
        out = ga.resolve_affiliate("Name", "", create=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["created"])
        self.assertEqual(out["ref_code"], "late")
        self.assertEqual(out["coupon"], "CP")
        self.assertEqual(attempts["n"], 2)

    def test_no_ref_code_after_retries_fails_with_reason(self):
        hit = {"id": 7, "email": "a@b.com", "status": "pending"}
        self._patch(ga, "search_affiliate", lambda f, kw: [hit] if f == "email" else [])
        self._patch(ga, "get_affiliate", lambda aid: {"ok": True, "ref_code": ""})
        out = ga.resolve_affiliate("Name", "a@b.com")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "no_ref_code")
        self.assertEqual(out["raw"], hit)


class UpdateCommissionCouponTests(PatchMixin, unittest.TestCase):
    def test_commission_input_validation(self):
        self.assertEqual(ga.update_affiliate_commission("", 10)["reason"], "missing_id")
        self.assertEqual(ga.update_affiliate_commission(1, "abc")["reason"], "bad_amount")
        self.assertEqual(ga.update_affiliate_commission(1, -3)["reason"], "bad_amount")

    def test_commission_patch_failure_and_soft_error(self):
        self._patch(gc, "_patch", lambda path, payload: {"ok": False, "error": "denied", "status_code": 403})
        out = ga.update_affiliate_commission(1, 10)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "denied")
        self._patch(gc, "_patch", lambda path, payload: {"ok": True, "data": {"error": "invalid"}})
        out = ga.update_affiliate_commission(1, 10)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "invalid")

    def test_commission_success_normalizes_type_and_rereads(self):
        captured: dict[str, Any] = {}

        def fake_patch(path, payload):
            captured["path"], captured["payload"] = path, payload
            return {"ok": True, "data": {}}

        self._patch(gc, "_patch", fake_patch)
        self._patch(ga, "get_affiliate", lambda aid: {"ok": True, "commission_rate": "12%"})
        out = ga.update_affiliate_commission(9, "12.4", ctype="bogus", on="bogus")
        self.assertTrue(out["ok"])
        self.assertEqual(out["commission_rate"], "12%")
        self.assertEqual(captured["path"], "admin/affiliates/9")
        self.assertEqual(captured["payload"]["commission"], {"type": "percentage", "amount": 12, "on": "product"})

    def test_commission_reread_failure_falls_back_to_label(self):
        self._patch(gc, "_patch", lambda path, payload: {"ok": True, "data": {}})
        self._patch(ga, "get_affiliate", lambda aid: {"ok": False})
        out = ga.update_affiliate_commission(9, 15)
        self.assertTrue(out["ok"])
        self.assertEqual(out["commission_rate"], "15%")

    def test_coupon_validation_and_free_shipping(self):
        self.assertEqual(ga.update_affiliate_coupon("", "C")["reason"], "missing")
        self.assertEqual(ga.update_affiliate_coupon(1, " ")["reason"], "missing")
        captured: dict[str, Any] = {}

        def fake_patch(path, payload):
            captured["payload"] = payload
            return {"ok": True, "data": {}}

        self._patch(gc, "_patch", fake_patch)
        self._patch(ga, "get_affiliate", lambda aid: {"ok": True, "coupon": "REAL"})
        out = ga.update_affiliate_coupon(1, "CODE", discount_type="free_shipping")
        self.assertTrue(out["ok"])
        self.assertEqual(out["coupon"], "REAL")
        self.assertNotIn("discount_value", captured["payload"]["coupon"])

    def test_coupon_bad_discount_value_defaults_to_ten(self):
        captured: dict[str, Any] = {}

        def fake_patch(path, payload):
            captured["payload"] = payload
            return {"ok": True, "data": {}}

        self._patch(gc, "_patch", fake_patch)
        self._patch(ga, "get_affiliate", lambda aid: {"ok": False})
        out = ga.update_affiliate_coupon(1, "CODE", discount_value="junk", discount_type="weird")
        self.assertTrue(out["ok"])
        self.assertEqual(out["coupon"], "CODE")  # 回读失败退传入码
        self.assertEqual(captured["payload"]["coupon"],
                         {"code": "CODE", "discount_type": "percentage", "discount_value": 10})


class ProductMatchingTests(PatchMixin, unittest.TestCase):
    def test_norm_token_set(self):
        self.assertEqual(ga._norm_token_set("AF 85mm F1.4!"), {"af", "85mm", "f1"})
        self.assertEqual(ga._norm_token_set(""), set())

    def test_list_products_maps_rows(self):
        self._patch(gc, "_get", lambda path, params: {
            "ok": True,
            "data": {"products": [{"product_id": 1, "title": "AF 85mm", "slug": "af-85"}]},
        })
        out = ga.list_products(keyword="85mm", limit=10, offset=20)
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["products"][0], {
            "id": 1, "name": "AF 85mm", "handle": "af-85", "product_type": "", "vendor": "",
        })

    def test_list_products_failure_passthrough(self):
        self._patch(gc, "_get", lambda path, params: {"ok": False, "reason": "not_configured"})
        self.assertEqual(ga.list_products(), {"ok": False, "reason": "not_configured"})

    def test_find_product_handle_empty_query(self):
        self.assertEqual(ga.find_product_handle("  "), {"ok": False, "reason": "empty_query"})

    def test_find_product_handle_confident_match(self):
        products = [
            {"id": 1, "name": "AF 85mm F1.4 Pro", "handle": "af-85mm-f14-pro"},
            {"id": 2, "name": "AF 27mm F1.2", "handle": "af-27mm-f12"},
        ]
        self._patch(ga, "list_products", lambda **kw: {"ok": True, "products": products})
        out = ga.find_product_handle("Viltrox AF 85mm F1.4")
        self.assertTrue(out["ok"])
        self.assertEqual(out["handle"], "af-85mm-f14-pro")
        self.assertEqual(out["id"], 1)

    def test_find_product_handle_pagination_fallback(self):
        pages = {"n": 0}
        products = [{"id": 1, "name": "AF 85mm", "handle": "af-85mm"}]

        def fake_list(keyword=None, limit=None, offset=None):
            if keyword:
                return {"ok": True, "products": []}  # keyword 搜索 0 命中 → 触发全量分页
            pages["n"] += 1
            return {"ok": True, "products": products}

        self._patch(ga, "list_products", fake_list)
        out = ga.find_product_handle("af 85mm")
        self.assertTrue(out["ok"])
        self.assertEqual(out["handle"], "af-85mm")
        self.assertEqual(pages["n"], 1)  # 第一页不足 250 条即止

    def test_find_product_handle_refuses_weak_match(self):
        products = [{"id": 1, "name": "totally different thing", "handle": "x"}]
        self._patch(ga, "list_products", lambda **kw: {"ok": True, "products": products})
        out = ga.find_product_handle("af 85mm f1.4 lens kit")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "no_confident_match")

    def test_product_referral_link_fallbacks(self):
        store = ga._default_store_url()
        self.assertEqual(ga.product_referral_link("", "abc"), f"{store}/?ref=abc")
        self.assertEqual(ga.product_referral_link("af-85", ""), f"{store}/products/af-85")
        self.assertEqual(ga.product_referral_link("/af-85/", "abc"), f"{store}/products/af-85?ref=abc")


if __name__ == "__main__":
    unittest.main()
