"""PDF -> Markdown for scientific papers.

Three stages:

  1. pre-pass   (pdf_hints)  read glyph sizes, baselines, fonts and accent
                             positions straight from the PDF
  2. extraction (pymupdf4llm) structure-aware Markdown, one chunk per page
  3. post-pass  (sciformat)  apply the pre-pass edits to each page, then
                             normalise scripts, degrees, numbers and spacing
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import equations
import pdf_hints
import sciformat

# Below this, the PDF almost certainly has no text layer (a scan).
MIN_USEFUL_CHARS = 200
# A page whose Markdown is shorter than this, while its text layer is not,
# lost its text in conversion.
MIN_PAGE_MD_CHARS = 80

# Leftovers from unmapped font encodings must be visible, not invisible
# control codes or private-use characters with an undefined meaning.
_UNKNOWN_GLYPH = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ue000-\uf8ff"
    r"\U000f0000-\U000ffffd\U00100000-\U0010fffd]"
)


class ExtractionError(Exception):
    """The PDF could not be turned into useful text."""


@dataclass
class Result:
    markdown: str
    pages: int
    edits_applied: int
    edits_found: int
    metadata: dict
    textless_pages: list[int]  # 1-based pages with no text layer (scans)
    ocr_layer_pages: list[int]  # 1-based pages read from an invisible OCR layer
    unresolved_glyphs: int = 0
    omitted_formulas: int = 0
    omitted_formula_pages: list[int] = field(default_factory=list)
    recovered_formulas: int = 0
    text_fallback_pages: list[int] = field(default_factory=list)


def pdf_to_markdown(
    path: Path,
    scripts: str = "unicode",
    keep_figure_text: bool = False,
    page_separators: bool = False,
    guess_glyphs: bool = False,
    equation_mode: str = "warn",
) -> Result:
    import pymupdf
    import pymupdf4llm

    if equation_mode not in ("warn", "text"):
        raise ValueError("equation_mode must be 'warn' or 'text'")

    # MuPDF prints warnings such as broken ICC colour profiles in figures
    # straight to stderr; they say nothing about the text.
    pymupdf.TOOLS.mupdf_display_errors(False)
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        raise ExtractionError(f"Cannot open {path.name}: {exc}") from None

    with doc:
        if doc.needs_pass:
            raise ExtractionError(f"{path.name} is password-protected.")
        hints = pdf_hints.collect_hints(doc)
        chunks = pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            header=False,  # drop running page headers/footers
            footer=False,
            use_ocr=False,
            show_progress=False,
        )
        metadata = dict(doc.metadata or {})
        page_count = doc.page_count
        # Restore text-bearing pages when layout conversion returns almost
        # nothing. This includes invisible OCR layers and some visible text.
        plain_fallback = {
            number: _plain_page_text(doc[number])
            for number, chunk in _numbered(chunks)
            if len((chunk.get("text") or "").strip()) < MIN_PAGE_MD_CHARS
            and len(doc[number].get_text().strip()) >= MIN_PAGE_MD_CHARS
        }
        invisible_pages = {
            number for number in plain_fallback if _is_invisible_text_layer(doc[number])
        }
        excerpts = {}
        omitted_formulas = recovered_formulas = 0
        omitted_formula_pages = []
        prepared_chunks = []
        for number, chunk in _numbered(chunks):
            if number in plain_fallback:
                prepared_chunks.append(chunk)
                continue  # The whole page's text has already been restored.
            recovery = equations.recover_formulas(
                doc[number], chunk, hints.raw_symbol_fonts, equation_mode
            )
            prepared_chunks.append(chunk | {"text": recovery.text})
            excerpts.update(recovery.excerpts)
            recovered_formulas += recovery.recovered
            omitted_formulas += recovery.omitted
            if recovery.omitted:
                omitted_formula_pages.append(number + 1)
        chunks = prepared_chunks

    # A decoded math-font character may also occur literally in body text
    # (e.g. a real quarter fraction). Unanchored replacements are guesses.
    residual = hints.residual_map() if guess_glyphs else {}
    pages_md = []
    applied = 0
    for number, chunk in _numbered(chunks):
        text = plain_fallback.get(number) or chunk.get("text") or ""
        text, glyphs = pdf_hints.apply_edits(text, hints.glyph_edits.get(number, []), in_tags=True)
        text, placed = pdf_hints.apply_edits(text, hints.edits.get(number, []))
        applied += glyphs + placed
        for ch, decoded in residual.items():
            text = text.replace(ch, decoded)
        text = _UNKNOWN_GLYPH.sub("\ufffd", text)
        if page_separators:
            text = f"<!-- page {number + 1} -->\n\n{text}"
        pages_md.append(text)

    body = sciformat.postprocess(
        "\n\n".join(pages_md),
        accent_words=hints.accent_words,
        # Control codes reach the Markdown as U+FFFD.
        extra_accents={"�" if ord(ch) < 32 else ch for ch in hints.accent_chars},
        scripts=scripts,
        keep_figure_text=keep_figure_text,
        fffd_guess=hints.fffd_guess(),
        guess_glyphs=guess_glyphs,
    )
    for token, excerpt in excerpts.items():
        body = body.replace(token, excerpt)
    body = _UNKNOWN_GLYPH.sub("\ufffd", body)
    if len(body.strip()) < MIN_USEFUL_CHARS:
        raise ExtractionError(
            f"Only {len(body.strip())} characters extracted from {path.name} — "
            "it is probably a scanned PDF without a text layer. OCR it first "
            "(e.g. with sum-ocr-mark or ocrmypdf) and rerun."
        )
    return Result(
        markdown=body,
        pages=page_count,
        edits_applied=applied,
        edits_found=sum(len(e) for e in hints.edits.values())
        + sum(len(e) for e in hints.glyph_edits.values()),
        metadata=metadata,
        textless_pages=hints.textless_pages,
        ocr_layer_pages=sorted(n + 1 for n in invisible_pages),
        text_fallback_pages=sorted(n + 1 for n in plain_fallback if n not in invisible_pages),
        unresolved_glyphs=body.count("\ufffd"),
        omitted_formulas=omitted_formulas,
        omitted_formula_pages=omitted_formula_pages,
        recovered_formulas=recovered_formulas,
    )


def _numbered(chunks: list[dict]):
    """(0-based page number, chunk) for pymupdf4llm's page chunks."""
    for index, chunk in enumerate(chunks):
        yield chunk.get("metadata", {}).get("page_number", index + 1) - 1, chunk


def _is_invisible_text_layer(page) -> bool:
    """An all-invisible text layer is consistent with text added by OCR.

    Visible text that a layout parser omitted must not be reported as OCR.
    MuPDF's trace type 3 means invisible text (PDF rendering mode 3).
    """
    spans = [span for span in page.get_texttrace() if span.get("chars")]
    return bool(spans) and all(span.get("type") == 3 for span in spans)


def _plain_page_text(page) -> str:
    """A page's text as paragraphs, straight from its text layer.

    Used when layout extraction loses nearly all text, including invisible
    OCR layers. Each text block becomes a paragraph with line breaks (and
    the hyphens that split words across them) joined up. Visible-text pages
    can lose important formatting; callers report those separately.
    """
    paragraphs = []
    for block in page.get_text("blocks", sort=True):
        if block[6] != 0:  # image block
            continue
        text = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", block[4].strip())
        text = re.sub(r"\s*\n\s*", " ", text)
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)
