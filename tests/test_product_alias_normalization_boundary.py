from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from app.domains.products import product_aliases
from app.shared.product_alias_normalization import APERTURE_REPLACEMENTS, normalize_product_alias


ROOT = Path(__file__).resolve().parents[1]
LEGACY_CORPUS_SHA256 = "d497c9ec604a2f7b2c4173b32870741841d0d1b07b4cc94438e6a52cf1a91775"


def _normalization_corpus() -> list[str]:
    brands = ["Viltrox", "VILTROX", "Sirui", "Sigma", "Sony", "Tamron", "富士", ""]
    focals = ["16 mm", "23mm", "27.5 mm", "35_mm", "50-mm", "75MM", "90 mm", "f = 135 mm"]
    apertures = ["F/1.0", "f 1.2", "F1.4", "f/1.7", "f1.8", "F2.0", "f 2.8", "F4.0"]
    mounts = ["APS-C E-mount", "Z_mount", "X / Mount", "Full- Frame"]
    return [
        f"{brand}  {focal} / {aperture} -- {mount}"
        for brand in brands
        for focal in focals
        for aperture in apertures
        for mount in mounts
    ]


def test_shared_normalizer_matches_2048_case_legacy_characterization() -> None:
    corpus = _normalization_corpus()
    shared_rows = [[value, normalize_product_alias(value)] for value in corpus]
    public_rows = [[value, product_aliases.normalize_alias(value)] for value in corpus]

    assert len(corpus) == 2048
    assert public_rows == shared_rows
    assert hashlib.sha256(
        json.dumps(shared_rows, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest() == LEGACY_CORPUS_SHA256


def test_product_alias_public_import_contract_is_preserved() -> None:
    from app.domains.products.product_aliases import normalize_alias

    assert normalize_alias("Viltrox 26 mm F/1.2 EVO") == "viltrox 26mm f12 evo"
    assert product_aliases.APERTURE_REPLACEMENTS == APERTURE_REPLACEMENTS


def test_importing_kol_sku_fit_does_not_load_products_domain() -> None:
    code = """
import importlib
import json
import sys

sku_fit = importlib.import_module('app.domains.kol.sku_fit')
print(json.dumps({
    'normalized': sku_fit._norm('Viltrox 26 mm F/1.2 EVO'),
    'products_package_loaded': 'app.domains.products' in sys.modules,
    'product_aliases_loaded': 'app.domains.products.product_aliases' in sys.modules,
    'shared_normalizer_loaded': 'app.shared.product_alias_normalization' in sys.modules,
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

    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "normalized": "viltrox 26mm f12 evo",
        "products_package_loaded": False,
        "product_aliases_loaded": False,
        "shared_normalizer_loaded": True,
    }
