"""test_final_v1_quality_eval_characterization 续篇(后半)。"""
from tests.test_final_v1_quality_eval_characterization_support import (  # noqa: F401
    Any,
    FinalV1QualityInputError,
    GOLDEN_REPORT,
    GOLDEN_REPORT_PASS,
    MODEL,
    PROMPT,
    SHA_A,
    SHA_B,
    SHA_C,
    SHA_D,
    SHA_GHOST,
    _canonical,
    _case_a_expected,
    _case_a_payload,
    _case_b_payload,
    _del_threshold,
    _gold_manifest,
    _gold_manifest_pass,
    _mutation_cases,
    _predictions_manifest,
    _predictions_manifest_pass,
    copy,
    evaluate_final_v1_quality,
    json,
    pytest,
)




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
