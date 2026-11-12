"""Turn an uploaded PDF / DOCX / TXT into clean screenplay text."""
import io
import re
import zipfile
from pathlib import Path

from planyourshot.loader import _strip_page_numbers

SUPPORTED = {"pdf", "docx", "txt"}


class ParseError(Exception):
    pass


def sniff(ext: str, data: bytes) -> None:
    """Reject files whose content doesn't match their extension."""
    if ext == "pdf" and not data.lstrip()[:5].startswith(b"%PDF"):
        raise ParseError("This file doesn't look like a PDF.")
    if ext == "docx" and not zipfile.is_zipfile(io.BytesIO(data)):
        raise ParseError("This file doesn't look like a Word (.docx) document.")
    if ext == "txt" and b"\x00" in data[:4096]:
        raise ParseError("This file doesn't look like plain text.")


def _pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ParseError("This PDF is password-protected.") from exc
    pages = []
    for page in reader.pages:
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except Exception:  # noqa: BLE001 - layout mode is newer; fall back
            text = page.extract_text() or ""
        pages.append(text)
    return _strip_page_numbers("\n\n".join(pages))


def _docx(path: Path) -> str:
    import docx

    doc = docx.Document(str(path))
    return "\n\n".join(p.text for p in doc.paragraphs)


def _txt(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode("latin-1")


def clean_text(text: str) -> str:
    """Drop NUL characters: Postgres text columns can't store them."""
    return text.replace("\x00", "")


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n").replace("\x00", "")
    lines = [ln.rstrip() for ln in text.split("\n")]
    # PDF layout extraction indents everything by the page margin; remove the
    # common indent so headings start at column 0 but relative indents survive.
    indents = [len(ln) - len(ln.lstrip(" ")) for ln in lines if ln.strip()]
    if indents and min(indents) > 0:
        cut = min(indents)
        lines = [ln[cut:] if ln.strip() else "" for ln in lines]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text(path: Path, ext: str) -> str:
    try:
        raw = {"pdf": _pdf, "docx": _docx, "txt": _txt}[ext](path)
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Couldn't read this {ext.upper()} file — it may be corrupted.") from exc
    text = normalize(raw)
    if len(text) < 20:
        if ext == "pdf":
            raise ParseError(
                "No selectable text found. This looks like a scanned PDF — export a text-based PDF and try again."
            )
        raise ParseError("The file is empty.")
    return text
