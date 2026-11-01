"""Fetch YouTube metadata + transcript. No LLM calls, nothing downloaded but text.

Adapted from the Hermes skill ~/.hermes/skills/youtube_learner/extract.py (copied, not imported,
so Hermes updates can't break the app).
"""
import re
from urllib.parse import parse_qs, urlparse

BLOCK_SECONDS = 60  # fallback grouping when a video has no chapters
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_video_id(value: str) -> str:
    value = value.strip()
    if _ID_RE.match(value):
        return value
    parsed = urlparse(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        else:  # /shorts/ID, /embed/ID, /live/ID
            parts = [p for p in parsed.path.split("/") if p]
            candidate = parts[1] if len(parts) >= 2 else ""
    else:
        candidate = ""
    if not _ID_RE.match(candidate):
        raise ValueError("That doesn't look like a YouTube video link.")
    return candidate


def fetch_metadata(video_id: str) -> dict:
    from yt_dlp import YoutubeDL

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    return {
        "title": info.get("title") or "Untitled",
        "channel": info.get("channel") or info.get("uploader") or "Unknown",
        "description": info.get("description") or "",
        "duration_s": info.get("duration"),
        "upload_date": info.get("upload_date"),  # YYYYMMDD
        "chapters": info.get("chapters") or [],
        "tags": info.get("tags") or [],
        "thumbnail": info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    }


def fetch_transcript(video_id: str) -> list[dict]:
    """Return [{'start': float, 'text': str}]. Prefers English, falls back to any language."""
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
    except Exception:
        transcripts = list(api.list(video_id))
        if not transcripts:
            raise
        fetched = transcripts[0].fetch()
    out = []
    for s in fetched.to_raw_data():
        text = (s.get("text") or "").replace("\n", " ").strip()
        if text:
            out.append({"start": float(s.get("start") or 0.0), "text": text})
    return out


def fmt_ts(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def group_transcript(segments: list[dict], chapters: list[dict]) -> list[dict]:
    """Return [{'heading', 'start', 'text'}] grouped by chapter or fixed time blocks."""
    groups = []
    if chapters:
        for ch in chapters:
            start = float(ch.get("start_time", 0))
            end = float(ch.get("end_time", float("inf")))
            text = " ".join(s["text"] for s in segments if start <= s["start"] < end)
            if text:
                groups.append({"heading": ch.get("title") or "", "start": start, "text": text})
        if groups:
            return groups
    buf, block_start = [], 0.0
    for s in segments:
        if buf and s["start"] - block_start >= BLOCK_SECONDS:
            groups.append({"heading": "", "start": block_start, "text": " ".join(buf)})
            buf = []
        if not buf:
            block_start = s["start"]
        buf.append(s["text"])
    if buf:
        groups.append({"heading": "", "start": block_start, "text": " ".join(buf)})
    return groups


def transcript_markdown(groups: list[dict]) -> str:
    lines = []
    for g in groups:
        lines += [f"### [{fmt_ts(g['start'])}] {g['heading']}".rstrip(), "", g["text"], ""]
    return "\n".join(lines).strip()


class NoTranscript(RuntimeError):
    """No readable captions. .video holds everything else extract() returns (no transcript)."""
    def __init__(self, msg: str, video: dict):
        super().__init__(msg)
        self.video = video


def extract(value: str) -> dict:
    """Fetch everything the app stores for one video. Raises with a readable message."""
    video_id = parse_video_id(value)
    try:
        meta = fetch_metadata(video_id)
    except Exception as exc:
        raise RuntimeError(f"Couldn't read the video's details (private, removed, or no network?): {exc}") from exc
    video = {"video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}", "title": meta["title"],
             "channel": meta["channel"], "duration_s": meta["duration_s"], "upload_date": meta["upload_date"],
             "description": meta["description"], "thumbnail": meta["thumbnail"]}
    try:
        segments = fetch_transcript(video_id)
    except Exception as exc:  # captions disabled, IP block, etc.
        raise NoTranscript(f"This video has no transcript we can read ({type(exc).__name__}).", video) from exc
    if not segments:
        raise NoTranscript("The transcript came back empty.", video)
    groups = group_transcript(segments, meta["chapters"])
    return {**video, "transcript_md": transcript_markdown(groups),
            "word_count": sum(len(s["text"].split()) for s in segments)}
