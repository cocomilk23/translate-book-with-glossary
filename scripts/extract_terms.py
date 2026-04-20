#!/usr/bin/env python3
"""Extract candidate terminology from one or more Markdown chunks.

Heuristic extractor intended for bootstrapping a per-book glossary.

Outputs:
- glossary.csv (draft entries)
- terminology_candidates.md (ranked list)

Design goals:
- be conservative (prefer missing a rare term over adding tons of noise)
- avoid code blocks/inline code/URLs

This is not NLP-heavy by design; it should run everywhere without extra deps.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`]+`")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
URL_RE = re.compile(r"https?://\S+")
HTML_TAG_RE = re.compile(r"<[^>]+>")

# Candidates:
ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
CAMEL_RE = re.compile(r"\b[A-Za-z]+[a-z][A-Z][A-Za-z0-9]*\b")
SNAKE_RE = re.compile(r"\b[a-z]+(?:_[a-z0-9]+){1,}\b")
KEBAB_RE = re.compile(r"\b[a-z]+(?:-[a-z0-9]+){1,}\b")
TITLE_PHRASE_RE = re.compile(
    r"\b(?:[A-Z][a-z0-9]+)(?:\s+(?:[A-Z][a-z0-9]+|of|and|to|for|in|on|with|via|the|a|an)){1,4}\b"
)

STOP_PHRASES = {
    "Table of Contents",
    "Figure",
    "Chapter",
    "Part",
    "Section",
}

STOP_SINGLE_WORDS = {
    "INTRODUCTION",
    "THANKS",
    "PRELUDE",
    "POSTLUDE",
    "INDEX",
    "CONTENTS",
    "CHAPTER",
    "PART",
    "SECTION",
    "CIP",
    "ISBN",
}

CONNECTOR_WORDS = {"of", "and", "to", "for", "in", "on", "with", "via", "the", "a", "an"}
WEAK_START_WORDS = {"The", "A", "An", "To", "This", "That", "These", "Those"}


@dataclass(frozen=True)
class Candidate:
    term: str
    kind: str


def clean_markdown(text: str) -> str:
    text = FENCE_RE.sub("\n", text)
    text = INLINE_CODE_RE.sub(" ", text)

    # Replace links with visible text only
    text = MD_LINK_RE.sub(lambda m: m.group(1), text)
    text = URL_RE.sub(" ", text)
    text = HTML_TAG_RE.sub(" ", text)

    cleaned_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append(line)
            continue

        # Remove typical page-number lines.
        if re.fullmatch(r"\d+", stripped):
            continue

        # Drop repeated cover/header lines like "IS IT OKAY TO CALL GOD MOTHER?".
        words = re.findall(r"[A-Za-z]+", stripped)
        if len(words) >= 2 and all(word.isupper() for word in words):
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def is_title_phrase_candidate(term: str) -> bool:
    words = term.split()
    if len(words) < 2:
        return False
    if words[0] in WEAK_START_WORDS:
        return False
    content_words = [word for word in words if word.lower() not in CONNECTOR_WORDS]
    if len(content_words) < 2:
        return False
    if words[-1].lower() in CONNECTOR_WORDS:
        return False
    return True


def iter_candidates(text: str) -> Iterable[Candidate]:
    # Title case multi-word phrases first (captures more meaningful terms)
    for m in TITLE_PHRASE_RE.finditer(text):
        term = m.group(0).strip()
        if term in STOP_PHRASES:
            continue
        if not is_title_phrase_candidate(term):
            continue
        if len(term) > 60:
            continue
        yield Candidate(term=term, kind="TitlePhrase")

    for rx, kind in [
        (ACRONYM_RE, "Acronym"),
        (CAMEL_RE, "CamelCase"),
        (SNAKE_RE, "snake_case"),
        (KEBAB_RE, "kebab-case"),
    ]:
        for m in rx.finditer(text):
            term = m.group(0).strip()
            if len(term) > 50:
                continue
            # Avoid common acronyms that are formatting noise
            if kind == "Acronym" and term in {"HTML", "HTTP", "HTTPS", "PDF", "EPUB", "DOCX"}:
                continue
            if kind == "Acronym" and term in STOP_SINGLE_WORDS:
                continue
            yield Candidate(term=term, kind=kind)


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract terminology candidates from markdown chunks")
    ap.add_argument("inputs", nargs="+", help="One or more markdown files (chunk*.md)")
    ap.add_argument("--out-dir", required=True, help="Temp dir to write glossary.csv and report")
    ap.add_argument("--min-count", type=int, default=2, help="Minimum frequency to include (default: 2)")
    ap.add_argument("--top", type=int, default=300, help="Max candidates to output (default: 300)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = []
    for p in args.inputs:
        text = Path(p).read_text(encoding="utf-8", errors="ignore")
        text = clean_markdown(text)
        raw.append(text)

    all_text = "\n".join(raw)

    counter = Counter()
    kind_map: dict[str, str] = {}
    for c in iter_candidates(all_text):
        counter[c.term] += 1
        kind_map.setdefault(c.term, c.kind)

    # Filter and sort
    items = [(t, n) for t, n in counter.items() if n >= args.min_count]
    items.sort(key=lambda x: (-x[1], x[0].lower()))
    items = items[: args.top]

    # Write CSV (draft glossary)
    csv_path = out_dir / "glossary.csv"
    new_file = not csv_path.exists()

    existing_terms = set()
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                existing_terms.add((row.get("source") or "").strip())

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

    rows = []
    for term, n in items:
        if term in existing_terms:
            continue
        rows.append(
            {
                "source": term,
                "preferred_zh": "",
                "allowed_zh": "",
                "forbid_zh": "",
                "keep_en": "",
                "definition": "",
                "rule": "",
                "first_seen": "bootstrap",
                "status": "draft",
                "kind": kind_map.get(term, ""),
                "count": str(n),
            }
        )

    if new_file:
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
    else:
        # append
        with csv_path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            for row in rows:
                w.writerow(row)

    # Write ranked report
    md_path = out_dir / "terminology_candidates.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# Terminology Candidates (bootstrap)\n\n")
        f.write(f"Inputs: {len(args.inputs)} file(s)\n\n")
        f.write("| Term | Count | Kind |\n|---|---:|---|\n")
        for term, n in items:
            f.write(f"| {term} | {n} | {kind_map.get(term,'')} |\n")

    print(f"Wrote: {csv_path}")
    print(f"Wrote: {md_path}")
    print(f"Added {len(rows)} new draft terms (min_count={args.min_count}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
