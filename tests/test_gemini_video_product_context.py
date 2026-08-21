from __future__ import annotations

from app.services.ai.analyzers.gemini_video_prompts import _video_final_v1_prompt
from app.workers.apify_jobs_video_context import _video_final_context
from app.workers.apify_jobs_worker_prep import _load_video_evidence


def test_manual_and_project_skus_are_bounded_context_not_presence_evidence() -> None:
    context = _video_final_context(
        {
            "project_id": 9,
            "project_name": "Launch",
            "product_sku": "AF-35-LAB",
            "product_name": "Viltrox AF 35mm LAB",
            "linked_products": [
                {
                    "sku": "AF-27-PRO",
                    "model_name": "AF 27mm F1.2 Pro",
                    "marketing_name": "Viltrox AF 27mm F1.2 Pro",
                    "relation_type": "manual",
                    "confidence": 1,
                },
                {
                    "sku": "AF-27-PRO",
                    "model_name": "duplicate",
                    "relation_type": "manual",
                    "confidence": 1,
                },
            ],
        }
    )

    product_context = context["product_context"]
    assert product_context["candidate_products_are_context_only"] is True
    assert product_context["candidate_products_require_timed_video_evidence"] is True
    assert [item["sku"] for item in product_context["candidate_products"]] == [
        "AF-35-LAB",
        "AF-27-PRO",
    ]
    assert all(item["association_is_evidence"] is False for item in product_context["candidate_products"])


def test_linked_product_json_text_is_supported_and_capped() -> None:
    linked = [
        {"sku": f"SKU-{index}", "model_name": f"Model {index}", "relation_type": "manual"}
        for index in range(20)
    ]
    context = _video_final_context({"linked_products": __import__("json").dumps(linked)})

    assert len(context["product_context"]["candidate_products"]) == 10
    assert context["product_context"]["candidate_products"][0]["sku"] == "SKU-0"


def test_final_v1_prompt_forbids_using_sku_association_as_video_evidence() -> None:
    prompt = _video_final_v1_prompt(
        title="Untitled lens test",
        profile_ctx="",
        subtitle_ctx="",
        subtitle_used=False,
        performance_context={
            "product_context": {
                "candidate_products": [{"sku": "AF-27-PRO", "association_is_evidence": False}]
            }
        },
    )

    assert "candidate_products 仅是员工手工关联或项目候选" in prompt
    assert "不是视频出现证据" in prompt
    assert '"association_is_evidence": false' in prompt


class _Cursor:
    def __init__(self) -> None:
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, _params=()) -> None:
        self.sql = " ".join(str(sql).split())

    def fetchone(self):
        return {
            "id": 71,
            "content_url": "https://www.youtube.com/watch?v=abcdefghijk",
            "product_sku": "AF-35-LAB",
            "linked_products": [{"sku": "AF-27-PRO", "relation_type": "manual"}],
        }


class _Conn:
    def __init__(self) -> None:
        self.value = _Cursor()

    def cursor(self, **_kwargs):
        return self.value


def test_worker_evidence_read_joins_manual_product_links_server_side() -> None:
    conn = _Conn()

    evidence = _load_video_evidence(conn, "71")

    assert evidence["product_sku"] == "AF-35-LAB"
    assert evidence["linked_products"][0]["sku"] == "AF-27-PRO"
    assert "FROM vkpi_kol_video_product_links l" in conn.value.sql
    assert "LEFT JOIN vkpi_products vp" in conn.value.sql
