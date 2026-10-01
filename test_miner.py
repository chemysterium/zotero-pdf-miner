"""Regression tests for export control flow, paths and scientific formatting."""

import contextlib
import io
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import extract
import sciformat
import zotero_pdf_miner as miner


def export_args(**overrides):
    values = dict(max_minutes=None, force=False, dry_run=False,
                  linked_attachment_base_dir=None, guess_glyphs=False,
                  scripts="unicode", keep_figure_text=False,
                  page_separators=False, no_front_matter=False)
    return SimpleNamespace(**(values | overrides))


class CliTests(unittest.TestCase):
    def run_cli(self, arguments):
        with patch.object(sys, "argv", ["zotero_pdf_miner.py", *arguments]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            miner.main()
        return output.getvalue()

    def test_pdf_dry_run_never_calls_writer(self):
        with patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "exists", return_value=False), \
                patch.object(miner, "write_markdown") as writer:
            output = self.run_cli(["--pdf", "sample.pdf", "--dry-run"])
        writer.assert_not_called()
        self.assertIn("sample.md", output)

    def test_pdf_force_dry_run_never_overwrites(self):
        with patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "exists", return_value=True), \
                patch.object(miner, "write_markdown") as writer:
            self.run_cli(["--pdf", "sample.pdf", "--force", "--dry-run"])
        writer.assert_not_called()

    def test_single_pdf_failure_has_readable_exit(self):
        with patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "exists", return_value=False), \
                patch.object(miner, "write_markdown", side_effect=extract.ExtractionError("no text")):
            with self.assertRaisesRegex(SystemExit, "Failed to extract.*no text"):
                self.run_cli(["--pdf", "sample.pdf"])

    def test_batch_failure_returns_nonzero_exit(self):
        with patch.object(miner, "build_client"), \
                patch.object(miner, "get_all_papers", return_value=[{"key": "A"}, {"key": "B"}]), \
                patch.object(miner, "process_papers", return_value=1):
            with self.assertRaises(SystemExit) as raised:
                self.run_cli(["--local", "--all"])
        self.assertEqual(raised.exception.code, 1)


class AttachmentPathTests(unittest.TestCase):
    def setUp(self):
        self.relative = {"linkMode": "linked_file", "path": "attachments:sub/paper.pdf"}

    def test_relative_link_uses_configured_base(self):
        with patch.object(miner, "ZOTERO_LINKED_ATTACHMENT_BASE_DIR", "configured"), \
                patch.object(miner, "ZOTERO_STORAGE_DIR", "unrelated/storage"), \
                patch.object(Path, "is_file", return_value=True):
            self.assertEqual(miner.local_pdf_path(self.relative), Path("configured/sub/paper.pdf"))

    def test_command_line_base_overrides_config(self):
        with patch.object(miner, "ZOTERO_LINKED_ATTACHMENT_BASE_DIR", "configured"), \
                patch.object(Path, "is_file", return_value=True):
            self.assertEqual(miner.local_pdf_path(self.relative, Path("override")),
                             Path("override/sub/paper.pdf"))

    def test_relative_link_without_base_fails_explicitly(self):
        with patch.object(miner, "ZOTERO_LINKED_ATTACHMENT_BASE_DIR", ""):
            with self.assertRaisesRegex(miner.ProcessingError, "Linked Attachment Base Directory"):
                miner.local_pdf_path(self.relative)

    def test_absolute_link_and_stored_pdf_paths(self):
        absolute = str(Path("paper.pdf").resolve())
        with patch.object(Path, "is_file", return_value=True), \
                patch.object(miner, "ZOTERO_STORAGE_DIR", "storage"):
            self.assertEqual(miner.local_pdf_path({"linkMode": "linked_file", "path": absolute}),
                             Path(absolute))
            self.assertEqual(miner.local_pdf_path({"key": "ABCD1234", "filename": "paper.pdf"}),
                             Path("storage/ABCD1234/paper.pdf"))


class PaginationTests(unittest.TestCase):
    def test_title_search_considers_later_pages_before_selecting(self):
        zot = Mock()
        first = {"key": "FIRST001", "data": {"itemType": "journalArticle", "title": "Same title"}}
        later = {"key": "SECOND02", "data": {"itemType": "journalArticle", "title": "Same title"}}
        zot.items.return_value = [first]
        zot.everything.return_value = [first, later]
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
            miner.resolve_item(zot, "Same title")
        self.assertEqual(raised.exception.code, 1)
        zot.everything.assert_called_once_with([first])

    def test_title_search_ignores_notes_and_annotations(self):
        zot = Mock()
        zot.everything.return_value = [
            {"key": "NOTE0001", "data": {"itemType": "note"}},
            {"key": "ANNOT001", "data": {"itemType": "annotation"}},
            {"key": "PAPER001", "data": {"itemType": "journalArticle", "title": "Title"}},
        ]
        self.assertEqual(miner.resolve_item(zot, "Title")["key"], "PAPER001")

    def test_pdf_found_on_later_attachment_page(self):
        zot = Mock()
        first_page = [{"data": {"itemType": "note"}}]
        pdf = {"key": "PDFKEY01", "data": {"itemType": "attachment",
                                            "contentType": "application/pdf"}}
        zot.children.return_value = first_page
        zot.everything.return_value = first_page + [pdf]
        self.assertEqual(miner.find_pdf_attachment(zot, "PARENT01")["key"], "PDFKEY01")
        zot.everything.assert_called_once_with(first_page)

    def test_all_subcollections_are_fetched_before_recursing(self):
        class PaginatedZotero:
            def collection_items_top(self, key, **kwargs):
                return ("items", key)

            def collections_sub(self, key, **kwargs):
                return ("collections", key)

            def everything(self, request):
                kind, key = request
                if kind == "collections":
                    return [{"key": "FIRST"}, {"key": "LATER"}] if key == "ROOT" else []
                return [{"key": key, "data": {"itemType": "journalArticle"}},
                        {"key": "SHARED", "data": {"itemType": "journalArticle"}}]

        papers = miner.get_collection_papers(PaginatedZotero(), "ROOT", recursive=True)
        self.assertEqual([paper["key"] for paper in papers], ["ROOT", "SHARED", "FIRST", "LATER"])


class BatchTests(unittest.TestCase):
    def test_one_paper_collection_without_pdf_is_still_a_skip(self):
        for single_item in (False, True):
            with self.subTest(single_item=single_item), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    patch.object(Path, "exists", return_value=False), \
                    patch.object(miner, "find_pdf_attachment",
                                 side_effect=miner.ProcessingError("no PDF")):
                failures = miner.process_papers(
                    Mock(), [{"key": "FIRST"}], Path("out"), export_args(),
                    missing_pdf_is_error=single_item,
                )
            self.assertEqual(failures, int(single_item))

    def run_batch(self, finder, writer):
        papers = [{"key": "FIRST", "title": "First"}, {"key": "SECOND", "title": "Second"}]
        with contextlib.redirect_stdout(io.StringIO()), \
                patch.object(Path, "exists", return_value=False), \
                patch.object(miner, "find_pdf_attachment", side_effect=finder), \
                patch.object(miner, "local_pdf_path", return_value=Path("paper.pdf")), \
                patch.object(miner, "write_markdown", side_effect=writer) as write:
            failures = miner.process_papers(Mock(), papers, Path("out"), export_args())
        return failures, write.call_count

    def test_attachment_api_failure_does_not_stop_batch(self):
        self.assertEqual(self.run_batch([RuntimeError("API failed"), {}], None), (1, 1))

    def test_extraction_failure_does_not_stop_batch(self):
        self.assertEqual(self.run_batch([{}, {}], [extract.ExtractionError("bad PDF"), None]), (1, 2))

    def test_missing_pdfs_are_normal_batch_skips(self):
        self.assertEqual(self.run_batch([miner.ProcessingError("no PDF"), {}], None), (0, 1))


class MetadataTests(unittest.TestCase):
    def test_personal_library_link(self):
        with patch.object(miner, "ZOTERO_LIBRARY_TYPE", "user"):
            metadata = miner.front_matter({"key": "PAPER001"}, Path("paper.pdf"), 2)
        self.assertIn("zotero://select/library/items/PAPER001", metadata)

    def test_group_library_link_uses_group_id(self):
        with patch.object(miner, "ZOTERO_LIBRARY_TYPE", "group"), \
                patch.object(miner, "ZOTERO_LIBRARY_ID", "12345"):
            metadata = miner.front_matter({"key": "PAPER001"}, Path("paper.pdf"), 2)
        self.assertIn("zotero://select/groups/12345/items/PAPER001", metadata)
        self.assertNotIn("select/library/", metadata)

    def test_standalone_pdf_has_no_zotero_link(self):
        self.assertNotIn("zotero_link:", miner.front_matter({"title": "Paper"}, Path("paper.pdf"), 2))

    def test_missing_group_id_does_not_make_a_misleading_personal_link(self):
        with patch.object(miner, "ZOTERO_LIBRARY_TYPE", "group"), \
                patch.object(miner, "ZOTERO_LIBRARY_ID", ""):
            self.assertIsNone(miner.zotero_select_link({"key": "PAPER001"}))


class FormattingPipelineTests(unittest.TestCase):
    def test_guessed_charge_obeys_html_script_mode(self):
        self.assertEqual(sciformat.postprocess("Cl<sup>\ufffd</sup>", scripts="html",
                                              guess_glyphs=True),
                         "Cl<sup>\u2212</sup>\n")

    def test_postprocess_defaults_preserve_unknown_glyphs(self):
        source = "Cl<sup>\ufffd</sup>"
        self.assertEqual(sciformat.postprocess(source), source + "\n")

    def test_generated_exponent_obeys_script_mode(self):
        source = "1.5 x 10\u22123 M"
        self.assertEqual(sciformat.postprocess(source, scripts="html"),
                         "1.5 \u00d7 10<sup>\u22123</sup> M\n")
        self.assertEqual(sciformat.postprocess(source), "1.5 \u00d7 10\u207b\u00b3 M\n")

    def test_html_isotope_and_footnote_spacing(self):
        self.assertEqual(sciformat.postprocess("<sup>6</sup> Li/<sup>7</sup> Li", scripts="html"),
                         "<sup>6</sup>Li/<sup>7</sup>Li\n")
        self.assertEqual(sciformat.postprocess("result.<sup>1</sup> In this", scripts="html"),
                         "result.<sup>1</sup> In this\n")

    def test_uncertain_symbols_can_be_preserved(self):
        source = "OH<sup>\ufffd</sup> and unknown \ufffd"
        self.assertEqual(sciformat.postprocess(source, guess_glyphs=False, fffd_guess="\u2212"),
                         source + "\n")
        guessed = sciformat.postprocess(source, guess_glyphs=True, fffd_guess="\u2212")
        self.assertNotIn("\ufffd", guessed)


class ExtractionTests(unittest.TestCase):
    def test_unknown_control_and_private_glyphs_are_visible_and_counted(self):
        import pymupdf
        import pymupdf4llm
        import pdf_hints

        document = pymupdf.open()
        document.new_page()
        text = "Scientific text " * 20 + " bad \x01 \x7f \ue123 \U000f0001 \U00100001\n\tgood"
        with patch.object(pymupdf, "open", return_value=document), \
                patch.object(pymupdf4llm, "to_markdown", return_value=[{"text": text}]), \
                patch.object(pdf_hints, "collect_hints", return_value=pdf_hints.PdfHints()):
            result = extract.pdf_to_markdown(Path("memory.pdf"))
        self.assertEqual(result.unresolved_glyphs, 5)
        self.assertIn("\n\tgood", result.markdown)
        self.assertNotIn("\x01", result.markdown)
        self.assertNotIn("\ue123", result.markdown)

    def test_font_fallback_does_not_replace_real_fraction_by_default(self):
        import pymupdf
        import pymupdf4llm
        import pdf_hints

        document = pymupdf.open()
        document.new_page()
        text = "Scientific text " * 20 + " pH \u00bc 7; a real fraction \u00bc."
        hints = pdf_hints.PdfHints(
            glyph_edits={0: [pdf_hints.Edit("pH ", "\u00bc", " 7", "=")]},
            residual={"\u00bc": Counter({"=": 1})},
        )
        with patch.object(pymupdf, "open", return_value=document), \
                patch.object(pymupdf4llm, "to_markdown",
                             return_value=[{"metadata": {"page_number": 1}, "text": text}]), \
                patch.object(pdf_hints, "collect_hints", return_value=hints):
            result = extract.pdf_to_markdown(Path("memory.pdf"))
        self.assertIn("pH = 7", result.markdown)
        self.assertIn("a real fraction \u00bc", result.markdown)

    def test_real_pdf_cli_export_and_nonwriting_dry_run(self):
        import pymupdf

        with tempfile.TemporaryDirectory(prefix="zotero-miner-test-") as folder:
            root = Path(folder)
            pdf = root / "paper.pdf"
            out = root / "export"
            with pymupdf.open() as document:
                for number in (1, 2):
                    page = document.new_page()
                    page.insert_text((72, 72), f"Scientific article page {number}", fontsize=16)
                    page.insert_textbox(
                        (72, 110, 520, 500),
                        "Results and discussion. The concentration was measured under "
                        "controlled laboratory conditions. " * 12,
                        fontsize=11,
                    )
                document.save(pdf)

            cli = CliTests()
            output = cli.run_cli(["--pdf", str(pdf), "-o", str(out), "--dry-run"])
            self.assertIn("Would extract", output)
            self.assertFalse(out.exists())

            for scripts in ("unicode", "html"):
                with self.subTest(scripts=scripts):
                    cli.run_cli(["--pdf", str(pdf), "-o", str(out), "--force",
                                 "--scripts", scripts, "--page-separators"])
                    markdown = (out / "paper.md").read_text(encoding="utf-8")
                    self.assertIn("pages: 2", markdown)
                    self.assertIn("Results and discussion.", markdown)
                    self.assertIn("<!-- page 1 -->", markdown)
                    self.assertIn("<!-- page 2 -->", markdown)
                    self.assertFalse((out / "paper.md.part").exists())

    def test_extraction_defaults_preserve_and_count_unknown_glyphs(self):
        import pymupdf
        import pymupdf4llm
        import pdf_hints

        def extract_text(guess):
            document = pymupdf.open()
            document.new_page()
            text = "Scientific text " * 20 + " OH<sup>\ufffd</sup>"
            chunks = [{"metadata": {"page_number": 1}, "text": text}]
            with patch.object(pymupdf, "open", return_value=document), \
                    patch.object(pymupdf4llm, "to_markdown", return_value=chunks), \
                    patch.object(pdf_hints, "collect_hints", return_value=pdf_hints.PdfHints()):
                return extract.pdf_to_markdown(Path("memory.pdf"), guess_glyphs=guess)

        conservative = extract_text(False)
        guessed = extract_text(True)
        self.assertEqual(conservative.unresolved_glyphs, 1)
        self.assertIn("\ufffd", conservative.markdown)
        self.assertEqual(guessed.unresolved_glyphs, 0)


if __name__ == "__main__":
    unittest.main()
