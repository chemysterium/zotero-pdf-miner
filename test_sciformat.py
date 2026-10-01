"""Unit tests for the text clean-up and edit matching.

Run with:  python -m unittest -v
"""

import unittest
from unittest.mock import Mock, patch

import advent
import pdf_hints
import sciformat
from pdf_hints import Edit


class ScriptTests(unittest.TestCase):
    def test_unicode_when_every_char_has_a_form(self):
        self.assertEqual(sciformat.convert_scripts("Ca<sup>2+</sup>"), "Ca²⁺")
        self.assertEqual(sciformat.convert_scripts("10<sup>−6 </sup>"), "10⁻⁶")
        self.assertEqual(sciformat.convert_scripts("H<sub>2</sub>O"), "H₂O")

    def test_html_kept_when_unicode_lacks_a_glyph(self):
        self.assertEqual(sciformat.convert_scripts("V<sub>c</sub>"), "V<sub>c</sub>")
        self.assertEqual(sciformat.convert_scripts("Chen<sup>a,*</sup>"), "Chen<sup>a,*</sup>")

    def test_adjacent_tags_merge(self):
        self.assertEqual(sciformat.convert_scripts("SO<sub>4</sub><sup>2</sup><sup>−</sup>"), "SO₄²⁻")

    def test_html_mode(self):
        self.assertEqual(sciformat.convert_scripts("Ca<sup>2+</sup>", "html"), "Ca<sup>2+</sup>")


class DegreeTests(unittest.TestCase):
    def test_raised_ring(self):
        self.assertEqual(sciformat.fix_degrees("288<sup>◦</sup> C"), "288°C")
        self.assertEqual(sciformat.fix_degrees("at 60<sup>o</sup>C"), "at 60°C")

    def test_angle(self):
        self.assertEqual(sciformat.fix_degrees("2θ = 5<sup>◦</sup>–80<sup>◦</sup>"), "2θ = 5°–80°")

    def test_plain_ring(self):
        self.assertEqual(sciformat.fix_degrees("T = 228 ◦C"), "T = 228 °C")

    def test_ring_is_degree_only_after_a_number(self):
        self.assertEqual(sciformat.fix_degrees("at 25 ∘C and 90∘"), "at 25 °C and 90°")
        self.assertEqual(sciformat.fix_degrees("ligand bound; ∘, sorption"), "ligand bound; ∘, sorption")

    def test_bullet_left_alone(self):
        self.assertEqual(sciformat.fix_degrees("◦ first item"), "◦ first item")


class AccentTests(unittest.TestCase):
    def test_word_map_from_pdf(self):
        words = {"Ikalainen": "Ikäläinen"}
        self.assertEqual(sciformat.fix_accents("T. Ikal¨ ainen¨, A.", words), "T. Ikäläinen, A.")

    def test_guess_when_unmapped(self):
        self.assertEqual(sciformat.fix_accents("K. Sipila¨ et al."), "K. Sipilä et al.")
        self.assertEqual(sciformat.fix_accents("P. Ferreiros´"), "P. Ferreirós")
        self.assertEqual(sciformat.fix_accents("Sipil¨a"), "Sipilä")

    def test_caron_goes_on_consonant(self):
        self.assertEqual(sciformat.fix_accents("Bene ˇs"), "Bene ˇs".replace(" ˇs", " š"))

    def test_apostrophe_is_not_an_accent(self):
        self.assertEqual(sciformat.fix_accents("Dunn´s test"), "Dunn’s test")

    def test_orphan_accent_fixes_the_word_before(self):
        words = {"Leon": "León"}
        self.assertEqual(sciformat.fix_accents("**T. Leon:** ´ Concept", words),
                         "**T. León:** Concept")
        self.assertEqual(sciformat.fix_accents("performance of ¨ five", words),
                         "performance of five")

    def test_acute_after_digit_is_a_prime(self):
        self.assertEqual(sciformat.fix_accents("the 5´-end"), "the 5′-end")

    def test_math_accent_on_lone_letter(self):
        self.assertEqual(sciformat.fix_accents("mean x¯ of"), "mean x̄ of")

    def test_dotless_i(self):
        self.assertEqual(sciformat.compose("ı", "́"), "í")
        self.assertEqual(sciformat.fix_accents("Jelı´nek"), "Jelínek")


class NumberTests(unittest.TestCase):
    def test_times_ten(self):
        self.assertEqual(sciformat.fix_numbers("8.102× 10⁻³"), "8.102 × 10⁻³")
        self.assertEqual(sciformat.fix_numbers("1.5 x 10−3 M"), "1.5 × 10⁻³ M")
        self.assertEqual(sciformat.fix_numbers("2 x 10-15 min"), "2 x 10-15 min")

    def test_isotopes(self):
        self.assertEqual(sciformat.fix_numbers("the ⁶ Li/⁷ Li ratio"), "the ⁶Li/⁷Li ratio")
        self.assertEqual(sciformat.fix_numbers("(¹³ C NMR)"), "(¹³C NMR)")

    def test_footnote_marks_are_not_isotopes(self):
        self.assertEqual(sciformat.fix_numbers("result.¹ In this"), "result.¹ In this")
        self.assertEqual(sciformat.fix_numbers("shown ² As a rule"), "shown ² As a rule")

    def test_micro(self):
        self.assertEqual(sciformat.fix_numbers("0.45 µm"), "0.45 μm")


class SpacingTests(unittest.TestCase):
    def test_space_before_punctuation(self):
        self.assertEqual(sciformat.fix_spacing("Ca²⁺ , Mg²⁺ and ( a )"), "Ca²⁺, Mg²⁺ and (a)\n")

    def test_letter_spaced_headings(self):
        self.assertEqual(sciformat.fix_spacing("#### H I G H L I G H T S"), "#### HIGHLIGHTS\n")
        self.assertEqual(sciformat.fix_spacing("#### A R T I C L E I N F O"), "#### ARTICLE INFO\n")

    def test_restore_hyphens(self):
        text = "high-salinity brine; highsalinity wastewater"
        self.assertEqual(sciformat.restore_hyphens(text), "high-salinity brine; high-salinity wastewater")


class UnmappedGlyphTests(unittest.TestCase):
    def test_context_guesses(self):
        self.assertEqual(sciformat.fix_unmapped_glyphs("Na₂WO₄ � 2H₂O"), "Na₂WO₄·2H₂O")
        self.assertEqual(sciformat.fix_unmapped_glyphs("(Cl<sup>�</sup>, x"), "(Cl⁻, x")
        self.assertEqual(sciformat.fix_unmapped_glyphs("at 50<sup>�</sup> C"), "at 50°C")
        self.assertEqual(sciformat.fix_unmapped_glyphs("8.00�10⁻⁴"), "8.00 × 10⁻⁴")
        self.assertEqual(sciformat.fix_unmapped_glyphs("s [m<sup>�3</sup>]"), "s [m<sup>−3</sup>]")

    def test_fallback_guess(self):
        text = sciformat.postprocess("h<sup>�1</sup> " + "x" * 300,
                                    fffd_guess="−", guess_glyphs=True)
        self.assertTrue(text.startswith("h⁻¹"))


def _span(text, font="Times", size=10.0, y=100.0, x=0.0):
    return {"text": text, "font": font, "size": size, "origin": (x, y),
            "bbox": (x, y - size, x + 5 * len(text), y)}


class LineHintTests(unittest.TestCase):
    """What the pre-pass reads from a line of spans."""

    @staticmethod
    def _symbol_edits(spans):
        """_line_edits plus the settling collect_hints() does at the end."""
        hints = pdf_hints.PdfHints()
        edits = pdf_hints._line_edits({"dir": (1, 0), "spans": spans}, hints)
        for edit, font in hints.pending_symbols:
            edit.replacement = pdf_hints._symbol_text(edit.target, font in hints.raw_symbol_fonts)
        return [e for e in edits if e.replacement != e.target]

    def test_symbol_font_letter(self):
        edits = self._symbol_edits(
            [_span("Sample of 10 "), _span("m", font="SymbolMT"), _span("g/L was used")])
        md, n = pdf_hints.apply_edits("Sample of 10 mg/L was used", edits)
        self.assertEqual(md, "Sample of 10 μg/L was used")

    def test_symbol_private_use_glyph(self):
        self.assertEqual(pdf_hints._symbol_text(""), "αβ")
        self.assertEqual(pdf_hints._symbol_text("", raw_font=False), "×")

    def test_mapped_symbol_font_keeps_its_symbols(self):
        # "·" from a Symbol font with a Unicode mapping is a real middle dot.
        edits = self._symbol_edits([_span("mol"), _span("·", font="SymbolMT"), _span("L")])
        self.assertEqual(edits, [])

    def test_unmapped_symbol_font_translates_its_symbols(self):
        edits = self._symbol_edits([
            _span("where "), _span("a", font="SymbolMT"), _span(" is 5 "),
            _span("´", font="SymbolMT"), _span(" 10")])
        self.assertEqual([e.replacement for e in edits], ["α", "×"])

    def test_long_left_context_keeps_the_near_end(self):
        edits = self._symbol_edits(
            [_span("x" * 40 + "10"), _span("m", font="SymbolMT"), _span("g")])
        self.assertTrue(edits[0].before.endswith("10"))

    def test_sub_and_superscripts(self):
        line = {"dir": (1, 0), "spans": [
            _span("with CO"), _span("2", size=7, y=102), _span(" and Ca"),
            _span("2+", size=7, y=96), _span(" ions")]}
        edits = pdf_hints._line_edits(line, pdf_hints.PdfHints())
        self.assertEqual([e.replacement for e in edits], ["<sub>2</sub>", "<sup>2+</sup>"])

    def test_isotope_superscript_anchors_right(self):
        line = {"dir": (1, 0), "spans": [
            _span("the "), _span("6", size=7, y=96), _span("Li content")]}
        (edit,) = pdf_hints._line_edits(line, pdf_hints.PdfHints())
        self.assertEqual((edit.before, edit.after), ("", "Li"))


class AdventTests(unittest.TestCase):
    """Decoding of fonts without a Unicode mapping (see advent.py)."""

    def test_tex_positions(self):
        self.assertEqual(advent._family_char("cmsy", 1, "C14"), "∘")  # name wins over code
        self.assertEqual(advent._family_char("cmsy", 2, "C0"), "−")
        self.assertEqual(advent._family_char("cmsy", 188, "onequarter"), "=")
        self.assertEqual(advent._family_char("cmmi", 61, None), "/")
        self.assertEqual(advent._family_char("cmr", 95, None), "˙")
        self.assertEqual(advent._family_char("symbol", 3, "C176"), "°")

    def test_old_elsevier_charge_range_and_equals_glyphs(self):
        # A second encoding moves the same glyph names to different bytes.
        for offset in (0, 64):
            with self.subTest(offset=offset), \
                    patch.object(advent, "_differences", return_value={
                        1 + offset: "C28", 2 + offset: "C27",
                        3 + offset: "C1", 4 + offset: "C30",
                    }):
                table = advent.PageDecoder._build(Mock(), 1, None, {}, font="AdvBMa1")
                self.assertEqual(table, {chr(1 + offset): "−", chr(2 + offset): "+",
                                         chr(3 + offset): "–", chr(4 + offset): "="})
                self.assertEqual(advent.PageDecoder._build(Mock(), 1, None, {}, font="OtherFont"), {})

    def test_glyph_names(self):
        self.assertEqual(advent._name_char("H9262"), "μ")
        self.assertEqual(advent._name_char("H9269"), "ς")
        self.assertEqual(advent._name_char("uni2212"), "−")
        self.assertEqual(advent._name_char("parenleftBig"), "(")
        self.assertEqual(advent._name_char("bracketleftex"), "")
        self.assertIsNone(advent._name_char("C14"))

    def test_family_names(self):
        self.assertEqual(advent.family_of("CMSY10"), "cmsy")
        self.assertEqual(advent.family_of("AdvP4C4E51"), "cmmi")
        self.assertIsNone(advent.family_of("TimesNewRomanPSMT"))

    def test_truncated_span_font_name(self):
        decoder = advent.PageDecoder.__new__(advent.PageDecoder)
        decoder.tables = {"Universal-GreekwithMathPi": ({"8": "°"}, False)}
        self.assertEqual(decoder.decode("Universal-GreekwithMathP", "8"), "°")
        self.assertIsNone(decoder.decode("Universal-GreekwithMathP", " "))

    def test_pi_font_tables(self):
        self.assertEqual(advent.PI_FONTS["AdvPSMP13"]["l"], "μ")
        self.assertEqual(advent.PI_FONTS["AdvPi1"]["m"], "μ")
        with self.assertRaises(ValueError):
            advent._pairs("ab", "α")

    def test_moved_accent(self):
        words = {"Rozej": "Rożej", "Muller": "Müller"}
        self.assertEqual(sciformat.fix_moved_accents("A. Rozej_ and", words, {"_"}), "A. Rożej and")
        self.assertEqual(sciformat.fix_moved_accents("see _Muller_ here", words, {"_"}),
                         "see _Muller_ here")
        self.assertEqual(sciformat.fix_moved_accents("Hrub�y", {"Hruby": "Hrubý"}, {"�"}),
                         "Hrubý")


class EditTests(unittest.TestCase):
    def test_subscript_edit(self):
        md, n = pdf_hints.apply_edits("precipitate CaSO4 with", [Edit("CaSO", "4", "", "<sub>4</sub>")])
        self.assertEqual((md, n), ("precipitate CaSO<sub>4</sub> with", 1))

    def test_chained_edits(self):
        edits = [Edit("Na", "2", "SO4", "<sub>2</sub>"), Edit("Na2SO", "4", "", "<sub>4</sub>")]
        md, n = pdf_hints.apply_edits("NaCl and Na2SO4.", edits)
        self.assertEqual(md, "NaCl and Na<sub>2</sub>SO<sub>4</sub>.")

    def test_no_match_inside_word(self):
        md, n = pdf_hints.apply_edits("XCaSO4", [Edit("CaSO", "4", "", "<sub>4</sub>")])
        self.assertEqual(n, 0)

    def test_already_tagged_is_left_alone(self):
        text = "removal of Ca<sup>2+</sup> via"
        md, n = pdf_hints.apply_edits(text, [Edit("Ca", "2+", "", "<sup>2+</sup>")])
        self.assertEqual((md, n), (text, 0))

    def test_italics_stay_balanced(self):
        md, _ = pdf_hints.apply_edits("(_K_ _sp_) of", [Edit("K", "sp", "", "<sub>sp</sub>")])
        self.assertEqual(md.count("_") % 2, 0)
        self.assertIn("K<sub>sp</sub>", md)

    def test_recurring_fragment_uses_next_occurrence(self):
        edits = [Edit("CO", "2", "", "<sub>2</sub>")] * 2
        md, n = pdf_hints.apply_edits("CO2 and CO2", edits)
        self.assertEqual((md, n), ("CO<sub>2</sub> and CO<sub>2</sub>", 2))

    def test_unmapped_glyph_matches_replacement_char(self):
        md, n = pdf_hints.apply_edits(
            "Na2WO4 � 2H2O", [Edit("Na2WO4 ", "\x01", " 2H2O,", "·")]
        )
        self.assertEqual(n, 0)  # trailing comma in the anchor is not in the text
        md, n = pdf_hints.apply_edits("Na2WO4 � 2H2O,", [Edit("Na2WO4 ", "\x01", " 2H2O,", "·")])
        self.assertEqual(md, "Na2WO4 · 2H2O,")


if __name__ == "__main__":
    unittest.main()
