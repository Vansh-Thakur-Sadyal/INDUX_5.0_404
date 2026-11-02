"""Stage 1 and 2 of the pipeline: get the text out, and keep its shape.

A loader's job is narrow and it is worth saying what it is NOT. It does not decide
whether a document is useful, it does not summarise, and it never invents structure
that was not in the file. If a PDF is a scan with no text layer, the loader says so
and the document is marked failed - because a knowledge base that silently indexes
zero characters from a 40-page PDF will answer "I don't know" forever and nobody
will know why.

What comes back is a ``Loaded``: the text, a section outline, a page or sheet count,
and a note about anything that was dropped. The note is shown on the console next to
the document, so the person who uploaded it can see what the machine actually read.

Formats
-------
``.pdf``             pypdf, page by page, with a page marker kept as a section
``.docx``            python-docx: paragraphs, headings and tables
``.csv`` ``.tsv``    header row plus one line per record, so a row stays one fact
``.xlsx`` ``.xlsm``  openpyxl, one block per sheet, same row-per-fact shape
``.json``            flattened to "key: value" lines, nesting preserved in the key
``.md`` ``.txt``     as written; markdown headings become sections
``.html`` ``.htm``   tags stripped, script and style dropped entirely
URL                  fetched, then treated as HTML or as whatever it turns out to be
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("saarthi.rag.loaders")

# Big enough for a district agronomy manual, small enough that one upload cannot
# fill the disk or spend an afternoon embedding.
MAX_BYTES = 25 * 1024 * 1024
MAX_CHARACTERS = 1_200_000

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".log"}
SUPPORTED = (TEXT_EXTENSIONS |
             {".pdf", ".docx", ".csv", ".tsv", ".xlsx", ".xlsm", ".json",
              ".html", ".htm"})


@dataclass
class Loaded:
    ok: bool
    text: str = ""
    sections: List[Dict[str, Any]] = field(default_factory=list)
    media_type: str = ""
    pages: int = 0
    note: str = ""
    error: str = ""

    @property
    def characters(self) -> int:
        return len(self.text)


def extension_of(name: str) -> str:
    name = (name or "").strip().lower()
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


def is_supported(name: str) -> bool:
    return extension_of(name) in SUPPORTED


def describe_supported() -> List[Dict[str, str]]:
    """What the upload button will accept, for the console to render."""
    return [
        {"kinds": "PDF", "extensions": ".pdf",
         "note": "Text layer only - a scanned page with no text is reported, not guessed at."},
        {"kinds": "Word", "extensions": ".docx",
         "note": "Paragraphs, headings and tables. The older .doc format is not read."},
        {"kinds": "Spreadsheet", "extensions": ".xlsx, .xlsm, .csv, .tsv",
         "note": "One row becomes one fact, with the header names kept on each row."},
        {"kinds": "Text", "extensions": ".txt, .md, .rst",
         "note": "Markdown headings are kept as sections and used in citations."},
        {"kinds": "Web page", "extensions": ".html, .htm, or a URL",
         "note": "Scripts, styles and navigation are dropped before indexing."},
        {"kinds": "Structured", "extensions": ".json",
         "note": "Flattened to key: value lines so nesting survives retrieval."},
    ]


# ------------------------------------------------------------------- helpers
def _clean(text: str) -> str:
    """Stage 3: normalise. Collapse the whitespace a converter leaves behind."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # A line of nothing but punctuation is page furniture, not content.
    kept = [line for line in text.split("\n")
            if line.strip() and not re.fullmatch(r"[\W_]{1,}", line.strip())]
    return "\n".join(kept).strip()


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


_SCRIPT = re.compile(r"(?is)<(script|style|noscript|svg)\b.*?</\1>")
_TAG = re.compile(r"(?s)<[^>]+>")
_BLOCK_END = re.compile(r"(?i)</(p|div|li|tr|h[1-6]|section|article|br)\s*>")


def _html_to_text(raw: str) -> str:
    body = _SCRIPT.sub(" ", raw)
    body = _BLOCK_END.sub("\n", body)
    body = _TAG.sub(" ", body)
    # Only the entities that actually turn up; a full table would be a dependency.
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
                         ("&rsquo;", "'"), ("&ldquo;", '"'), ("&rdquo;", '"')):
        body = body.replace(entity, char)
    return body


_MD_HEADING = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")


def _sections_from_markdown(text: str) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    for match in _MD_HEADING.finditer(text):
        found.append({"title": match.group(2).strip(),
                      "level": len(match.group(1)), "offset": match.start()})
    return found


# -------------------------------------------------------------------- formats
def _load_pdf(data: bytes) -> Loaded:
    try:
        from pypdf import PdfReader
    except ImportError:                                            # pragma: no cover
        return Loaded(False, error="Reading PDF needs the pypdf package.")
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:                                       # noqa: BLE001
        return Loaded(False, error="This file could not be opened as a PDF: %s"
                                   % str(exc)[:160])
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")                                     # empty owner password
        except Exception:                                          # noqa: BLE001
            return Loaded(False, error="The PDF is password protected.")

    parts: List[str] = []
    sections: List[Dict[str, Any]] = []
    blank_pages = 0
    offset = 0
    for number, page in enumerate(reader.pages, 1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:                                   # noqa: BLE001
            log.info("PDF page %d unreadable: %s", number, str(exc)[:100])
            page_text = ""
        page_text = page_text.strip()
        if not page_text:
            blank_pages += 1
            continue
        label = "Page %d" % number
        sections.append({"title": label, "level": 2, "offset": offset})
        block = label + "\n" + page_text
        parts.append(block)
        offset += len(block) + 2

    text = _clean("\n\n".join(parts))
    total = len(reader.pages)
    if not text:
        return Loaded(False, pages=total, media_type="application/pdf",
                      error=("No text could be read from any of the %d pages. This is "
                             "usually a scan: it needs OCR before it can be indexed."
                             % total))
    note = ""
    if blank_pages:
        note = ("%d of %d pages held no readable text and were skipped."
                % (blank_pages, total))
    return Loaded(True, text=text, sections=sections, pages=total,
                  media_type="application/pdf", note=note)


def _load_docx(data: bytes) -> Loaded:
    try:
        import docx                                                # python-docx
    except ImportError:                                            # pragma: no cover
        return Loaded(False, error="Reading .docx needs the python-docx package.")
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:                                       # noqa: BLE001
        return Loaded(False, error="This file could not be opened as a Word document: %s"
                                   % str(exc)[:160])

    lines: List[str] = []
    sections: List[Dict[str, Any]] = []
    offset = 0
    for paragraph in document.paragraphs:
        content = (paragraph.text or "").strip()
        if not content:
            continue
        style = (paragraph.style.name if paragraph.style else "") or ""
        if style.lower().startswith("heading"):
            level = 2
            tail = style.split()[-1]
            if tail.isdigit():
                level = min(6, max(1, int(tail)))
            sections.append({"title": content, "level": level, "offset": offset})
        lines.append(content)
        offset += len(content) + 1

    # A table in a Word document is usually the dose chart - the single most useful
    # thing in the file. Rendering it row by row keeps each row retrievable on its own.
    tables = 0
    for table in document.tables:
        rows = [[(cell.text or "").strip() for cell in row.cells] for row in table.rows]
        rows = [r for r in rows if any(r)]
        if len(rows) < 2:
            continue
        tables += 1
        header = rows[0]
        for row in rows[1:]:
            pairs = ["%s: %s" % (h, v) for h, v in zip(header, row) if v]
            if pairs:
                lines.append("; ".join(pairs))

    text = _clean("\n".join(lines))
    if not text:
        return Loaded(False, error="The document held no readable text.")
    note = "%d table(s) were flattened to one line per row." % tables if tables else ""
    return Loaded(True, text=text, sections=sections, note=note,
                  media_type="application/vnd.openxmlformats-officedocument."
                             "wordprocessingml.document")


def _rows_to_lines(rows: List[List[str]], label: str = "") -> List[str]:
    """Header plus records. One row becomes one retrievable line.

    A spreadsheet dumped as a grid of numbers retrieves terribly: "2400" matches
    nothing. Carrying the column name onto every row - "modal price: 2400" - is what
    makes a price table answerable.
    """
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return []
    header = [(c or "").strip() or ("column %d" % (i + 1))
              for i, c in enumerate(rows[0])]
    lines: List[str] = []
    if label:
        lines.append(label)
    for row in rows[1:]:
        pairs = ["%s: %s" % (h, str(v).strip())
                 for h, v in zip(header, row) if str(v or "").strip()]
        if pairs:
            lines.append("; ".join(pairs))
    if len(lines) <= (1 if label else 0):                 # header only, no records
        lines.append(", ".join(header))
    return lines


def _load_csv(data: bytes, delimiter: str = ",") -> Loaded:
    raw = _decode(data)
    try:
        sniffed = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t|")
        delimiter = sniffed.delimiter
    except csv.Error:
        pass
    rows = [list(r) for r in csv.reader(io.StringIO(raw), delimiter=delimiter)]
    lines = _rows_to_lines(rows)
    text = _clean("\n".join(lines))
    if not text:
        return Loaded(False, error="The file held no rows.")
    return Loaded(True, text=text, media_type="text/csv",
                  note="%d record(s), one line each." % max(len(rows) - 1, 0))


def _load_xlsx(data: bytes) -> Loaded:
    try:
        from openpyxl import load_workbook
    except ImportError:                                            # pragma: no cover
        return Loaded(False, error="Reading .xlsx needs the openpyxl package.")
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:                                       # noqa: BLE001
        return Loaded(False, error="This file could not be opened as a workbook: %s"
                                   % str(exc)[:160])
    blocks: List[str] = []
    sections: List[Dict[str, Any]] = []
    offset = 0
    for sheet in book.worksheets:
        rows = [["" if c is None else str(c) for c in row]
                for row in sheet.iter_rows(values_only=True)]
        lines = _rows_to_lines(rows, label="Sheet: " + str(sheet.title))
        if len(lines) <= 1:
            continue
        sections.append({"title": str(sheet.title), "level": 2, "offset": offset})
        block = "\n".join(lines)
        blocks.append(block)
        offset += len(block) + 2
    book.close()
    text = _clean("\n\n".join(blocks))
    if not text:
        return Loaded(False, error="Every sheet in the workbook was empty.")
    return Loaded(True, text=text, sections=sections,
                  media_type="application/vnd.openxmlformats-officedocument."
                             "spreadsheetml.sheet",
                  note="%d sheet(s) read." % len(sections))


def _flatten(value: Any, prefix: str = "") -> List[str]:
    if isinstance(value, dict):
        out: List[str] = []
        for key, item in value.items():
            out.extend(_flatten(item, (prefix + "." if prefix else "") + str(key)))
        return out
    if isinstance(value, list):
        out = []
        for index, item in enumerate(value):
            out.extend(_flatten(item, "%s[%d]" % (prefix, index)))
        return out
    text = str(value).strip()
    if not text:
        return []
    return ["%s: %s" % (prefix, text) if prefix else text]


def _load_json(data: bytes) -> Loaded:
    try:
        parsed = json.loads(_decode(data))
    except json.JSONDecodeError as exc:
        return Loaded(False, error="This is not valid JSON: %s" % str(exc)[:160])
    text = _clean("\n".join(_flatten(parsed)))
    if not text:
        return Loaded(False, error="The JSON held no values.")
    return Loaded(True, text=text, media_type="application/json")


def _load_html(data: bytes) -> Loaded:
    raw = _decode(data)
    title = ""
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    if match:
        title = _TAG.sub(" ", match.group(1)).strip()
    text = _clean(_html_to_text(raw))
    if not text:
        return Loaded(False, error="The page held no readable text.")
    sections = [{"title": title, "level": 1, "offset": 0}] if title else []
    return Loaded(True, text=text, sections=sections, media_type="text/html",
                  note="Scripts, styles and markup were removed.")


def _load_text(data: bytes) -> Loaded:
    text = _clean(_decode(data))
    if not text:
        return Loaded(False, error="The file was empty.")
    return Loaded(True, text=text, sections=_sections_from_markdown(text),
                  media_type="text/plain")


# -------------------------------------------------------------------- entry
def load_bytes(data: bytes, filename: str) -> Loaded:
    """Turn an uploaded file into text. Never raises - it reports instead."""
    if not data:
        return Loaded(False, error="The file was empty.")
    if len(data) > MAX_BYTES:
        return Loaded(False, error="The file is %.1f MB; the limit is %d MB."
                                   % (len(data) / 1048576.0, MAX_BYTES // 1048576))

    extension = extension_of(filename)
    try:
        if extension == ".pdf":
            loaded = _load_pdf(data)
        elif extension == ".docx":
            loaded = _load_docx(data)
        elif extension in (".csv", ".tsv"):
            loaded = _load_csv(data, "\t" if extension == ".tsv" else ",")
        elif extension in (".xlsx", ".xlsm"):
            loaded = _load_xlsx(data)
        elif extension == ".json":
            loaded = _load_json(data)
        elif extension in (".html", ".htm"):
            loaded = _load_html(data)
        elif extension in TEXT_EXTENSIONS:
            loaded = _load_text(data)
        else:
            return Loaded(False, error=(
                "%s files are not read. Supported: %s."
                % (extension or "Extensionless", ", ".join(sorted(SUPPORTED)))))
    except Exception as exc:                                       # noqa: BLE001
        log.exception("loader failed for %s", filename)
        return Loaded(False, error="The file could not be read: %s" % str(exc)[:200])

    if loaded.ok and loaded.characters > MAX_CHARACTERS:
        loaded.text = loaded.text[:MAX_CHARACTERS]
        loaded.note = (loaded.note + " " if loaded.note else "") + (
            "Only the first %d characters were indexed." % MAX_CHARACTERS)
    return loaded


def load_text(text: str, title: str = "") -> Loaded:
    """Text pasted straight into the console."""
    cleaned = _clean(text or "")
    if not cleaned:
        return Loaded(False, error="Nothing was pasted.")
    if len(cleaned) > MAX_CHARACTERS:
        cleaned = cleaned[:MAX_CHARACTERS]
    sections = _sections_from_markdown(cleaned)
    if title:
        sections.insert(0, {"title": title, "level": 1, "offset": 0})
    return Loaded(True, text=cleaned, sections=sections, media_type="text/plain")


async def load_url(url: str, timeout: float = 20.0) -> Tuple[Loaded, str]:
    """Fetch a page and read it. Returns the result and the resolved title.

    Only http and https, and the body is read as bytes and then dispatched the same
    way an upload is - so a URL that serves a PDF is read as a PDF rather than as a
    screenful of binary.
    """
    import httpx

    if not re.match(r"(?i)^https?://", (url or "").strip()):
        return Loaded(False, error="Only http:// and https:// addresses can be fetched."), ""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                     headers={"User-Agent": "Saarthi-KB/1.0"}) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        return Loaded(False, error="The page could not be fetched: %s" % str(exc)[:160]), ""
    if response.status_code != 200:
        return Loaded(False, error="The page answered HTTP %d." % response.status_code), ""

    content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
    name = url.split("?")[0].rstrip("/").split("/")[-1] or "page"
    if "." not in name:
        name += {"application/pdf": ".pdf", "text/plain": ".txt",
                 "application/json": ".json"}.get(content_type, ".html")

    loaded = load_bytes(response.content, name)
    title = ""
    if loaded.sections:
        title = str(loaded.sections[0].get("title") or "")
    return loaded, title
