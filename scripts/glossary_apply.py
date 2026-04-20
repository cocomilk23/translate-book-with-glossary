#!/usr/bin/env python3
"""Apply safe terminology replacements in translated markdown files.

This is optional. By default, it only replaces *forbid_zh* variants with
preferred_zh (when preferred_zh is non-empty).

It can also normalize source English terms into preferred Chinese translations
for direct-final delivery workflows.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


def split_list(s: str) -> list[str]:
    s = (s or "").strip()
    if not s:
        return []
    parts = re.split(r"[|;,]", s)
    return [p.strip() for p in parts if p.strip()]


def truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"true", "1", "yes", "y"}


def resolve_input_paths(temp_dir: Path, file_arg: str, files: list[str] | None, glob_pattern: str | None) -> list[Path]:
    if files:
        return [Path(path) if Path(path).is_absolute() else temp_dir / path for path in files]
    if glob_pattern:
        return sorted(temp_dir.glob(glob_pattern))
    return [temp_dir / file_arg]


def is_ascii_term(text: str) -> bool:
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def apply_replacement_rule(text: str, source: str, target: str, whole_word: bool) -> tuple[str, bool]:
    if whole_word:
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(source)}(?![A-Za-z0-9_])")
        new_text, count = pattern.subn(target, text)
        return new_text, count > 0
    if source in text:
        return text.replace(source, target), True
    return text, False


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply glossary replacements")
    ap.add_argument("--temp-dir", required=True)
    ap.add_argument("--file", default="output.md")
    ap.add_argument("--files", nargs="*", help="Optional explicit files to patch")
    ap.add_argument("--glob", help="Optional glob relative to temp dir, e.g. 'output_chunk*.md'")
    ap.add_argument("--in-place", action="store_true", help="Overwrite the input file instead of writing *.patched.md")
    ap.add_argument(
        "--replace-source-en",
        action="store_true",
        help="Also replace exact source English terms with preferred Chinese when keep_en is false",
    )
    args = ap.parse_args()

    temp_dir = Path(args.temp_dir)
    csv_path = temp_dir / "glossary.csv"

    if not csv_path.exists():
        raise SystemExit(f"Missing glossary: {csv_path}")

    input_paths = resolve_input_paths(temp_dir, args.file, args.files, args.glob)
    if not input_paths:
        raise SystemExit("No input files matched.")

    replacements: list[tuple[str, str, bool]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            source = (row.get("source") or "").strip()
            pref = (row.get("preferred_zh") or "").strip()
            keep_en = truthy(row.get("keep_en") or "")
            target = source if keep_en else pref
            if not target:
                continue
            for bad in split_list(row.get("forbid_zh") or ""):
                if bad and bad != target:
                    replacements.append((bad, target, False))
            if args.replace_source_en and source and pref and not keep_en and source != pref:
                replacements.append((source, pref, is_ascii_term(source)))

    # Replace longer strings first to avoid partial clobber
    replacements.sort(key=lambda x: len(x[0]), reverse=True)

    total_changed = 0
    for in_path in input_paths:
        if not in_path.exists():
            raise SystemExit(f"Missing translated file: {in_path}")

        text = in_path.read_text(encoding="utf-8", errors="ignore")
        count = 0
        for bad, pref, whole_word in replacements:
            text, changed = apply_replacement_rule(text, bad, pref, whole_word)
            if changed:
                count += 1

        if args.in_place:
            out_path = in_path
        else:
            out_path = in_path.with_suffix(in_path.suffix + ".patched")

        out_path.write_text(text, encoding="utf-8")
        total_changed += count
        print(f"Wrote: {out_path} (replacement_rules_applied={count})")

    print(f"Patched {len(input_paths)} file(s); replacement_rules_applied={total_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
