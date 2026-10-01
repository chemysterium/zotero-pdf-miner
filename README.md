# Zotero PDF Miner

Extract the text of the PDFs in your [Zotero](https://www.zotero.org/) library
into Markdown files — one `.md` per paper — keeping the formatting that
scientific papers depend on:

| In the PDF | Plain extraction gives | Zotero PDF Miner writes |
| --- | --- | --- |
| chemical formulas | `CaSO4`, `Na2SO4` | `CaSO₄`, `Na₂SO₄` |
| ions, charges | `Ca<sup>2+</sup> ,` / `SO42−` | `Ca²⁺,` |
| powers of ten | `8.102× 10<sup>−3 </sup>` | `8.102 × 10⁻³` |
| units | `mmol kg<sup>−1</sup>`, `0.45 µm` | `mmol kg⁻¹`, `0.45 μm` |
| temperatures, angles | `288<sup>◦</sup> C`, `5<sup>◦</sup> –80<sup>◦</sup>` | `288°C`, `5°–80°` |
| TeX-accented names | `Sipila¨`, `Ferreiros´`, `Jelı´nek` | `Sipilä`, `Ferreirós`, `Jelínek` |
| Advent-font symbols (Elsevier, Springer, T&F) | `mg=L`, `pH ¼ 5.5`, `Fe3þ`, `22 �C`, `[13e15]` | `mg/L`, `pH = 5.5`, `Fe³⁺`, `22 °C`, `[13–15]` |
| Pi/Symbol-font Greek | `10 lm`, `h ¼ 44%`, `10 mg` | `10 μm`, `η = 44%`, `10 μg` |
| spaced headings | `H I G H L I G H T S` | `HIGHLIGHTS` |

Each file starts with YAML front matter (title, authors, year, journal, DOI,
Zotero key and a `zotero://` link back to the item), so the output drops
straight into Obsidian, a static site, or an LLM/RAG pipeline.

It is a spin-off of
[zotero-ollama-summarizer](https://github.com/chemysterium/zotero-ollama-summarizer):
the same Zotero plumbing and the same Markdown clean-up ideas, but it produces
the full text instead of a summary, needs no LLM, and never writes to Zotero.

## Requirements

- Python 3.10+
- Zotero 7 with your PDFs synced to this computer
- Either Zotero running with the local API enabled (Settings → Advanced →
  *Allow other applications on this computer to communicate with Zotero*),
  or a Zotero web API key

## Setup

```
pip install -r requirements.txt
```

With Zotero running, that is all — use `--local`. To use the web API instead,
copy `config.example.ini` to `config.ini` and fill in `zotero_library_id` and
`zotero_api_key` from <https://www.zotero.org/settings/keys> (read access is
enough). Every setting can also be given as an environment variable of the same
name in upper case (`ZOTERO_API_KEY`, `OUTPUT_DIR`, ...).

The PDFs themselves are always read from this computer. Stored attachments use
`~/Zotero/storage` by default (set `zotero_storage_dir` if yours is elsewhere);
linked attachments use their absolute path or the configured linked-file base.

## Usage

A whole collection (by name or key), into `markdown/<collection name>/`:

```
python zotero_pdf_miner.py --local --collection "Isotope separation"
```

Including subcollections, into a folder of your choice:

```
python zotero_pdf_miner.py --local -c "Thesis Reading" --recursive -o notes/
```

One paper, by item key or title search:

```
python zotero_pdf_miner.py --local ABCD1234
python zotero_pdf_miner.py --local "partial title of the paper"
```

The whole library, an hour at a time:

```
python zotero_pdf_miner.py --local --all --max-minutes 60
```

A PDF that isn't in Zotero:

```
python zotero_pdf_miner.py --pdf paper.pdf
```

This writes `paper.md` beside the PDF unless you choose a folder with `-o`.

Files are named `<title> (<item key>).md`. Papers that already have a file are
skipped, so rerunning a command only picks up what is new — or what an
interrupted run didn't reach.

| Flag | Effect |
| --- | --- |
| `--collection`, `-c` | Extract every paper in a collection |
| `--recursive`, `-r` | With `--collection`, include subcollections |
| `--all`, `-a` | Extract every paper in the library |
| `--pdf FILE` | Convert one PDF file, without Zotero |
| `--local`, `-l` | Use the running Zotero's local API (no API key needed) |
| `--output-dir`, `-o` | Output folder (default `markdown/`, plus the collection name) |
| `--force` | Overwrite existing `.md` files |
| `--dry-run` | Show what would be extracted |
| `--linked-attachment-base-dir DIR` | Base folder for relative linked attachments (`attachments:...`) |
| `--guess-glyphs` | Enable heuristic replacements of unresolved symbols (off by default) |
| `--equations warn\|text` | Warn about omitted formula regions (default), or retain marked text-layer excerpts |
| `--max-minutes N`, `-m N` | Stop starting new papers after N minutes |
| `--scripts unicode\|html` | Sub/superscripts as Unicode (`10⁻³`, falling back to `<sup>` where Unicode has no glyph) or always as `<sub>`/`<sup>` |
| `--keep-figure-text` | Keep text found inside figures (axis labels, legends) |
| `--page-separators` | Mark page boundaries with `<!-- page N -->` |
| `--no-front-matter` | Leave out the YAML bibliographic block |

Scanned PDFs that were OCR-ed carry their text as an invisible layer, which
pymupdf4llm ignores; those pages are read from the layer directly (as plain
paragraphs — OCR has no sub/superscripts to keep), and the run says so.
Scanned PDFs without such a layer have no text to extract. The miner says so, naming the pages
without a text layer; OCR them first (for example with `ocrmypdf`, or with
[sum-ocr-mark](https://github.com/chemysterium/sum-ocr-mark)) and rerun with `--force`.

### Display equations

The layout converter can detect a formula box but emit no Markdown for it.
The default `--equations warn` reports the number of omitted regions and their
page numbers. To retain text from those regions at their original positions:

```
python zotero_pdf_miner.py --local -c "Thesis Reading" --equations text --force
python zotero_pdf_miner.py --pdf paper.pdf --equations text
```

Each recovered region is clearly labelled and placed in a literal `text` block.
These are **approximate text-layer excerpts, not reconstructed equations**:
stacked fractions, reading order, arrows and detached scripts can still be
damaged. Known fonts and baseline scripts are decoded, but prose cleanup and
context guesses are not applied inside these excerpts. Unicode scripts are
used where available; otherwise literal `<sub>`/`<sup>` notation is retained,
regardless of the prose `--scripts` mode. Image-only equations cannot be restored
this way. Compare important formulas with the PDF; this option is not LaTeX
conversion, OCR, or a completeness guarantee. Detection depends on the layout
converter providing formula-region metadata; misclassified regions may remain
undetected or be reported as formulas.

If layout extraction returns almost no text from a text-bearing page, its full
text layer is used as a plain-text fallback. Visible-text fallbacks are reported
separately from invisible OCR layers, since their formatting may have been lost.

## How it works

Three passes per PDF:

1. **Pre-pass** (`pdf_hints.py`) reads the PDF's glyphs directly with PyMuPDF:
   font size and baseline of every span (a smaller span below the baseline
   is a subscript, above it a superscript), the font each glyph comes from
   (Symbol and Advent fonts encode Greek letters and math symbols as
   unrelated characters), and where accent glyphs are drawn (an accent sits
   over the letter it belongs to, whatever order the text stream lists them
   in). From this it builds a list of small, anchored edits per page —
   "`CaSO` + `4` → `CaSO<sub>4</sub>`" — and a table of correctly accented words.
2. **Extraction** with [pymupdf4llm](https://pypi.org/project/pymupdf4llm/),
   page by page, which gets the structure right: headings, paragraphs,
   lists, tables, reading order across columns; running headers and
   footers are dropped.
3. **Post-pass** applies the pre-pass edits to each page's Markdown (each
   one searched for near where the previous one matched, so a fragment is
   fixed where it occurs), then `sciformat.py` normalises what's left:
   sub/superscripts to Unicode, degree signs, isotopes (`⁶Li`), `× 10ⁿ`,
   ligatures, µ → μ, spacing around scripts and punctuation, compound-word
   hyphens lost to line-end dehyphenation, and figure-internal text.

`python -m unittest -v` runs the formatting, CLI, extraction and equation tests.

Relative linked attachments use Zotero's **Linked Attachment Base Directory**,
not its storage folder. Set `zotero_linked_attachment_base_dir` in `config.ini`
(or `ZOTERO_LINKED_ATTACHMENT_BASE_DIR`) or pass `--linked-attachment-base-dir`.

Exports warn when unresolved replacement characters remain, including their count.
Unresolved control codes and private-use glyphs are made visible as `�` too,
instead of leaving invisible damage in the Markdown.
Heuristic symbol guesses are disabled by default; use `--guess-glyphs` to enable
them and check the result against the PDF. This also enables unanchored,
document-wide replacements inferred from math fonts; without it, a real `¼`
elsewhere in the text is preserved even if a math font uses that character for `=`.
Font and glyph-position based repairs remain enabled. Batch runs return exit
code 1 when extraction or attachment lookup fails; items without PDF attachments
are ordinary skips in batch runs.
An explicitly requested single Zotero item without a PDF returns exit code 1.
Title searches check every result page before deciding whether a match is unique.
Group-library exports use a `zotero://select/groups/<group ID>/items/<key>` link
instead of pointing to the personal library.

### Fonts without a Unicode mapping

About a third of the PDFs in a typical chemistry/engineering library are
typeset with *Advent* fonts (Elsevier, Springer, Taylor & Francis, Wiley):
math and symbol fonts embedded under hashed names such as `AdvP4C4E74`, with no
information on what their glyphs mean. Plain extraction then gives `mg=L`,
`pH ¼ 5.5`, `Fe3þ`, `22 �C`, `10 lm` for mg/L, pH = 5.5, Fe³⁺, 22 °C, 10 μm.
The character *codes* in these fonts change from paper to paper, so a fixed
lookup table doesn't work. `advent.py` decodes them from what does stay put:

- **TeX-derived fonts** (`AdvP4C4E74` = cmsy, `AdvP4C4E51` = cmmi,
  `AdvP4C4E59` = cmr, `AdvP4C4E46` = cmex, their MathTime cousins, and genuine
  `CMSY10`/`CMEX10`/... without a mapping): each glyph's name in the PDF's
  `/Differences` array (`C14`) or its code gives its position in the TeX font,
  and that position fixes the character (cmsy 14 is the degree ring).
- **Named glyphs**: `uniXXXX`, TeX delimiter names (`parenleftBig`), and
  Linotype Mathematical Pi names (`H9262` is μ, `H11005` is =). The older
  `AdvBMa1` font is decoded using its glyph names for charges, ranges and `=`;
  changing their byte positions does not change their meaning.
- **Pi fonts** (`AdvPi1`, `AdvPSMP13`, `AdvGreekM`, ...) whose glyph names say
  nothing (`m`): per-font tables, read off rendered glyphs from the papers
  in which each font occurs.

Accents drawn by these fonts are composed with the letter they sit on
(`Ro_zej` → Rożej, `Hrub�y` → Hrubý).

## Limitations

- Display equations come through only as well as pymupdf4llm reads them —
  inline chemistry and units are handled, typeset integrals and fractions
  are not rebuilt. Some equations can be omitted entirely by the layout
  extraction. Omitted detected regions produce warnings; `--equations text`
  retains approximate excerpts where a text layer exists. A successful run is
  not proof that every formula was preserved; compare formula-heavy pages with
  the source PDF.
- A superscript stacked over a subscript (`SO₄²⁻`) is sometimes split onto a
  separate line by the extraction, and ends up detached (`SO₄ ... 2−`).
- With `--guess-glyphs`, unmapped glyphs the pre-pass cannot place are guessed
  from context, or left as `�` where there is no safe guess. Rare Pi fonts are
  not in the tables yet; their Greek letters stay Latin (`m` for μ).
