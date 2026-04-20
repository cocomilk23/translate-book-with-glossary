#!/usr/bin/env python3
"""Reconcile per-chunk terminology decisions into a locked glossary.

Each translation sub-agent can write a CSV sidecar file like:
    <temp_dir>/term_decisions_chunkNNNN.csv

Expected columns:
- source
- proposed_zh
- keep_en
- confidence
- chunk_id
- source_sentence
- reason

This script aggregates all decisions in a batch, chooses one canonical decision
per source term, updates glossary.csv, and writes a markdown report.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


FIELDNAMES = [
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


def normalize_term(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def split_list(text: str) -> list[str]:
    text = normalize_term(text)
    if not text:
        return []
    parts = re.split(r"[|;,]", text)
    return [normalize_term(part) for part in parts if normalize_term(part)]


def join_list(values: list[str]) -> str:
    seen = set()
    ordered = []
    for value in values:
        norm = normalize_term(value)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        ordered.append(norm)
    return " | ".join(ordered)


def truthy(value: str) -> bool:
    return normalize_term(value).lower() in {"true", "1", "yes", "y"}


def parse_confidence(value: str) -> float:
    text = normalize_term(value)
    if not text:
        return 0.5
    try:
        num = float(text)
    except ValueError:
        return 0.5
    if num > 1.0:
        num = num / 100.0
    if num < 0.0:
        return 0.0
    if num > 1.0:
        return 1.0
    return num


def load_glossary(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            clean = {name: row.get(name, "") for name in FIELDNAMES}
            if normalize_term(clean.get("source", "")):
                rows.append(clean)
        return rows


def write_glossary(csv_path: Path, rows: list[dict]) -> None:
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in FIELDNAMES})


def load_decision_rows(files: list[Path]) -> list[dict]:
    rows = []
    for path in files:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                source = normalize_term(row.get("source", ""))
                if not source:
                    continue
                proposed = normalize_term(row.get("proposed_zh", ""))
                keep_en = truthy(row.get("keep_en", ""))
                candidate = source if keep_en else proposed
                if not candidate:
                    continue
                rows.append(
                    {
                        "source": source,
                        "candidate": candidate,
                        "proposed_zh": proposed,
                        "keep_en": keep_en,
                        "confidence": parse_confidence(row.get("confidence", "")),
                        "chunk_id": normalize_term(row.get("chunk_id", "")) or path.stem.replace("term_decisions_", ""),
                        "source_sentence": normalize_term(row.get("source_sentence", "")),
                        "reason": normalize_term(row.get("reason", "")),
                        "file": path.name,
                    }
                )
    return rows


def choose_winner(choices: dict[str, dict], source: str) -> tuple[str, bool]:
    def rank(item: tuple[str, dict]) -> tuple[float, int, int, int, str]:
        candidate, meta = item
        # Prefer non-English translations on exact ties, but only on exact ties.
        translated_pref = 1 if candidate != source else 0
        return (
            meta["score"],
            meta["count"],
            translated_pref,
            -len(candidate),
            candidate,
        )

    winner, meta = max(choices.items(), key=rank)
    return winner, bool(meta["keep_en_votes"])


def update_existing_row(row: dict, winner: str, winner_keep_en: bool, alternatives: list[str], total_count: int) -> tuple[dict, str]:
    source = normalize_term(row.get("source", ""))
    existing_keep_en = truthy(row.get("keep_en", ""))
    existing_preferred = normalize_term(row.get("preferred_zh", ""))

    if existing_keep_en or existing_preferred:
        final_target = source if existing_keep_en else existing_preferred
        decision_mode = "kept_existing"
    else:
        final_target = source if winner_keep_en else winner
        row["preferred_zh"] = "" if winner_keep_en else winner
        row["keep_en"] = "true" if winner_keep_en else "false"
        row["status"] = "locked"
        if not normalize_term(row.get("first_seen", "")):
            row["first_seen"] = "batch_reconcile"
        if not normalize_term(row.get("kind", "")):
            row["kind"] = "decision"
        decision_mode = "locked_new"

    forbid = split_list(row.get("forbid_zh", ""))
    for alt in alternatives:
        if alt != final_target and alt != source:
            forbid.append(alt)
    row["forbid_zh"] = join_list(forbid)

    existing_count = normalize_term(row.get("count", ""))
    try:
        base_count = int(existing_count) if existing_count else 0
    except ValueError:
        base_count = 0
    row["count"] = str(max(base_count, total_count))
    row["status"] = normalize_term(row.get("status", "")) or "locked"
    return row, decision_mode


def new_glossary_row(source: str, winner: str, winner_keep_en: bool, alternatives: list[str], total_count: int) -> dict:
    forbid = [alt for alt in alternatives if alt != source and alt != winner]
    return {
        "source": source,
        "preferred_zh": "" if winner_keep_en else winner,
        "allowed_zh": "",
        "forbid_zh": join_list(forbid),
        "keep_en": "true" if winner_keep_en else "false",
        "definition": "",
        "rule": "",
        "first_seen": "batch_reconcile",
        "status": "locked",
        "kind": "decision",
        "count": str(total_count),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconcile term decisions into glossary.csv")
    ap.add_argument("--temp-dir", required=True)
    ap.add_argument("--decision-files", nargs="*", help="Optional explicit decision CSV files to reconcile")
    ap.add_argument("--glob", default="term_decisions_chunk*.csv", help="Glob to use when --decision-files is not passed")
    ap.add_argument("--report", default="terminology_batch_report.md", help="Report filename inside temp dir")
    args = ap.parse_args()

    temp_dir = Path(args.temp_dir)
    csv_path = temp_dir / "glossary.csv"
    report_path = temp_dir / args.report

    if args.decision_files:
        decision_files = [Path(path) for path in args.decision_files]
    else:
        decision_files = sorted(temp_dir.glob(args.glob))

    if not decision_files:
        print("No term decision files found; nothing to reconcile.")
        return 0

    glossary_rows = load_glossary(csv_path)
    row_map = {normalize_term(row["source"]): row for row in glossary_rows}
    decision_rows = load_decision_rows(decision_files)

    if not decision_rows:
        print("Decision files contained no valid terminology rows; nothing to reconcile.")
        return 0

    grouped: dict[str, dict] = {}
    for row in decision_rows:
        source = row["source"]
        grouped.setdefault(
            source,
            {
                "choices": defaultdict(lambda: {"score": 0.0, "count": 0, "keep_en_votes": 0, "evidence": []}),
                "chunks": set(),
            },
        )
        slot = grouped[source]["choices"][row["candidate"]]
        slot["score"] += row["confidence"]
        slot["count"] += 1
        slot["keep_en_votes"] += 1 if row["keep_en"] else 0
        slot["evidence"].append(row)
        grouped[source]["chunks"].add(row["chunk_id"])

    report_rows = []
    for source in sorted(grouped):
        choices = grouped[source]["choices"]
        winner, winner_keep_en = choose_winner(choices, source)
        alternatives = [candidate for candidate in choices if candidate != winner]
        total_count = sum(meta["count"] for meta in choices.values())

        if source in row_map:
            updated_row, mode = update_existing_row(row_map[source], winner, winner_keep_en, alternatives, total_count)
            row_map[source] = updated_row
        else:
            updated_row = new_glossary_row(source, winner, winner_keep_en, alternatives, total_count)
            glossary_rows.append(updated_row)
            row_map[source] = updated_row
            mode = "locked_new"

        report_rows.append(
            {
                "source": source,
                "winner": source if truthy(updated_row.get("keep_en", "")) else normalize_term(updated_row.get("preferred_zh", "")),
                "mode": mode,
                "choices": choices,
                "chunks": sorted(grouped[source]["chunks"]),
            }
        )

    glossary_rows.sort(key=lambda row: normalize_term(row.get("source", "")).lower())
    write_glossary(csv_path, glossary_rows)

    with report_path.open("w", encoding="utf-8") as f:
        f.write("# Terminology Batch Reconciliation Report\n\n")
        f.write(f"Decision files: {len(decision_files)}\n\n")
        f.write("| source | chosen | mode | variants | chunks |\n|---|---|---|---:|---|\n")
        for row in report_rows:
            f.write(
                f"| {row['source']} | {row['winner']} | {row['mode']} | {len(row['choices'])} | {', '.join(row['chunks'])} |\n"
            )

        for row in report_rows:
            f.write(f"\n## {row['source']}\n\n")
            f.write("| candidate | score | count | keep_en_votes |\n|---|---:|---:|---:|\n")
            sorted_choices = sorted(
                row["choices"].items(),
                key=lambda item: (-item[1]["score"], -item[1]["count"], item[0]),
            )
            for candidate, meta in sorted_choices:
                f.write(
                    f"| {candidate} | {meta['score']:.2f} | {meta['count']} | {meta['keep_en_votes']} |\n"
                )
            evidence = []
            for _, meta in sorted_choices:
                evidence.extend(meta["evidence"])
            if evidence:
                f.write("\nSample evidence:\n\n")
                for item in evidence[:5]:
                    sentence = item["source_sentence"] or "(no sentence provided)"
                    f.write(
                        f"- `{item['chunk_id']}` -> `{item['candidate']}`"
                        f" (confidence={item['confidence']:.2f})"
                        f": {sentence}\n"
                    )

    print(f"Wrote: {csv_path}")
    print(f"Wrote: {report_path}")
    print(f"Reconciled {len(report_rows)} source term(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
