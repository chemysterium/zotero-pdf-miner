"""
Extract the text of PDFs in a Zotero library into Markdown files, keeping
the formatting scientific papers depend on: sub/superscripts (CaSO₄, Ca²⁺,
10⁻³), Greek letters, degree signs, accented author names, tables.

Read-only: nothing is written back to Zotero.

Setup:
    pip install -r requirements.txt

    Either run Zotero 7 with its local API enabled (Settings -> Advanced ->
    "Allow other applications on this computer to communicate with Zotero")
    and pass --local, or copy config.example.ini to config.ini and fill in
    your Zotero web API credentials.

Usage:
    python zotero_pdf_miner.py --collection "Thesis Reading"
    python zotero_pdf_miner.py --collection "Thesis Reading" --recursive -o notes/
    python zotero_pdf_miner.py --local --collection WXYZ9876
    python zotero_pdf_miner.py ABCD1234
    python zotero_pdf_miner.py "partial title of the paper"
    python zotero_pdf_miner.py --all --max-minutes 60
    python zotero_pdf_miner.py --pdf paper.pdf
"""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CONFIG_PATH = Path(__file__).resolve().parent / "config.ini"
_CONFIG = configparser.ConfigParser()
if CONFIG_PATH.exists():
    _CONFIG.read(CONFIG_PATH, encoding="utf-8")


def _setting(section: str, key: str, default: str = "") -> str:
    """Resolve a setting: environment variable first, then config.ini, then default."""
    env_value = os.environ.get(key.upper())
    if env_value:
        return env_value
    return _CONFIG.get(section, key, fallback=default)


ZOTERO_LIBRARY_ID = _setting("zotero", "zotero_library_id")
ZOTERO_LIBRARY_TYPE = _setting("zotero", "zotero_library_type", "user")
ZOTERO_API_KEY = _setting("zotero", "zotero_api_key")
ZOTERO_STORAGE_DIR = _setting(
    "zotero", "zotero_storage_dir", str(Path.home() / "Zotero" / "storage")
)
OUTPUT_DIR = _setting("output", "output_dir", "markdown")
ZOTERO_LINKED_ATTACHMENT_BASE_DIR = _setting("zotero", "zotero_linked_attachment_base_dir")

ITEM_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")
LOCAL_API = "http://localhost:23119/api"

# Every Zotero request costs about the same couple of seconds whatever it
# returns, so a large page size keeps library-wide listings fast.
PAGE_SIZE = 100


class ProcessingError(Exception):
    """Raised for per-item failures that shouldn't abort a whole collection run."""


# --------------------------------------------------------------------------
# Zotero
# --------------------------------------------------------------------------

def build_client(local: bool):
    from pyzotero import zotero

    if local:
        import httpx

        try:
            httpx.get(f"{LOCAL_API}/users/0/items", params={"limit": 1}, timeout=10)
        except httpx.HTTPError:
            sys.exit(
                "Cannot reach Zotero's local API at http://localhost:23119.\n"
                "Start Zotero and check Settings -> Advanced -> 'Allow other "
                "applications on this computer to communicate with Zotero'."
            )
        return zotero.Zotero(ZOTERO_LIBRARY_ID or "0", ZOTERO_LIBRARY_TYPE, local=True)

    if not (ZOTERO_LIBRARY_ID and ZOTERO_API_KEY):
        sys.exit(
            "Missing Zotero credentials. Either start Zotero and use --local, or "
            "copy config.example.ini to config.ini and fill in zotero_library_id "
            "and zotero_api_key (from https://www.zotero.org/settings/keys)."
        )
    return zotero.Zotero(ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE, ZOTERO_API_KEY)


def resolve_item(zot, query: str) -> dict:
    if ITEM_KEY_RE.match(query):
        item = zot.item(query)
        return item["data"] | {"key": query}

    matches = zot.everything(zot.items(q=query, qmode="titleCreatorYear",
                                      itemType="-attachment", limit=PAGE_SIZE))
    matches = [m for m in matches if m["data"].get("itemType") not in ("note", "annotation")]
    if not matches:
        sys.exit(f"No Zotero items matched: {query!r}")
    if len(matches) > 1:
        print(f"Multiple matches for {query!r}, pick one and rerun with its key:")
        for m in matches:
            print(f"  {m['key']}  {m['data'].get('title', '(no title)')}")
        sys.exit(1)
    return matches[0]["data"] | {"key": matches[0]["key"]}


def resolve_collection(zot, query: str) -> dict:
    collections = zot.everything(zot.collections(limit=PAGE_SIZE))
    if ITEM_KEY_RE.match(query):
        for c in collections:
            if c["key"] == query:
                return c
    matches = [c for c in collections if c["data"]["name"].lower() == query.lower()]
    if not matches:
        matches = [c for c in collections if query.lower() in c["data"]["name"].lower()]
    if not matches:
        sys.exit(f"No collection matched: {query!r}")
    if len(matches) > 1:
        print(f"Multiple collections matched {query!r}, pick one and rerun with its key:")
        for c in matches:
            print(f"  {c['key']}  {c['data']['name']}")
        sys.exit(1)
    return matches[0]


def _papers(items: list[dict]) -> list[dict]:
    return [
        it["data"] | {"key": it["key"]}
        for it in items
        if it["data"].get("itemType") not in ("attachment", "note", "annotation")
    ]


def get_collection_papers(zot, collection_key: str, recursive: bool = False) -> list[dict]:
    papers = _papers(zot.everything(zot.collection_items_top(collection_key, limit=PAGE_SIZE)))
    if recursive:
        seen = {p["key"] for p in papers}
        children = zot.everything(zot.collections_sub(collection_key, limit=PAGE_SIZE))
        for child in children:
            for paper in get_collection_papers(zot, child["key"], recursive=True):
                if paper["key"] not in seen:
                    seen.add(paper["key"])
                    papers.append(paper)
    return papers


def get_all_papers(zot) -> list[dict]:
    return _papers(zot.everything(zot.top(limit=PAGE_SIZE)))


def find_pdf_attachment(zot, parent_key: str) -> dict:
    for child in zot.everything(zot.children(parent_key, limit=PAGE_SIZE)):
        data = child["data"]
        if data.get("itemType") == "attachment" and data.get("contentType") == "application/pdf":
            return data | {"key": child["key"]}
    raise ProcessingError("no PDF attachment")


def local_pdf_path(attachment: dict, linked_base_dir: Path | None = None) -> Path:
    """Where the attachment's PDF lives on disk.

    Stored files sit in <storage>/<attachment key>/<filename>; linked files
    keep their own path, absolute or relative to the base directory as
    "attachments:...".
    """
    if attachment.get("linkMode") == "linked_file":
        raw = attachment.get("path", "")
        if not raw:
            raise ProcessingError("Linked PDF has no file path.")
        if raw.startswith("attachments:"):
            base = linked_base_dir or ZOTERO_LINKED_ATTACHMENT_BASE_DIR
            if not base:
                raise ProcessingError(
                    "Relative linked PDF needs --linked-attachment-base-dir or "
                    "zotero_linked_attachment_base_dir in config.ini. Use Zotero's "
                    "Linked Attachment Base Directory setting."
                )
            path = Path(base).expanduser() / raw[len("attachments:"):]
        else:
            path = Path(raw)
    else:
        path = Path(ZOTERO_STORAGE_DIR) / attachment["key"] / attachment.get("filename", "")
    if not path.is_file():
        raise ProcessingError(
            f"PDF is not on this computer ({path}). Sync it in Zotero, or set "
            "zotero_storage_dir in config.ini."
        )
    return path


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def safe_stem(title: str, key: str) -> str:
    """Filename stem: a Windows-safe, trimmed title plus the item key.

    The key is appended after trimming, so two papers with the same opening
    words never overwrite each other.
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:80].rstrip(" .") or "untitled"
    return f"{cleaned} ({key})" if key else cleaned


def _authors(item: dict) -> list[str]:
    names = []
    for creator in item.get("creators", []):
        if creator.get("creatorType", "author") != "author":
            continue
        if creator.get("name"):
            names.append(creator["name"])
        else:
            names.append(", ".join(x for x in (creator.get("lastName"), creator.get("firstName")) if x))
    return names


def zotero_select_link(item: dict) -> str | None:
    """Link to an item in the configured personal or group library."""
    if not item.get("key"):
        return None
    if ZOTERO_LIBRARY_TYPE == "group":
        if not ZOTERO_LIBRARY_ID:
            return None
        library = f"groups/{ZOTERO_LIBRARY_ID}"
    else:
        library = "library"
    return f"zotero://select/{library}/items/{item['key']}"


def front_matter(item: dict, pdf: Path, pages: int) -> str:
    """YAML front matter with the item's bibliographic data.

    Values are written as JSON strings, which YAML reads as double-quoted
    scalars, so titles full of colons and quotes need no special handling.
    """
    year = re.search(r"\d{4}", item.get("date", "") or "")
    fields = {
        "title": item.get("title"),
        "authors": _authors(item),
        "year": year.group(0) if year else None,
        "publication": item.get("publicationTitle") or item.get("bookTitle")
        or item.get("proceedingsTitle"),
        # Zotero sometimes keeps a prefix: "Doi 10.1063/...", "https://doi.org/10...."
        "doi": re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:?\s*)", "", item.get("DOI") or "",
                      flags=re.IGNORECASE) or None,
        "url": item.get("url"),
        "item_type": item.get("itemType"),
        "zotero_key": item.get("key"),
        "zotero_link": zotero_select_link(item),
        "source_pdf": pdf.name,
        "pages": pages,
        "extracted": dt.date.today().isoformat(),
    }
    lines = ["---"]
    for name, value in fields.items():
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            lines.append(f"{name}:")
            lines += [f"  - {json.dumps(v, ensure_ascii=False)}" for v in value]
        elif isinstance(value, int):
            lines.append(f"{name}: {value}")
        else:
            lines.append(f"{name}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def write_markdown(pdf: Path, out_path: Path, item: dict, args) -> None:
    import extract

    result = extract.pdf_to_markdown(
        pdf,
        scripts=args.scripts,
        keep_figure_text=args.keep_figure_text,
        page_separators=args.page_separators,
        guess_glyphs=args.guess_glyphs,
        equation_mode=args.equations,
    )
    text = result.markdown
    if not args.no_front_matter:
        text = front_matter(item, pdf, result.pages) + text
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".md.part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out_path)
    print(
        f"  wrote {out_path.name} ({result.pages} pages, {len(result.markdown)} chars, "
        f"{result.edits_applied}/{result.edits_found} layout fixes applied)"
    )
    if result.ocr_layer_pages:
        print(
            f"  note: {len(result.ocr_layer_pages)} page(s) read from an OCR text layer "
            f"({_ranges(result.ocr_layer_pages)}); their text is as good as that OCR was."
        )
    if result.textless_pages:
        print(
            f"  warning: {len(result.textless_pages)} of {result.pages} pages have no "
            f"text layer (scanned?): {_ranges(result.textless_pages)}. OCR the PDF "
            "(e.g. with ocrmypdf) and rerun with --force to get their text."
        )
    if result.text_fallback_pages:
        print(
            f"  warning: {len(result.text_fallback_pages)} page(s) restored as plain text "
            f"({_ranges(result.text_fallback_pages)}) because layout extraction returned "
            "too little text; tables and formula layout may be lost. Check the PDF."
        )

    if result.unresolved_glyphs:
        print(
            f"  warning: {result.unresolved_glyphs} unresolved glyph(s) remain. "
            "Check their context against the PDF; --guess-glyphs enables heuristic replacements."
        )
    if result.omitted_formulas:
        print(
            f"  warning: {result.omitted_formulas} detected formula region(s) omitted "
            f"on page(s) {_ranges(result.omitted_formula_pages)}. "
            "Use --equations text to retain approximate text-layer excerpts; "
            "image-only formulas need OCR or manual transcription."
        )
    if result.recovered_formulas:
        print(
            f"  note: {result.recovered_formulas} formula region(s) retained as marked "
            "text-layer excerpts, not reconstructed equations. Check them against the PDF."
        )


def _ranges(pages: list[int]) -> str:
    """[1, 2, 3, 7] -> "1-3, 7"."""
    out, start = [], None
    for i, page in enumerate(pages):
        if start is None:
            start = page
        if i + 1 == len(pages) or pages[i + 1] != page + 1:
            out.append(str(start) if start == page else f"{start}-{page}")
            start = None
    return ", ".join(out)


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------

def process_papers(
    zot, papers: list[dict], out_dir: Path, args, *, missing_pdf_is_error: bool = False
) -> int:
    """Extract every paper; returns the number of failures."""
    deadline = time.monotonic() + args.max_minutes * 60 if args.max_minutes else None
    done = skipped = no_pdf = failed = 0

    for i, paper in enumerate(papers, 1):
        if deadline is not None and time.monotonic() >= deadline:
            print(
                f"Time limit of {args.max_minutes:g} min reached — "
                f"{len(papers) - i + 1} paper(s) left. Rerun to continue."
            )
            break
        title = paper.get("title") or "Untitled"
        print(f"[{i}/{len(papers)}] {title} ({paper['key']})")
        out_path = out_dir / f"{safe_stem(title, paper['key'])}.md"
        if out_path.exists() and not args.force:
            print("  already extracted, skipping (use --force to redo)")
            skipped += 1
            continue
        try:
            attachment = find_pdf_attachment(zot, paper["key"])
        except ProcessingError:
            print("  no PDF attachment, skipping")
            no_pdf += 1
            continue
        except Exception as exc:
            print(f"  failed to list attachments: {exc}")
            failed += 1
            continue
        try:
            pdf = local_pdf_path(attachment, args.linked_attachment_base_dir)
            if args.dry_run:
                print(f"  would extract {pdf.name} -> {out_path.name}")
            else:
                write_markdown(pdf, out_path, paper, args)
            done += 1
        except Exception as exc:  # one broken PDF must not stop the run
            print(f"  skipped: {exc}")
            failed += 1

    verb = "would extract" if args.dry_run else "extracted"
    print(
        f"Done. {done} {verb}, {skipped} already there, {no_pdf} without a PDF, "
        f"{failed} failed. Output: {out_dir}"
    )
    return failed + (no_pdf if missing_pdf_is_error else 0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("item", nargs="?", help="Zotero item key (8 chars) or a title search")
    parser.add_argument("--collection", "-c", help="Collection name or key: extract every paper in it")
    parser.add_argument("--recursive", "-r", action="store_true",
                        help="With --collection, include subcollections")
    parser.add_argument("--all", "-a", action="store_true", help="Extract every paper in the library")
    parser.add_argument("--pdf", type=Path, help="Convert a single PDF file, without Zotero")
    parser.add_argument("--local", "-l", action="store_true",
                        help="Read the library through a running Zotero's local API (no API key)")
    parser.add_argument("--output-dir", "-o", type=Path,
                        help=f"Where to write the .md files (default: {OUTPUT_DIR}; "
                        "with --collection, a subfolder named after it)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing .md files")
    parser.add_argument("--dry-run", action="store_true", help="List what would be extracted")
    parser.add_argument("--linked-attachment-base-dir", type=Path,
                        help="Base folder for Zotero's relative linked attachments")
    parser.add_argument("--guess-glyphs", action="store_true",
                        help="Guess unresolved symbols from context (off by default)")
    parser.add_argument("--equations", choices=["warn", "text"], default="warn",
                        help="Warn about omitted layout formulas (default), or retain "
                        "marked text-layer excerpts with approximate layout")
    parser.add_argument("--max-minutes", "-m", type=float, metavar="N",
                        help="Stop starting new papers after N minutes")
    parser.add_argument("--scripts", choices=["unicode", "html"], default="unicode",
                        help="Sub/superscripts as Unicode (CaSO₄, 10⁻³; HTML only where "
                        "Unicode has no glyph) or always as <sub>/<sup> (default: unicode)")
    parser.add_argument("--keep-figure-text", action="store_true",
                        help="Keep text found inside figures (axis labels etc.)")
    parser.add_argument("--page-separators", action="store_true",
                        help="Mark page boundaries with <!-- page N --> comments")
    parser.add_argument("--no-front-matter", action="store_true",
                        help="Leave out the YAML block with bibliographic data")
    args = parser.parse_args()

    if sum(bool(x) for x in (args.item, args.collection, args.all, args.pdf)) != 1:
        parser.error("provide exactly one of: item, --collection, --all, or --pdf")
    if args.recursive and not args.collection:
        parser.error("--recursive only applies to --collection")
    if args.max_minutes is not None and args.max_minutes <= 0:
        parser.error("--max-minutes must be greater than 0")

    out_dir = args.output_dir or Path(OUTPUT_DIR)

    if args.pdf:
        if not args.pdf.is_file():
            sys.exit(f"File not found: {args.pdf}")
        out_path = (args.output_dir or args.pdf.parent) / f"{args.pdf.stem}.md"
        if out_path.exists() and not args.force:
            sys.exit(f"{out_path} exists (use --force to overwrite).")
        if args.dry_run:
            print(f"Would extract {args.pdf} -> {out_path}")
            return
        item = {"title": args.pdf.stem}
        try:
            write_markdown(args.pdf, out_path, item, args)
        except Exception as exc:
            sys.exit(f"Failed to extract {args.pdf}: {exc}")
        return

    zot = build_client(args.local)

    if args.collection:
        collection = resolve_collection(zot, args.collection)
        name = collection["data"]["name"]
        papers = get_collection_papers(zot, collection["key"], args.recursive)
        print(f"Found {len(papers)} papers in collection {name!r} ({collection['key']}).")
        if not args.output_dir:
            out_dir = out_dir / safe_stem(name, "")
    elif args.all:
        print("Listing every paper in the library...")
        papers = get_all_papers(zot)
        print(f"Found {len(papers)} papers.")
    else:
        paper = resolve_item(zot, args.item)
        print(f"Found: {paper.get('title', 'Untitled')} ({paper['key']})")
        papers = [paper]

    failed = process_papers(zot, papers, out_dir, args, missing_pdf_is_error=bool(args.item))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
