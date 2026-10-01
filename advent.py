"""Decode Advent fonts: the math and symbol fonts of Elsevier, Springer,
Taylor & Francis and others, which carry no Unicode mapping.

A PDF typeset with Advent embeds math fonts under hashed names
(AdvP4C4E74, AdvP4C4E51, ...). Text extraction then yields each glyph's
character code, or the Latin character a standard glyph name stands for,
instead of what the glyph shows: "¼" for "=", "þ" for "+", control codes for
minus signs, "m" for μ, "e" for an en dash.

Most of these fonts are renamed TeX fonts, which fixes their layout:

  AdvP4C4E74  cmsy  math symbols      (0 −, 1 ·, 2 ×, 14 ∘ ...)
  AdvP4C4E51  cmmi  math italic       (11 α ... 61 "/", 59 ",")
  AdvP4C4E59  cmr   text accents etc. (19 ´, 20 ˇ, 95 ˙, 127 ¨ ...)
  AdvP4C4E46  cmex  big operators     (80/88 ∑, 82/90 ∫ ...)
  AdvPSSym    Adobe Symbol            (glyph "C176" is code 176, °)

(The ring ∘ is a degree sign after a number and a plot marker elsewhere;
sciformat.fix_degrees tells the two apart.)

A glyph's position in the TeX font is its name when that name is "C<n>",
and otherwise its character code. The PDF's /Differences arrays give the
names, since the character codes themselves differ from paper to paper.
The Pi fonts (Greek, arrows, dingbats) have glyph names that say nothing
("m" for μ), so they are listed per font in PI_FONTS.
"""

from __future__ import annotations

import re

import pymupdf

CMSY = (
    "−·×∗÷⋄±∓⊕⊖⊗⊘⊙◯∘•≍≡⊆⊇≤≥⪯⪰∼≈⊂⊃≪≫≺≻"
    "←→↑↓↔↗↘≃⇐⇒⇑⇓⇔↖↙∝′∞∈∋△▽/↦∀∃¬∅ℜℑ⊤⊥"
    "ℵABCDEFGHIJKLMNOPQRSTUVWXYZ∪∩⊎∧∨"
    "⊢⊣⌊⌋⌈⌉{}⟨⟩|‖↕⇕\\≀√⨿∇∫⊔⊓⊑⊒§†‡¶♣♦♥♠"
)
CMMI = (
    "ΓΔΘΛΞΠΣΥΦΨΩαβγδεζηθικλμνξπρστυφχψωεϑϖϱςφ"
    "↼↽⇀⇁’‘▹◃0123456789.,</>⋆"
    "∂ABCDEFGHIJKLMNOPQRSTUVWXYZ♭♮♯⌣⌢"
    "ℓabcdefghijklmnopqrstuvwxyzıȷ℘⃗⁀"
)
CMR = (
    "ΓΔΘΛΞΠΣΥΦΨΩﬀﬁﬂﬃﬄıȷ`´ˇ˘¯˚¸ßæœøÆŒØ̸"
    "!”#$%&’()*+,-./0123456789:;¡=¿?"
    "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[“]ˆ˙"
    "‘abcdefghijklmnopqrstuvwxyz–—˝˜¨"
)
# cmex: sized delimiters and big operators. Pieces of extensible delimiters
# (brace middles, bracket extenders) carry no text and are dropped.
CMEX = {
    **dict.fromkeys((0, 16, 18, 32), "("), **dict.fromkeys((1, 17, 19, 33), ")"),
    **dict.fromkeys((2, 20, 34, 104), "["), **dict.fromkeys((3, 21, 35, 105), "]"),
    **dict.fromkeys((4, 22, 36, 106), "⌊"), **dict.fromkeys((5, 23, 37, 107), "⌋"),
    **dict.fromkeys((6, 24, 38, 108), "⌈"), **dict.fromkeys((7, 25, 39, 109), "⌉"),
    **dict.fromkeys((8, 26, 40, 110), "{"), **dict.fromkeys((9, 27, 41, 111), "}"),
    **dict.fromkeys((10, 28, 42, 68), "⟨"), **dict.fromkeys((11, 29, 43, 69), "⟩"),
    12: "|", 13: "‖", **dict.fromkeys((14, 30, 44, 46), "/"), **dict.fromkeys((15, 31, 45, 47), "\\"),
    **dict.fromkeys(range(48, 68), ""), **dict.fromkeys((119, 122, 123, 124, 125), ""),
    **dict.fromkeys((72, 73), "∮"), **dict.fromkeys((74, 75), "⨀"), **dict.fromkeys((76, 77), "⨁"),
    **dict.fromkeys((78, 79), "⨂"), **dict.fromkeys((80, 88), "∑"), **dict.fromkeys((81, 89), "∏"),
    **dict.fromkeys((82, 90), "∫"), **dict.fromkeys((83, 91), "⋃"), **dict.fromkeys((84, 92), "⋂"),
    **dict.fromkeys((86, 94), "⋀"), **dict.fromkeys((87, 95), "⋁"), **dict.fromkeys((96, 97), "∐"),
    **dict.fromkeys(range(112, 117), "√"), 120: "↑", 121: "↓", 126: "⇑", 127: "⇓",
}

# Standard glyph names Advent uses for glyphs outside the TeX layout, in the
# cmsy-based font: they draw what the matching cmr position would.
CMSY_EXTRA_NAMES = {"onequarter": "=", "thorn": "+", "eth": "(", "Thorn": ")", "onehalf": "["}
CMSY_EXTRA_CODES = {188: "=", 254: "+", 240: "(", 222: ")", 189: "[", 138: "]"}

# Adobe Symbol encoding, codes 32-255 (Advent's symbol font names glyphs by
# these codes: "C176" is 176, the degree sign).
SYMBOL_ENCODING = dict(zip(range(32, 127), (
    " !∀#∃%&∍()∗+,−./0123456789:;<=>?"
    "≅ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ[∴]⊥_"
    "‾αβχδεφγηιϕκλμνοπθρστυϖωξψζ{|}∼"
)))
SYMBOL_ENCODING.update(zip(range(160, 256), (
    "€ϒ′≤⁄∞ƒ♣♦♥♠↔←↑→↓°±″≥×∝∂•÷≠≡≈…⏐⎯↵"
    "ℵℑℜ℘⊗⊕∅∩∪⊃⊇⊄⊂⊆∈∉∠∇®©™∏√⋅¬∧∨⇔⇐⇑⇒⇓"
    "◊〈®©™∑⎛⎜⎝⎡⎢⎣⎧⎨⎩⎪〉∫⌠⎮⌡⎞⎟⎠⎤⎥⎦⎫⎬⎭"
)))

# Fonts whose layout is known, by name. The TeX fonts themselves (CMSY10,
# CMMI7, ...) turn up without a Unicode mapping too.
# The cmsy-like ones show the tell-tale "¼ þ ð Þ" for "= + ( )" and control
# codes for −, ·, ×; the cmmi-like ones ";" ":" "=" for ", . /".
FAMILIES = {
    **dict.fromkeys(("AdvP4C4E74", "AdvMacMthSy", "AdvMacMthSyN", "AdvMT_SY", "AdvMTSYan",
                     "AdvMTSY_A", "AdvMathSymb", "Advcl-sym"), "cmsy"),
    **dict.fromkeys(("AdvP4C4E51", "AdvMacMthIt", "AdvMT_MI", "AdvMTMI"), "cmmi"),
    "AdvP4C4E59": "cmr",
    **dict.fromkeys(("AdvP4C4E46", "AdvMT_EX", "AdvMTEX", "AdvMathExtr"), "cmex"),
    "AdvPSSym": "symbol",
}
_TEX_FAMILY = [
    (re.compile(r"^CMSY\d+$"), "cmsy"),
    (re.compile(r"^CMBSY\d+$"), "cmsy"),
    (re.compile(r"^CMMIB?\d+$"), "cmmi"),
    (re.compile(r"^CMEX\d+$"), "cmex"),
    (re.compile(r"^CM(R|BX|SL|TI|SS|SSBX|SSI|BXTI|CSC)\d+$"), "cmr"),
]


def family_of(font: str) -> str | None:
    if font in FAMILIES:
        return FAMILIES[font]
    for pattern, family in _TEX_FAMILY:
        if pattern.match(font):
            return family
    return None


# Glyph names from Linotype's Mathematical Pi fonts ("H9262" is μ). The
# names are the same in every PDF, even when the character codes are not.
# Read off the glyphs themselves in a few hundred papers.
_GREEK_UPPER = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
_GREEK_LOWER = "αβγδεζηθικλμνξοπρσςτυφχψω"
MATHPI_NAMES: dict[str, str] = {
    **{f"H{9001 + i}": ch for i, ch in enumerate(_GREEK_UPPER)},
    **{f"H{9251 + i}": ch for i, ch in enumerate(_GREEK_LOWER)},
    "H9277": "ϑ", "H9278": "ϕ", "H9280": "ϵ",
    "H11001": "+", "H11002": "−", "H11003": "×", "H11005": "=", "H11006": "±",
    "H11008": "∝", "H11009": "∞", "H11011": "∼", "H11013": "≡", "H11015": "≈",
    "H11021": "<", "H11022": ">", "H11032": "′", "H11033": "″", "H11034": "°",
    "H11036": "⊥", "H11061": "≅", "H11080": "·", "H11128": "∂", "H11229": "≃",
    "H11270": "≪", "H11271": "≫", "H11349": "≤", "H11350": "≥", "H11351": "≲",
    "H11407": "≳", "H11545": "+", "H11546": "−", "H11549": "=", "H11569": "*",
    "H11612": "∇", "H12135": "◆", "H12331": "◇", "H17004": "◆", "H17009": "▲",
    "H17015": "©", "H17016": "®", "H17033": "●", "H17034": "○", "H17039": "■",
    "H17040": "□", "H17050": "©", "H18528": "•", "H20648": "‖", "H20848": "∫",
    "H20849": "(", "H20850": ")", "H20851": "[", "H20852": "]", "H20853": "{",
    "H20854": "}", "H20855": "⟨", "H20856": "⟩", "H20857": "√", "H20858": "∑",
    "H20862": "/", "H20863": "∏", "H20864": "⟦", "H20865": "⟧", "H20875": "[",
    "H20876": "]", "H20877": "{", "H20879": "|", "H20881": "√", "H20885": "∫",
    "H20888": "∑", "H22845": "☆", "H23008": "™", "H33355": "⩽", "H33356": "⩾",
    "H5008": "–", "H5009": "−", "H6036": "ℏ",
}

def _pairs(keys: str, values: str) -> dict[str, str]:
    """keys[i] -> values[i]; the two strings must line up exactly."""
    if len(keys) != len(values):
        raise ValueError(f"table length mismatch: {keys!r} vs {values!r}")
    return dict(zip(keys, values))


# Greek in alphabetical order on the Latin letters (a α, b β, c γ ... x ω),
# the layout of Advent's Mathematical Pi 1 cuts.
_GREEK_ALPHABETICAL = {
    **_pairs("abcdefghijklmnopqrstuvwx", "αβγδεζηθικλμνξοπρστυφχψω"),
    **_pairs("ABCDEFGHIJKLMNOPQRSTUVWX", _GREEK_UPPER),
}

# Pi fonts whose glyph names say nothing ("m" for μ): extracted character ->
# what the glyph draws, per font. Read off rendered glyphs of each font in
# the papers where it occurs; only characters seen are listed.
PI_FONTS: dict[str, dict[str, str]] = {
    "AdvPSMP13": {**_GREEK_ALPHABETICAL, "!": "ϒ"},
    "AdvPSMP10": {**_GREEK_ALPHABETICAL, "/": "ϕ"},
    "AdvPi1": _pairs(
        "8Dmd.7O4+baCgpst6*tLq·lFk(&S5\\",
        "°Δμδ•−Ω>±βαΨγπστ×∼τΛ∂′λΦκ†≈Σ<⊥"),
    "AdvPi2": _pairs(
        "msrcZadbolt?gkefnxyzpwjFOD%&*~^547",
        "μσρψηαδβωλτ→γκεϕνξθζπχφΦΩΔ‰□○△◇⩾⩽÷"),
    "AdvPi3": _pairs("#&~\"!^14", "©■▲▶▼◆®≫"),
    "AdvPS44A44B": _pairs("e$K]dCGD", "–·−=—+±+"),
    "AdvPSSPS-AS": _pairs(")A@&", "−–=✉"),
    "AdvPS3F4C13": _pairs("mDUua3bpdh4r", "μΔΩωαεβπδηφρ"),
    "AdvP3F4C13": _pairs("mDabvqpldUchsjVntg", "μΔαβ∂θπλδΩχησψ∇ντγ"),
    "AdvPS4721B4": _pairs("rsdb4ulhqnagUmf", "ρσδβφωληθναγΩμφ"),
    "AdvP4721B4": _pairs("hlapDndFxmbjXgckqzft", "ηλαπΔνδΦξμβψΞγχκθζφτ"),
    "AdvGreekM": _pairs("mgDbar1jlLutdwnihpVsfFCqQ", "μγΔβαρεξλΛθτδφνιηπΩσϕΦΨϑΘ"),
    "AdvPSMP4": _pairs("P[\\", "⩾><"),
    "AdvPS7DA6": _pairs("28.,#$@", "−°><≤≥≫"),
    "AdvP7DA6": _pairs("128D5m?9ad3p", "+−°Δ=μ·′αδ×π"),
    "AdvTir_symb": _pairs("-?9B", "−+×≤"),
    "AdvPS3FDD77": _pairs(",/zwy", "•⋯≈∼≅"),
    "AdvPSMPi6": _pairs("dsh.neq,jmvx", "●○□▼△◇☆▽■▲◁▷"),
    "AdvPS40C6FB": _pairs("/4[Y", "→↔↑↓"),
    "AdvPS586D": _pairs("wqe", "®©™"),
    "AdvMPi-One": _pairs(")%2^?", "—≦−≧·"),
    "Advsymbol": {"m": "μ"},
    "AdvBM13": _pairs("maDS", "μαΔΣ"),
    "AdvGRTU": _pairs("map", "μαπ"),
}

# Verified from ionic charges, ranges and pK equations in an older Elsevier
# article. Match glyph names, not byte positions: PDFs can re-encode a font.
FONT_GLYPH_NAMES = {
    "AdvBMa1": {"C28": "−", "C27": "+", "C1": "–", "C30": "="},
}

_CNAME = re.compile(r"^C(\d{1,3})$")
_UNINAME = re.compile(r"^uni([0-9A-Fa-f]{4})$")
# TeX's names for sized delimiters and big operators ("parenleftBig",
# "summationdisplay"); pieces of extensible ones carry no text.
_TEX_PIECE = re.compile(r"(ex|tp|bt|mid|extension)$")
_TEX_NAMES = [
    ("parenleft", "("), ("parenright", ")"), ("bracketleft", "["),
    ("bracketright", "]"), ("braceleft", "{"), ("braceright", "}"),
    ("angbracketleft", "⟨"), ("angbracketright", "⟩"), ("floorleft", "⌊"),
    ("floorright", "⌋"), ("ceilingleft", "⌈"), ("ceilingright", "⌉"),
    ("radical", "√"), ("summation", "∑"), ("product", "∏"),
    ("contintegral", "∮"), ("integral", "∫"), ("coproduct", "∐"),
    ("slashbig", "/"), ("slashBig", "/"), ("backslash", "\\"),
]


def _name_char(name: str) -> str | None:
    """What a glyph name says, beyond the standard names pymupdf knows."""
    m = _UNINAME.match(name)
    if m:
        return chr(int(m.group(1), 16))
    if name in MATHPI_NAMES:
        return MATHPI_NAMES[name]
    for prefix, char in _TEX_NAMES:
        if name.startswith(prefix) and name != prefix:
            return "" if _TEX_PIECE.search(name) else char
    return None


def _family_char(family: str, code: int, name: str | None) -> str | None:
    if name:
        m = _CNAME.match(name)
        if m:
            code = int(m.group(1))
        elif family == "cmsy" and name in CMSY_EXTRA_NAMES:
            return CMSY_EXTRA_NAMES[name]
    if family == "cmsy" and code in CMSY_EXTRA_CODES:
        return CMSY_EXTRA_CODES[code]
    if family == "symbol":
        return SYMBOL_ENCODING.get(code)
    if family == "cmex":
        return CMEX.get(code)
    table = {"cmsy": CMSY, "cmmi": CMMI, "cmr": CMR}[family]
    return table[code] if 0 <= code < len(table) else None


def _differences(doc, font_xref: int) -> dict[int, str]:
    """Character code -> glyph name, from the font's /Encoding /Differences."""
    try:
        kind, value = doc.xref_get_key(font_xref, "Encoding")
        if kind == "xref":  # an /Encoding object: its /Differences array
            kind, value = doc.xref_get_key(int(value.split()[0]), "Differences")
        elif kind == "dict":  # an inline /Encoding dictionary
            m = re.search(r"/Differences\s*\[(.*?)\]", value, re.S)
            kind, value = ("array", f"[{m.group(1)}]") if m else ("null", "")
        if kind == "xref":  # the array stored as an object of its own
            kind, value = "array", doc.xref_object(int(value.split()[0]))
    except (ValueError, RuntimeError):  # fonts without an object (xref 0), damaged files
        return {}
    if kind != "array":
        return {}
    names: dict[int, str] = {}
    code = 0
    for token in re.findall(r"\d+|/[^\s/\[\]]+", value):
        if token.startswith("/"):
            names[code] = token[1:]
            code += 1
        else:
            code = int(token)
    return names


def _builtin_names(doc, font_xref: int) -> dict[int, str]:
    """Character code -> glyph name from the embedded font's own encoding.

    For fonts whose PDF entry has no /Differences: Linotype's Pi fonts keep
    their "H11034"-style names in the font program itself. CFF fonts are
    read with fontTools when it is installed; Type 1 fonts list their
    encoding in plain text ("dup 56 /H11034 put").
    """
    try:
        _name, ext, _type, buf = doc.extract_font(font_xref)
    except (ValueError, RuntimeError):
        return {}
    if not buf:
        return {}
    if ext == "cff":
        try:
            import io

            from fontTools.cffLib import CFFFontSet
        except ImportError:
            return {}
        try:
            cff = CFFFontSet()
            cff.decompile(io.BytesIO(buf), None)
            encoding = cff[cff.fontNames[0]].Encoding
        except Exception:  # damaged or unusual font programs
            return {}
        if isinstance(encoding, str):  # "StandardEncoding": nothing to learn
            return {}
        return {code: name for code, name in enumerate(encoding) if name != ".notdef"}
    head = buf[:20000].decode("latin-1")
    return {int(code): name for code, name in re.findall(r"dup (\d+) /(\S+) put", head)}


def _undecoded(ch: str) -> bool:
    """A character extraction plainly failed to translate."""
    return ch == "�" or (len(ch) == 1 and (ord(ch) < 32 or 0xE000 <= ord(ch) <= 0xF8FF))


class PageDecoder:
    """Translate the characters of fonts without a usable Unicode mapping."""

    def __init__(self, doc, page, cache: dict | None = None):
        """`cache` (shared by the pages of one document) keeps each font's
        table, so embedded font programs are read only once."""
        cache = {} if cache is None else cache
        # font name -> ({extracted character: decoded text}, only fix failures)
        self.tables: dict[str, tuple[dict[str, str], bool]] = {}
        for xref, _ext, _type, basefont, _ref, _enc in page.get_fonts():
            name = basefont.split("+")[-1]
            if name in self.tables:
                continue
            family = family_of(name)
            table = self._build(doc, xref, family, cache, font=name)
            if table:
                # A font with a Unicode mapping is trusted except where it
                # plainly failed; Advent's own mappings are identity maps
                # ("¼" for "="), so a known layout overrides them.
                try:
                    has_map = doc.xref_get_key(xref, "ToUnicode")[0] != "null"
                except (ValueError, RuntimeError):
                    has_map = False
                self.tables[name] = (table, has_map and family is None)

    @staticmethod
    def _build(
        doc, xref: int, family: str | None, cache: dict, font: str | None = None
    ) -> dict[str, str]:
        if xref in cache:
            return cache[xref]
        table: dict[str, str] = {}
        known_names = FONT_GLYPH_NAMES.get(font, {})
        names = _differences(doc, xref)
        if not names and not family:
            # Only names that say something are used from the font program
            # (Pi "H" names, uniXXXX); standard ones pymupdf already knows.
            names = {c: n for c, n in _builtin_names(doc, xref).items()
                     if _name_char(n) is not None or n in known_names}
        # What pymupdf reports for a code: the glyph name's character when
        # the name is a standard one, else the raw code.
        for code, name in names.items():
            uni = pymupdf.glyph_name_to_unicode(name)  # U+FFFD if unknown
            standard = bool(uni) and uni not in (0, 0xFFFD)
            extracted = chr(uni) if standard else chr(code)
            decoded = known_names.get(name)
            if decoded is None:
                decoded = None if standard and not family else _name_char(name)
            if decoded is None and family:
                decoded = _family_char(family, code, name)
            if decoded is not None:
                table[extracted] = decoded
        if family:
            for code in range(0, 256):
                ch = chr(code)
                if ch in table or code in names:
                    continue
                decoded = _family_char(family, code, None)
                if decoded is not None and decoded != ch:
                    table[ch] = decoded
        cache[xref] = table
        return table

    def decode(self, font: str, ch: str) -> str | None:
        """What `ch` in `font` really is, or None if it stands for itself.

        Spaces are never decoded: pymupdf inserts them for gaps between
        glyphs, so they are not the font's code 32. In a font of unknown
        layout that has a Unicode mapping, only characters it failed on
        are touched.
        """
        if ch.isspace():
            return None
        entry = self.tables.get(font)
        if entry is None and len(font) >= 20:
            # Span font names are cut to 24 characters ("...GreekwithMathP").
            entry = next((v for k, v in self.tables.items() if k.startswith(font)), None)
        table, failures_only = entry or (None, False)
        if table is None:
            table = PI_FONTS.get(font)
        elif failures_only and not _undecoded(ch):
            return None
        if not table:
            return None
        decoded = table.get(ch)
        return decoded if decoded is not None and decoded != ch else None
