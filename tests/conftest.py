import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
COLLECTION = ROOT.parent / "calhacks13-20261022-CRS-Sandbox.json"


@pytest.fixture(scope="session")
def personas():
    if not COLLECTION.exists():
        pytest.skip("Postman collection not present")
    from extract_personas import extract
    return extract(COLLECTION)
