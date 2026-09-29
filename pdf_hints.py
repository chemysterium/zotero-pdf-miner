"""Read the PDF's glyph layout for what pymupdf4llm's Markdown loses.

pymupdf4llm marks superscripts only when the PDF flags them, never marks
subscripts ("CaSO4", "NH3"), passes Symbol-font letters through as Latin
("10 mg" for 10 μg), and emits TeX accents as separate characters. All of
this is visible in the PDF itself: a subscript is a smaller span sitting
below the baseline, a Symbol-font "m" is a μ, an accent glyph sits on top of
the letter it belongs to.

This module walks each page's spans and records:

  - edits: per page, in reading order, a fragment of text as it will appear
    in the Markdown plus how to rewrite it ("CaSO" + "4" -> "CaSO<sub>4</sub>")
  - accent_words: accent-free skeleton -> correctly accented word
    ("Ikalainen" -> "Ikäläinen")

apply_edits() then finds each fragment in the page's Markdown and rewrites
it. Fragments are located in order, near where the previous one was found,
so a common fragment is rewritten where it occurs, not wherever it
first appears in the paper.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import advent
from sciformat import SPACING_ACCENTS, compose, skeleton

# A span this much smaller than the line's body text may be a script.
SCRIPT_SIZE_RATIO = 0.85
# ... and it is one if its baseline sits this far (in body-text sizes) off.
SUP_SHIFT = 0.15
SUB_SHIFT = 0.08
MAX_SCRIPT_LEN = 12
# A page with less text than this has no real text layer (a scan).
MIN_PAGE_CHARS = 80
# Longer neighbouring "words" are URLs or formula soup, not anchors.
MAX_CONTEXT = 30

# Adobe Symbol font encoding. A Symbol font whose text comes out as Latin
# letters (or as private-use codes) has no Unicode mapping, and all of its
# glyphs need translating. The letters alone prove that; for the Latin-1
# symbols it has to be known from elsewhere in the paper, since a properly
# mapped Symbol font can legitimately produce "·" or "×".
SYMBOL_LETTERS = {
    "a": "α", "b": "β", "c": "χ", "d": "δ", "e": "ε", "f": "φ", "g": "γ",
    "h": "η", "i": "ι", "j": "ϕ", "k": "κ", "l": "λ", "m": "μ", "n": "ν",
    "o": "ο", "p": "π", "q": "θ", "r": "ρ", "s": "σ", "t": "τ", "u": "υ",
    "v": "ϖ", "w": "ω", "x": "ξ", "y": "ψ", "z": "ζ",
    "A": "Α", "B": "Β", "C": "Χ", "D": "Δ", "E": "Ε", "F": "Φ", "G": "Γ",
    "H": "Η", "I": "Ι", "J": "ϑ", "K": "Κ", "L": "Λ", "M": "Μ", "N": "Ν",
    "O": "Ο", "P": "Π", "Q": "Θ", "R": "Ρ", "S": "Σ", "T": "Τ", "U": "Υ",
    "V": "ς", "W": "Ω", "X": "Ξ", "Y": "Ψ", "Z": "Ζ",
}
SYMBOL_OTHERS = {
    "-": "−", "~": "∼", "@": "≅", "\"": "∀", "$": "∃", "'": "∍", "\\": "∴", "^": "⊥",
    "¡": "ϒ", "¢": "′", "£": "≤", "¤": "⁄", "¥": "∞", "¦": "ƒ", "§": "♣",
    "¨": "♦", "©": "♥", "ª": "♠", "«": "↔", "¬": "←", "­": "↑", "®": "→",
    "¯": "↓", "°": "°", "±": "±", "²": "″", "³": "≥", "´": "×", "µ": "∝",
    "¶": "∂", "·": "•", "¸": "÷", "¹": "≠", "º": "≡", "»": "≈", "¼": "…",
    "À": "ℵ", "Á": "ℑ", "Â": "ℜ", "Ã": "℘", "Ä": "⊗", "Å": "⊕", "Æ": "∅",
    "Ç": "∩", "È": "∪", "É": "⊃", "Ê": "⊇", "Ë": "⊄", "Ì": "⊂", "Í": "⊆",
    "Î": "∈", "Ï": "∉", "Ð": "∠", "Ñ": "∇", "Õ": "∏", "Ö": "√", "×": "⋅",
    "Ø": "¬", "Ù": "∧", "Ú": "∨", "Û": "⇔", "Ü": "⇐", "Ý": "⇑", "Þ": "⇒",
    "ß": "⇓", "à": "◊", "á": "〈", "å": "∑", "ñ": "〉", "ò": "∫",
}

# Characters a math font uses in place of others ("\u00bc" for "="), and that are
# rare enough in real text that a leftover one can be replaced across the
# whole paper once the font's use of it is known.
RESIDUAL_CANDIDATES = set("\u00bc\u00bd\u00be\u00fe\u00de\u00f0\u00d0")
_CUT = "\u2063"  # invisible separator: marks a word break at a script span


@dataclass
class Edit:
    """Rewrite `before + target + after` to `before + replacement + after`."""

    before: str
    target: str
    after: str
    replacement: str


@dataclass
class PdfHints:
    edits: dict[int, list[Edit]] = field(default_factory=dict)  # 0-based page
    # Applied before `edits`, whose anchors are written in the fixed glyphs.
    glyph_edits: dict[int, list[Edit]] = field(default_factory=dict)
    textless_pages: list[int] = field(default_factory=list)  # 1-based
    accent_words: dict[str, str] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=lambda: {"sub": 0, "sup": 0, "symbol": 0})
    # How control-code glyphs not drawn over a letter were read, to guess
    # the ones pymupdf4llm left as U+FFFD where no edit could reach them.
    unmapped: Counter = field(default_factory=Counter)
    # Symbol fonts seen producing Latin letters, i.e. without Unicode mapping.
    raw_symbol_fonts: set[str] = field(default_factory=set)
    # Raw characters that turned out to be accents ("_" for a dot above, a
    # control code for a caron): pymupdf4llm may move them within the word.
    accent_chars: set[str] = field(default_factory=set)
    # What odd Latin-1 characters from math fonts decoded to ("¼" -> "=").
    residual: dict[str, Counter] = field(default_factory=dict)

    def residual_map(self) -> dict[str, str]:
        """Characters to replace wherever edits missed them: each one only
        if it always decoded to the same thing in this paper."""
        return {ch: c.most_common(1)[0][0] for ch, c in self.residual.items() if len(c) == 1}
    pending_symbols: list[tuple[Edit, str]] = field(default_factory=list)

    def fffd_guess(self) -> str | None:
        """The one symbol nearly all unmapped glyphs turned out to be."""
        total = sum(self.unmapped.values())
        if not total:
            return None
        symbol, count = self.unmapped.most_common(1)[0]
        return symbol if count >= 0.8 * total else None


def _is_symbol_font(name: str) -> bool:
    """Adobe's Symbol font and its clones (SymbolMT, Symbol-Bold, ...), not
    other fonts that merely have "symbol" in their name (SymbolsDD)."""
    return re.match(r"^symbol(mt|ps|itc)?([-,].*)?$", name.lower()) is not None


def _is_raw_symbol(text: str) -> bool:
    """True if Symbol-font text shows it has no Unicode mapping."""
    return any(ch in SYMBOL_LETTERS or 0xF020 <= ord(ch) <= 0xF0FF for ch in text)


def _symbol_text(text: str, raw_font: bool = True) -> str:
    """Translate Symbol-encoded text; raw_font enables the Latin-1 symbols."""
    out = []
    for ch in text:
        code = ord(ch)
        pua = 0xF020 <= code <= 0xF0FF  # Symbol glyphs in the private use area
        if pua:
            ch = chr(code - 0xF000)
        if ch in SYMBOL_LETTERS:
            ch = SYMBOL_LETTERS[ch]
        elif pua or raw_font:
            ch = SYMBOL_OTHERS.get(ch, ch)
        out.append(ch)
    return "".join(out)


def _body_span(spans: list[dict]) -> dict:
    """The span that sets the line's body size: the one with most characters."""
    return max(spans, key=lambda s: len(s["text"].strip()) * s["size"])


def _context(before: str, after: str) -> tuple[str, str]:
    """The word fragments that touch a span on either side.

    A script belongs to the word it is attached to ("CaSO" + "4"); when it
    stands at the start of a word it belongs to what follows ("6" + "Li").
    """
    return re.search(r"\S*$", before).group(0), re.match(r"\S*", after).group(0)


def _line_edits(line: dict, hints: PdfHints) -> list[Edit]:
    spans = [s for s in line["spans"] if s["text"]]
    if len(spans) < 2 or line.get("dir", (1, 0))[1] != 0:  # skip rotated text
        return []
    body = _body_span(spans)
    body_size = body["size"]
    baseline = body["origin"][1]
    texts = [s["text"] for s in spans]

    edits = []
    for i, span in enumerate(spans):
        raw = span["text"].strip()
        if not raw:
            continue
        before = "".join(texts[:i])
        after = "".join(texts[i + 1:])

        if _is_symbol_font(span["font"]):
            if _is_raw_symbol(raw):
                hints.raw_symbol_fonts.add(span["font"])
            # Whether the font is unmapped may only become clear later in the
            # paper, so the replacement is settled in collect_hints().
            if _symbol_text(raw) != raw:
                left, right = _context(before, after)
                left, right = left[-MAX_CONTEXT:], right[:MAX_CONTEXT]
                if not left and not right and len(raw) == 1:
                    # A lone "a" could be alpha or the article; add a word of
                    # context on each side so it is found in the right place.
                    left = re.search(r"(?:\S+\s+)?$", before).group(0)[-25:]
                    right = re.match(r"(?:\s+\S+)?", after).group(0)[:25]
                edit = Edit(left, raw, right, raw)
                hints.pending_symbols.append((edit, span["font"]))
                edits.append(edit)
            continue

        if span["size"] > body_size * SCRIPT_SIZE_RATIO or len(raw) > MAX_SCRIPT_LEN:
            continue
        shift = (span["origin"][1] - baseline) / body_size
        if shift <= -SUP_SHIFT:
            tag = "sup"
        elif shift >= SUB_SHIFT:
            tag = "sub"
        else:
            continue

        # Anchor on the word the script is attached to ("CaSO" + "4", or
        # "Na" + "2" + "SO4"); a subscript needs a base on its left, a
        # superscript may lead its word ("6" + "Li"). A script with a space
        # on both sides can't be placed reliably.
        left, right = _context(before, after)
        if not left and (tag == "sub" or not right):
            continue
        if len(left) > MAX_CONTEXT or len(right) > MAX_CONTEXT:
            continue
        if not re.search(r"[^\d\s.,]", left + raw + right):
            continue  # "10"+"3" would also match the number 103 elsewhere
        edits.append(Edit(left, raw, right, f"<{tag}>{raw}</{tag}>"))
        hints.stats[tag] += 1
    return edits


def _overlapped_letter(chars: list[dict], i: int) -> int | None:
    """Index of the letter glyph drawn under/over chars[i], if any.

    The accent's centre must fall inside the letter's box: an apostrophe-like
    "´" set after a letter ("Dunn´s") sits beside it, not on it.
    """
    c = chars[i]
    center = (c["bbox"][0] + c["bbox"][2]) / 2
    best, best_dist = None, None
    for j, other in enumerate(chars):
        letter = other["c"]
        if j == i or len(letter) != 1 or not letter.isalpha():
            continue
        x0, x1 = other["bbox"][0], other["bbox"][2]
        if not x0 <= center <= x1:
            continue
        dist = abs((x0 + x1) / 2 - center)
        if best is None or dist < best_dist:
            best, best_dist = j, dist
    return best


def _fix_line_glyphs(line: dict, hints: PdfHints, decoder: advent.PageDecoder) -> bool:
    """Resolve glyphs that don't stand for themselves, in place.

    Characters of Advent fonts are decoded to what their glyphs draw (see
    advent.py). Accent glyphs drawn on top of a letter, whether a TeX
    spacing accent ("¨" over "a") or a decoded Advent one, are composed
    with it ("ä").

    Returns True if an Advent glyph was changed.
    """
    chars = [c for s in line["spans"] for c in s.get("chars", [])]
    advent_changed = False
    for i, c in enumerate(chars):
        ch = c["c"]
        decoded = decoder.decode(c.get("_font", ""), ch)
        if decoded is not None:
            c["c"] = decoded
            advent_changed = True
            if ch in RESIDUAL_CANDIDATES:
                hints.residual.setdefault(ch, Counter())[decoded] += 1
            if decoded in SPACING_ACCENTS:
                hints.accent_chars.add(ch)
            elif len(ch) == 1 and ord(ch) < 32:
                hints.unmapped[decoded] += 1
        if c["c"] in SPACING_ACCENTS:
            j = _overlapped_letter(chars, i)
            composed = compose(chars[j]["c"], SPACING_ACCENTS[c["c"]]) if j is not None else None
            if composed:
                chars[j]["c"] = composed
                c["c"] = ""

    fixed_line = "".join(c["c"] for c in chars)
    for word in re.findall(r"[^\W\d_]+", fixed_line):
        if word != skeleton(word):
            hints.accent_words.setdefault(skeleton(word), word)
    return advent_changed


def _glyph_edits(line: dict, raw_texts: list[str]) -> list[Edit]:
    """Edits for the words of a line whose Advent glyphs were resolved.

    pymupdf4llm writes control-code glyphs as U+FFFD and the others as the
    wrong character, so the fixed word is put in place of the raw one.
    Words are split at script spans too, so a word never runs into a
    superscript pymupdf4llm has already tagged.
    """
    spans = line["spans"]
    body_size = max(s["size"] for s in spans)

    def tokens(texts: list[str]) -> list[str]:
        """Words and separators: [word, sep, word, sep, ...]; a script span
        is its own word, with an empty separator where no space was."""
        parts = []
        for span, text in zip(spans, texts):
            small = span["size"] <= body_size * SCRIPT_SIZE_RATIO
            parts.append(f"{_CUT}{text}{_CUT}" if small else text)
        return [t for t in re.split(rf"(\s+|{_CUT}+)", "".join(parts))]

    raw_tokens = tokens(raw_texts)
    fixed_tokens = tokens([s["text"] for s in spans])
    if len(raw_tokens) != len(fixed_tokens):
        return []

    def sep(k: int) -> str:
        return " " if raw_tokens[k].strip(_CUT) else ""

    def whole_word(tokens: list[str], k: int, step: int) -> str:
        """Tokens from k outwards up to the next real space: the full word
        ("Na2WO4"), not just the script fragment next to the target ("4")."""
        parts = []
        while 0 <= k < len(tokens):
            parts.append(tokens[k])
            if 0 <= k + step < len(tokens) and sep(k + step):
                break
            k += 2 * step
        return "".join(parts[::step])

    edits = []
    # Even indexes are words, odd ones the separators between them. Each
    # edit is anchored on its neighbours: a lone "�" means nothing on its own.
    for k in range(0, len(raw_tokens), 2):
        raw, fixed = raw_tokens[k], fixed_tokens[k]
        if raw == fixed:
            continue
        before = whole_word(fixed_tokens, k - 2, -1) + sep(k - 1) if k >= 2 else ""
        after = sep(k + 1) + whole_word(raw_tokens, k + 2, 1) if k + 2 < len(raw_tokens) else ""
        if not (before + after).strip() and len(raw) < 3:
            continue
        edits.append(Edit(before, raw, after, fixed))
    return edits


def collect_hints(doc, pages: list[int] | None = None) -> PdfHints:
    """Scan the PDF's spans for scripts, odd glyphs and accents."""
    import pymupdf

    hints = PdfHints()
    flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    font_cache: dict = {}
    for number in pages if pages is not None else range(doc.page_count):
        glyph_edits: list[Edit] = []
        page_edits: list[Edit] = []
        page_chars = 0
        decoder = advent.PageDecoder(doc, doc[number], font_cache)
        raw = doc[number].get_text("rawdict", flags=flags)
        for block in raw["blocks"]:
            for line in block.get("lines", []):
                raw_texts = []
                for span in line["spans"]:
                    for c in span.get("chars", []):
                        c["_font"] = span["font"]
                    raw_texts.append("".join(c["c"] for c in span.get("chars", [])))
                advent_changed = _fix_line_glyphs(line, hints, decoder)
                for span in line["spans"]:
                    span["text"] = "".join(c["c"] for c in span.get("chars", []))
                    page_chars += len(span["text"].strip())
                if advent_changed:
                    glyph_edits += _glyph_edits(line, raw_texts)
                page_edits += _line_edits(line, hints)
        hints.glyph_edits[number] = glyph_edits
        hints.edits[number] = page_edits
        # A scanned page is an image with (next to) no text; a page that is
        # just a figure or blank has no text either, but it isn't a scan.
        if page_chars < MIN_PAGE_CHARS and doc[number].get_images():
            hints.textless_pages.append(number + 1)

    for edit, font in hints.pending_symbols:
        edit.replacement = _symbol_text(edit.target, font in hints.raw_symbol_fonts)
        if edit.replacement != edit.target:
            hints.stats["symbol"] += 1
    for number, page_edits in hints.edits.items():
        hints.edits[number] = [e for e in page_edits if e.replacement != e.target]
    return hints


# --------------------------------------------------------------------------
# Applying edits to the Markdown
# --------------------------------------------------------------------------

# What pymupdf4llm may put between two characters of one word: styling
# markers, a stray space, and (in the anchor words only) script tags it or an
# earlier edit added.
_GLUE = r"[ _*]{0,3}"
_TAG_GLUE = r"(?:[ _*]|</?su[bp]>){0,4}"


def _char_pattern(ch: str, raw: bool = False) -> str:
    """Pattern for one character; `raw` text from an unmapped font may
    appear in the Markdown as the character itself or as U+FFFD."""
    if ch.isspace():
        return r"\s+"
    if ord(ch) < 32:  # pymupdf4llm writes unmapped glyphs as U+FFFD
        return "[\ufffd\x00-\x1f]"
    if raw:
        return f"(?:{re.escape(ch)}|\ufffd)"
    return re.escape(ch)


def _fragment_pattern(text: str, glue: str, raw: bool = False) -> str:
    return glue.join(_char_pattern(ch, raw) for ch in text)


def _edit_pattern(edit: Edit, in_tags: bool = False) -> re.Pattern:
    """Groups: 1 anchor before, 2 glue, 3 target, 4 glue + anchor after.

    in_tags lets the target sit inside, or next to, existing script tags —
    right for glyph edits, which swap characters and keep the tags.
    """
    whole = edit.before + edit.target + edit.after
    glue = _TAG_GLUE if in_tags else _GLUE
    pattern = (
        (r"(?<![A-Za-z0-9])" if whole[:1].isalnum() else "")
        + f"({_fragment_pattern(edit.before, _TAG_GLUE)}(?:</su[bp]>)*)"
        + f"({glue if edit.before or in_tags else ''})"
        + f"({_fragment_pattern(edit.target, _GLUE, raw=in_tags)})"
        + f"({glue if edit.after or in_tags else ''}(?:<su[bp]>)*"
        f"{_fragment_pattern(edit.after, _TAG_GLUE, raw=in_tags)})"
        + (r"(?![A-Za-z0-9])" if whole[-1:].isalnum() else "")
    )
    return re.compile(pattern)


def _inside_tag(text: str) -> bool:
    """True if `text` ends inside an unclosed <sup> or <sub>."""
    return any(text.count(f"<{t}>") > text.count(f"</{t}>") for t in ("sup", "sub"))


def _rewrite(match: re.Match, replacement: str) -> str:
    """The matched text with its target replaced.

    Groups: 1 anchor before, 2 glue, 3 target, 4 glue + anchor after. A script
    is pulled onto its base, dropping the glue in between ("_V_ _c_" ->
    "_V<sub>c</sub>"); a Symbol-font letter keeps its surroundings as they
    were. Dropped glue may hold one half of an italic pair, which is put back
    so the rest of the line doesn't turn italic.
    """
    is_script = replacement.startswith(("<sup>", "<sub>"))
    glue = "" if is_script else match.group(2)
    new = match.group(1) + glue + replacement + match.group(4)
    if (match.group(0).count("_") - new.count("_")) % 2:
        new = match.group(1) + glue + replacement + "_" + match.group(4)
    return new


def apply_edits(markdown: str, edits: list[Edit], in_tags: bool = False) -> tuple[str, int]:
    """Apply one page's edits to that page's Markdown; returns (text, applied).

    Each edit is searched for from where the previous one matched, then from
    the top of the page — pymupdf4llm reorders columns and tables, so reading
    order in the Markdown only roughly follows the PDF's. A target already
    inside a <sup>/<sub> was handled by pymupdf4llm and is left alone,
    unless in_tags is set (glyph edits).
    """
    cursor = 0
    applied = 0
    for edit in edits:
        pattern = _edit_pattern(edit, in_tags)
        found = None
        for start in (cursor, 0):
            for match in pattern.finditer(markdown, start):
                if in_tags or not _inside_tag(markdown[: match.start(3)]):
                    found = match
                    break
            if found:
                break
        if not found:
            continue
        new = _rewrite(found, edit.replacement)
        markdown = markdown[: found.start()] + new + markdown[found.end():]
        cursor = found.start() + len(new)
        applied += 1
    return markdown, applied
