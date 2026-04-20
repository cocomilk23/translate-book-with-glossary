#!/usr/bin/env python3
"""Collect per-chunk terminology candidates and update glossary incrementally.

Sub-agents write candidate terms (one per line) to:
    <temp_dir>/candidates_chunkNNNN.txt

This script:
  1) merges all candidate files into `terminology_candidates_incremental.md`
  2) appends missing terms into `glossary.csv` as status=draft

This supports an incremental terminology learning loop.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path


def normalize_term(t: str) -> str:
    t = t.strip()
    t = re.sub(r"\s+", " ", t)
    return t


def parse_candidate_line(line: str) -> str:
    # Legacy files sometimes used: "source<TAB>note". Only the source belongs in glossary.csv.
    source = line.split("\t", 1)[0]
    return normalize_term(source)


def load_existing_sources(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        out = set()
        for row in r:
            src = normalize_term((row.get("source") or ""))
            if src:
                out.add(src)
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect new-term candidates and append to glossary.csv")
    ap.add_argument("--temp-dir", required=True)
    ap.add_argument("--min-count", type=int, default=1)
    args = ap.parse_args()

    temp_dir = Path(args.temp_dir)
    csv_path = temp_dir / "glossary.csv"

    candidate_files = sorted(temp_dir.glob("candidates_chunk*.txt"))
    counter = Counter()
    for fp in candidate_files:
        for line in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = parse_candidate_line(line)
            if not t:
                continue
            if len(t) > 80:
                continue
            counter[t] += 1

    existing = load_existing_sources(csv_path)
    new_terms = [(t, n) for t, n in counter.items() if n >= args.min_count and t not in existing]
    new_terms.sort(key=lambda x: (-x[1], x[0].lower()))

    fieldnames = [
        "source",
        "preferred_zh",
        "allowed_zh",
        "forbid_zh",
        "keep_en",
        "definition",
        "rule",
        "first_seen",
        "status",
        "kind",
        "count",
    ]

    if not csv_path.exists():
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()

    with csv_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        for term, n in new_terms:
            w.writerow(
                {
                    "source": term,
                    "preferred_zh": "",
                    "allowed_zh": "",
                    "forbid_zh": "",
                    "keep_en": "",
                    "definition": "",
                    "rule": "",
                    "first_seen": "incremental",
                    "status": "draft",
                    "kind": "",
                    "count": str(n),
                }
            )

    report_path = temp_dir / "terminology_candidates_incremental.md"
    with report_path.open("w", encoding="utf-8") as f:
        f.write("# Terminology Candidates (incremental)\n\n")
        f.write(f"Candidate files: {len(candidate_files)}\n\n")
        f.write("| Term | Count |\n|---|---:|\n")
        for term, n in sorted(counter.items(), key=lambda x: (-x[1], x[0].lower())):
            f.write(f"| {term} | {n} |\n")

    print(f"Wrote: {report_path}")
    print(f"Glossary appended: {len(new_terms)} new draft term(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
