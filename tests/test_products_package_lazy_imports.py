from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_product_alias_leaf_does_not_eager_load_heavy_siblings() -> None:
    code = """
import json
import sys
from app.domains.products.product_aliases import normalize_alias
print(json.dumps({
    'normalized': normalize_alias('26 mm F/1.2 EVO'),
    'campaign_loaded': 'app.domains.products.product_campaign_card' in sys.modules,
    'fit_monitor_loaded': 'app.domains.products.product_fit_monitor' in sys.modules,
    'specs_loaded': 'app.domains.products.product_specs' in sys.modules,
}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])

    assert payload == {
        "normalized": "26mm f12 evo",
        "campaign_loaded": False,
        "fit_monitor_loaded": False,
        "specs_loaded": False,
    }
