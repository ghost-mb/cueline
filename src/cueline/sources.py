"""Turn inputs (YouTube links, files, pasted text, Hermes transcripts) into source records.

Content keeps location markers the chat can cite: `### [m:ss]` for videos, `[p.N]` for PDF pages.
"""
import io
import re
import threading
from pathlib import Path

from . import youtube

HERMES_RAW_DIR = Path.home() / ".youtube_learner" / "raw"  # read-only import
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
OCR_MAX_PAGES = 30  # scanned pages read per PDF (each takes a few seconds on a CPU)
OCR_HINT = ("Scanned pages need Cueline's OCR add-on (reads text from images on this computer). Install it with: "
            "uv tool install --force 'cueline[ocr] @ git+https://github.com/ghost-mb/cueline'")
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm"}


def _words(text: str) -> int:
    return len(text.split())


AUDIO_PENDING = "(No captions: transcribing the audio on this computer. The transcript appears here when it is ready.)"


def from_youtube(url: str) -> dict:
    """A YouTube video as a source. Without readable captions it falls back to transcribing the audio locally:
    the record then has meta.youtube_audio and placeholder content, and the caller must stt.queue() it."""
    from . import stt
    extra = {}
    try:
        v = youtube.extract(url)
        content, words = v["transcript_md"], v["word_count"]
    except youtube.NoTranscript as exc:
        v = exc.video
        try:
            stt.need_audio("faster_whisper", "yt_dlp")
        except ValueError:
            raise ValueError(f"{exc} {stt.AUDIO_HINT.replace('This needs', 'To transcribe its audio instead, add')}")
        if (v["duration_s"] or 0) > stt.MAX_SECONDS:
            raise ValueError(f"{exc} It is also longer than 4 hours, too long to transcribe.")
        content, words, extra = AUDIO_PENDING, 0, {"youtube_audio": True}
    return {
        "kind": "youtube", "title": v["title"], "url": v["url"], "content": content, "word_count": words,
        "meta": {"video_id": v["video_id"], "channel": v["channel"], "duration_s": v["duration_s"],
                 "upload_date": v["upload_date"], "thumbnail": v["thumbnail"],
                 "description": (v["description"] or "")[:3000], **extra},
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
        hint = f" {OCR_HINT}" if kind == "pdf" and not _ocr_ready() else ""
        raise ValueError(f"No readable text found in that file.{hint}")
    return {"kind": kind, "title": title, "url": None, "content": content, "word_count": _words(content), "meta": meta}


def _pdf_text(data: bytes) -> tuple[str, int]:
    """Text per page with [p.N] markers. Pages without a text layer (scans) are read with OCR when installed."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts, scanned = {}, []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            parts[i] = text
        else:
            scanned.append(i)
    if scanned and _ocr_ready():
        parts.update(_ocr_pages(data, scanned[:OCR_MAX_PAGES]))
        if len(scanned) > OCR_MAX_PAGES:
            parts[scanned[OCR_MAX_PAGES]] = (f"[Only the first {OCR_MAX_PAGES} scanned pages were read. "
                                             "Split the PDF to add the rest.]")
    return "\n\n".join(f"[p.{i}]\n{parts[i]}" for i in sorted(parts)), len(reader.pages)


def _ocr_ready() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) for m in ("rapidocr", "pypdfium2", "onnxruntime"))


_ocr_engine = None
_ocr_lock = threading.Lock()  # one OCR job at a time: shared engine, and it saturates the CPU anyway


def _ocr_pages(data: bytes, pages: list[int]) -> dict[int, str]:
    """OCR the given 1-based pages on this computer (rapidocr). Returns {page: text} for pages with any text."""
    with _ocr_lock:
        return _ocr_locked(data, pages)


def _ocr_locked(data: bytes, pages: list[int]) -> dict[int, str]:
    global _ocr_engine
    import numpy as np
    import pypdfium2 as pdfium
    from rapidocr import RapidOCR
    if _ocr_engine is None:
        import logging
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)  # it logs several INFO lines per page
        _ocr_engine = RapidOCR()
    pdf = pdfium.PdfDocument(data)
    out = {}
    try:
        for n in pages:
            image = pdf[n - 1].render(scale=2).to_pil().convert("RGB")  # ~150 dpi for a typical scan
            lines = _ocr_engine(np.array(image)).txts or ()
            if lines:
                out[n] = "\n".join(lines)
    finally:
        pdf.close()
    return out


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
