#!/usr/bin/env python3
"""Check terminology consistency against a per-book glossary.csv.

This script is intentionally conservative: it does not try to "infer" whether
an English term was translated; instead it:

1) Flags disallowed Chinese variants if they appear (forbid_zh)
2) Reports batch terminology decisions collected in sidecar files
3) Reports unresolved draft glossary rows that still need decisions
4) Optionally flags missing preferred terms when allowed_zh variants appear

Usage:
  python3 glossary_check.py --temp-dir <book_temp_dir> [--file output.md]

Outputs:
  <temp-dir>/terminology_report.md
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path


def split_list(s: str) -> list[str]:
    s = (s or "").strip()
    if not s:
        return []
    # allow separators: | ; ,
    parts = re.split(r"[|;,]", s)
    return [p.strip() for p in parts if p.strip()]


def load_glossary(csv_path: Path) -> list[dict]:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        rows = []
        for row in r:
            src = (row.get("source") or "").strip()
            if not src:
                continue
            rows.append(row)
        return rows


def normalize_term(t: str) -> str:
    t = t.strip()
    t = re.sub(r"\s+", " ", t)
    return t


def main() -> int:
    ap = argparse.ArgumentParser(description="Terminology QA against glossary.csv")
    ap.add_argument("--temp-dir", required=True)
    ap.add_argument("--file", default="output.md", help="Translated markdown to check (default: output.md)")
    args = ap.parse_args()

    temp_dir = Path(args.temp_dir)
    csv_path = temp_dir / "glossary.csv"
    in_path = temp_dir / args.file
    out_path = temp_dir / "terminology_report.md"

    if not csv_path.exists():
        raise SystemExit(f"Missing glossary: {csv_path}")
    if not in_path.exists():
        raise SystemExit(f"Missing translated file: {in_path}")

    text = in_path.read_text(encoding="utf-8", errors="ignore")
    rows = load_glossary(csv_path)

    issues = []

    # 1) Sidecar terminology decisions collected during chunk translation
    decision_files = sorted(temp_dir.glob("term_decisions_chunk*.csv"))
    decision_counter = Counter()
    for fp in decision_files:
        with fp.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                source = normalize_term(row.get("source", ""))
                if source:
                    decision_counter[source] += 1
    if decision_counter:
        issues.append(
            (
                "Batch terminology decisions",
                f"Found {sum(decision_counter.values())} decision row(s) across {len(decision_files)} sidecar file(s).",
            )
        )

    # 2) Unresolved draft glossary rows without a preferred translation / keep_en decision
    unresolved_drafts = []
    for row in rows:
        status = (row.get("status") or "").strip().lower()
        preferred = (row.get("preferred_zh") or "").strip()
        keep_en = (row.get("keep_en") or "").strip().lower()
        if status == "draft" and not preferred and keep_en not in {"true", "1", "yes"}:
            unresolved_drafts.append(row)
    if unresolved_drafts:
        issues.append(
            (
                "Unresolved draft glossary rows",
                f"Found {len(unresolved_drafts)} draft term(s) without preferred_zh or keep_en=true.",
            )
        )

    # 3) forbid variants
    forbid_hits = []
    for row in rows:
        preferred = (row.get("preferred_zh") or "").strip()
        forbid = split_list(row.get("forbid_zh") or "")
        for bad in forbid:
            if bad and bad in text:
                forbid_hits.append((row.get("source") or "", bad, preferred))

    if forbid_hits:
        issues.append(("Forbidden variants", f"Found {len(forbid_hits)} forbidden term variants."))

    # 4) allowed present but preferred missing (weak heuristic)
    drift_hits = []
    for row in rows:
        preferred = (row.get("preferred_zh") or "").strip()
        if not preferred:
            continue
        allowed = split_list(row.get("allowed_zh") or "")
        if not allowed:
            continue
        pref_in = preferred in text
        if pref_in:
            continue
        for alt in allowed:
            if alt and alt in text:
                drift_hits.append((row.get("source") or "", alt, preferred))

    if drift_hits:
        issues.append(("Preferred missing", f"Found {len(drift_hits)} places where an allowed variant appears but preferred_zh does not."))

    with out_path.open("w", encoding="utf-8") as f:
        f.write("# Terminology QA Report\n\n")
        f.write(f"Input: `{in_path.name}`\n\n")
        f.write(f"Glossary: `{csv_path.name}` (rows: {len(rows)})\n\n")

        if not issues:
            f.write("✅ No terminology issues detected by conservative checks.\n")
        else:
            f.write("## Summary\n\n")
            for title, desc in issues:
                f.write(f"- **{title}:** {desc}\n")

        if decision_counter:
            f.write("\n## Batch terminology decisions\n\n")
            f.write("| term | count |\n|---|---:|\n")
            for term, count in decision_counter.most_common(200):
                f.write(f"| {term} | {count} |\n")
            if len(decision_counter) > 200:
                f.write(f"\n- ... ({len(decision_counter)-200} more unique decided terms)\n")

        if unresolved_drafts:
            f.write("\n## Unresolved draft glossary rows\n\n")
            f.write("| source | first_seen | count |\n|---|---|---:|\n")
            for row in unresolved_drafts[:200]:
                src = (row.get("source") or "").strip()
                first_seen = (row.get("first_seen") or "").strip()
                count = (row.get("count") or "").strip()
                f.write(f"| {src} | {first_seen} | {count or ''} |\n")
            if len(unresolved_drafts) > 200:
                f.write(f"\n- ... ({len(unresolved_drafts)-200} more unresolved draft rows)\n")

        if forbid_hits:
            f.write("\n## Forbidden variants found\n\n")
            f.write("| source | found | preferred_zh |\n|---|---|---|\n")
            for src, bad, pref in forbid_hits:
                f.write(f"| {src} | {bad} | {pref} |\n")

        if drift_hits:
            f.write("\n## Allowed variant used but preferred missing (heuristic)\n\n")
            f.write("| source | used | preferred_zh |\n|---|---|---|\n")
            for src, used, pref in drift_hits:
                f.write(f"| {src} | {used} | {pref} |\n")

    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
