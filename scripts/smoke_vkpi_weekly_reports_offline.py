"""scripts/smoke_vkpi_weekly_reports_offline.py

P1.6 Weekly reports offline smoke (FORCE_OFFLINE).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = "1"


def main():
    failures = []
    
    print("[1] Module imports...")
    try:
        from app.services.vkpi import weekly_report_templates, weekly_report_generator
        print("  ✓")
    except Exception as exc:
        failures.append(f"Cannot import: {exc}")
        sys.exit(1)
    
    print("[2] PROMPT_VERSION exported...")
    if not weekly_report_templates.PROMPT_VERSION:
        failures.append("PROMPT_VERSION not set")
    print(f"  ✓ {weekly_report_templates.PROMPT_VERSION}")
    
    print("[3] TEMPLATES has 6 keys...")
    expected_keys = {
        "layer1_universal",
        "layer2_leader",
        "layer2_kol_ops",
        "layer2_marketing_analyst",
        "layer2_content_reviewer",
        "layer3_personal",
    }
    actual_keys = set(weekly_report_templates.TEMPLATES.keys())
    missing = expected_keys - actual_keys
    if missing:
        failures.append(f"Missing template keys: {missing}")
    else:
        print(f"  ✓ {len(actual_keys)} templates")
    
    print("[4] Each template has required fields...")
    required_fields = {"title_pattern", "audience", "layer", "sections", "max_output_tokens"}
    for key, t in weekly_report_templates.TEMPLATES.items():
        missing_fields = required_fields - set(t.keys())
        if missing_fields:
            failures.append(f"Template {key} missing: {missing_fields}")
    print("  ✓")
    
    print("[5] template_keys_for_role(leader) returns 2 templates...")
    keys = weekly_report_templates.template_keys_for_role("leader")
    if len(keys) != 2:
        failures.append(f"leader should get 2 templates, got {len(keys)}: {keys}")
    if "layer1_universal" not in keys:
        failures.append("leader missing layer1_universal")
    if "layer2_leader" not in keys:
        failures.append("leader missing layer2_leader")
    if "layer3_personal" in keys:
        failures.append("leader should NOT get layer3_personal")
    print("  ✓ leader: 2 templates (no personal)")
    
    print("[6] template_keys_for_role(kol_ops) returns 3 templates...")
    keys = weekly_report_templates.template_keys_for_role("kol_ops")
    if len(keys) != 3:
        failures.append(f"kol_ops should get 3 templates, got {len(keys)}: {keys}")
    if "layer1_universal" not in keys:
        failures.append("kol_ops missing layer1_universal")
    if "layer2_kol_ops" not in keys:
        failures.append("kol_ops missing layer2_kol_ops")
    if "layer3_personal" not in keys:
        failures.append("kol_ops missing layer3_personal")
    print("  ✓ kol_ops: 3 templates")
    
    print("[7] template_keys_for_role(unknown) falls back...")
    keys = weekly_report_templates.template_keys_for_role("random_role")
    # Should at least have layer1 + layer3
    if "layer1_universal" not in keys:
        failures.append("unknown role missing layer1")
    if "layer3_personal" not in keys:
        failures.append("unknown role missing layer3")
    print("  ✓ unknown role gets layer1 + layer3")
    
    print("[8] build_prompt renders correctly...")
    prompt = weekly_report_templates.build_prompt(
        "layer2_leader",
        staff_name="Test Leader",
        staff_role="leader",
        period_start="2026-05-03",
        period_end="2026-05-10",
        data_context="No data context for smoke test.",
    )
    if "Test Leader" not in prompt:
        failures.append("staff_name missing in prompt")
    if "leader" not in prompt:
        failures.append("staff_role missing in prompt")
    if "2026-05-03" not in prompt:
        failures.append("period dates missing in prompt")
    if "anomaly_detection" not in prompt:
        failures.append("section descriptions missing in prompt")
    print("  ✓")
    
    print("[9] title_for renders correctly...")
    t1 = weekly_report_templates.title_for("layer1_universal", "2026-05-03", "2026-05-10")
    if "2026-05-03" not in t1:
        failures.append("title not rendered")
    if "V-KPI" not in t1:
        failures.append("title missing brand")
    
    t2 = weekly_report_templates.title_for("layer3_personal", "2026-05-03", "2026-05-10")
    if "你的本周" not in t2 and "Your" not in t2:
        failures.append("personal title not localized")
    print(f"  ✓ titles: '{t1[:40]}...' / '{t2[:40]}...'")
    
    print("[10] build_prompt unknown template raises...")
    try:
        weekly_report_templates.build_prompt(
            "fake_template",
            staff_name="x", staff_role="x",
            period_start="x", period_end="x",
            data_context="x",
        )
        failures.append("fake template should have raised")
    except ValueError:
        pass
    except Exception as e:
        failures.append(f"unexpected error type: {e}")
    print("  ✓ unknown raises ValueError")
    
    print("[11] All section_prompts mapped (no missing)...")
    section_keys_in_templates = set()
    for t in weekly_report_templates.TEMPLATES.values():
        for s in t["sections"]:
            section_keys_in_templates.add(s)
    
    missing_sections = section_keys_in_templates - set(weekly_report_templates.SECTION_PROMPTS.keys())
    if missing_sections:
        failures.append(f"Sections missing prompts: {missing_sections}")
    print(f"  ✓ {len(section_keys_in_templates)} sections, all mapped")
    
    # Final
    print()
    if failures:
        print(f"FAIL: {len(failures)} issues:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("VKPI_WEEKLY_REPORTS_OFFLINE_SMOKE_OK")


if __name__ == "__main__":
    main()
