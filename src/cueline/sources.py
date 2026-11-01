"""Turn inputs (YouTube links, files, pasted text, Hermes transcripts) into source records.

Content keeps location markers the chat can cite: `### [m:ss]` for videos, `[p.N]` for PDF pages.
"""
import io
import re
from pathlib import Path

from . import youtube

HERMES_RAW_DIR = Path.home() / ".youtube_learner" / "raw"  # read-only import
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm"}


def _words(text: str) -> int:
    return len(text.split())


def from_youtube(url: str) -> dict:
    v = youtube.extract(url)
    return {
        "kind": "youtube", "title": v["title"], "url": v["url"], "content": v["transcript_md"],
        "word_count": v["word_count"],
        "meta": {"video_id": v["video_id"], "channel": v["channel"], "duration_s": v["duration_s"],
                 "upload_date": v["upload_date"], "thumbnail": v["thumbnail"],
                 "description": (v["description"] or "")[:3000]},
    }


MIN_WEB_CHARS = 300
BLOCKED_HINT = ("The site may block automated reading, need JavaScript, or sit behind a login or paywall. "
                "Try another page, or copy the text and use Paste text.")


def from_web(url: str) -> dict:
    """A blog post or web page as a source: the main text of the page (or a linked PDF). Raises ValueError with a
    plain reason when the page can't be read, so nothing half-empty is ever saved."""
    import html as htmllib
    from . import feeds  # safe fetch: public addresses only, 5 MB cap
    url = url.strip()
    if not re.match(r"https?://", url):
        url = "https://" + url
    try:
        body, ctype, final = feeds.fetch(url)
    except ValueError as exc:
        msg = str(exc)
        if re.search(r"\b(401|403|429|451|503)\b", msg):
            raise ValueError(f"The site refused the request ({msg.rstrip('.')}). {BLOCKED_HINT}") from exc
        raise
    ctype = (ctype or "").lower()
    if "pdf" in ctype or final.lower().split("?")[0].endswith(".pdf"):
        record = from_file(Path(final.split("?")[0]).name or "page.pdf", body)
        return {**record, "url": final, "meta": {**record["meta"], "site": _site(final)}}
    if ctype and "html" not in ctype and "text/plain" not in ctype:
        raise ValueError(f"That link is not a web page ({ctype.split(';')[0]}).")
    page = body.decode("utf-8", errors="replace")
    text = feeds.article_text(page) if "html" in ctype or "<html" in page[:2000].lower() else page.strip()
    if len(text) < MIN_WEB_CHARS:
        raise ValueError(f"Cueline found almost no readable text on that page. {BLOCKED_HINT}")
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', page, re.I) or \
        re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    title = re.sub(r"\s+", " ", htmllib.unescape(m.group(1))).strip()[:150] if m else ""
    return {"kind": "web", "title": title or _site(final), "url": final, "content": text, "word_count": _words(text),
            "meta": {"site": _site(final)}}


def _site(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "").removeprefix("www.")


def from_text(title: str, text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("The pasted text is empty.")
    title = title.strip() or (text.splitlines()[0][:70] if text else "Pasted text")
    return {"kind": "text", "title": title, "url": None, "content": text, "word_count": _words(text), "meta": {}}


def from_file(filename: str, data: bytes) -> dict:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("That file is larger than 30 MB.")
    ext = Path(filename).suffix.lower()
    title = Path(filename).stem.replace("_", " ").strip() or filename
    if ext == ".pdf":
        content, pages = _pdf_text(data)
        meta = {"filename": filename, "pages": pages}
        kind = "pdf"
    elif ext == ".docx":
        content, meta, kind = _docx_text(data), {"filename": filename}, "doc"
    elif ext in TEXT_EXTENSIONS:
        content, meta, kind = data.decode("utf-8", errors="replace"), {"filename": filename}, "doc"
    else:
        raise ValueError(f"Cueline can't read {ext or 'this'} files yet. Try PDF, DOCX, TXT or MD.")
    content = content.strip()
    if not content:
        raise ValueError("No readable text found in that file (scanned PDFs need OCR, which isn't supported yet).")
    return {"kind": kind, "title": title, "url": None, "content": content, "word_count": _words(content), "meta": meta}


def _pdf_text(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(f"[p.{i}]\n{text}")
    return "\n\n".join(parts), len(reader.pages)


def _docx_text(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    lines = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower() if para.style is not None else ""
        if style.startswith("heading"):
            level = re.sub(r"\D", "", style) or "2"
            text = "#" * min(int(level) + 1, 4) + " " + text
        lines.append(text)
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n\n".join(lines)


# ---------- Hermes transcript library (read-only) ----------
def _frontmatter(text: str) -> tuple[dict, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            meta[key.strip()] = value
    return meta, text[match.end():]


def hermes_transcripts() -> list[dict]:
    out = []
    if not HERMES_RAW_DIR.is_dir():
        return out
    for path in sorted(HERMES_RAW_DIR.glob("*.md")):
        try:
            meta, _ = _frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        out.append({"file": path.name, "title": meta.get("title") or path.stem,
                    "channel": meta.get("channel", ""), "duration": meta.get("duration", ""),
                    "video_id": meta.get("video_id", "")})
    return out


def from_hermes_transcript(filename: str) -> dict:
    path = (HERMES_RAW_DIR / filename).resolve()
    if path.parent != HERMES_RAW_DIR.resolve() or path.suffix != ".md" or not path.is_file():
        raise ValueError("Unknown Hermes transcript.")
    meta, body = _frontmatter(path.read_text(encoding="utf-8"))
    transcript = body.split("## Transcript", 1)[-1].strip()
    video_id = meta.get("video_id", "")
    return {
        "kind": "youtube", "title": meta.get("title") or path.stem,
        "url": meta.get("url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None),
        "content": transcript, "word_count": _words(transcript),
        "meta": {"video_id": video_id, "channel": meta.get("channel"), "duration": meta.get("duration"),
                 "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None,
                 "imported_from": "hermes"},
    }
