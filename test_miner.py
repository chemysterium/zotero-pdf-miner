"""Regression tests for export control flow, paths and scientific formatting."""

import contextlib
import io
import sys
import tempfile
import unittest
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


class FormattingPipelineTests(unittest.TestCase):
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
