"""Extract the 20 test personas from the CRS Postman collection into fixtures/raw/personas.json (gitignored)."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COLLECTION = ROOT.parent / "calhacks13-20261022-CRS-Sandbox.json"
OUT = ROOT / "fixtures" / "raw" / "personas.json"

ENDPOINTS = {
    "identity": "identity-manager-verification/basic",
    "reverse_phone": "accurate-append/reverse",
    "fraud_finder": "fraud-finder/fraud-finder",
}


def _walk(items, path=()):
    for item in items:
        if "item" in item:
            yield from _walk(item["item"], path + (item["name"],))
        else:
            yield path, item


def _url(request) -> str:
    url = request["url"]
    return url if isinstance(url, str) else url.get("raw", "")


def extract(collection_path: Path = DEFAULT_COLLECTION) -> dict:
    collection = json.loads(collection_path.read_text(encoding="utf-8"))
    personas: dict[str, dict] = {}
    for path, item in _walk(collection["item"]):
        if not path or path[0] != "Test personas" or len(path) < 2:
            continue
        m = re.match(r"(P\d{2})\b", path[1])
        if not m:
            continue
        url = _url(item["request"])
        for key, frag in ENDPOINTS.items():
            if url.endswith(frag):
                body = json.loads(item["request"]["body"]["raw"])
                personas.setdefault(m.group(1), {"id": m.group(1)})[key] = body
    return dict(sorted(personas.items()))


def main() -> None:
    collection = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_COLLECTION
    personas = extract(collection)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(personas, indent=2), encoding="utf-8")
    complete = sum(1 for p in personas.values() if all(k in p for k in ENDPOINTS))
    print(f"personas: {len(personas)}, with all 3 request bodies: {complete}")


if __name__ == "__main__":
    main()
