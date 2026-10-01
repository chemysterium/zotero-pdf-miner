"""Equation-loss detection and explicitly approximate recovery."""

import unittest
from unittest.mock import Mock, patch

import equations


def formula_box(start, end, bbox=(0, 0, 100, 20)):
    return {"class": "formula", "pos": (start, end), "bbox": bbox}


class FormulaRecoveryTests(unittest.TestCase):
    def test_excerpt_decodes_source_fonts_and_preserves_scripts(self):
        def span(text, x, y=100, size=10, font="Times"):
            return {"font": font, "size": size, "origin": (x, y), "chars": [
                {"c": char, "bbox": (x + 5 * i, y - size, x + 5 * (i + 1), y)}
                for i, char in enumerate(text)
            ]}

        page = Mock()
        page.get_text.return_value = {"blocks": [{"lines": [
            {"dir": (1, 0), "spans": [span("CO", 0), span("2", 10, y=102, size=7)]},
            {"dir": (1, 0), "spans": [span("pH ", 0), span("\x04", 15, font="AdvBMa1"),
                                     span(" 7", 20)]},
        ]}]}
        decoder = Mock()
        decoder.decode.side_effect = lambda font, char: "=" if char == "\x04" else None
        with patch.object(equations.advent, "PageDecoder", return_value=decoder):
            text = equations.formula_text(page, (0, 0, 100, 200), set())
        self.assertEqual(text, "CO\u2082\npH = 7")

    def test_default_warns_without_reading_or_changing_text(self):
        chunk = {"text": "Before\n\nAfter", "page_boxes": [formula_box(6, 8)]}
        with patch.object(equations, "formula_text") as reader:
            result = equations.recover_formulas(Mock(), chunk, set(), "warn")
        self.assertEqual(result.text, chunk["text"])
        self.assertEqual((result.recovered, result.omitted), (0, 1))
        reader.assert_not_called()

    def test_restores_multiple_formulas_at_their_original_offsets(self):
        chunk = {"text": "Before\n\nMiddle\n\nAfter",
                 "page_boxes": [formula_box(6, 8), formula_box(14, 16)]}
        with patch.object(equations, "formula_text", side_effect=["x = 2", "y = 3"]):
            result = equations.recover_formulas(Mock(), chunk, set(), "text")
        restored = result.text
        for token, excerpt in result.excerpts.items():
            restored = restored.replace(token, excerpt)
        self.assertLess(restored.index("Before"), restored.index("x = 2"))
        self.assertLess(restored.index("x = 2"), restored.index("Middle"))
        self.assertLess(restored.index("Middle"), restored.index("y = 3"))
        self.assertLess(restored.index("y = 3"), restored.index("After"))
        self.assertEqual((result.recovered, result.omitted), (2, 0))

    def test_existing_formula_content_is_never_duplicated(self):
        for content in ("x = 2", "![formula](formula.png)"):
            with self.subTest(content=content), patch.object(equations, "formula_text") as reader:
                result = equations.recover_formulas(Mock(), {
                    "text": content, "page_boxes": [formula_box(0, len(content))],
                }, set(), "text")
            self.assertEqual(result.text, content)
            self.assertEqual((result.recovered, result.omitted), (0, 0))
            reader.assert_not_called()

    def test_picture_regions_are_not_recovered_as_equations(self):
        chunk = {"text": "\n\n", "page_boxes": [{"class": "picture", "pos": (0, 2)}]}
        with patch.object(equations, "formula_text") as reader:
            result = equations.recover_formulas(Mock(), chunk, set(), "text")
        self.assertEqual(result.omitted, 0)
        reader.assert_not_called()

    def test_image_only_or_unreadable_formula_remains_a_warning(self):
        for outcome in ("", RuntimeError("damaged PDF"), ValueError("bad bbox")):
            with self.subTest(outcome=outcome), \
                    patch.object(equations, "formula_text", side_effect=[outcome]):
                result = equations.recover_formulas(Mock(), {
                    "text": "\n\n", "page_boxes": [formula_box(0, 2)],
                }, set(), "text")
            self.assertEqual((result.recovered, result.omitted), (0, 1))

    def test_invalid_offsets_are_reported_without_rewriting_prose(self):
        for position in (None, (), (-1, 2), (0, 999), (2, 0), ("0", "2")):
            with self.subTest(position=position), patch.object(equations, "formula_text") as reader:
                result = equations.recover_formulas(Mock(), {
                    "text": "Body", "page_boxes": [{"class": "formula", "pos": position}],
                }, set(), "text")
            self.assertEqual(result.text, "Body")
            self.assertEqual(result.omitted, 1)
            reader.assert_not_called()

    def test_literal_excerpt_cannot_close_its_code_fence(self):
        excerpt = equations._excerpt("x = ``` + y")
        self.assertIn("````text\n", excerpt)
        self.assertTrue(excerpt.endswith("\n````\n\n"))

    def test_invalid_bounding_box_is_rejected_before_pdf_access(self):
        for bbox in ((), (0, 0, 0, 0), (0, 0, float("nan"), 1)):
            with self.subTest(bbox=bbox), self.assertRaises(ValueError):
                equations.formula_text(Mock(), bbox, set())


if __name__ == "__main__":
    unittest.main()
