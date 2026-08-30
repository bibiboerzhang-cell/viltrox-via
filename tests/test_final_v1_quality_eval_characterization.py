"""CC51 characterization — evaluate_final_v1_quality 裁判逻辑动刀前锁行为.

口径:固定 gold/predictions 输入,整份报告与录制 golden 深比对(含 metrics 小数位、
checks 阈值原数、fingerprints、per-case 行);另锁全部输入校验错误码。
golden 由改刀前原码录制;改刀前后本文件必须同绿,判据阈值一个数字都不许动。
"""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.domains.kol.final_v1_quality_eval import (
    FinalV1QualityInputError,
    evaluate_final_v1_quality,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_GHOST = "e" * 64
MODEL = "gemini-2.5-pro"
PROMPT = "final_v1_p7"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _case_a_payload() -> dict[str, Any]:
    return {
        "schema_version": "video_analysis_final_v1",
        "layer1_visual_content": {
            "content_summary": "Portrait session with the new 85mm lens.",
            "scene_timeline": [],
            "product_presence": [],
            "brand_exposure": [],
            "competitor_presence": [],
            "production_observations": {},
            "evidence": {},
            "brand_product_evidence": {
                "viltrox_status": "Present",
                "inspection_complete": True,
                "checked_modalities": ["Visual", "audio", "subtitle", "telepathy"],
                "viltrox_evidence": [
                    {
                        "modality": "visual",
                        "timestamp_seconds": 13.2,
                        "observation": "logo visible on the barrel",
                    },
                    {"modality": "hologram", "timestamp": "bad", "text": ""},
                ],
                "viltrox_products": [
                    {
                        "sku": "af85",
                        "evidence": [
                            {
                                "source_modality": "subtitle",
                                "timestamp": "0:31",
                                "claim": "subtitle names the lens",
                            }
                        ],
                    },
                    "not-a-dict",
                ],
                "competitors": [
                    {
                        "name": "other co lens",
                        "evidence": {
                            "modality": "audio",
                            "timestamp_seconds": 44.0,
                            "description": "narrator compares",
                        },
                    }
                ],
            },
        },
        "layer2_viewer_emotion": {},
        "layer3_three_values": {},
        "layer4_attribution": {},
        "layer5_recommendations": {},
        "layer6_flags_and_scores": {},
    }


def _case_b_payload() -> dict[str, Any]:
    return {
        "schema_version": "video_analysis_final_v1",
        "layer1_visual_content": {
            "content_summary": "Talking-head episode, no lens shown.",
            "scene_timeline": [],
            "brand_product_evidence": {
                "viltrox_status": "absent",
                "inspection_complete": True,
                "checked_modalities": ["visual"],
                "viltrox_evidence": [],
                "viltrox_products": [{"name": "Mystery Lens"}],
                "competitors": [],
            },
        },
    }


def _case_a_expected() -> dict[str, Any]:
    return {
        "brand_status": "present",
        "inspection_complete": True,
        "checked_modalities": ["visual", "audio", "subtitle"],
        "products": [{"key": "AF 85mm F1.8", "aliases": ["af85", "85 art"]}],
        "competitors": [{"key": "OtherCo", "aliases": ["other co lens"]}],
        "evidence": [
            {
                "claim_id": "c1",
                "entity_type": "brand",
                "entity_key": "Viltrox",
                "modality": "visual",
                "timestamp_seconds": 12.5,
                "observation": "logo on lens barrel",
                "in_title": False,
            },
            {
                "claim_id": "c2",
                "entity_type": "product",
                "entity_key": "AF 85MM f1.8",
                "modality": "subtitle",
                "timestamp_seconds": 30,
                "observation": "subtitle names the 85mm",
                "in_title": False,
            },
            {
                "claim_id": "c3",
                "entity_type": "competitor",
                "entity_key": "OtherCo",
                "modality": "audio",
                "timestamp_seconds": 45.0,
                "observation": "narrator compares against OtherCo",
                "in_title": False,
            },
        ],
    }


def _gold_manifest() -> dict[str, Any]:
    return {
        "schema_version": "gemini_final_v1_quality_gold_v1",
        "dataset_id": "final-v1-quality-dev-01",
        "dataset_kind": "synthetic",
        "claim_status": "descriptive_only",
        "timestamp_tolerance_seconds": 1.5,
        "metric_thresholds": {
            "brand_accuracy_min": 0.5,
            "unknown_as_absent_max": 0,
            "non_title_evidence_recall_min": 1.0,
            "product_precision_min": 0.9,
            "product_recall_min": 1.0,
            "competitor_f1_min": 0.9,
            "evidence_modality_support_min": 0.7,
            "evidence_timestamp_support_min": 0.8,
            "unsupported_absent_max": 0,
            "schema_coverage_min": 0.5,
        },
        "cases": [
            {
                "case_id": "case-present",
                "media_sha256": SHA_A,
                "model": MODEL,
                "prompt_version": PROMPT,
                "expected": _case_a_expected(),
            },
            {
                "case_id": "case-absent",
                "media_sha256": SHA_B,
                "model": MODEL,
                "prompt_version": PROMPT,
                "expected": {
                    "brand_status": "absent",
                    "inspection_complete": True,
                    "checked_modalities": ["visual", "audio"],
                    "products": [],
                    "competitors": [],
                    "evidence": [],
                },
            },
            {
                "case_id": "case-unknown",
                "media_sha256": SHA_C,
                "model": MODEL,
                "prompt_version": PROMPT,
                "expected": {
                    "brand_status": "unknown",
                    "inspection_complete": False,
                    "checked_modalities": [],
                    "products": [],
                    "competitors": [],
                    "evidence": [],
                },
            },
            {
                "case_id": "case-drift",
                "media_sha256": SHA_D,
                "model": MODEL,
                "prompt_version": PROMPT,
                "expected": {
                    "brand_status": "unknown",
                    "inspection_complete": False,
                    "checked_modalities": ["subtitle"],
                    "products": [],
                    "competitors": [],
                    "evidence": [],
                },
            },
        ],
    }


def _predictions_manifest() -> dict[str, Any]:
    return {
        "schema_version": "gemini_final_v1_quality_predictions_v1",
        "dataset_id": "final-v1-quality-dev-01",
        "execution": {"model_invoked": False},
        "predictions": [
            {
                "case_id": "case-present",
                "media_sha256": SHA_A,
                "model": MODEL,
                "prompt_version": PROMPT,
                "output": {"video_analysis_final_v1": _case_a_payload()},
            },
            {
                "case_id": "case-absent",
                "media_sha256": SHA_B,
                "model": MODEL,
                "prompt_version": PROMPT,
                "output": _case_b_payload(),
            },
            {
                "case_id": "case-drift",
                "media_sha256": SHA_D,
                "model": "gemini-2.0-flash",
                "prompt_version": "final_v1_p6",
                "output": _case_b_payload(),
            },
            {
                "case_id": "case-ghost",
                "media_sha256": SHA_GHOST,
                "model": MODEL,
                "prompt_version": PROMPT,
                "output": {},
            },
        ],
    }


def _gold_manifest_pass() -> dict[str, Any]:
    return {
        "schema_version": "gemini_final_v1_quality_gold_v1",
        "dataset_id": "final-v1-quality-signed-02",
        "dataset_kind": "curated",
        "claim_status": "descriptive_only",
        "metric_thresholds": {
            "brand_accuracy_min": 0.0,
            "unknown_as_absent_max": 9,
            "non_title_evidence_recall_min": 0.0,
            "product_precision_min": 0.0,
            "product_recall_min": 0.0,
            "competitor_f1_min": 0.0,
            "evidence_modality_support_min": 0.0,
            "evidence_timestamp_support_min": 0.0,
            "unsupported_absent_max": 9,
            "schema_coverage_min": 0.0,
        },
        "cases": [
            {
                "case_id": "case-present",
                "media_sha256": SHA_A,
                "model": MODEL,
                "prompt_version": PROMPT,
                "expected": _case_a_expected(),
            }
        ],
    }


def _predictions_manifest_pass() -> dict[str, Any]:
    return {
        "schema_version": "gemini_final_v1_quality_predictions_v1",
        "dataset_id": "final-v1-quality-signed-02",
        "execution": {"model_invoked": True},
        "predictions": [
            {
                "case_id": "case-present",
                "media_sha256": SHA_A,
                "model": MODEL,
                "prompt_version": PROMPT,
                "output": {"video_analysis_final_v1": _case_a_payload()},
            }
        ],
    }


GOLDEN_REPORT = r"""{
  "schema_version": "gemini_final_v1_quality_report_v1",
  "evaluation_status": "evaluated",
  "claim_status": "descriptive_only",
  "dataset": {
    "dataset_id": "final-v1-quality-dev-01",
    "dataset_kind": "synthetic",
    "case_count": 4,
    "gold_fingerprint": "sha256:5686f351a0238a5b517c778d4438aa0e28fb7b95164552f3c76b14f77ef9eac2",
    "predictions_fingerprint": "sha256:ee1d456b57a793e5b147903f2bf98e6796b53686489e99aee85effbc6cfad492",
    "timestamp_tolerance_seconds": 1.5
  },
  "accuracy_claim": {
    "allowed": false,
    "reason": "synthetic_gold_and_no_verified_gemini_execution",
    "declared_model_invoked": false
  },
  "quality_gate": {
    "metric_status": "fail",
    "production_acceptance_eligible": false,
    "reason": "descriptive_only_offline_evaluation",
    "checks": [
      {
        "metric": "brand_accuracy_min",
        "observed": 0.5,
        "comparator": ">=",
        "threshold": 0.5,
        "passed": true
      },
      {
        "metric": "unknown_as_absent_max",
        "observed": 0,
        "comparator": "<=",
        "threshold": 0.0,
        "passed": true
      },
      {
        "metric": "non_title_evidence_recall_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 1.0,
        "passed": true
      },
      {
        "metric": "product_precision_min",
        "observed": 0.5,
        "comparator": ">=",
        "threshold": 0.9,
        "passed": false
      },
      {
        "metric": "product_recall_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 1.0,
        "passed": true
      },
      {
        "metric": "competitor_f1_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 0.9,
        "passed": true
      },
      {
        "metric": "evidence_modality_support_min",
        "observed": 0.75,
        "comparator": ">=",
        "threshold": 0.7,
        "passed": true
      },
      {
        "metric": "evidence_timestamp_support_min",
        "observed": 0.75,
        "comparator": ">=",
        "threshold": 0.8,
        "passed": false
      },
      {
        "metric": "unsupported_absent_max",
        "observed": 1,
        "comparator": "<=",
        "threshold": 0.0,
        "passed": false
      },
      {
        "metric": "schema_coverage_min",
        "observed": 0.380952,
        "comparator": ">=",
        "threshold": 0.5,
        "passed": false
      }
    ]
  },
  "metrics": {
    "brand_status": {
      "confusion_matrix": {
        "present": {
          "present": 1,
          "absent": 0,
          "unknown": 0,
          "invalid": 0
        },
        "absent": {
          "present": 0,
          "absent": 1,
          "unknown": 0,
          "invalid": 0
        },
        "unknown": {
          "present": 0,
          "absent": 0,
          "unknown": 0,
          "invalid": 2
        }
      },
      "case_count": 4,
      "correct_count": 2,
      "accuracy": 0.5,
      "macro_f1": 0.666667,
      "unknown_as_absent_count": 0,
      "per_class": {
        "present": {
          "precision": 1.0,
          "recall": 1.0,
          "f1": 1.0
        },
        "absent": {
          "precision": 1.0,
          "recall": 1.0,
          "f1": 1.0
        },
        "unknown": {
          "precision": 1.0,
          "recall": 0.0,
          "f1": 0.0
        }
      }
    },
    "non_title_evidence": {
      "expected_count": 3,
      "matched_count": 3,
      "recall": 1.0,
      "title_fields_read_as_evidence": 0
    },
    "products": {
      "expected_count": 1,
      "predicted_count": 2,
      "true_positive": 1,
      "false_positive": 1,
      "false_negative": 0,
      "precision": 0.5,
      "recall": 1.0,
      "f1": 0.666667,
      "hallucination_count": 1,
      "hallucination_rate": 0.5
    },
    "competitors": {
      "expected_count": 1,
      "predicted_count": 1,
      "true_positive": 1,
      "false_positive": 0,
      "false_negative": 0,
      "precision": 1.0,
      "recall": 1.0,
      "f1": 1.0
    },
    "evidence_support": {
      "claim_count": 4,
      "modality_supported_count": 3,
      "modality_support_rate": 0.75,
      "timestamp_supported_count": 3,
      "timestamp_support_rate": 0.75,
      "absent_prediction_count": 1,
      "unsupported_absent_count": 1,
      "supported_absent_rate": 0.0,
      "malformed_structured_evidence_count": 2
    },
    "schema_coverage": {
      "fields_present": 32,
      "fields_required": 84,
      "coverage": 0.380952
    }
  },
  "input_integrity": {
    "expected_case_count": 4,
    "prediction_count": 4,
    "missing_or_drifted": [
      "case-drift:prediction_model_mismatch",
      "case-drift:prediction_prompt_version_mismatch",
      "case-unknown:prediction_missing"
    ],
    "unexpected_case_ids": [
      "case-ghost"
    ]
  },
  "cases": [
    {
      "case_id": "case-present",
      "provenance_valid": true,
      "errors": [],
      "brand_expected": "present",
      "brand_predicted": "present",
      "products": {
        "expected": 1,
        "predicted": 1,
        "true_positive": 1,
        "false_positive": 0,
        "false_negative": 0
      },
      "competitors": {
        "expected": 1,
        "predicted": 1,
        "true_positive": 1,
        "false_positive": 0,
        "false_negative": 0
      },
      "non_title_evidence_expected": 3,
      "non_title_evidence_matched": 3,
      "schema_fields_present": 21,
      "schema_fields_required": 21,
      "schema_missing_or_invalid": []
    },
    {
      "case_id": "case-absent",
      "provenance_valid": true,
      "errors": [],
      "brand_expected": "absent",
      "brand_predicted": "absent",
      "products": {
        "expected": 0,
        "predicted": 1,
        "true_positive": 0,
        "false_positive": 1,
        "false_negative": 0
      },
      "competitors": {
        "expected": 0,
        "predicted": 0,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0
      },
      "non_title_evidence_expected": 0,
      "non_title_evidence_matched": 0,
      "schema_fields_present": 11,
      "schema_fields_required": 21,
      "schema_missing_or_invalid": [
        "layer2_viewer_emotion",
        "layer3_three_values",
        "layer4_attribution",
        "layer5_recommendations",
        "layer6_flags_and_scores",
        "layer1_visual_content.product_presence",
        "layer1_visual_content.brand_exposure",
        "layer1_visual_content.competitor_presence",
        "layer1_visual_content.production_observations",
        "layer1_visual_content.evidence"
      ]
    },
    {
      "case_id": "case-unknown",
      "provenance_valid": false,
      "errors": [
        "prediction_missing"
      ],
      "brand_expected": "unknown",
      "brand_predicted": "invalid",
      "products": {
        "expected": 0,
        "predicted": 0,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0
      },
      "competitors": {
        "expected": 0,
        "predicted": 0,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0
      },
      "non_title_evidence_expected": 0,
      "non_title_evidence_matched": 0,
      "schema_fields_present": 0,
      "schema_fields_required": 21,
      "schema_missing_or_invalid": [
        "schema_version",
        "layer1_visual_content",
        "layer2_viewer_emotion",
        "layer3_three_values",
        "layer4_attribution",
        "layer5_recommendations",
        "layer6_flags_and_scores",
        "layer1_visual_content.content_summary",
        "layer1_visual_content.scene_timeline",
        "layer1_visual_content.product_presence",
        "layer1_visual_content.brand_exposure",
        "layer1_visual_content.competitor_presence",
        "layer1_visual_content.production_observations",
        "layer1_visual_content.evidence",
        "layer1_visual_content.brand_product_evidence",
        "layer1_visual_content.brand_product_evidence.viltrox_status",
        "layer1_visual_content.brand_product_evidence.inspection_complete",
        "layer1_visual_content.brand_product_evidence.checked_modalities",
        "layer1_visual_content.brand_product_evidence.viltrox_evidence",
        "layer1_visual_content.brand_product_evidence.viltrox_products",
        "layer1_visual_content.brand_product_evidence.competitors"
      ]
    },
    {
      "case_id": "case-drift",
      "provenance_valid": false,
      "errors": [
        "prediction_model_mismatch",
        "prediction_prompt_version_mismatch"
      ],
      "brand_expected": "unknown",
      "brand_predicted": "invalid",
      "products": {
        "expected": 0,
        "predicted": 0,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0
      },
      "competitors": {
        "expected": 0,
        "predicted": 0,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0
      },
      "non_title_evidence_expected": 0,
      "non_title_evidence_matched": 0,
      "schema_fields_present": 0,
      "schema_fields_required": 21,
      "schema_missing_or_invalid": [
        "schema_version",
        "layer1_visual_content",
        "layer2_viewer_emotion",
        "layer3_three_values",
        "layer4_attribution",
        "layer5_recommendations",
        "layer6_flags_and_scores",
        "layer1_visual_content.content_summary",
        "layer1_visual_content.scene_timeline",
        "layer1_visual_content.product_presence",
        "layer1_visual_content.brand_exposure",
        "layer1_visual_content.competitor_presence",
        "layer1_visual_content.production_observations",
        "layer1_visual_content.evidence",
        "layer1_visual_content.brand_product_evidence",
        "layer1_visual_content.brand_product_evidence.viltrox_status",
        "layer1_visual_content.brand_product_evidence.inspection_complete",
        "layer1_visual_content.brand_product_evidence.checked_modalities",
        "layer1_visual_content.brand_product_evidence.viltrox_evidence",
        "layer1_visual_content.brand_product_evidence.viltrox_products",
        "layer1_visual_content.brand_product_evidence.competitors"
      ]
    }
  ],
  "diagnostics": {
    "provider_calls_during_evaluation": false,
    "llm_calls_during_evaluation": false,
    "database_reads_during_evaluation": false,
    "database_writes_during_evaluation": false,
    "title_fields_used_as_evidence": false
  }
}"""

GOLDEN_REPORT_PASS = r"""{
  "schema_version": "gemini_final_v1_quality_report_v1",
  "evaluation_status": "evaluated",
  "claim_status": "descriptive_only",
  "dataset": {
    "dataset_id": "final-v1-quality-signed-02",
    "dataset_kind": "curated",
    "case_count": 1,
    "gold_fingerprint": "sha256:caeac4670d5c053cf8f4b66b830529e2929d20a4fc87a1818c7f364e29270356",
    "predictions_fingerprint": "sha256:903bdd7308154bbd0e4dd874e111974f688da7418dbbfa8fac8226819e1c7124",
    "timestamp_tolerance_seconds": 2.0
  },
  "accuracy_claim": {
    "allowed": false,
    "reason": "offline_framework_does_not_verify_human_adjudication_or_provider_execution",
    "declared_model_invoked": true
  },
  "quality_gate": {
    "metric_status": "pass",
    "production_acceptance_eligible": false,
    "reason": "descriptive_only_offline_evaluation",
    "checks": [
      {
        "metric": "brand_accuracy_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 0.0,
        "passed": true
      },
      {
        "metric": "unknown_as_absent_max",
        "observed": 0,
        "comparator": "<=",
        "threshold": 9.0,
        "passed": true
      },
      {
        "metric": "non_title_evidence_recall_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 0.0,
        "passed": true
      },
      {
        "metric": "product_precision_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 0.0,
        "passed": true
      },
      {
        "metric": "product_recall_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 0.0,
        "passed": true
      },
      {
        "metric": "competitor_f1_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 0.0,
        "passed": true
      },
      {
        "metric": "evidence_modality_support_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 0.0,
        "passed": true
      },
      {
        "metric": "evidence_timestamp_support_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 0.0,
        "passed": true
      },
      {
        "metric": "unsupported_absent_max",
        "observed": 0,
        "comparator": "<=",
        "threshold": 9.0,
        "passed": true
      },
      {
        "metric": "schema_coverage_min",
        "observed": 1.0,
        "comparator": ">=",
        "threshold": 0.0,
        "passed": true
      }
    ]
  },
  "metrics": {
    "brand_status": {
      "confusion_matrix": {
        "present": {
          "present": 1,
          "absent": 0,
          "unknown": 0,
          "invalid": 0
        },
        "absent": {
          "present": 0,
          "absent": 0,
          "unknown": 0,
          "invalid": 0
        },
        "unknown": {
          "present": 0,
          "absent": 0,
          "unknown": 0,
          "invalid": 0
        }
      },
      "case_count": 1,
      "correct_count": 1,
      "accuracy": 1.0,
      "macro_f1": 1.0,
      "unknown_as_absent_count": 0,
      "per_class": {
        "present": {
          "precision": 1.0,
          "recall": 1.0,
          "f1": 1.0
        },
        "absent": {
          "precision": 1.0,
          "recall": 1.0,
          "f1": 1.0
        },
        "unknown": {
          "precision": 1.0,
          "recall": 1.0,
          "f1": 1.0
        }
      }
    },
    "non_title_evidence": {
      "expected_count": 3,
      "matched_count": 3,
      "recall": 1.0,
      "title_fields_read_as_evidence": 0
    },
    "products": {
      "expected_count": 1,
      "predicted_count": 1,
      "true_positive": 1,
      "false_positive": 0,
      "false_negative": 0,
      "precision": 1.0,
      "recall": 1.0,
      "f1": 1.0,
      "hallucination_count": 0,
      "hallucination_rate": 0.0
    },
    "competitors": {
      "expected_count": 1,
      "predicted_count": 1,
      "true_positive": 1,
      "false_positive": 0,
      "false_negative": 0,
      "precision": 1.0,
      "recall": 1.0,
      "f1": 1.0
    },
    "evidence_support": {
      "claim_count": 3,
      "modality_supported_count": 3,
      "modality_support_rate": 1.0,
      "timestamp_supported_count": 3,
      "timestamp_support_rate": 1.0,
      "absent_prediction_count": 0,
      "unsupported_absent_count": 0,
      "supported_absent_rate": 1.0,
      "malformed_structured_evidence_count": 2
    },
    "schema_coverage": {
      "fields_present": 21,
      "fields_required": 21,
      "coverage": 1.0
    }
  },
  "input_integrity": {
    "expected_case_count": 1,
    "prediction_count": 1,
    "missing_or_drifted": [],
    "unexpected_case_ids": []
  },
  "cases": [
    {
      "case_id": "case-present",
      "provenance_valid": true,
      "errors": [],
      "brand_expected": "present",
      "brand_predicted": "present",
      "products": {
        "expected": 1,
        "predicted": 1,
        "true_positive": 1,
        "false_positive": 0,
        "false_negative": 0
      },
      "competitors": {
        "expected": 1,
        "predicted": 1,
        "true_positive": 1,
        "false_positive": 0,
        "false_negative": 0
      },
      "non_title_evidence_expected": 3,
      "non_title_evidence_matched": 3,
      "schema_fields_present": 21,
      "schema_fields_required": 21,
      "schema_missing_or_invalid": []
    }
  ],
  "diagnostics": {
    "provider_calls_during_evaluation": false,
    "llm_calls_during_evaluation": false,
    "database_reads_during_evaluation": false,
    "database_writes_during_evaluation": false,
    "title_fields_used_as_evidence": false
  }
}"""


def test_mixed_dataset_report_deep_equals_recorded_golden() -> None:
    report = evaluate_final_v1_quality(_gold_manifest(), _predictions_manifest())
    golden = json.loads(GOLDEN_REPORT)
    assert report == golden
    # 类型级锁定(int/float/bool 序列化形态必须一致,守住 6 位小数与整数计数口径)。
    assert _canonical(report) == _canonical(golden)


def test_mixed_dataset_threshold_checks_locked_to_the_digit() -> None:
    report = evaluate_final_v1_quality(_gold_manifest(), _predictions_manifest())
    table = [
        (item["metric"], item["observed"], item["comparator"], item["threshold"], item["passed"])
        for item in report["quality_gate"]["checks"]
    ]
    assert table == [
        ("brand_accuracy_min", 0.5, ">=", 0.5, True),
        ("unknown_as_absent_max", 0, "<=", 0.0, True),
        ("non_title_evidence_recall_min", 1.0, ">=", 1.0, True),
        ("product_precision_min", 0.5, ">=", 0.9, False),
        ("product_recall_min", 1.0, ">=", 1.0, True),
        ("competitor_f1_min", 1.0, ">=", 0.9, True),
        ("evidence_modality_support_min", 0.75, ">=", 0.7, True),
        ("evidence_timestamp_support_min", 0.75, ">=", 0.8, False),
        ("unsupported_absent_max", 1, "<=", 0.0, False),
        ("schema_coverage_min", 0.380952, ">=", 0.5, False),
    ]
    assert report["quality_gate"]["metric_status"] == "fail"
    assert report["quality_gate"]["production_acceptance_eligible"] is False
    assert report["dataset"]["timestamp_tolerance_seconds"] == 1.5
    assert report["input_integrity"]["missing_or_drifted"] == [
        "case-drift:prediction_model_mismatch",
        "case-drift:prediction_prompt_version_mismatch",
        "case-unknown:prediction_missing",
    ]
    assert report["input_integrity"]["unexpected_case_ids"] == ["case-ghost"]


def test_pass_dataset_report_deep_equals_recorded_golden() -> None:
    report = evaluate_final_v1_quality(_gold_manifest_pass(), _predictions_manifest_pass())
    golden = json.loads(GOLDEN_REPORT_PASS)
    assert report == golden
    assert _canonical(report) == _canonical(golden)
    assert report["quality_gate"]["metric_status"] == "pass"
    # 无 tolerance 字段时默认 2.0(判据数字,不许动)。
    assert report["dataset"]["timestamp_tolerance_seconds"] == 2.0
    assert report["accuracy_claim"] == {
        "allowed": False,
        "reason": "offline_framework_does_not_verify_human_adjudication_or_provider_execution",
        "declared_model_invoked": True,
    }


def test_evaluation_never_mutates_inputs() -> None:
    gold = _gold_manifest()
    predictions = _predictions_manifest()
    gold_before = copy.deepcopy(gold)
    predictions_before = copy.deepcopy(predictions)
    evaluate_final_v1_quality(gold, predictions)
    assert gold == gold_before
    assert predictions == predictions_before


def _del_threshold(gold: dict[str, Any], _predictions: dict[str, Any]) -> None:
    del gold["metric_thresholds"]["brand_accuracy_min"]


def _mutation_cases() -> list[tuple[str, Any, str]]:
    cases: list[tuple[str, Any, str]] = []

    def add(name: str, code: str, apply: Any) -> None:
        gold = _gold_manifest()
        predictions = _predictions_manifest()
        apply(gold, predictions)
        cases.append((name, (gold, predictions), code))

    add("dataset_id_empty", "gold_dataset_id_required", lambda g, p: g.update(dataset_id=""))
    add(
        "claim_status_wrong",
        "gold_claim_status_must_be_descriptive_only",
        lambda g, p: g.update(claim_status="verified"),
    )
    add("cases_not_list", "gold_cases_required", lambda g, p: g.update(cases=None))
    add("cases_empty", "gold_cases_empty", lambda g, p: g.update(cases=[]))
    add(
        "case_id_duplicate",
        "gold_case_id_duplicate",
        lambda g, p: g["cases"][1].update(case_id="case-present"),
    )
    add(
        "media_sha_invalid",
        "gold_media_sha256_invalid",
        lambda g, p: g["cases"][0].update(media_sha256="xyz"),
    )
    add(
        "media_sha_duplicate",
        "gold_media_sha256_duplicate",
        lambda g, p: g["cases"][1].update(media_sha256=SHA_A),
    )
    add(
        "brand_status_invalid",
        "gold_brand_status_invalid",
        lambda g, p: g["cases"][0]["expected"].update(brand_status="maybe"),
    )
    add(
        "inspection_not_bool",
        "gold_inspection_complete_invalid",
        lambda g, p: g["cases"][0]["expected"].update(inspection_complete="yes"),
    )
    add(
        "modalities_invalid",
        "gold_checked_modalities_invalid",
        lambda g, p: g["cases"][0]["expected"].update(checked_modalities=["psychic"]),
    )
    add(
        "product_entity_invalid",
        "gold_product_invalid",
        lambda g, p: g["cases"][0]["expected"].update(products=[{"aliases": ["x"]}]),
    )
    add(
        "entity_key_duplicate",
        "gold_entity_key_duplicate",
        lambda g, p: g["cases"][0]["expected"]["products"].append(
            {"key": "af85MMf1.8", "aliases": []}
        ),
    )
    add(
        "absent_partial_inspection",
        "gold_absent_requires_complete_visual_audio_inspection",
        lambda g, p: g["cases"][1]["expected"].update(checked_modalities=["visual"]),
    )
    add(
        "title_as_evidence",
        "gold_title_cannot_be_evidence",
        lambda g, p: g["cases"][0]["expected"]["evidence"][0].update(in_title=True),
    )
    add(
        "evidence_entity_orphan",
        "gold_evidence_entity_orphan",
        lambda g, p: g["cases"][0]["expected"]["evidence"][1].update(entity_key="ghost lens"),
    )
    add(
        "evidence_modality_invalid",
        "gold_evidence_modality_invalid",
        lambda g, p: g["cases"][0]["expected"]["evidence"][0].update(modality="psychic"),
    )
    add(
        "evidence_timestamp_negative",
        "gold_evidence_timestamp_invalid",
        lambda g, p: g["cases"][0]["expected"]["evidence"][0].update(timestamp_seconds=-1),
    )
    add(
        "claim_id_duplicate",
        "gold_claim_id_duplicate",
        lambda g, p: g["cases"][0]["expected"]["evidence"][1].update(claim_id="c1"),
    )
    add(
        "present_without_brand_evidence",
        "gold_present_requires_brand_evidence",
        lambda g, p: g["cases"][0]["expected"].update(
            evidence=[item for item in g["cases"][0]["expected"]["evidence"] if item["entity_type"] != "brand"]
        ),
    )
    add(
        "predictions_schema",
        "predictions_schema_version_invalid",
        lambda g, p: p.update(schema_version="nope"),
    )
    add(
        "prediction_case_duplicate",
        "prediction_case_id_duplicate",
        lambda g, p: p["predictions"][1].update(case_id="case-present"),
    )
    add(
        "prediction_sha_invalid",
        "prediction_media_sha256_invalid",
        lambda g, p: p["predictions"][0].update(media_sha256="zz"),
    )
    add(
        "prediction_output_missing",
        "prediction_output_required",
        lambda g, p: p["predictions"][0].update(output=None),
    )
    add(
        "dataset_id_mismatch",
        "prediction_dataset_id_mismatch",
        lambda g, p: p.update(dataset_id="other-dataset"),
    )
    add("threshold_missing", "metric_threshold_missing:brand_accuracy_min", _del_threshold)
    return cases


@pytest.mark.parametrize(
    "name,inputs,code",
    [pytest.param(name, inputs, code, id=name) for name, inputs, code in _mutation_cases()],
)
def test_input_error_codes_are_locked(name: str, inputs: tuple[Any, Any], code: str) -> None:
    gold, predictions = inputs
    with pytest.raises(FinalV1QualityInputError) as exc:
        evaluate_final_v1_quality(gold, predictions)
    assert str(exc.value) == code


@pytest.mark.parametrize(
    "gold,code",
    [
        ([], "gold_must_be_object"),
        ({**_gold_manifest(), "schema_version": "nope"}, "gold_schema_version_invalid"),
    ],
    ids=["gold_not_object", "gold_schema_version"],
)
def test_gold_shell_error_codes(gold: Any, code: str) -> None:
    with pytest.raises(FinalV1QualityInputError) as exc:
        evaluate_final_v1_quality(gold, _predictions_manifest())
    assert str(exc.value) == code


def test_predictions_must_be_object_error() -> None:
    with pytest.raises(FinalV1QualityInputError) as exc:
        evaluate_final_v1_quality(_gold_manifest(), "not-a-dict")
    assert str(exc.value) == "predictions_must_be_object"
