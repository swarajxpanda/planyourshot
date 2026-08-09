from pathlib import Path
import re 
from pypdf import PdfReader

def load_text(path: str) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".txt":
        return p.read_text(encoding="utf-8")
    if suffix == ".pdf":
        reader = PdfReader(str(p))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    raise ValueError(f"Unsupported file type: {suffix} (use .txt or .pdf)")

def _strip_page_numbers(text: str) -> str:
    return "\n".join(
        line for line in text.split("\n")
        if not re.fullmatch(r"\s*\d+\.?\s*", line)
    )