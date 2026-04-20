---
name: translate-book-with-glossary
description: Translate books (PDF/DOCX/EPUB) into any language using parallel sub-agents, while building and enforcing a per-book terminology glossary (术语表). Bootstraps glossary from the first N chunks (default 4), requires per-chunk terminology decisions for new terms, reconciles those decisions after each parallel batch, rewrites translated chunks to the batch-approved terminology, runs terminology QA, and by default performs a single serial auto-normalization pass on the merged output to reduce term drift. Outputs glossary.csv + QA reports alongside the translated book.
---

# Translate Book (with Glossary)

This skill is a strict upgrade of `translate-book`: it keeps the same robust chunking + parallel translation + merge/build pipeline, **and adds a per-book glossary loop** so terminology stays consistent across chunks.

## Outputs (in the book temp dir)

- `glossary.csv` — canonical terminology mapping (machine-readable)
- `terminology_candidates.md` — bootstrap candidate list from first N chunks
- `term_decisions_chunkNNNN.csv` — per-chunk terminology decisions for new terms
- `terminology_batch_report.md` — batch reconciliation report for newly decided terms
- `terminology_report.md` — QA report on the merged translation
- `output.md` / `book.docx` / `book.epub` / `book.pdf` — translated book artifacts

## Parameters to collect

- `file_path` (required): input PDF/DOCX/EPUB
- `target_lang` (default: `zh`)
- `concurrency` (default: `8`)
- `bootstrap_chunks` (default: `4`) — how many initial chunks to mine terms from
- `auto_normalize` (default: `true`) — after merge, run a **single serial** terminology normalization pass on `output.md` using the glossary rules (no parallel; avoid token waste)
- `custom_instructions` (optional)

If `file_path` is missing, ask the user.

## Workflow

### 1) Convert to markdown chunks

```bash
python3 {baseDir}/scripts/convert.py "<file_path>" --olang "<target_lang>"
```

This creates a temp directory: `{filename}_temp/` containing `chunk0001.md ...` and `manifest.json`.

### 2) Bootstrap glossary from first N chunks (default N=4)

1) Glob the first N chunk paths.
2) Run term extraction:

```bash
python3 {baseDir}/scripts/extract_terms.py {temp_dir}/chunk0001.md {temp_dir}/chunk0002.md ... \
  --out-dir "{temp_dir}" \
  --min-count 2
```

This creates/updates:
- `{temp_dir}/glossary.csv` (new rows appended as `status=draft`)
- `{temp_dir}/terminology_candidates.md`

**Important:** `glossary.csv` is the source of truth. For high-quality results, lock the top terms by filling:
- `preferred_zh`
- `keep_en` (true/false)
- `rule` (e.g., first occurrence: 中文（English）)
- optionally `forbid_zh` (variants that should be replaced)

### 3) Translate chunks in parallel by batch, BUT enforce glossary and make explicit new-term decisions

Discover chunks to translate (source chunk exists but no `output_chunkNNNN.md`).

Process chunks in batches of size `concurrency` (default 8). For each batch, spawn one sub-agent per chunk. Each sub-agent MUST:

1. Read `{temp_dir}/glossary.csv`
2. Read its source chunk `chunkNNNN.md`
3. Translate to target language while enforcing terminology:
   - If `preferred_zh` is provided for a `source` term, use it consistently.
   - If `keep_en` is true, keep the English term (optionally add Chinese explanation on first occurrence per `rule`).
   - If an important new term appears that is not in the glossary, the sub-agent MUST make a best-effort terminology decision immediately so the output remains publication-ready:
     - choose `proposed_zh`, OR
     - set `keep_en=true` for proper nouns / model names / benchmark names / product names that should remain in English
   - The translated正文 should directly use that decision. Do not leave unresolved placeholders or inline markers in the text.
4. Write the translated content to `output_chunkNNNN.md`.
5. Write terminology decisions for any newly encountered terms to `{temp_dir}/term_decisions_chunkNNNN.csv` with header:

```csv
source,proposed_zh,keep_en,confidence,chunk_id,source_sentence,reason
```

If no new terms were encountered, still write the CSV with only the header row.

**Sub-agent translation prompt (append to the base prompt):**

- 术语表强约束：翻译前读取 `{temp_dir}/glossary.csv`。
- 命中 `source` 术语时，必须使用对应 `preferred_zh`。
- 新术语：必须先做术语判断，再写正文。
- 如果判断应译为中文：正文直接使用 `proposed_zh`，并把该决策写入 `{temp_dir}/term_decisions_chunkNNNN.csv`。
- 如果判断应保留英文：正文直接保留英文，并在 CSV 中写 `keep_en=true`。
- `confidence` 用 0-1 小数；`source_sentence` 记录原文句子；`reason` 简述原因。

Keep all the original `translate-book` markdown preservation rules.

### 4) Verify completeness, retry missing chunks, then reconcile terminology decisions for the batch

Same as `translate-book` for completeness/retry.

After each translated batch, reconcile `term_decisions_chunkNNNN.csv` from that batch:

```bash
python3 {baseDir}/scripts/reconcile_terms.py --temp-dir "{temp_dir}" \
  --decision-files "{temp_dir}/term_decisions_chunk0001.csv" "{temp_dir}/term_decisions_chunk0002.csv" ...
```

This writes:
- `{temp_dir}/terminology_batch_report.md`
- updates `{temp_dir}/glossary.csv` by locking the batch-approved terminology

Then rewrite the translated chunk outputs in this batch to the approved terminology:

```bash
python3 {baseDir}/scripts/glossary_apply.py --temp-dir "{temp_dir}" \
  --files "{temp_dir}/output_chunk0001.md" "{temp_dir}/output_chunk0002.md" ... \
  --replace-source-en --in-place
```

This is the incremental alignment loop: each batch can propose new terminology independently, then a single reconciliation pass chooses one translation, updates the glossary, and normalizes the already translated chunk files before the next batch starts.

### 5) Merge and build the book

Translate the title (for Chinese wrap with `《》`), then:

```bash
python3 {baseDir}/scripts/merge_and_build.py --temp-dir "{temp_dir}" --title "<translated_title>" --cleanup
```

### 6) Terminology QA + auto normalization (default ON)

First run conservative checks:

```bash
python3 {baseDir}/scripts/glossary_check.py --temp-dir "{temp_dir}" --file output.md
```

This writes `{temp_dir}/terminology_report.md`.

#### Auto normalization (serial, glossary-driven)

If `auto_normalize=true`, perform a **single serial** normalization pass on the merged `output.md`.

Method (LLM-driven, but still controlled by glossary):
- Read `{temp_dir}/glossary.csv` and `{temp_dir}/terminology_report.md`.
- Edit `{temp_dir}/output.md` **in place** to enforce:
  - `preferred_zh` when present
  - replace any `forbid_zh` occurrences with `preferred_zh`
  - review any remaining low-confidence / conflicting decisions from `terminology_batch_report.md`; when the glossary already has a clear winner, normalize the merged output accordingly
- Guardrails:
  - Do not change Markdown structure
  - Do not change links/URLs/paths
  - Do not rename proper nouns / model names / benchmark names unless `glossary.csv` explicitly says so
  - Do not invent new terminology that is not in the glossary

**Why serial:** one agent editing one file is cheaper and avoids cross-agent drift.

#### Optional mechanical patching (non-destructive)

If you want a deterministic pass that only replaces forbidden variants (no other rewrites), run:

```bash
python3 {baseDir}/scripts/glossary_apply.py --temp-dir "{temp_dir}" --file output.md --replace-source-en
```

This writes `output.md.patched` by default.

### 7) Report results

Tell the user:
- output file locations
- how many chunks translated
- where `glossary.csv` and QA reports are
- any terminology issues requiring manual decisions (from `terminology_report.md`)

## Notes / Guardrails

- The glossary bootstrap is **not expected to be complete**; it is a starter set. Completeness is achieved via iteration: each batch writes `term_decisions_chunkNNNN.csv`, `reconcile_terms.py` chooses one winner per source term, and `glossary_apply.py` rewrites the batch outputs to that winner before later batches run.
- Default posture is **auto normalization ON**, but it must follow glossary constraints and remain conservative.
- Do not destroy intermediate artifacts unless the build succeeded.
- Keep proper nouns/model names/benchmark names conservative: often `keep_en=true` is correct.
