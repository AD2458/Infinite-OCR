# Infinite OCR

One continuous, structure-aware OCR output for an entire PDF — built on Bodhan AI's `indic-ocr`.

| | |
|---|---|
| **Input** | Any PDF, especially multi-script or scanned Indian-language documents |
| **Output** | One continuous, structure-aware digitized text for the whole document |
| **Reviewer** | Flag and correct specific regions — only that part gets redone |
| **Bar** | Near-zero manual cleanup; page-break tables and sentences reconstruct correctly |

## The core idea

`indic-ocr` is **page-local**: it takes one PNG/JPEG page per request and is billed per page.
It has no PDF endpoint and no cross-page awareness. So the model is not the product —
**everything above the page is.**

```
PDF ─rasterize─► page PNGs ─┬─► indic-ocr ─► {markdown, blocks[]}
                            │                        │
                       sha256 cache            normalize → IR
                            │                        │
                            │          furniture strip ─ reading order
                            │                        │
                            │          cross-page stitch (sentence/table/list)
                            │                        │
                            │                   render (anchored)
                            │                        │
                            └◄── crop bbox ◄── reviewer flags region
                                  re-OCR one block → version+1 → re-render
```

The design rests on one rule: **the rendered document is a pure function of the IR.**
Both headline requirements fall out of it:

- **Continuous output** — page boundaries become a *stitching decision* recorded as a `Link`, not a structural fact baked into the text.
- **Targeted redo** — a `Block` is simultaneously the unit of OCR, caching, correction and versioning, so re-OCRing one block cannot perturb any other region.

## Multilingual support

**Yes — and it needs no configuration.** `indic-ocr` exposes no `language` parameter
(unlike `indic-transcribe`/`indic-translate`, which do). A page mixing Hindi, English and
Sanskrit goes through in **one request**, at no cost premium.

The model handles scripts. The real engineering is making *the pipeline* script-aware,
because every default heuristic is English-shaped. `infinite_ocr/script/` handles:

| Hazard | Handling |
|---|---|
| Sentence enders aren't `.` | danda `।` `॥`, Urdu `۔` `؟`, per-script terminator sets |
| No capitalisation in Indic | continuation inferred from geometry + block type instead |
| RTL (Urdu, Sindhi, Kashmiri) | columns ordered right-to-left; bidi isolates on render |
| Hyphen rejoin is Latin-only | gated on script, so Indic hyphens survive |
| ZWJ/ZWNJ, nukta, chillu | NFC for storage; separate lossy key for comparison |
| Non-ASCII numerals `१२` `০৯` | Unicode-aware digit folding for lists + page numbers |
| HTML tags look Latin | markup stripped before script measurement |
| Aksharas ≠ codepoints | metrics on extended grapheme clusters |

Script-histogram outlier detection doubles as the strongest automatic quality signal
available for multi-script documents, and costs nothing — it's pure post-processing.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Your API key

Create a file named **`.env`** in the project root (same folder as `README.md`):

```
BODHAN_API_KEY=your_actual_key_here
```

Or from PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env          # replace the placeholder with your real key
```

Then confirm it works — this costs **no credits**:

```powershell
.\.venv\Scripts\python.exe -m infinite_ocr.cli check
```

Notes:
- No quotes, no spaces around `=`, and the key goes on one line.
- `.env` is gitignored, so the key is never committed.
- `.env` is loaded at package import, so scripts and notebooks pick it up too — not just the CLI.
- An already-exported `BODHAN_API_KEY` environment variable takes precedence over `.env`.

## Web UI (easiest way to test)

```powershell
.\.venv\Scripts\python.exe serve.py
```

Opens **http://127.0.0.1:8000** — drag in a PDF and watch it run.

- **Pages** box defaults to `3`. Billing is per page, so start small; set `0` for the whole document.
- Live progress shows **pages billed vs. served from cache** — a re-upload of the same PDF bills nothing.
- Four tabs: the continuous document, every stitching decision (with its reason, and a Reject button), the review queue of flagged regions, and all blocks.
- The document tab has a **Preview / Source** toggle. Preview renders tables as real tables; Source shows the raw markup for copying.
- Click any block to open the editor: it shows the **bbox-cropped image of that exact region**, so you can verify the text against what was on the page. Fix by hand (pinned, never overwritten) or **re-OCR just that region**.
- Noto fonts load automatically, so Devanagari/Tamil/Urdu etc. render properly instead of as tofu.

### Export formats

Download as **HTML** (best for reading — tables render, fonts embedded), **Word**, **Excel** (one sheet per table), **Markdown**, **Markdown with flattened tables**, or **plain text**.

All exports are **free** — pure post-processing over cached data, so no API credits regardless of how often you regenerate them.

Why tables are HTML by default: the API's `html` table format preserves merged cells via `rowspan`/`colspan`, while `markdown` flattens them into separate columns and drops in-cell line breaks. HTML is the lossless choice, so it stays the default; the flattened Markdown export exists for when readability matters more than fidelity.

## CLI

```powershell
# OCR a PDF into one continuous document (every page goes through the model)
.\.venv\Scripts\python.exe -m infinite_ocr.cli run book.pdf -o out/book.md

# See what needs a human look, worst first
.\.venv\Scripts\python.exe -m infinite_ocr.cli review out/book.ir.json

# Redo exactly one region
.\.venv\Scripts\python.exe -m infinite_ocr.cli redo out/book.ir.json p012-b03

# Pin a manual fix (never overwritten by a later redo)
.\.venv\Scripts\python.exe -m infinite_ocr.cli set-text out/book.ir.json p012-b03 "correct text"

# Reject a wrong table merge
.\.venv\Scripts\python.exe -m infinite_ocr.cli reject-link out/book.ir.json p011-b04
```

See it work without an API key:

```powershell
.\.venv\Scripts\python.exe demo_offline.py
```

## Cost control

Billing is per page, so the cache is the budget. The key is `sha256(page_png)`, so
**a page is billed once ever** — re-running the pipeline is free, and the stitching,
furniture, render and triage layers can be tuned indefinitely offline against recorded
page JSON.

### Why every page is OCR'd, even born-digital ones

An earlier version read the PDF's embedded text layer to skip OCR on digital-born
pages. **That was removed**, because PDF text extraction silently corrupts Indic
scripts:

```
inserted : कंप्यूटर प्रिंटर विवरण सर्वर निविदा
extracted: ········ ······· ····· ····· ······      (round-trip through PyMuPDF)

कंप्यूटर  →  कपंय् टर      (combining marks reordered)
विवरण     →  ववरण          (matra dropped)
```

The corruption is **undetectable**. `कपंय् टर` is well-formed, NFC-normalised,
all-Devanagari text with no invalid grapheme clusters — it passes every structural
check, and even a full Devanagari phonotactic grammar reports zero violations. It is
simply not a Hindi *word*. Separating the two requires a lexicon, not a validator.

The vision model reads glyphs off the rendered image and has no font-encoding layer,
so this entire class of failure cannot occur. One credit per page is cheaper than
shipping confidently wrong Hindi.

Raw API responses are stored **verbatim**, never parse-and-discard, so every downstream
layer stays replayable forever.

## Reliability notes

`max_tokens` applies **per block** (default 2048). If one block exceeds it, the request
fails **502 and the whole page is lost** — large HTML tables are the usual cause.
The client escalates `2048 → 4096 → 8192`, then falls back from `html` to the much
smaller `markdown` table format before giving up. `html` is the default because it
preserves `rowspan`/`colspan`, which matters for the multi-level headers common in
Indian exam papers and budget tables. `401` fails fast and is never retried.

## Layout

```
infinite_ocr/
  ingest/    rasterize.py       # PDF → PNG only; text layer deliberately unused
  client/    bodhan.py cache.py # retries, rate limits, max_tokens ladder
  script/    detect terminators numerals normalize   # the multilingual layer
  ir/        models normalize furniture order        # Document IR
  stitch/    sentences tables lists links levels     # cross-page merging
  render/    markdown.py        # deterministic, anchored
  review/    confidence.py redo.py                   # triage + targeted redo
  pipeline.py  cli.py
tests/       63 tests, all offline (no credits)
```

## Status

| Milestone | State |
|---|---|
| 0–2 Scaffold, IR, rasterizer, cache | done |
| 3 Bodhan client (ladder, retries, rate limits) | done, untested against live API |
| 4–6 Script layer, furniture, order, stitching | done |
| 7 Anchored render + targeted redo | done |
| 8 Confidence triage + CLI review loop | done |
| 9 Reviewer web UI | not started |
| 9 Eval harness (CER/WER, table F1, stitch accuracy) | not started |

**Not yet validated against the live API** — the client is written to the documented
contract but has only been exercised against recorded fixtures. The first live run should
use a small page range to confirm the response shape before spending real credits.
