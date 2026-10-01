"""Detect omitted layout formulas and optionally retain marked text excerpts.

These are text-layer excerpts, not reconstructed LaTeX or mathematical syntax.
Keep them out of prose cleanup: a caret or an underscore can carry meaning.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from uuid import uuid4

import advent
import pdf_hints
import sciformat


@dataclass
class FormulaRecovery:
    text: str
    excerpts: dict[str, str] = field(default_factory=dict)
    recovered: int = 0
    omitted: int = 0


def formula_text(page, bbox, raw_symbol_fonts: set[str], font_cache: dict | None = None) -> str:
    """Read source glyphs within a formula box, keeping the extracted lines.

    Font and baseline repairs use the same pre-pass as article text. Always
    prefer Unicode scripts in these literal, fixed-width excerpts. Characters
    without Unicode script forms keep explicit HTML tags as literal notation.
    """
    import pymupdf

    if len(bbox) != 4 or not all(math.isfinite(float(value)) for value in bbox):
        raise ValueError("Invalid formula bounding box")
    clip = pymupdf.Rect(bbox)
    if clip.is_empty or clip.is_infinite:
        raise ValueError("Empty formula bounding box")
    flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    raw = page.get_text("rawdict", clip=clip, flags=flags, sort=True)
    decoder = advent.PageDecoder(page.parent, page, font_cache)
    hints = pdf_hints.PdfHints(raw_symbol_fonts=set(raw_symbol_fonts))
    lines = []
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line["spans"]:
                for char in span.get("chars", []):
                    char["_font"] = span["font"]
            pdf_hints._fix_line_glyphs(line, hints, decoder)
            for span in line["spans"]:
                span["text"] = "".join(char["c"] for char in span.get("chars", []))
            edits = pdf_hints._line_edits(line, hints)
            for edit, font in hints.pending_symbols:
                edit.replacement = pdf_hints._symbol_text(edit.target, font in hints.raw_symbol_fonts)
            hints.pending_symbols.clear()
            text = "".join(span["text"] for span in line["spans"])
            text, _ = pdf_hints.apply_edits(text, edits)
            text = sciformat.fix_degrees(sciformat.convert_scripts(text))
            if text.strip():
                lines.append(text.rstrip())
    return unicodedata.normalize("NFC", "\n".join(lines))


def _excerpt(text: str) -> str:
    # A source formula containing backticks cannot close its own fence.
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    return ("\n\n> PDF formula text — original layout is not reconstructed.\n\n"
            f"{fence}text\n{text}\n{fence}\n\n")


def recover_formulas(page, chunk: dict, raw_symbol_fonts: set[str], mode: str) -> FormulaRecovery:
    """Use layout offsets to replace only empty formula regions in situ."""
    result = FormulaRecovery(chunk.get("text") or "")
    replacements = []
    font_cache = {}
    previous_end = -1
    for box in chunk.get("page_boxes", []):
        if box.get("class") != "formula":
            continue
        position = box.get("pos", ())
        valid = (isinstance(position, (list, tuple)) and len(position) == 2
                 and all(isinstance(value, int) for value in position)
                 and 0 <= position[0] <= position[1] <= len(result.text))
        if not valid or position[0] < previous_end:
            result.omitted += 1
            continue
        start, end = position
        previous_end = end
        if result.text[start:end].strip():
            continue  # A converter that already emitted text or an image needs no fallback.
        if mode != "text":
            result.omitted += 1
            continue
        try:
            text = formula_text(page, box.get("bbox", ()), raw_symbol_fonts, font_cache)
        except (ValueError, TypeError, RuntimeError):
            text = ""
        if not text.strip():
            result.omitted += 1
            continue
        token = "ZOTEROPDFFORMULA" + uuid4().hex.upper()
        result.excerpts[token] = _excerpt(text)
        replacements.append((start, end, token))
        result.recovered += 1
    for start, end, token in reversed(replacements):
        result.text = result.text[:start] + token + result.text[end:]
    return result
