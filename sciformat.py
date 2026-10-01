"""Clean up the Markdown pymupdf4llm makes of a scientific paper.

pymupdf4llm gets the structure right (headings, paragraphs, tables), but the
details that carry meaning in a paper come out damaged:

  - sub/superscripts: "Ca<sup>2+</sup>", "10<sup>−6 </sup>", "CaSO4"
  - degrees drawn with a ring glyph: "288<sup>◦</sup> C"
  - TeX accents split off their letter: "Sipila¨", "Ferreiros´"
  - letter-spaced headings: "H I G H L I G H T S"
  - ligatures, micro signs, stray spaces before punctuation

Everything here is a pure text transformation. Fixes that need to know what
the PDF looked like (which spans were lowered or raised, which letter an
accent sat on) are gathered from the PDF by pdf_hints.py and handed in.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Sub/superscripts
# --------------------------------------------------------------------------

SUPERSCRIPTS = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
    "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "−": "⁻", "–": "⁻",
    "=": "⁼", "(": "⁽", ")": "⁾", "n": "ⁿ", "i": "ⁱ",
}

SUBSCRIPTS = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
    "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "−": "₋", "=": "₌",
    "(": "₍", ")": "₎", "a": "ₐ", "e": "ₑ", "o": "ₒ", "x": "ₓ", "h": "ₕ",
    "k": "ₖ", "l": "ₗ", "m": "ₘ", "n": "ₙ", "p": "ₚ", "s": "ₛ", "t": "ₜ",
    "i": "ᵢ", "j": "ⱼ", "r": "ᵣ", "u": "ᵤ", "v": "ᵥ",
}

SUP_CHARS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ"
SUB_CHARS = "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜᵢⱼᵣᵤᵥ"

_SCRIPT_TAG = re.compile(r"<(sup|sub)>(.*?)</\1>", re.DOTALL)


def _merge_adjacent_tags(text: str) -> str:
    """"<sup>2</sup><sup>+</sup>" -> "<sup>2+</sup>", and trim inner spaces."""
    text = re.sub(r"</(sup|sub)>\s*<\1>", "", text)
    text = re.sub(r"<(sup|sub)>\s+", r"<\1>", text)
    text = re.sub(r"\s+</(sup|sub)>", r"</\1>", text)
    return re.sub(r"<(sup|sub)></\1>", "", text)


def convert_scripts(text: str, mode: str = "unicode") -> str:
    """Render <sup>/<sub> spans as Unicode where every character allows it.

    mode "unicode" turns "10<sup>−3</sup>" into "10⁻³" and keeps the HTML tag
    only when a character has no Unicode form ("<sup>a,*</sup>", "V<sub>c</sub>"),
    so the file stays readable as plain text and still renders correctly.
    mode "html" leaves every tag in place.
    """
    text = _merge_adjacent_tags(text)
    if mode == "html":
        return text

    def replace(match: re.Match) -> str:
        tag, inner = match.group(1), match.group(2)
        table = SUPERSCRIPTS if tag == "sup" else SUBSCRIPTS
        if inner and all(ch in table for ch in inner):
            return "".join(table[ch] for ch in inner)
        return match.group(0)

    return _SCRIPT_TAG.sub(replace, text)


# --------------------------------------------------------------------------
# Degrees
# --------------------------------------------------------------------------

# Publishers draw the degree sign as a raised small ring, white bullet (U+25E6),
# ring operator (U+2218) or a superscript "o"; all of these mean °.
_RING = "◦∘°º"


def fix_degrees(text: str) -> str:
    # A raised ring (or "o") in front of a temperature unit: 288<sup>◦</sup> C
    text = re.sub(rf"<sup>\s*[{_RING}o]\s*</sup>\s*([CFK])(?![A-Za-z])", r"°\1", text)
    # A raised ring on its own is an angle: 2θ = 5<sup>◦</sup>–80<sup>◦</sup>
    text = re.sub(rf"<sup>\s*[{_RING}]\s*</sup>", "°", text)
    # Unraised ring glyphs: "228 ◦C", "90◦". Only after a digit or before a
    # unit, since ◦ is also a list bullet and ∘ is function composition.
    text = re.sub(rf"[{_RING}] ?([CF])(?![A-Za-z])", r"°\1", text)
    text = re.sub(rf"(?<=\d)\s?[◦∘º](?![\w])", "°", text)
    return text


# --------------------------------------------------------------------------
# Accents
# --------------------------------------------------------------------------

# Spacing (standalone) accent characters that TeX-built PDFs emit as separate
# glyphs, mapped to the combining mark they stand for.
SPACING_ACCENTS = {
    "¨": "̈", "´": "́", "`": "̀", "ˆ": "̂", "˜": "̃",
    "ˇ": "̌", "˘": "̆", "˚": "̊", "¸": "̧", "˙": "̇",
    "˝": "̋", "˛": "̨", "¯": "̄",
}
# Grave (`) is left out of text matching: in Markdown it is a code fence.
_ACC = "".join(ch for ch in SPACING_ACCENTS if ch != "`")
_LETTER = r"A-Za-zÀ-ÖØ-öø-ɏ"
_ACCENT_WORD = re.compile(rf"[{_LETTER}{_ACC}]*[{_ACC}][{_LETTER}{_ACC}]*")
_VOWELS = set("aeiouyıAEIOUY")
# Accents that live mostly on consonants (š, ç, ę ...).
_CONSONANT_ACCENTS = {"ˇ", "¸", "˛"}


def skeleton(word: str) -> str:
    """A word with every accent (spacing or combined) and space removed."""
    decomposed = unicodedata.normalize("NFD", word.replace("ı", "i").replace("ȷ", "j"))
    return "".join(
        ch for ch in decomposed
        if not unicodedata.combining(ch) and ch not in SPACING_ACCENTS and not ch.isspace()
    )


def compose(letter: str, combining: str) -> str | None:
    """letter + combining mark as one precomposed character, if one exists.

    Dotless ı/ȷ are what TeX puts under an accent ("ı´" is í), so they are
    swapped for i/j first.
    """
    letter = {"ı": "i", "ȷ": "j"}.get(letter, letter)
    composed = unicodedata.normalize("NFC", letter + combining)
    return composed if len(composed) == 1 else None


def _compose(letter: str, accent: str) -> str | None:
    return compose(letter, SPACING_ACCENTS[accent])


def _guess_accents(word: str) -> str:
    """Attach each spacing accent to a neighbouring letter, if one fits.

    The fallback for words the PDF pass could not map, so it stays close:
    for vowel accents, an adjacent vowel, then a vowel two places off
    ("Ferreiros´" -> "Ferreirós"), then an adjacent consonant; carons,
    cedillas and ogoneks take the adjacent letters in order. A lone letter
    with an accent is math ("x¯", "ˆy") and gets a combining mark (x̄, ŷ).
    Anything else is left exactly as it was.
    """
    chars = list(word)
    letters = [c for c in chars if c.isalpha() and c not in SPACING_ACCENTS]

    def is_letter(j: int) -> bool:
        return 0 <= j < len(chars) and chars[j].isalpha() and chars[j] not in SPACING_ACCENTS

    for i, ch in enumerate(chars):
        if ch not in SPACING_ACCENTS:
            continue
        neighbours = [j for j in (i - 1, i + 1) if is_letter(j)]
        if ch in _CONSONANT_ACCENTS:
            candidates = neighbours
        else:
            near_vowels = [j for j in neighbours if chars[j] in _VOWELS]
            far_vowels = [j for j in (i - 2, i + 2) if is_letter(j) and chars[j] in _VOWELS]
            candidates = near_vowels + far_vowels + [j for j in neighbours if j not in near_vowels]
        for j in candidates:
            composed = _compose(chars[j], ch)
            if composed:
                chars[j], chars[i] = composed, ""
                break
        else:
            if len(letters) == 1 and neighbours:
                j = neighbours[0]
                chars[j], chars[i] = chars[j] + SPACING_ACCENTS[ch], ""
    return "".join(chars)


def fix_accents(text: str, words: dict[str, str] | None = None) -> str:
    """Rejoin accents split off their letters: "Sipila¨" -> "Sipilä".

    `words` maps the accent-free skeleton of a word to its correct spelling,
    as read from glyph positions in the PDF (see pdf_hints). A broken word is
    looked up alone and joined with its neighbour, since pymupdf4llm sometimes
    breaks a word at the accent ("Ikal¨ ainen¨" -> "Ikäläinen").
    """
    words = words or {}
    # An acute before a word-final "s" is an apostrophe typed on the wrong
    # key ("Dunn´s test"), not an accent.
    text = re.sub(r"(?<=[A-Za-z])´(?=s\b)", "’", text)
    out: list[str] = []
    pos = 0
    for match in _ACCENT_WORD.finditer(text):
        if match.start() < pos:
            continue
        start, end = match.start(), match.end()
        if not any(ch.isalpha() and ch not in SPACING_ACCENTS for ch in match.group(0)):
            before = text[pos:start]
            if match.group(0) == "´" and before[-1:].isdigit():
                out.append(before + "′")  # a prime typed as an acute: "5´-end"
                pos = end
                continue
            # An orphan: pymupdf4llm moved the accent out of its word
            # ("T. Leon:** ´ Concept"). The word is usually just before it;
            # fix that from the PDF's spelling, and drop the accent.
            prev = None
            for w in re.finditer(rf"[{_LETTER}]{{2,}}", before[-40:]):
                prev = w
            fixed = words.get(skeleton(prev.group(0))) if prev else None
            if fixed and fixed != prev.group(0):
                offset = pos + len(before) - len(before[-40:])
                out.append(text[pos:offset + prev.start()])
                out.append(fixed)
                out.append(text[offset + prev.end():start].rstrip(" "))
            else:
                out.append(before.rstrip(" "))
            pos = end
            continue
        candidates = []
        after = re.match(rf" ([{_LETTER}{_ACC}]+)", text[end:])
        if after:
            candidates.append((start, end + after.end()))
        before = re.search(rf"([{_LETTER}]+) $", text[pos:start])
        if before:
            candidates.append((pos + before.start(1), end))
        candidates.append((start, end))

        replacement = None
        for c_start, c_end in candidates:
            fixed = words.get(skeleton(text[c_start:c_end]))
            if fixed:
                replacement = (c_start, c_end, fixed)
                break
        if replacement is None:
            replacement = (start, end, _guess_accents(match.group(0)))
        c_start, c_end, fixed = replacement
        out.append(text[pos:c_start])
        out.append(fixed)
        pos = c_end
    out.append(text[pos:])
    return "".join(out)


def fix_moved_accents(text: str, words: dict[str, str], extra: set[str]) -> str:
    """Repair words whose raw accent character pymupdf4llm moved or kept.

    Advent fonts draw accents with ordinary codes ("_" is a dot above, a
    control code a caron, which arrives as "�"), and pymupdf4llm may put
    them anywhere in the word: "Ro_zej" comes out as "Rozej_". Such a word
    is fixed only when the PDF pass saw it spelled properly ("Rożej").
    An even number of "_" is italics, not an accent.
    """
    extra_class = "".join(re.escape(ch) for ch in sorted(extra))
    token = re.compile(rf"[{_LETTER}{extra_class}]*[{extra_class}][{_LETTER}{extra_class}]*")

    def replace(m: re.Match) -> str:
        word = m.group(0)
        if set(word) & extra == {"_"} and word.count("_") % 2 == 0:
            return word
        letters = "".join(ch for ch in word if ch not in extra)
        fixed = words.get(skeleton(letters)) if len(letters) >= 2 else None
        return fixed or word

    return token.sub(replace, text)


# --------------------------------------------------------------------------
# Numbers, isotopes, units
# --------------------------------------------------------------------------

ELEMENTS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe "
    "Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg "
    "Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr"
).split()
_ELEMENT_RE = "|".join(sorted(ELEMENTS, key=len, reverse=True))
_WORDLIKE_ELEMENTS = {"I", "In", "As", "At", "Be", "He", "No", "Am", "Es", "Ho", "Po", "Re", "Pa", "Ga", "Os"}


def fix_unmapped_glyphs(text: str, scripts: str = "unicode") -> str:
    """Best guesses for U+FFFD glyphs the PDF pass could not place.

    Fonts without a Unicode mapping come out as "�". Where the context
    leaves only one reading, it is filled in: a raised one after a number
    before C is a degree, after an ion it is a charge, and one in front of
    "2H₂O" is the hydrate dot.
    """
    text = re.sub(r"(?<=\d)\s?<sup>�</sup>\s?(?=[CFK](?![A-Za-z]))", "°", text)
    charge = "<sup>−</sup>" if scripts == "html" else "⁻"
    text = re.sub(r"(?<=[A-Za-z)\]])<sup>�</sup>", charge, text)
    text = re.sub(r"(?<=[A-Za-z0-9₀-₉]) ?� ?(?=\d*\s?H₂O\b)", "·", text)
    # Between a number and a power of ten: "8.00�10⁻⁴" is 8.00 × 10⁻⁴.
    text = re.sub(rf"(?<=\d) ?� ?(?=10(?:[{SUP_CHARS}]|<sup>|[−-]\d))", " × ", text)
    # Leading a superscript it is a minus sign: "OH<sup>�</sup>", "m<sup>�3</sup>".
    text = re.sub(r"<sup>�(?=\d*</sup>)", "<sup>−", text)
    return text


def fix_numbers(text: str, scripts: str = "unicode") -> str:
    # "8.102× 10⁻³" / "8.102 x10⁻³" -> "8.102 × 10⁻³"
    # A typographic minus after the 10 ("x 10−3") also marks an exponent; a
    # hyphen doesn't ("2 x 10-15 min" is a range).
    text = re.sub(rf"(\d)\s*[×xX]\s*10(?=[{SUP_CHARS}]|<sup>|−\d)", r"\1 × 10", text)
    # Exponent that lost its raising: "1.5 × 10−3" -> "1.5 × 10⁻³"
    text = re.sub(
        r"(\d) × 10([−-])(\d{1,3})(?![\d.,])",
        lambda m: f"{m.group(1)} × 10" + (
            f"<sup>−{m.group(3)}</sup>" if scripts == "html"
            else "⁻" + "".join(SUPERSCRIPTS[d] for d in m.group(3))
        ),
        text,
    )
    # Isotopes: "⁶ Li" -> "⁶Li", only where the superscript starts a word
    # (after a space, bracket or slash — a footnote mark follows a word or
    # punctuation: "result.¹ In this"). Element symbols that are also words
    # (In, As, I, ...) must not be followed by a lowercase word.
    def join_isotope(m: re.Match) -> str:
        if m.group(2) in _WORDLIKE_ELEMENTS and re.match(r" [a-z]", m.string[m.end():]):
            return m.group(0)
        return m.group(1) + m.group(2)

    text = re.sub(
        rf"(?:^|(?<=[\s(\[/]))([⁰¹²³⁴⁵⁶⁷⁸⁹]+) ({_ELEMENT_RE})(?![a-z])",
        join_isotope, text, flags=re.MULTILINE,
    )
    text = re.sub(
        rf"(?:^|(?<=[\s(\[/]))(<sup>\d+</sup>) ({_ELEMENT_RE})(?![a-z])",
        join_isotope, text, flags=re.MULTILINE,
    )
    # Micro sign (U+00B5) -> Greek mu (U+03BC), so "µm" and "μm" search alike.
    return text.replace("µ", "μ")


# --------------------------------------------------------------------------
# Whitespace and layout debris
# --------------------------------------------------------------------------

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}

# Spaced-out headings that don't rejoin into the right words by themselves.
_SPACED_HEADINGS = {
    "ARTICLEINFO": "ARTICLE INFO",
    "GRAPHICALABSTRACT": "GRAPHICAL ABSTRACT",
    "ARTICLEHISTORY": "ARTICLE HISTORY",
}

_FIGURE_TEXT = re.compile(
    r"<!-- Start of picture text -->.*?<!-- End of picture text -->\n?", re.DOTALL
)


def fix_spacing(text: str) -> str:
    # pymupdf4llm puts a space after every styled span: "Ca²⁺ , Mg²⁺ ."
    text = re.sub(rf"(?<=[{SUP_CHARS}{SUB_CHARS}>°]) +([,;:.)\]])", r"\1", text)
    text = re.sub(r"(?<=\w) +([,;])(?= )", r"\1", text)
    text = re.sub(r"\( +(?=\S)", "(", text)
    # Ranges: "5° –80°" -> "5°–80°"
    text = re.sub(r"(?<=[°\d]) +([–—])(?=\d)", r"\1", text)
    text = re.sub(r"(?<=\S) +\)", ")", text)
    # Letter-spaced headings: "#### H I G H L I G H T S" -> "#### HIGHLIGHTS"
    def join_heading(m: re.Match) -> str:
        joined = m.group(2).replace(" ", "")
        return m.group(1) + _SPACED_HEADINGS.get(joined, joined)

    text = re.sub(r"(?m)^(#{1,6}\s+)((?:[A-Z] ){3,}[A-Z])\s*$", join_heading, text)
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def restore_hyphens(text: str) -> str:
    """Put back hyphens that line-end dehyphenation removed from compounds.

    pymupdf4llm joins "high-\\nsalinity" into "highsalinity". If the same
    paper writes "high-salinity" elsewhere, that is the spelling it uses.
    """
    compounds = set(re.findall(r"\b([a-z]{2,})-([a-z]{3,})\b", text))
    for first, second in compounds:
        text = re.sub(rf"\b{first}{second}\b", f"{first}-{second}", text)
    return text


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def postprocess(
    text: str,
    accent_words: dict[str, str] | None = None,
    scripts: str = "unicode",
    keep_figure_text: bool = False,
    fffd_guess: str | None = None,
    extra_accents: set[str] | None = None,
    guess_glyphs: bool = False,
) -> str:
    """Run every clean-up in order.

    When guess_glyphs is enabled, fffd_guess replaces "�" glyphs nothing else
    could resolve; the PDF pass supplies it when nearly all of a paper's
    unmapped glyphs were one symbol. Guesses are disabled by default.
    """
    for lig, plain in _LIGATURES.items():
        text = text.replace(lig, plain)
    if not keep_figure_text:
        text = _FIGURE_TEXT.sub("", text)
    text = fix_accents(text, accent_words)
    if extra_accents and accent_words:
        text = fix_moved_accents(text, accent_words, extra_accents)
    text = fix_degrees(text)
    text = convert_scripts(text, "html")  # merge/trim tags before matching them
    if guess_glyphs:
        text = fix_unmapped_glyphs(text, scripts="html")
    if guess_glyphs and fffd_guess:
        text = text.replace("�", fffd_guess)
    text = fix_numbers(text, scripts="html")
    text = convert_scripts(text, scripts)
    text = restore_hyphens(text)
    text = fix_spacing(text)
    return unicodedata.normalize("NFC", text)
