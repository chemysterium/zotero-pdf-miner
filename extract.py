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
from dataclasses import dataclass
from pathlib import Path

import pdf_hints
import sciformat

# Below this, the PDF almost certainly has no text layer (a scan).
MIN_USEFUL_CHARS = 200
# A page whose Markdown is shorter than this, while its text layer is not,
# lost its text in conversion.
MIN_PAGE_MD_CHARS = 80


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


def pdf_to_markdown(
    path: Path,
    scripts: str = "unicode",
    keep_figure_text: bool = False,
    page_separators: bool = False,
) -> Result:
    import pymupdf
    import pymupdf4llm

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
        # pymupdf4llm skips invisible text, which is exactly what an OCR text
        # layer on a scanned page is; such pages are read directly instead.
        ocr_layer = {
            number: _plain_page_text(doc[number])
            for number, chunk in _numbered(chunks)
            if len((chunk.get("text") or "").strip()) < MIN_PAGE_MD_CHARS
            and len(doc[number].get_text().strip()) >= MIN_PAGE_MD_CHARS
        }

    residual = hints.residual_map()
    pages_md = []
    applied = 0
    for number, chunk in _numbered(chunks):
        text = ocr_layer.get(number) or chunk.get("text") or ""
        text, glyphs = pdf_hints.apply_edits(text, hints.glyph_edits.get(number, []), in_tags=True)
        text, placed = pdf_hints.apply_edits(text, hints.edits.get(number, []))
        applied += glyphs + placed
        for ch, decoded in residual.items():
            text = text.replace(ch, decoded)
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
    )
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
        ocr_layer_pages=sorted(n + 1 for n in ocr_layer),
    )


def _numbered(chunks: list[dict]):
    """(0-based page number, chunk) for pymupdf4llm's page chunks."""
    for index, chunk in enumerate(chunks):
        yield chunk.get("metadata", {}).get("page_number", index + 1) - 1, chunk


def _plain_page_text(page) -> str:
    """A page's text as paragraphs, straight from its text layer.

    Used for OCR layers, which carry no styling worth keeping: each text
    block becomes a paragraph, with line breaks (and the hyphens that split
    words across them) joined up.
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
