"""Score out/extracted.jsonl against the ground truth in tests/fixtures/extractions.json.

Run after a real `uv run docpipe run` to measure the model's field-level accuracy
on the sample set:  uv run python scripts/eval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("vendor", "invoice_number", "date", "due_date", "subtotal", "tax", "total", "po_number")


def same(a: object, b: object) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(a - b) < 0.01
    if isinstance(a, str) and isinstance(b, str):
        return " ".join(a.lower().split()) == " ".join(b.lower().split())
    return a == b


def main(extracted: Path, truth_path: Path) -> int:
    truth = json.loads(truth_path.read_text())
    rows = {r["source_file"]: r for r in map(json.loads, extracted.read_text().splitlines())}
    doc_hits = field_hits = field_total = 0
    for name, expected in truth.items():
        got = rows.get(name)
        if got is None:
            print(f"MISSING  {name}")
            continue
        doc_ok = got["doc_type"] == expected["doc_type"]
        doc_hits += doc_ok
        if expected["invoice"] is None:
            print(f"{'ok ' if doc_ok else 'BAD'}  {name}: doc_type={got['doc_type']}")
            continue
        misses = []
        for f in FIELDS:
            field_total += 1
            exp, act = expected["invoice"][f], (got["invoice"] or {}).get(f)
            if same(exp, act):
                field_hits += 1
            else:
                misses.append(f"{f}: expected {exp!r}, got {act!r}")
        status = "ok " if doc_ok and not misses else "BAD"
        print(f"{status}  {name}" + (" | " + "; ".join(misses) if misses else ""))
    print()
    print(f"Classification: {doc_hits}/{len(truth)}")
    pct = 100 * field_hits / max(field_total, 1)
    print(f"Fields:         {field_hits}/{field_total} ({pct:.1f}%)")
    return 0


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out" / "extracted.jsonl"
    sys.exit(main(out, ROOT / "tests" / "fixtures" / "extractions.json"))
